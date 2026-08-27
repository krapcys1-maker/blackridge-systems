from __future__ import annotations

import json
import shutil
import sys
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from blackridge.composition import (
    ContractDefinition,
    EvidenceReference,
    _host_component_process,
    _sandbox_component_argv,
    _sandbox_resource_target,
    _verify_evidence,
    generate_system,
    run_generated_system,
    run_generated_system_sandboxed,
    solve_composition,
)
from blackridge.errors import BlackridgeError
from blackridge.evidence import (
    EvidencePromotion,
    ManualReview,
    ManualVerdict,
    ProbeEvidence,
)
from blackridge.io import load_composition_definition

ROOT = Path(__file__).parents[1]
POSITIVE = ROOT / "examples" / "composition-linear-calibration.yaml"
NO_ADAPTER = ROOT / "examples" / "composition-linear-no-adapter.yaml"
BROKEN_OUTPUT = ROOT / "examples" / "composition-linear-broken-output.yaml"
PRODUCTION_UNREVIEWED = ROOT / "examples" / "composition-production-unreviewed.yaml"
TIMEOUT_HOSTILE = ROOT / "examples" / "composition-timeout-calibration.yaml"
RESOURCE_HOSTILE = ROOT / "examples" / "composition-resource-calibration.yaml"
FANIN = ROOT / "examples" / "composition-fanin-calibration.yaml"
BUNDLED_RESOURCE = ROOT / "examples" / "composition-bundled-resource-calibration.yaml"
INPUT = {"topic": "evidence-driven composition"}


def test_host_component_gets_isolated_runtime_identity_and_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = tmp_path / "inspect_environment.py"
    component.write_text(
        "import getpass, json, os, sys, tempfile\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({\n"
        "  'user': getpass.getuser(),\n"
        "  'home': os.environ['HOME'],\n"
        "  'temp': tempfile.gettempdir(),\n"
        "  'secret': os.environ['BLACKRIDGE_TEST_ALLOWED'],\n"
        "  'names': sorted(os.environ),\n"
        "}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BLACKRIDGE_TEST_ALLOWED", "allowed-value")
    monkeypatch.setenv("HOME", str(tmp_path / "real-host-home"))

    process = _host_component_process(
        "environment-fixture",
        {
            "argv": [sys.executable, str(component)],
            "working_directory": str(tmp_path),
            "environment_allowlist": ["BLACKRIDGE_TEST_ALLOWED"],
            "timeout_seconds": 5,
        },
        {},
    )

    assert process["exit_code"] == 0
    observed = json.loads(str(process["stdout"]))
    assert observed["user"] == "blackridge"
    assert observed["secret"] == "allowed-value"
    assert observed["home"] == observed["temp"]
    assert not Path(observed["home"]).exists()
    assert "HOME" in process["environment_names"]
    assert "USERNAME" in process["environment_names"]
    assert "real-host-home" not in observed["home"]
    assert "PYTHONIOENCODING" in observed["names"]


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
    blocked = next(item for item in plan.qualifications if item.subject_id == "blocked-report-sink")
    assert blocked.eligible is False
    assert blocked.reasons == ["deliberate policy-blocked alternative"]


def test_solver_keeps_missing_adapter_route_incomplete() -> None:
    definition = load_composition_definition(NO_ADAPTER)

    plan = solve_composition(definition, definition_file=NO_ADAPTER)

    assert plan.complete is False
    assert plan.unresolved_capabilities == ["report-sink"]
    assert plan.selected_adapter_ids == []
    assert [step.subject_id for step in plan.steps] == ["fixture-research-source"]


