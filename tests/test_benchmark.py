from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from blackridge.benchmark import (
    BenchmarkCalibrationProbe,
    BenchmarkComparisonProbe,
    BenchmarkEvaluator,
    ResearchOutput,
    load_benchmark_definition,
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
    blackridge.update(
        {"run_id": "blackridge-attempt-one", "method": "blackridge-hybrid"}
    )
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


def test_public_schema_stops_deeper_checks_for_invalid_output() -> None:
    probe = BenchmarkEvaluator().evaluate(DEFINITION, SCHEMA_INVALID)

    for case in probe.observations["cases"]:
        checks = {check["check_id"]: check for check in case["checks"]}
        assert case["exit_code"] == 0
        assert set(checks) == {"process-completed", "output-contract"}
        assert checks["process-completed"]["matched"] is True
        assert checks["output-contract"]["matched"] is False
        assert "unchecked_payload" in checks["output-contract"]["observed"]["error"]


def test_calibration_retains_equal_controls_and_detected_boundaries() -> None:
    probe = BenchmarkCalibrationProbe().probe(DEFINITION, REFERENCE, BROKEN)
    comparison = probe.observations["comparison"]

    assert probe.observations["all_controls_identical"] is True
    assert comparison["reference_all_critical_matched"] is True
    assert comparison["broken_all_processes_exited_zero"] is True
    assert comparison["broken_all_critical_matched"] is False
    assert comparison["detected_broken_check_count"] >= 4
    assert comparison["weighted_success_score_used"] is False


def test_controlled_comparison_retains_raw_arms_without_winner(tmp_path: Path) -> None:
    baseline, blackridge = comparison_plans(tmp_path)

    probe = BenchmarkComparisonProbe().probe(DEFINITION, baseline, blackridge)

    assert probe.observations["valid_two_arm_comparison"] is True
    assert probe.observations["baseline"]["task_success"] is True
    assert probe.observations["blackridge"]["task_success"] is False
    assert probe.observations["weighted_success_score"] is None
    assert probe.observations["automatic_winner"] is None


def test_comparison_refuses_different_model_controls(tmp_path: Path) -> None:
    baseline, blackridge = comparison_plans(tmp_path)
    data = yaml.safe_load(blackridge.read_text(encoding="utf-8"))
    data["model_identifier"] = "different-model"
    blackridge.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(BlackridgeError, match="model_identifier"):
        BenchmarkComparisonProbe().probe(DEFINITION, baseline, blackridge)
