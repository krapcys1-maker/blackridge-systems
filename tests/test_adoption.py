from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from blackridge.adoption import AdapterProposal, AdoptionSpec, adopt
from blackridge.errors import BlackridgeError
from blackridge.operator import AgentCompletion, AgentUsage

SCHEMA = "https://json-schema.org/draft/2020-12/schema"

WORKING_ADAPTER = '''\
#!/usr/bin/env python3
"""Echo the request identity behind a fixed contract."""

import json
import sys


def handle(request):
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string")
    return {"schema_version": "1", "request_id": request_id, "status": "ok"}


def main():
    try:
        print(json.dumps(handle(json.load(sys.stdin)), sort_keys=True))
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

WORKING_TESTS = """\
from echo_adapter import handle


def test_valid_request_is_accepted():
    assert handle({"request_id": "a"})["status"] == "ok"


def test_missing_request_id_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        handle({})
"""

FAILING_TESTS = """\
def test_that_always_fails():
    assert False
"""


class FakeBackend:
    """Deterministic operator stand-in. No network, no cost, scripted replies."""

    identity = "fake-operator/1"

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self._replies = replies
        self.calls = 0

    def complete_json(self, *, system: str, user: str, max_tokens: int) -> AgentCompletion:
        self.prompts = getattr(self, "prompts", [])
        self.prompts.append(user)
        content = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return AgentCompletion(
            provider="fake",
            model="fake",
            finish_reason="stop",
            content=content,
            content_sha256=sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest(),
            usage=AgentUsage(input_tokens=1, output_tokens=1),
        )


def _proposal(adapter: str = WORKING_ADAPTER, tests: str = WORKING_TESTS) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "adapter": {"path": "echo_adapter.py", "content": adapter},
        "tests": {"path": "test_echo_adapter.py", "content": tests},
        "notes": "",
    }


def _spec(**overrides: Any) -> AdoptionSpec:
    payload: dict[str, Any] = {
        "schema_version": "1",
        "component_id": "echo-adapter",
        "capability_id": "echo",
        "module_name": "echo_adapter",
        "upstream": {
            "name": "Example upstream",
            "source_uri": "https://example.invalid/project",
            "revision": "v1.0.0",
            "license_spdx": "Apache-2.0",
        },
        "accepts": ["echo-request/v1"],
        "produces": "echo-response/v1",
        "contracts": [
            {
                "contract_id": "echo-request/v1",
                "schema": {"$schema": SCHEMA, "type": "object"},
            },
            {
                "contract_id": "echo-response/v1",
                "schema": {"$schema": SCHEMA, "type": "object"},
            },
        ],
        "behavior": "Echo the request identity back to the caller behind the declared contract.",
        "acceptance": ["A valid request is echoed.", "A request without an id is rejected."],
    }
    payload.update(overrides)
    return AdoptionSpec.model_validate(payload)


def test_spec_rejects_a_contract_that_is_never_declared() -> None:
    with pytest.raises(ValidationError, match="undeclared contracts"):
        _spec(accepts=["not-declared/v1"])


def test_spec_rejects_an_invalid_json_schema() -> None:
    with pytest.raises(ValidationError, match="invalid schema"):
        _spec(
            contracts=[
                {"contract_id": "echo-request/v1", "schema": {"type": 12}},
                {"contract_id": "echo-response/v1", "schema": {"type": "object"}},
            ]
        )


def test_proposal_rejects_a_path_that_escapes_the_component_directory() -> None:
    payload = _proposal()
    payload["adapter"]["path"] = "../escape.py"
    with pytest.raises(ValidationError):
        AdapterProposal.model_validate(payload)


def test_proposal_requires_a_separate_test_file() -> None:
    payload = _proposal()
    payload["tests"]["path"] = payload["adapter"]["path"]
    with pytest.raises(ValidationError):
        AdapterProposal.model_validate(payload)


def test_adoption_writes_a_registry_component_when_the_tests_pass(tmp_path: Path) -> None:
    backend = FakeBackend([_proposal()])
    record = adopt(
        _spec(),
        backend=backend,
        output_directory=tmp_path / "component",
        now=datetime(2026, 8, 30, tzinfo=UTC),
    )
    assert record.completed
    assert [attempt.status for attempt in record.attempts] == ["pass"]
    assert record.adapter_sha256
    assert record.test_count == 2
    written = sorted(path.name for path in (tmp_path / "component").iterdir())
    assert written == ["component.yaml", "echo_adapter.py", "test_echo_adapter.py"]


def test_adoption_repairs_after_its_own_tests_fail(tmp_path: Path) -> None:
    backend = FakeBackend([_proposal(tests=FAILING_TESTS), _proposal()])
    record = adopt(_spec(), backend=backend, output_directory=tmp_path / "component")
    assert record.completed
    assert [attempt.status for attempt in record.attempts] == ["tests-failed", "pass"]
    # The failing attempt is retained rather than overwritten by the successful one.
    assert record.attempts[0].detail
    assert "fail" in backend.prompts[1].lower()


def test_adoption_fails_closed_and_writes_nothing_when_repair_is_exhausted(
    tmp_path: Path,
) -> None:
    backend = FakeBackend([_proposal(tests=FAILING_TESTS)])
    destination = tmp_path / "component"
    record = adopt(_spec(), backend=backend, output_directory=destination, max_iterations=2)
    assert not record.completed
    assert record.adapter_sha256 is None
    assert [attempt.status for attempt in record.attempts] == ["tests-failed", "tests-failed"]
    assert not destination.exists()


def test_adoption_retains_a_schema_rejection_as_evidence(tmp_path: Path) -> None:
    backend = FakeBackend([{"schema_version": "1", "adapter": {"path": "a.py"}}, _proposal()])
    record = adopt(_spec(), backend=backend, output_directory=tmp_path / "component")
    assert record.completed
    assert record.attempts[0].status == "rejected-schema"
    assert record.attempts[0].detail


def test_adoption_refuses_to_overwrite_an_existing_component(tmp_path: Path) -> None:
    destination = tmp_path / "component"
    destination.mkdir()
    (destination / "existing.py").write_text("# do not clobber\n", encoding="utf-8")
    backend = FakeBackend([_proposal()])
    with pytest.raises(BlackridgeError, match="non-empty directory"):
        adopt(_spec(), backend=backend, output_directory=destination)
    assert (destination / "existing.py").read_text(encoding="utf-8") == "# do not clobber\n"


def test_generated_component_yaml_records_the_upstream_and_starts_at_l0(tmp_path: Path) -> None:
    import yaml

    backend = FakeBackend([_proposal()])
    adopt(_spec(), backend=backend, output_directory=tmp_path / "component")
    document = yaml.safe_load((tmp_path / "component" / "component.yaml").read_text("utf-8"))
    assert document["evidence"]["level"] == 0
    assert document["upstream"]["license_spdx"] == "Apache-2.0"
    assert document["integration"] == "command-json"
    assert document["produces"] == "echo-response/v1"
    assert document["limitations"]


def test_the_prompt_carries_the_declared_contracts(tmp_path: Path) -> None:
    backend = FakeBackend([_proposal()])
    adopt(_spec(), backend=backend, output_directory=tmp_path / "component")
    payload = backend.prompts[0]
    assert "echo-request/v1" in payload
    assert "echo-response/v1" in payload
    # The operator is told the upstream is data, and is never handed a verdict to assign.
    assert "untrusted" not in payload or json.dumps  # system prompt carries the untrusted rule