def test_solver_and_runtime_preserve_independent_branches_for_fanin(tmp_path: Path) -> None:
    definition = load_composition_definition(FANIN)
    plan = solve_composition(definition, definition_file=FANIN)

    assert plan.complete is True
    assert [step.subject_id for step in plan.steps] == [
        "fixture-length-branch",
        "fixture-uppercase-branch",
        "fixture-join-branches",
    ]
    join = plan.steps[-1]
    assert [join.input_contract, *join.additional_input_contracts] == [
        "uppercase/v1",
        "length/v1",
    ]

    bundle = tmp_path / "fanin"
    generated = generate_system(
        definition,
        plan,
        definition_file=FANIN,
        output_directory=bundle,
    )
    probe = run_generated_system(
        bundle,
        {"text": "Blackridge"},
        expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
    )

    assert probe.observations["all_steps_completed"] is True
    assert probe.observations["available_contracts"] == [
        "joined/v1",
        "length/v1",
        "seed/v1",
        "uppercase/v1",
    ]
    assert probe.observations["final_artifact"] == {
        "observed_contracts": ["length/v1", "uppercase/v1"],
        "summary": "BLACKRIDGE:10",
    }
    join_observation = probe.observations["steps"][-1]
    assert join_observation["input_artifact"] == {
        "inputs": {
            "uppercase/v1": {"uppercase": "BLACKRIDGE"},
            "length/v1": {"length": 10},
        }
    }


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
    bundled_components = sorted(path.name for path in (bundle / "components").iterdir())
    assert bundled_components == ["fixture-report-sink.py", "fixture-research-source.py"]
    runtime = yaml.safe_load((bundle / "runtime.yaml").read_text(encoding="utf-8"))
    launches = [step["launch"] for step in runtime["steps"] if step["step_type"] == "component"]
    assert all(launch["working_directory"] == "components" for launch in launches)
    assert all(not Path(launch["artifact_file"]).is_absolute() for launch in launches)
    assert all(launch["argv"] == ["{python}", "{artifact}"] for launch in launches)
    assert generated.execution_ready is True
    assert generated.release_ready is False
    assert probe.observations["all_steps_completed"] is True
    assert probe.observations["final_artifact"]["report"] == {
        "title": "Evidence for evidence-driven composition",
        "based_on": "fixture-paper-001",
    }
    assert all(step.get("output_contract_valid") is True for step in probe.observations["steps"])
    adapter = next(step for step in probe.observations["steps"] if step["step_type"] == "adapter")
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


def test_generated_bundle_runs_after_its_definition_source_is_removed(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    shutil.copytree(ROOT / "examples", examples)
    definition_file = examples / "composition-linear-calibration.yaml"
    definition = load_composition_definition(definition_file)
    plan = solve_composition(definition, definition_file=definition_file)
    bundle = tmp_path / "generated"
    generated = generate_system(
        definition,
        plan,
        definition_file=definition_file,
        output_directory=bundle,
    )
    shutil.rmtree(examples)

    probe = run_generated_system(
        bundle,
        INPUT,
        expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
    )

    assert probe.observations["all_steps_completed"] is True
    assert probe.observations["final_artifact"]["report"]["based_on"] == "fixture-paper-001"


def test_bundled_resource_runs_without_source_tree_and_tampering_is_blocked(
    tmp_path: Path,
) -> None:
    examples = tmp_path / "examples"
    shutil.copytree(ROOT / "examples", examples)
    definition_file = examples / "composition-bundled-resource-calibration.yaml"
    definition = load_composition_definition(definition_file)
    plan = solve_composition(definition, definition_file=definition_file)
    bundle = tmp_path / "generated-resource"
    generated = generate_system(
        definition,
        plan,
        definition_file=definition_file,
        output_directory=bundle,
    )
    runtime = yaml.safe_load((bundle / "runtime.yaml").read_text(encoding="utf-8"))
    resource_step = next(
        step for step in runtime["steps"] if step["subject_id"] == "fixture-resource-calculator"
    )
    assert resource_step["launch"]["resources"][0]["copy_timeout_seconds"] == 300
    shutil.rmtree(examples)

    probe = run_generated_system(
        bundle,
        {"value": 14},
        expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
    )
    assert probe.observations["all_steps_completed"] is True
    assert probe.observations["final_artifact"] == {"result": "locked:42"}

    resource = (
        bundle
        / "resources"
        / "fixture-resource-calculator"
        / "calculation-data"
        / "resource_data.json"
    )
    resource.write_text(resource.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BlackridgeError, match=r"resource_data\.json"):
        run_generated_system(
            bundle,
            {"value": 14},
            expected_provenance_sha256=generated.artifact_sha256["provenance.json"],
        )


def test_runtime_preflights_every_bundled_component_hash_before_execution(
    tmp_path: Path,
) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "generated"
    generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )
    sink = bundle / "components" / "fixture-report-sink.py"
    sink.write_text(sink.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
    provenance_file = bundle / "provenance.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    provenance["artifact_sha256"]["components/fixture-report-sink.py"] = sha256(
        sink.read_bytes()
    ).hexdigest()
    provenance_file.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    resigned_root = sha256(provenance_file.read_bytes()).hexdigest()

    def unexpected_process(*_args, **_kwargs):
        raise AssertionError("no component may execute after a preflight hash mismatch")

    probe = run_generated_system(
        bundle,
        INPUT,
        expected_provenance_sha256=resigned_root,
        _component_process=unexpected_process,
    )

    assert probe.observations["all_steps_completed"] is False
    assert probe.observations["failure_reason"] == (
        "component fixture-report-sink launch artifact failed integrity"
    )
    assert [step["status"] for step in probe.observations["steps"]] == [
        "skipped",
        "skipped",
        "failed",
    ]


