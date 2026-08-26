from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from blackridge.composition import (
    ContractDefinition,
    EvidenceReference,
    _sandbox_component_argv,
    _verify_evidence,
    generate_system,
    run_generated_system,
    run_generated_system_sandboxed,
    solve_composition,
)
from blackridge.errors import BlackridgeError
from blackridge.evidence import ManualReview, ManualVerdict, ProbeEvidence
from blackridge.io import load_composition_definition

ROOT = Path(__file__).parents[1]
POSITIVE = ROOT / "examples" / "composition-linear-calibration.yaml"
NO_ADAPTER = ROOT / "examples" / "composition-linear-no-adapter.yaml"
BROKEN_OUTPUT = ROOT / "examples" / "composition-linear-broken-output.yaml"
PRODUCTION_UNREVIEWED = ROOT / "examples" / "composition-production-unreviewed.yaml"
TIMEOUT_HOSTILE = ROOT / "examples" / "composition-timeout-calibration.yaml"
RESOURCE_HOSTILE = ROOT / "examples" / "composition-resource-calibration.yaml"
INPUT = {"topic": "evidence-driven composition"}


def test_contract_identifier_cannot_escape_generated_contract_directory() -> None:
    with pytest.raises(ValueError, match="contract_id"):
        ContractDefinition.model_validate(
            {"contract_id": r"..\..\escaped", "schema": {"type": "object"}}
        )


def test_solver_rejects_blocked_option_and_selects_one_adapter() -> None:
    definition = load_composition_definition(POSITIVE)

    plan = solve_composition(definition, definition_file=POSITIVE)

    assert plan.complete is True
    assert plan.release_ready is False
    assert plan.selected_component_ids == ["fixture-report-sink", "fixture-research-source"]
    assert plan.selected_adapter_ids == ["paper-title-to-document-name"]
    assert [step.step_type for step in plan.steps] == ["component", "adapter", "component"]
    blocked = next(
        item for item in plan.qualifications if item.subject_id == "blocked-report-sink"
    )
    assert blocked.eligible is False
    assert blocked.reasons == ["deliberate policy-blocked alternative"]


def test_solver_keeps_missing_adapter_route_incomplete() -> None:
    definition = load_composition_definition(NO_ADAPTER)

    plan = solve_composition(definition, definition_file=NO_ADAPTER)

    assert plan.complete is False
    assert plan.unresolved_capabilities == ["report-sink"]
    assert plan.selected_adapter_ids == []
    assert [step.subject_id for step in plan.steps] == ["fixture-research-source"]


def test_production_mode_rejects_unreviewed_claimed_l3() -> None:
    production = load_composition_definition(PRODUCTION_UNREVIEWED)

    plan = solve_composition(production, definition_file=PRODUCTION_UNREVIEWED)

    assert plan.complete is False
    assert plan.unresolved_capabilities == ["research-source"]
    qualification = plan.qualifications[0]
    assert qualification.reasons == ["claimed evidence level has no named manual review"]
    assert qualification.evidence_observations["launch_artifact_hash_matches"] is True


@pytest.mark.parametrize(
    ("definition_file", "component_id"),
    [
        (TIMEOUT_HOSTILE, "hostile-timeout-component"),
        (RESOURCE_HOSTILE, "hostile-resource-pressure-component"),
    ],
)
def test_hostile_control_definitions_stay_hash_locked_and_solvable(
    definition_file: Path,
    component_id: str,
) -> None:
    definition = load_composition_definition(definition_file)

    plan = solve_composition(definition, definition_file=definition_file)

    assert plan.complete is True
    assert plan.selected_component_ids == [component_id]
    qualification = next(item for item in plan.qualifications if item.subject_id == component_id)
    assert qualification.evidence_observations["launch_artifact_hash_matches"] is True


def test_generator_writes_locked_layout_and_runtime_completes(tmp_path: Path) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "generated"

    generated = generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )
    probe = run_generated_system(
        bundle,
        INPUT,
        expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
    )

    expected = {
        "README.md",
        "blackridge.blueprint.yaml",
        "components.lock.yaml",
        "compose.yaml",
        "provenance.json",
        "runtime.yaml",
    }
    assert expected <= {path.name for path in bundle.iterdir() if path.is_file()}
    assert generated.execution_ready is True
    assert generated.release_ready is False
    assert probe.observations["all_steps_completed"] is True
    assert probe.observations["final_artifact"]["report"] == {
        "title": "Evidence for evidence-driven composition",
        "based_on": "fixture-paper-001",
    }
    assert all(
        step.get("output_contract_valid") is True for step in probe.observations["steps"]
    )
    adapter = next(
        step for step in probe.observations["steps"] if step["step_type"] == "adapter"
    )
    assert adapter["operations"][0] == {"op": "add", "path": "/document", "value": {}}
    assert "verdict" not in probe.model_dump()


def test_generator_refuses_incomplete_or_tampered_plan(tmp_path: Path) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    tampered = plan.model_copy(update={"selected_adapter_ids": []})

    with pytest.raises(BlackridgeError, match="no longer matches"):
        generate_system(
            definition,
            tampered,
            definition_file=POSITIVE,
            output_directory=tmp_path / "tampered",
        )

    incomplete_definition = load_composition_definition(NO_ADAPTER)
    incomplete = solve_composition(incomplete_definition, definition_file=NO_ADAPTER)
    with pytest.raises(BlackridgeError, match="incomplete"):
        generate_system(
            incomplete_definition,
            incomplete,
            definition_file=NO_ADAPTER,
            output_directory=tmp_path / "incomplete",
        )


