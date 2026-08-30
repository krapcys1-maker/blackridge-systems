from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from blackridge.benchmark import (
    BenchmarkCalibrationProbe,
    BenchmarkComparisonProbe,
    BenchmarkEvaluator,
    CandidateInvocation,
    ResearchOutput,
    ResearchRequest,
    load_benchmark_definition,
    load_benchmark_run_plan,
)
from blackridge.errors import BlackridgeError

ROOT = Path(__file__).parents[1]
BENCHMARK_ROOT = ROOT / "benchmarks" / "scientific-researcher-v1"
DEFINITION = BENCHMARK_ROOT / "evaluator" / "benchmark.yaml"
REFERENCE = BENCHMARK_ROOT / "calibration-reference.yaml"
BROKEN = BENCHMARK_ROOT / "calibration-broken.yaml"
SCHEMA_INVALID = BENCHMARK_ROOT / "calibration-schema-invalid.yaml"


def comparison_plans(tmp_path: Path) -> tuple[Path, Path]:
    baseline = yaml.safe_load(REFERENCE.read_text(encoding="utf-8"))
    blackridge = yaml.safe_load(BROKEN.read_text(encoding="utf-8"))
    baseline.update({"run_id": "baseline-attempt-one", "method": "from-scratch"})
    blackridge.update({"run_id": "blackridge-attempt-one", "method": "blackridge-hybrid"})
    baseline_path = tmp_path / "baseline.yaml"
    blackridge_path = tmp_path / "blackridge.yaml"
    baseline_path.write_text(yaml.safe_dump(baseline), encoding="utf-8")
    blackridge_path.write_text(yaml.safe_dump(blackridge), encoding="utf-8")
    return baseline_path, blackridge_path


def test_frozen_benchmark_has_functional_and_robustness_cases() -> None:
    definition = load_benchmark_definition(DEFINITION)

    assert definition.task_id == "scientific-researcher-v1"
    assert {case.category for case in definition.cases} == {"functional", "robustness"}
    assert all(case.critical for case in definition.cases)


def test_output_contract_rejects_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        ResearchOutput.model_validate(
            {
                "schema_version": "1",
                "request_id": "example",
                "status": "answered",
                "answer": "An answer",
                "claims": [],
                "sources": [],
                "secret_unchecked_payload": True,
            }
        )


def test_input_contract_rejects_boolean_minimum_sources() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest.model_validate(
            {
                "schema_version": "1",
                "request_id": "boolean-minimum",
                "question": "Which evidence supports this decision?",
                "minimum_sources": True,
                "documents": [
                    {
                        "document_id": "evidence-one",
                        "title": "Evidence Record",
                        "full_text": "A retained record supports the named decision under review.",
                    }
                ],
            }
        )


def test_reference_fixture_matches_every_critical_check() -> None:
    probe = BenchmarkEvaluator().evaluate(DEFINITION, REFERENCE)

    assert probe.observations["case_count"] == 2
    assert probe.observations["all_critical_matched"] is True
    assert probe.observations["critical_match_rate"] == 1
    assert probe.observations["weighted_success_score"] is None


def test_green_broken_fixture_fails_artifact_checks() -> None:
    probe = BenchmarkEvaluator().evaluate(DEFINITION, BROKEN)
    cases = probe.observations["cases"]

    assert all(case["exit_code"] == 0 for case in cases)
    assert probe.observations["all_critical_matched"] is False
    unmatched = {
        (case["case_id"], check["check_id"])
        for case in cases
        for check in case["checks"]
        if not check["matched"]
    }
    assert ("grounded-ten-source-synthesis", "source-identities") in unmatched
    assert ("grounded-ten-source-synthesis", "citation-quotes-grounded") in unmatched
    assert ("insufficient-corpus-abstention", "expected-status") in unmatched
    assert ("insufficient-corpus-abstention", "clean-abstention") in unmatched


def test_public_schema_keeps_fixed_check_denominator_for_invalid_output() -> None:
    probe = BenchmarkEvaluator().evaluate(DEFINITION, SCHEMA_INVALID)

    for case in probe.observations["cases"]:
        checks = {check["check_id"]: check for check in case["checks"]}
        boundary_checks = {
            "process-completed",
            "frozen-input-integrity",
            "output-contract",
        }
        assert case["exit_code"] == 0
        assert boundary_checks < set(checks)
        assert checks["process-completed"]["matched"] is True
        assert checks["frozen-input-integrity"]["matched"] is True
        assert checks["output-contract"]["matched"] is False
        assert "unchecked_payload" in checks["output-contract"]["observed"]["error"]
        blocked_checks = [
            check for check_id, check in checks.items() if check_id not in boundary_checks
        ]
        assert all(check["matched"] is False for check in blocked_checks)
        assert all(
            check["observed"] == "blocked by invalid output contract" for check in blocked_checks
        )


def test_calibration_retains_equal_controls_and_detected_boundaries() -> None:
    probe = BenchmarkCalibrationProbe().probe(DEFINITION, REFERENCE, BROKEN)
    comparison = probe.observations["comparison"]

    assert probe.observations["all_controls_identical"] is True
    assert comparison["reference_all_critical_matched"] is True
    assert comparison["broken_all_processes_exited_zero"] is True
    assert comparison["broken_all_critical_matched"] is False
    assert comparison["detected_broken_check_count"] >= 4
    assert comparison["weighted_success_score_used"] is False


def test_harness_control_comparison_retains_raw_arms_without_winner(
    tmp_path: Path,
) -> None:
    baseline, blackridge = comparison_plans(tmp_path)

    probe = BenchmarkComparisonProbe().probe(DEFINITION, baseline, blackridge)

    assert probe.observations["valid_two_arm_comparison"] is False
    assert probe.observations["harness_control_only"] is True
    assert probe.observations["baseline"]["task_success"] is True
    assert probe.observations["blackridge"]["task_success"] is False
    assert probe.observations["weighted_success_score"] is None
    assert probe.observations["automatic_winner"] is None


def test_docker_candidate_command_disables_root_and_swap(tmp_path: Path) -> None:
    candidate = CandidateInvocation(
        backend="docker",
        argv=["python", "/workspace/candidate.py"],
        cwd="/workspace",
        workspace=str(tmp_path),
        docker_image="sha256:" + "a" * 64,
        memory_mib=256,
    )
    run = load_benchmark_run_plan(REFERENCE).model_copy(update={"candidate": candidate})

    command = BenchmarkEvaluator()._candidate_command(DEFINITION, tmp_path / "run.yaml", run)

    assert command.argv[command.argv.index("--user") : command.argv.index("--user") + 2] == [
        "--user",
        "65534:65534",
    ]
    assert command.argv[
        command.argv.index("--memory-swap") : command.argv.index("--memory-swap") + 2
    ] == ["--memory-swap", "256m"]
    assert "--interactive" in command.argv
    assert "HOME=/tmp" in command.argv
    assert "TMPDIR=/tmp" in command.argv


def test_comparison_refuses_different_model_controls(tmp_path: Path) -> None:
    baseline, blackridge = comparison_plans(tmp_path)
    data = yaml.safe_load(blackridge.read_text(encoding="utf-8"))
    data["model_identifier"] = "different-model"
    blackridge.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(BlackridgeError, match="model_identifier"):
        BenchmarkComparisonProbe().probe(DEFINITION, baseline, blackridge)
