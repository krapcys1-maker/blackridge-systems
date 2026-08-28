import pytest

from blackridge.models import SystemRequest
from blackridge.operator import AgentCompletion, AgentUsage
from blackridge.planning import plan_system


class _Backend:
    identity = "test:operator"

    def complete_json(self, **_: object) -> AgentCompletion:
        capabilities = []
        for index in range(4):
            capability_id = f"repository-stage-{index}"
            capabilities.append(
                {
                    "id": capability_id,
                    "description": (
                        "Find and verify real repositories through an auditable boundary."
                    ),
                    "accepts": [f"stage-{index}-input/v1"],
                    "produces": [f"stage-{index}-output/v1"],
                    "searches": [
                        {"keywords": ["repository", "search", f"stage{index}"]},
                        {"keywords": ["component", "verification", f"stage{index}"]},
                    ],
                    "seeds": [],
                    "acceptance": [
                        {
                            "id": "find-real-repository",
                            "description": (
                                "A representative query returns an auditable repository."
                            ),
                            "given": "A concrete capability query.",
                            "when": "The search boundary is invoked.",
                            "then": ["At least one exact repository identity is retained."],
                        }
                    ],
                    "required": True,
                }
            )
        content = {
            "schema_version": "1",
            "name": "planned-system",
            "goal": "Build a sufficiently detailed planned system for a real user.",
            "constraints": {"reuse_policy": "reuse-first"},
            "capabilities": capabilities,
        }
        return AgentCompletion(
            provider="test",
            model="fixed",
            finish_reason="stop",
            content=content,
            content_sha256="0" * 64,
            usage=AgentUsage(),
        )


def test_plan_system_validates_model_proposal() -> None:
    request, record = plan_system(
        "Create a reusable system that discovers and verifies software components.",
        backend=_Backend(),
    )
    assert isinstance(request, SystemRequest)
    assert request.name == "planned-system"
    assert record.operator == "test:operator"
    assert len(record.brief_sha256) == 64
    assert len(record.validated_request_sha256) == 64


class _InvalidBackend(_Backend):
    def complete_json(self, **kwargs: object) -> AgentCompletion:
        completion = super().complete_json(**kwargs)
        completion.content["capabilities"] = completion.content["capabilities"][:1]
        return completion


def test_plan_system_rejects_prompt_compliant_shape_when_semantics_are_incomplete() -> None:
    with pytest.raises(ValueError, match="4-10 capabilities"):
        plan_system(
            "Create a reusable system that discovers and verifies software components.",
            backend=_InvalidBackend(),
        )


def test_plan_system_rejects_oversized_brief() -> None:
    with pytest.raises(ValueError, match="byte limit"):
        plan_system("x" * 100_001, backend=_Backend())
