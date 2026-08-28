"""Natural-language capability planning with deterministic schema validation."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict

from blackridge.models import SystemRequest
from blackridge.operator import AgentBackend, AgentCompletion

_IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ATOMIC_SEARCH_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*")
MIN_PLANNED_CAPABILITIES = 4
MAX_PLANNED_CAPABILITIES = 10
MIN_SEARCHES_PER_CAPABILITY = 2
MAX_SEARCHES_PER_CAPABILITY = 4
MIN_SEARCH_TOKENS = 2
MAX_SEARCH_TOKENS = 4
MAX_BRIEF_BYTES = 100_000


class PlanningRecord(BaseModel):
    """Evidence retained around a model proposal; validation remains deterministic."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    created_at: datetime
    operator: str
    brief_sha256: str
    prompt_sha256: str
    completion: AgentCompletion
    validated_request_sha256: str


def _planning_prompt(brief: str) -> tuple[str, str]:
    system = (
        "You are the intellectual operator of a reuse-first software foundry. Convert the "
        "user brief into a capability graph for discovery and verification. Propose; never "
        "claim that a component works. Output one JSON object only."
    )
    example: dict[str, Any] = {
        "schema_version": "1",
        "name": "example-system",
        "goal": "Build a concrete system whose complete behavior is described here.",
        "constraints": {"reuse_policy": "reuse-first"},
        "capabilities": [
            {
                "id": "example-capability",
                "description": (
                    "Provide one independently replaceable behavior required by the goal."
                ),
                "accepts": ["input-contract/v1"],
                "produces": ["output-contract/v1"],
                "searches": [
                    {
                        "keywords": ["descriptive", "repository", "terms"],
                        "language": None,
                        "stars": None,
                        "updated": None,
                        "license": None,
                    }
                ],
                "seeds": [],
                "acceptance": [
                    {
                        "id": "observable-scenario",
                        "description": (
                            "A concrete representative scenario proves the required behavior."
                        ),
                        "given": "A representative real input.",
                        "when": "The capability is executed through its public boundary.",
                        "then": ["An observable output satisfies the declared contract."],
                    }
                ],
                "required": True,
            }
        ],
    }
    user = f"""Return JSON matching this exact structural example:
{json.dumps(example, indent=2)}

Rules:
- Split the goal into 4-10 independently replaceable capabilities.
- Give every capability 2-4 meaningfully different GitHub search queries.
- Every search query should contain 2-4 short atomic search tokens, not sentences or a list of
  alternative multi-word queries. Example: ["repository", "sandbox", "agent"].
- Search tokens must describe behavior or stable integration boundaries, not invented brands.
- Define explicit accepts/produces contracts and at least one observable acceptance scenario.
- Preserve unknown requirements as constraints; do not invent credentials or claim verification.
- Use lowercase kebab-case identifiers.

USER BRIEF:
{brief}
"""
    return system, user


def validate_planned_request(request: SystemRequest) -> None:
    """Enforce the semantic planning contract independently of the model prompt."""

    capability_count = len(request.capabilities)
    if not MIN_PLANNED_CAPABILITIES <= capability_count <= MAX_PLANNED_CAPABILITIES:
        raise ValueError(
            "planned request must contain "
            f"{MIN_PLANNED_CAPABILITIES}-{MAX_PLANNED_CAPABILITIES} capabilities"
        )
    for capability in request.capabilities:
        if not capability.accepts or not capability.produces:
            raise ValueError(
                f"planned capability {capability.id!r} must declare accepts and produces contracts"
            )
        if not capability.acceptance:
            raise ValueError(
                f"planned capability {capability.id!r} must declare an acceptance scenario"
            )
        search_count = len(capability.searches)
        if not MIN_SEARCHES_PER_CAPABILITY <= search_count <= MAX_SEARCHES_PER_CAPABILITY:
            raise ValueError(
                f"planned capability {capability.id!r} must contain "
                f"{MIN_SEARCHES_PER_CAPABILITY}-{MAX_SEARCHES_PER_CAPABILITY} searches"
            )
        for search in capability.searches:
            token_count = len(search.keywords)
            if not MIN_SEARCH_TOKENS <= token_count <= MAX_SEARCH_TOKENS:
                raise ValueError(
                    f"planned capability {capability.id!r} search must contain "
                    f"{MIN_SEARCH_TOKENS}-{MAX_SEARCH_TOKENS} tokens"
                )
            invalid = [
                token for token in search.keywords if not _ATOMIC_SEARCH_TOKEN.fullmatch(token)
            ]
            if invalid:
                raise ValueError(
                    f"planned capability {capability.id!r} contains non-atomic search tokens: "
                    + ", ".join(repr(token) for token in invalid)
                )


def plan_system(brief: str, *, backend: AgentBackend) -> tuple[SystemRequest, PlanningRecord]:
    cleaned = brief.strip()
    if len(cleaned) < 20:
        raise ValueError("brief must contain at least 20 characters")
    if len(cleaned.encode("utf-8")) > MAX_BRIEF_BYTES:
        raise ValueError(f"brief exceeds the {MAX_BRIEF_BYTES}-byte limit")
    system, user = _planning_prompt(cleaned)
    completion = backend.complete_json(system=system, user=user)
    request = SystemRequest.model_validate(completion.content)
    if not _IDENTIFIER.fullmatch(request.name):
        raise ValueError("planned system name is not lowercase kebab-case")
    validate_planned_request(request)
    canonical = request.model_dump_json()
    record = PlanningRecord(
        created_at=datetime.now(UTC),
        operator=backend.identity,
        brief_sha256=sha256(cleaned.encode("utf-8")).hexdigest(),
        prompt_sha256=sha256((system + "\n" + user).encode("utf-8")).hexdigest(),
        completion=completion,
        validated_request_sha256=sha256(canonical.encode("utf-8")).hexdigest(),
    )
    return request, record
