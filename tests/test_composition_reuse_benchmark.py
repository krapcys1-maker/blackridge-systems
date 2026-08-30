from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from blackridge.composition import CompositionDefinition, CompositionPlan, solve_composition
from blackridge.formats import load_yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = REPOSITORY_ROOT / "benchmarks" / "composition-reuse-v1"
CASES = BENCHMARK / "cases"
SPEC = REPOSITORY_ROOT / "evolution" / "benchmark" / "composition-reuse-v1.json"


def _spec() -> dict[str, object]:
    return json.loads(SPEC.read_text(encoding="utf-8"))


def _plan(stem: str) -> CompositionPlan:
    case = CASES / f"{stem}.yaml"
    definition = CompositionDefinition.model_validate(load_yaml(case))
    return solve_composition(definition, definition_file=case)


def _reasons(plan: CompositionPlan, subject_id: str) -> list[str]:
    for observation in plan.qualifications:
        if observation.subject_id == subject_id:
            return observation.reasons
    raise AssertionError(f"no qualification observation for {subject_id}")


def test_frozen_cases_match_the_recorded_manifest() -> None:
    manifest = json.loads((BENCHMARK / "case-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["cases"].items():
        actual = sha256((CASES / name).read_bytes()).hexdigest()
        assert actual == expected, f"{name} drifted from the frozen manifest"


def test_benchmark_spec_matches_the_case_manifest() -> None:
    manifest = json.loads((BENCHMARK / "case-manifest.json").read_text(encoding="utf-8"))
    recorded = {case["id"]: case["file_sha256"] for case in _spec()["cases"]}
    assert recorded == {
        name.removesuffix(".yaml"): value for name, value in manifest["cases"].items()
    }


def test_evaluator_hash_matches_the_frozen_spec() -> None:
    spec = _spec()
    evaluator = REPOSITORY_ROOT / str(spec["evaluator"])
    assert sha256(evaluator.read_bytes()).hexdigest() == spec["evaluator_sha256"]


def test_qualified_component_is_reused_without_new_code() -> None:
    plan = _plan("reuse-complete")
    assert plan.complete
    assert plan.selected_component_ids == ["grounded-researcher-v1"]
    assert plan.selected_adapter_ids == []
    assert [step.step_type for step in plan.steps] == ["component"]


def test_blocked_entry_loses_to_a_qualified_alternative() -> None:
    plan = _plan("blocked-preferred-fallback")
    assert plan.complete
    assert plan.selected_component_ids == ["grounded-researcher-v1"]
    assert _reasons(plan, "grounded-researcher-vendor-fork") == [
        "vendor fork has no independent manual review"
    ]


@pytest.mark.parametrize(
    ("stem", "expected_reason"),
    [
        ("evidence-floor", "evidence L3 is below required L4"),
        ("license-blocked", "license GPL-3.0-only is not allowed"),
        ("hash-drift", "command launch artifact SHA-256 does not match its lock"),
    ],
)
def test_unqualified_pools_fail_closed_with_an_explicit_reason(
    stem: str, expected_reason: str
) -> None:
    plan = _plan(stem)
    assert not plan.complete
    assert plan.unresolved_capabilities == ["grounded-research-synthesis"]
    assert expected_reason in _reasons(plan, "grounded-researcher-v1")


def test_unroutable_graph_is_not_reported_as_an_empty_pool() -> None:
    plan = _plan("adapter-gap")
    assert not plan.complete
    assert _reasons(plan, "grounded-researcher-v1") == []
    assert "Eligible components exist, but no complete contract route was found." in plan.warnings


def test_freeze_tool_reports_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "tools" / "freeze_composition_reuse.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
