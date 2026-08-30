"""Provider-neutral model boundary with retained, redacted usage evidence."""

from __future__ import annotations

import json
import math
import os
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from blackridge.errors import BlackridgeError, ConfigurationError, ExternalToolError


class AgentUsage(BaseModel):
    """Provider-reported token usage and a conservative local cost estimate."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)


class AgentCompletion(BaseModel):
    """One structured operator response without provider credentials or hidden reasoning."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    response_id: str | None = None
    finish_reason: str
    content: dict[str, Any]
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    usage: AgentUsage


class AgentBackend(Protocol):
    """Minimal replaceable contract shared by subscription and API-backed operators."""

    @property
    def identity(self) -> str: ...

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> AgentCompletion: ...


def load_secret(name: str, *, env_file: Path | None = None) -> str:
    """Load one named secret without mutating or returning unrelated environment values."""

    value = os.environ.get(name, "").strip()
    if not value and env_file is not None and env_file.is_file():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, candidate = line.split("=", 1)
            if key.strip() == name:
                value = candidate.strip().strip("\"'")
                break
    if not value:
        raise ConfigurationError(f"required secret is unavailable: {name}")
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise ConfigurationError(f"secret contains forbidden control characters: {name}")
    return value


class DeepSeekBackend:
    """DeepSeek JSON-mode adapter behind the provider-neutral operator contract."""

    endpoint = "https://api.deepseek.com/chat/completions"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-v4-flash",
        timeout_seconds: float = 300,
        max_total_cost_usd: float = 2.0,
        max_total_calls: int = 8,
        max_total_tokens: int = 200_000,
        input_price_per_million: float = 0.44,
        cached_input_price_per_million: float = 0.014,
        output_price_per_million: float = 1.32,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ConfigurationError("DeepSeek API key cannot be empty")
        if max_total_cost_usd <= 0:
            raise ConfigurationError("max_total_cost_usd must be positive")
        if max_total_calls < 1:
            raise ConfigurationError("max_total_calls must be positive")
        if max_total_tokens < 1:
            raise ConfigurationError("max_total_tokens must be positive")
        if any(
            price < 0
            for price in self._validated_prices(
                input_price_per_million,
                cached_input_price_per_million,
                output_price_per_million,
            )
        ):
            raise ConfigurationError("operator token prices cannot be negative")
        self._api_key = api_key
        self.model = model
        self.max_total_cost_usd = max_total_cost_usd
        self.max_total_calls = max_total_calls
        self.max_total_tokens = max_total_tokens
        self.calls_made = 0
        self.total_tokens = 0
        self.total_estimated_cost_usd = 0.0
        self._prices = (
            input_price_per_million,
            cached_input_price_per_million,
            output_price_per_million,
        )
        self._client = client or httpx.Client(timeout=timeout_seconds)

    @staticmethod
    def _validated_prices(*prices: float) -> tuple[float, ...]:
        if not all(isinstance(price, (int, float)) for price in prices):
            raise ConfigurationError("operator token prices must be numeric")
        validated = tuple(float(price) for price in prices)
        if not all(math.isfinite(price) for price in validated):
            raise ConfigurationError("operator token prices must be finite")
        return validated

    @property
    def identity(self) -> str:
        return f"deepseek:{self.model}"

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> AgentCompletion:
        if self.calls_made >= self.max_total_calls:
            raise BlackridgeError("DeepSeek call budget is exhausted")
        if not 1 <= max_tokens <= 32768:
            raise ConfigurationError("max_tokens must be between 1 and 32768")
        if not 0 <= temperature <= 2:
            raise ConfigurationError("temperature must be between 0 and 2")
        prompt_bytes = len(system.encode("utf-8")) + len(user.encode("utf-8"))
        if prompt_bytes > 1_000_000:
            raise ConfigurationError("operator prompt exceeds the one-megabyte safety limit")
        input_price, _, output_price = self._prices
        conservative_token_bound = prompt_bytes + max_tokens
        if self.total_tokens + conservative_token_bound > self.max_total_tokens:
            raise BlackridgeError("DeepSeek token budget would be exceeded by this request")
        conservative_cost_bound = (
            prompt_bytes * input_price + max_tokens * output_price
        ) / 1_000_000
        if self.total_estimated_cost_usd + conservative_cost_bound > self.max_total_cost_usd:
            raise BlackridgeError("DeepSeek cost budget would be exceeded by this request")

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        self.calls_made += 1
        try:
            response = self._client.post(
                self.endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            response_content = getattr(response, "content", None)
            if isinstance(response_content, bytes) and len(response_content) > 2_000_000:
                raise ExternalToolError("DeepSeek response exceeded the two-megabyte limit")
            envelope = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise ExternalToolError(f"DeepSeek returned HTTP {status}") from exc
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise ExternalToolError(f"DeepSeek request failed: {type(exc).__name__}") from exc

        try:
            choice = envelope["choices"][0]
            message = choice["message"]
            raw_content = message["content"]
            finish_reason = str(choice["finish_reason"])
            content = json.loads(raw_content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ExternalToolError(
                "DeepSeek returned an invalid JSON completion envelope"
            ) from exc
        if not isinstance(content, dict):
            raise ExternalToolError("DeepSeek JSON completion must be an object")
        if finish_reason != "stop":
            raise ExternalToolError(f"DeepSeek completion did not finish cleanly: {finish_reason}")

        usage_data = envelope.get("usage") or {}
        if not isinstance(usage_data, dict):
            raise ExternalToolError("DeepSeek returned invalid token usage")
        try:
            input_tokens = int(usage_data.get("prompt_tokens", 0))
            output_tokens = int(usage_data.get("completion_tokens", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExternalToolError("DeepSeek returned invalid token counts") from exc
        if input_tokens < 0 or output_tokens < 0:
            raise ExternalToolError("DeepSeek returned negative token counts")
        details = usage_data.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            details = {}
        raw_cached_tokens = details.get(
            "cached_tokens",
            usage_data.get("prompt_cache_hit_tokens", 0),
        )
        if raw_cached_tokens is None:
            raw_cached_tokens = 0
        try:
            cached_tokens = int(raw_cached_tokens)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExternalToolError("DeepSeek returned invalid cached token counts") from exc
        cached_tokens = min(input_tokens, max(0, cached_tokens))
        uncached_tokens = input_tokens - cached_tokens
        input_price, cached_price, output_price = self._prices
        cost = round(
            (
                uncached_tokens * input_price
                + cached_tokens * cached_price
                + output_tokens * output_price
            )
            / 1_000_000,
            8,
        )
        response_tokens = input_tokens + output_tokens
        if self.total_tokens + response_tokens > self.max_total_tokens:
            raise BlackridgeError(
                "DeepSeek response crossed the configured token budget; the response was rejected"
            )
        if self.total_estimated_cost_usd + cost > self.max_total_cost_usd:
            raise BlackridgeError(
                "DeepSeek response crossed the configured experiment cost budget; "
                "the response was rejected"
            )
        self.total_tokens += response_tokens
        self.total_estimated_cost_usd += cost
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return AgentCompletion(
            provider="deepseek",
            model=str(envelope.get("model") or self.model),
            response_id=(str(envelope["id"]) if envelope.get("id") else None),
            finish_reason=finish_reason,
            content=content,
            content_sha256=sha256(canonical).hexdigest(),
            usage=AgentUsage(
                input_tokens=input_tokens,
                cached_input_tokens=cached_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
            ),
        )
