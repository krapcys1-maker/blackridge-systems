import json
from pathlib import Path

import httpx
import pytest

from blackridge.errors import BlackridgeError, ConfigurationError, ExternalToolError
from blackridge.operator import DeepSeekBackend, load_secret


class _Response:
    def __init__(self, value: dict, status_code: int = 200) -> None:
        self.value = value
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("failure", request=request, response=response)

    def json(self) -> dict:
        return self.value


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs: object) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


def test_load_secret_reads_only_requested_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=do-not-return\nDEEPSEEK_API_KEY='wanted'\n", encoding="utf-8")
    assert load_secret("DEEPSEEK_API_KEY", env_file=env_file) == "wanted"


def test_load_secret_rejects_missing_value(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="required secret"):
        load_secret("BLACKRIDGE_DEFINITELY_MISSING", env_file=tmp_path / ".env")


def test_deepseek_completion_is_structured_and_costed() -> None:
    envelope = {
        "id": "response-1",
        "model": "deepseek-v4-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"answer": 42})},
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 40},
        },
    }
    client = _Client(_Response(envelope))
    backend = DeepSeekBackend(api_key="secret", client=client)  # type: ignore[arg-type]
    completion = backend.complete_json(system="Return JSON.", user="Return JSON now.")
    assert completion.content == {"answer": 42}
    assert completion.usage.cached_input_tokens == 40
    assert completion.usage.estimated_cost_usd > 0
    assert client.calls[0]["url"] == DeepSeekBackend.endpoint
    headers = client.calls[0]["headers"]
    assert isinstance(headers, dict) and headers["Authorization"] == "Bearer secret"


def test_deepseek_rejects_non_stop_completion() -> None:
    client = _Client(
        _Response(
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {"finish_reason": "length", "message": {"content": '{"partial": true}'}}
                ],
                "usage": {},
            }
        )
    )
    backend = DeepSeekBackend(api_key="secret", client=client)  # type: ignore[arg-type]
    with pytest.raises(ExternalToolError, match="did not finish cleanly"):
        backend.complete_json(system="JSON", user="JSON")


def test_deepseek_converts_truncated_completion_json_to_bounded_external_error() -> None:
    client = _Client(
        _Response(
            {
                "model": "deepseek-v4-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"files": ['}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2},
            }
        )
    )
    backend = DeepSeekBackend(api_key="secret", client=client)  # type: ignore[arg-type]

    with pytest.raises(ExternalToolError, match="invalid JSON completion envelope"):
        backend.complete_json(system="JSON", user="JSON")

    assert backend.calls_made == 1
    assert backend.total_tokens == 0
    assert backend.total_estimated_cost_usd == 0


def test_deepseek_rejects_cost_bound_before_network_call() -> None:
    client = _Client(_Response({}))
    backend = DeepSeekBackend(
        api_key="secret",
        client=client,  # type: ignore[arg-type]
        max_total_cost_usd=0.000001,
    )
    with pytest.raises(BlackridgeError, match="cost budget"):
        backend.complete_json(system="JSON", user="JSON")
    assert client.calls == []


def test_deepseek_enforces_call_budget() -> None:
    envelope = {
        "id": "response-1",
        "model": "deepseek-v4-flash",
        "choices": [{"finish_reason": "stop", "message": {"content": '{"answer": 42}'}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    client = _Client(_Response(envelope))
    backend = DeepSeekBackend(
        api_key="secret",
        client=client,  # type: ignore[arg-type]
        max_total_calls=1,
    )
    backend.complete_json(system="JSON", user="JSON")
    with pytest.raises(BlackridgeError, match="call budget"):
        backend.complete_json(system="JSON", user="JSON")
    assert len(client.calls) == 1


def test_deepseek_rejects_non_finite_prices() -> None:
    with pytest.raises(ConfigurationError, match="finite"):
        DeepSeekBackend(api_key="secret", input_price_per_million=float("nan"))