def _rewrite_runtime_and_relock(bundle: Path, mutate) -> str:
    runtime_file = bundle / "runtime.yaml"
    runtime = yaml.safe_load(runtime_file.read_text(encoding="utf-8"))
    mutate(runtime)
    runtime_file.write_text(yaml.safe_dump(runtime, sort_keys=False), encoding="utf-8")
    provenance_file = bundle / "provenance.json"
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    provenance["artifact_sha256"]["runtime.yaml"] = sha256(runtime_file.read_bytes()).hexdigest()
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

    with pytest.raises(BlackridgeError, match="component launch disagrees with its lock"):
        run_generated_system_sandboxed(
            bundle,
            INPUT,
            expected_provenance_sha256=provenance_sha256,
        )


def test_generated_runtime_locks_explicit_sandbox_resources(tmp_path: Path) -> None:
    examples = tmp_path / "examples"
    shutil.copytree(ROOT / "examples", examples)
    definition_file = examples / POSITIVE.name
    definition_value = yaml.safe_load(definition_file.read_text(encoding="utf-8"))
    definition_value["sandbox_resources"] = {
        "memory_mb": 4096,
        "cpus": 3.5,
        "pids": 512,
    }
    definition_value["sandbox_image"] = {
        "reference": "example.invalid/runtime@sha256:" + "a" * 64,
        "expected_id": "sha256:" + "b" * 64,
    }
    definition_file.write_text(
        yaml.safe_dump(definition_value, sort_keys=False), encoding="utf-8"
    )
    definition = load_composition_definition(definition_file)
    plan = solve_composition(definition, definition_file=definition_file)
    bundle = tmp_path / "resource-bounded"

    generate_system(
        definition,
        plan,
        definition_file=definition_file,
        output_directory=bundle,
    )

    runtime = yaml.safe_load((bundle / "runtime.yaml").read_text(encoding="utf-8"))
    assert runtime["sandbox_resources"] == {
        "memory_mb": 4096,
        "cpus": 3.5,
        "pids": 512,
    }
    assert runtime["sandbox_image"] == {
        "reference": "example.invalid/runtime@sha256:" + "a" * 64,
        "expected_id": "sha256:" + "b" * 64,
    }
    with pytest.raises(BlackridgeError, match="disagrees with the generated image lock"):
        run_generated_system_sandboxed(
            bundle,
            INPUT,
            expected_provenance_sha256=sha256(
                (bundle / "provenance.json").read_bytes()
            ).hexdigest(),
            image_ref="sha256:" + "c" * 64,
        )


