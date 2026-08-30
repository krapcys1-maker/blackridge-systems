"""Tests for the first system this foundry composed from real components.

The auditor is three `command-json` components joined by the solver: two independent
observers fanned out from one request, and a merger fanned in from both. These tests run it
hermetically through the replay path, so no network call is made and the assertions are
deterministic.

Every positive case is paired with the control that must fail.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from blackridge.composition import (
    CompositionDefinition,
    generate_system,
    run_generated_system,
    solve_composition,
)
from blackridge.formats import load_yaml

REPOSITORY = Path(__file__).resolve().parents[1]
SYSTEM = REPOSITORY / "systems" / "supply-chain-auditor-v1"
DEFINITION = SYSTEM / "definition.yaml"
OSV = REPOSITORY / "components" / "osv_scanner_v1" / "osv_scanner.py"
SCORECARD = REPOSITORY / "components" / "scorecard_posture_v1" / "scorecard_posture.py"
MERGER = REPOSITORY / "components" / "audit_merger_v1" / "audit_merger.py"

REPLAY_VULN = {
    "PyPI/jinja2@3.1.2": [
        {
            "id": "GHSA-h5c8-rqwp-cp95",
            "summary": "HTML attribute injection",
            "aliases": ["CVE-2024-22195"],
            "database_specific": {"severity": "MODERATE"},
        }
    ],
    "PyPI/requests@2.31.0": [],
}
REPLAY_SCORECARD = {
    "score": 5.9,
    "checks": [{"name": "License", "score": 10, "reason": "license file detected"}],
}


def _request(**overrides: Any) -> dict[str, Any]:
    request = {
        "schema_version": "1",
        "request_id": "audit-001",
        "repository": "pallets/jinja",
        "packages": [
            {"ecosystem": "PyPI", "name": "jinja2", "version": "3.1.2"},
            {"ecosystem": "PyPI", "name": "requests", "version": "2.31.0"},
        ],
        "replay": {"osv": REPLAY_VULN, "scorecard": REPLAY_SCORECARD},
    }
    request.update(overrides)
    return request


def _run(component: Path, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    completed = subprocess.run(
        [sys.executable, str(component)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_scanner_reports_replayed_vulnerabilities_deterministically() -> None:
    code, report = _run(OSV, _request())
    assert code == 0
    assert report["status"] == "ok"
    assert report["total_vulnerabilities"] == 1
    assert report["unknown"] == []
    jinja = next(item for item in report["packages"] if item["name"] == "jinja2")
    assert jinja["vulnerabilities"][0]["id"] == "GHSA-h5c8-rqwp-cp95"
    assert jinja["vulnerabilities"][0]["severity"] == "MODERATE"


def test_scanner_reports_a_missing_replay_entry_instead_of_claiming_clean() -> None:
    request = _request()
    request["replay"] = {"osv": {"PyPI/jinja2@3.1.2": []}, "scorecard": REPLAY_SCORECARD}
    code, report = _run(OSV, request)
    assert code == 0
    # An unqueried package must never be silently counted as having no vulnerabilities.
    assert report["status"] == "partial"
    assert any("requests@2.31.0" in item for item in report["unknown"])


def test_scanner_rejects_a_request_outside_its_contract() -> None:
    code, report = _run(OSV, {"schema_version": "1", "request_id": "x", "packages": []})
    assert code == 1
    assert report["status"] == "error"


def test_posture_reports_replayed_score() -> None:
    code, posture = _run(SCORECARD, _request())
    assert code == 0
    assert posture["status"] == "ok"
    assert posture["score"] == 5.9


def test_posture_rejects_a_malformed_repository() -> None:
    code, posture = _run(SCORECARD, _request(repository="not-a-repo"))
    assert code == 1
    assert posture["status"] == "error"


def _envelope(vulnerabilities: dict[str, Any], posture: dict[str, Any]) -> dict[str, Any]:
    return {
        "inputs": {
            "vulnerability-report/v1": vulnerabilities,
            "security-posture/v1": posture,
        }
    }


def test_merger_produces_a_findings_verdict_from_both_observers() -> None:
    _, vulnerabilities = _run(OSV, _request())
    _, posture = _run(SCORECARD, _request())
    code, report = _run(MERGER, _envelope(vulnerabilities, posture))
    assert code == 0
    assert report["verdict"] == "findings"
    assert report["vulnerability_count"] == 1
    assert report["affected_packages"] == ["PyPI/jinja2@3.1.2"]
    assert report["posture_score"] == 5.9
    assert report["unknown"] == []


def test_merger_never_reports_clean_when_an_observation_is_missing() -> None:
    """The safety property: absence of evidence is not evidence of absence."""

    _, posture = _run(SCORECARD, _request())
    incomplete = {
        "schema_version": "1",
        "request_id": "audit-001",
        "status": "partial",
        "packages": [],
        "total_vulnerabilities": 0,
        "unknown": ["PyPI/requests@2.31.0: database unreachable"],
    }
    code, report = _run(MERGER, _envelope(incomplete, posture))
    assert code == 0
    assert report["verdict"] == "unknown"
    assert report["verdict"] != "clean"
    assert report["unknown"]


def test_merger_rejects_reports_describing_different_requests() -> None:
    _, vulnerabilities = _run(OSV, _request())
    _, posture = _run(SCORECARD, _request(request_id="a-different-audit"))
    code, report = _run(MERGER, _envelope(vulnerabilities, posture))
    assert code == 1
    assert "different requests" in report["error"]


def test_merger_rejects_an_envelope_missing_a_required_input() -> None:
    _, vulnerabilities = _run(OSV, _request())
    code, report = _run(MERGER, {"inputs": {"vulnerability-report/v1": vulnerabilities}})
    assert code == 1
    assert "security-posture/v1" in report["error"]


@pytest.fixture(scope="module")
def solved() -> tuple[CompositionDefinition, Any]:
    definition = CompositionDefinition.model_validate(load_yaml(DEFINITION))
    return definition, solve_composition(definition, definition_file=DEFINITION)


def test_solver_selects_all_three_components_and_routes_the_fan_in(solved: Any) -> None:
    _, plan = solved
    assert plan.complete
    assert sorted(plan.selected_component_ids) == [
        "audit-merger",
        "osv-scanner",
        "scorecard-posture",
    ]
    assert plan.selected_adapter_ids == []
    merge_step = next(step for step in plan.steps if step.subject_id == "audit-merger")
    assert merge_step.additional_input_contracts == ["security-posture/v1"]
    assert merge_step.output_contract == "audit-report/v1"


def test_generated_system_runs_end_to_end_and_satisfies_the_output_contract(
    solved: Any, tmp_path: Path
) -> None:
    definition, plan = solved
    bundle = tmp_path / "bundle"
    generated = generate_system(
        definition, plan, definition_file=DEFINITION, output_directory=bundle
    )
    assert generated.execution_ready
    evidence = run_generated_system(
        bundle,
        _request(),
        expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
    )
    observations = evidence.observations
    assert observations["all_steps_completed"] is True
    assert observations["final_validation_errors"] == []
    assert observations["final_contract"] == "audit-report/v1"
    report = observations["final_artifact"]
    assert report["verdict"] == "findings"
    assert report["vulnerability_count"] == 1
    assert report["limitations"]


def test_the_same_request_produces_the_same_report(solved: Any, tmp_path: Path) -> None:
    definition, plan = solved
    reports = []
    for index in range(2):
        bundle = tmp_path / f"bundle-{index}"
        generated = generate_system(
            definition, plan, definition_file=DEFINITION, output_directory=bundle
        )
        evidence = run_generated_system(
            bundle,
            _request(),
            expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
        )
        reports.append(evidence.observations["final_artifact"])
    assert reports[0] == reports[1]


def test_definition_locks_match_the_component_bytes_on_disk() -> None:
    """Editing a component must invalidate its lock until the definition is re-frozen."""

    result = subprocess.run(
        [sys.executable, str(REPOSITORY / "tools" / "freeze_supply_chain_auditor.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY,
    )
    assert result.returncode == 0, result.stdout + result.stderr