def test_runtime_rejects_tampered_generated_artifact(tmp_path: Path) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "generated"
    generated = generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )
    runtime = bundle / "runtime.yaml"
    runtime.write_text(runtime.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")

    with pytest.raises(BlackridgeError, match=r"integrity failed: runtime\.yaml"):
        run_generated_system(
            bundle,
            INPUT,
            expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
        )


def _rewrite_runtime_and_relock(bundle: Path, mutate) -> str:
    runtime_file = bundle / "runtime.yaml"
    runtime = yaml.safe_load(runtime_file.read_text(encoding="utf-8"))
    mutate(runtime)
    runtime_file.write_text(yaml.safe_dump(runtime, sort_keys=False), encoding="utf-8")
    provenance_file = bundle / "provenance.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    provenance["artifact_sha256"]["runtime.yaml"] = sha256(
        runtime_file.read_bytes()
    ).hexdigest()
    provenance_file.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return sha256(provenance_file.read_bytes()).hexdigest()


def test_runtime_rejects_a_relocked_bundle_without_a_new_external_trust_root(
    tmp_path: Path,
) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "relocked"
    generated = generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )
    _rewrite_runtime_and_relock(bundle, lambda runtime: runtime.update({"mode": "production"}))

    with pytest.raises(BlackridgeError, match="externally supplied SHA-256"):
        run_generated_system(
            bundle,
            INPUT,
            expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
        )


def test_sandboxed_generated_runner_keeps_production_disabled(tmp_path: Path) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "production"
    generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )
    provenance_sha256 = _rewrite_runtime_and_relock(
        bundle, lambda runtime: runtime.update({"mode": "production"})
    )

    with pytest.raises(BlackridgeError, match="remains calibration-only"):
        run_generated_system_sandboxed(
            bundle,
            INPUT,
            expected_provenance_sha256=provenance_sha256,
        )


def test_sandboxed_generated_runner_refuses_environment_forwarding(tmp_path: Path) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "environment"
    generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )

    def add_environment(runtime) -> None:
        component = next(step for step in runtime["steps"] if step["step_type"] == "component")
        component["launch"]["environment_allowlist"] = ["DEEPSEEK_API_KEY"]

    provenance_sha256 = _rewrite_runtime_and_relock(bundle, add_environment)

    with pytest.raises(BlackridgeError, match="forwards no component environment"):
        run_generated_system_sandboxed(
            bundle,
            INPUT,
            expected_provenance_sha256=provenance_sha256,
        )


def test_sandboxed_component_maps_a_different_python_venv_safely(tmp_path: Path) -> None:
    component = tmp_path / "component.py"
    previous_venv_python = tmp_path / "old-venv" / "Scripts" / "python.exe"

    assert _sandbox_component_argv(
        [str(previous_venv_python), str(component)],
        artifact_file=str(component.resolve()),
        container_artifact="/workspace/components/component-1.py",
        subject_id="portable-python-component",
    ) == ["python", "/workspace/components/component-1.py"]

    with pytest.raises(BlackridgeError, match="unmapped absolute argv path"):
        _sandbox_component_argv(
            [str(previous_venv_python), str(component), str(tmp_path / "unlocked.txt")],
            artifact_file=str(component.resolve()),
            container_artifact="/workspace/components/component-1.py",
            subject_id="portable-python-component",
        )


def test_evidence_review_is_bound_to_exact_probe_subject(tmp_path: Path) -> None:
    probe = ProbeEvidence(
        probe_id="a" * 32,
        observed_at=datetime(2026, 8, 26, tzinfo=UTC),
        provider="test",
        subject="actual-component",
        request={"fixture": True},
        observations={"probe_completed": True},
        sources=["https://example.test/source"],
    )
    probe_file = tmp_path / "probe.json"
    probe_file.write_text(probe.model_dump_json(indent=2), encoding="utf-8")
    review = ManualReview.create(
        reviewer="manual tester",
        verdict=ManualVerdict.PASS,
        capability_id="test-capability",
        scenario_id="test-scenario",
        scenario_description="Inspect the exact subject in retained evidence.",
        expected=["The probe subject matches the promoted component."],
        observed=["The deliberately mismatched subject was retained."],
        probe_id=probe.probe_id,
        probe_file="probe.json",
        probe_sha256=sha256(probe_file.read_bytes()).hexdigest(),
        notes="This fixture checks subject binding rather than component behavior.",
    )
    review_file = tmp_path / "review.json"
    review_file.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    evidence = EvidenceReference(
        level=3,
        review_file="review.json",
        review_sha256=sha256(review_file.read_bytes()).hexdigest(),
        capability_id="test-capability",
        scenario_id="test-scenario",
        probe_subject="different-component",
    )

    reasons, observations = _verify_evidence(
        evidence,
        definition_directory=tmp_path,
        mode="production",
    )

    assert reasons == ["reviewed probe subject does not match evidence reference"]
    assert observations["probe_id_matches_review"] is True
    assert observations["probe_subject_matches"] is False


def test_green_exit_broken_output_fails_contract(tmp_path: Path) -> None:
    definition = load_composition_definition(BROKEN_OUTPUT)
    plan = solve_composition(definition, definition_file=BROKEN_OUTPUT)
    bundle = tmp_path / "broken"
    generated = generate_system(
        definition,
        plan,
        definition_file=BROKEN_OUTPUT,
        output_directory=bundle,
    )

    probe = run_generated_system(
        bundle,
        deepcopy(INPUT),
        expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
    )
    steps = probe.observations["steps"]
    broken = steps[-1]

    assert plan.complete is True
    assert probe.observations["all_steps_completed"] is False
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "completed"
    assert broken["process"]["exit_code"] == 0
    assert broken["output_contract_valid"] is False
    assert {error["instance_path"] for error in broken["output_validation_errors"]} == {
        "/report",
        "/trace",
    }
    assert "verdict" not in probe.model_dump()