def test_runtime_rejects_resigned_adapter_operations_that_disagree_with_lock(
    tmp_path: Path,
) -> None:
    definition = load_composition_definition(POSITIVE)
    plan = solve_composition(definition, definition_file=POSITIVE)
    bundle = tmp_path / "adapter-mismatch"
    generate_system(
        definition,
        plan,
        definition_file=POSITIVE,
        output_directory=bundle,
    )

    def change_adapter(runtime) -> None:
        adapter = next(step for step in runtime["steps"] if step["step_type"] == "adapter")
        adapter["operations"][-1]["from"] = "/paper/missing"

    provenance_sha256 = _rewrite_runtime_and_relock(bundle, change_adapter)

    with pytest.raises(BlackridgeError, match="adapter runtime disagrees with its lock"):
        run_generated_system(
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


def test_sandbox_resource_target_preserves_the_generated_bundle_basename(
    tmp_path: Path,
) -> None:
    bundled_resource = tmp_path / "resources" / "component" / "wheel.whl"

    assert _sandbox_resource_target(2, str(bundled_resource)) == (
        "/workspace/resources/component-2/wheel.whl"
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
        promotion=EvidencePromotion(
            target_level=3,
            subject_type="component",
            probe_provider=probe.provider,
            probe_subject=probe.subject,
            probe_completed=True,
            subject_revision="a" * 40,
            subject_license_spdx="MIT",
            artifact_sha256="b" * 64,
        ),
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
        subject_type="component",
        subject_revision="a" * 40,
        subject_license_spdx="MIT",
        artifact_sha256="b" * 64,
    )

    assert reasons == ["reviewed probe subject does not match evidence reference"]
    assert observations["probe_id_matches_review"] is True
    assert observations["probe_subject_matches"] is False


def test_evidence_review_cannot_escape_repository_boundary(tmp_path: Path) -> None:
    definition_directory = tmp_path / "definitions"
    definition_directory.mkdir()
    outside_review = tmp_path / "review.json"
    outside_review.write_text("{}", encoding="utf-8")
    evidence = EvidenceReference(
        level=3,
        review_file="../review.json",
        review_sha256=sha256(outside_review.read_bytes()).hexdigest(),
        capability_id="test-capability",
        scenario_id="test-scenario",
        probe_subject="fixture-component",
    )

    reasons, observations = _verify_evidence(
        evidence,
        definition_directory=definition_directory,
        mode="production",
        subject_type="component",
        subject_revision="a" * 40,
        subject_license_spdx="MIT",
        artifact_sha256="b" * 64,
    )

    assert reasons == ["manual review file resolves outside the repository"]
    assert observations["review_within_repository"] is False


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        (
            lambda probe, promotion: probe.observations.update({"probe_completed": False}),
            "reviewed probe did not complete successfully",
        ),
        (
            lambda probe, promotion: setattr(probe, "provider", "unexpected-provider"),
            "reviewed probe provider does not match promotion",
        ),
        (
            lambda probe, promotion: setattr(promotion, "subject_revision", "c" * 40),
            "review promotion revision does not match qualified subject",
        ),
        (
            lambda probe, promotion: setattr(promotion, "artifact_sha256", "d" * 64),
            "review promotion artifact does not match qualified subject",
        ),
    ],
)
def test_production_promotion_rejects_unbound_or_failed_evidence(
    tmp_path: Path,
    mutation,
    expected_reason: str,
) -> None:
    probe = ProbeEvidence(
        probe_id="a" * 32,
        observed_at=datetime(2026, 8, 26, tzinfo=UTC),
        provider="component-contract-probe/v1",
        subject="fixture-component",
        request={"fixture": True},
        observations={"probe_completed": True},
        sources=["https://example.test/source"],
    )
    promotion = EvidencePromotion(
        target_level=3,
        subject_type="component",
        probe_provider=probe.provider,
        probe_subject=probe.subject,
        probe_completed=True,
        subject_revision="a" * 40,
        subject_license_spdx="MIT",
        artifact_sha256="b" * 64,
    )
    mutation(probe, promotion)
    probe_file = tmp_path / "probe.json"
    probe_file.write_text(probe.model_dump_json(indent=2), encoding="utf-8")
    review = ManualReview.create(
        reviewer="manual tester",
        verdict=ManualVerdict.PASS,
        capability_id="test-capability",
        scenario_id="test-scenario",
        scenario_description="Inspect the exact component and its locked artifact.",
        expected=["The promoted evidence is bound to the qualified component."],
        observed=["The retained fixture was inspected manually."],
        probe_id=probe.probe_id,
        probe_file="probe.json",
        probe_sha256=sha256(probe_file.read_bytes()).hexdigest(),
        promotion=promotion,
        notes="This fixture verifies fail-closed evidence promotion bindings.",
    )
    review_file = tmp_path / "review.json"
    review_file.write_text(review.model_dump_json(indent=2), encoding="utf-8")
    evidence = EvidenceReference(
        level=3,
        review_file="review.json",
        review_sha256=sha256(review_file.read_bytes()).hexdigest(),
        capability_id="test-capability",
        scenario_id="test-scenario",
        probe_subject="fixture-component",
    )

    reasons, _ = _verify_evidence(
        evidence,
        definition_directory=tmp_path,
        mode="production",
        subject_type="component",
        subject_revision="a" * 40,
        subject_license_spdx="MIT",
        artifact_sha256="b" * 64,
    )

    assert expected_reason in reasons


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
