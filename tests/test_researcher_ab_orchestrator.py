from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from blackridge.benchmark import BenchmarkRunPlan

REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "run_researcher_ab_under_test", REPOSITORY / "tools" / "run_researcher_ab.py"
)
assert SPEC is not None and SPEC.loader is not None
ORCHESTRATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ORCHESTRATOR)
builder_prompt = ORCHESTRATOR.builder_prompt
eligible_component = ORCHESTRATOR.eligible_component
measure_reuse = ORCHESTRATOR.measure_reuse
run_plan = ORCHESTRATOR.run_plan
validate_and_write_bundle = ORCHESTRATOR.validate_and_write_bundle


def test_eligible_component_has_hash_bound_l2_review() -> None:
    manifest, source, observations = eligible_component(REPOSITORY)

    assert manifest["component_id"] == "grounded-researcher-v1"
    assert manifest["evidence"]["level"] == 2
    assert observations["review_hash_matches"] is True
    assert observations["probe_completed"] is True
    assert observations["probe_subject_matches"] is True
    assert source.is_file()


def test_hybrid_installs_exact_component_and_measures_reuse(tmp_path: Path) -> None:
    bundle = {
        "files": [
            {
                "path": "requirements.lock",
                "content": "# No third-party runtime dependencies.\n",
            }
        ],
        "candidate_command": ["python", "/workspace/candidate.py"],
        "selected_component_id": "grounded-researcher-v1",
        "reused_source_lines": 0,
    }

    selected = validate_and_write_bundle(REPOSITORY, tmp_path, bundle, "blackridge-hybrid")
    measurement = measure_reuse(tmp_path, selected, bundle["reused_source_lines"])
    manifest, source, _ = eligible_component(REPOSITORY)

    assert (tmp_path / "candidate.py").read_bytes() == source.read_bytes()
    assert measurement["candidate_sha256"] == manifest["artifact_sha256"]
    assert measurement["exact_artifact_match"] is True
    assert measurement["generated_source_lines"] == 0
    assert measurement["reused_source_lines"] == len(
        source.read_text(encoding="utf-8").splitlines()
    )
    assert measurement["builder_claim_matches_measurement"] is False


def test_from_scratch_cannot_select_retained_component(tmp_path: Path) -> None:
    bundle = {
        "files": [],
        "candidate_command": ["python", "/workspace/candidate.py"],
        "selected_component_id": "grounded-researcher-v1",
        "reused_source_lines": 0,
    }

    with pytest.raises(RuntimeError, match="attempted component reuse"):
        validate_and_write_bundle(REPOSITORY, tmp_path, bundle, "from-scratch")


def test_run_plan_remains_strictly_loadable(tmp_path: Path) -> None:
    _, source, _ = eligible_component(REPOSITORY)
    retained_lines = len(source.read_text(encoding="utf-8").splitlines())
    bundle = {
        "files": [],
        "candidate_command": ["python", "/workspace/candidate.py"],
        "selected_component_id": "grounded-researcher-v1",
        "reused_source_lines": retained_lines,
    }
    selected = validate_and_write_bundle(REPOSITORY, tmp_path, bundle, "blackridge-hybrid")
    measurement = measure_reuse(tmp_path, selected, bundle["reused_source_lines"])

    plan = run_plan(
        method="blackridge-hybrid",
        attempt=1,
        workspace=tmp_path,
        duration=1.0,
        reuse=measurement,
    )

    assert BenchmarkRunPlan.model_validate(plan).telemetry.reused_source_lines == retained_lines


def test_prompts_keep_component_out_of_baseline() -> None:
    baseline = builder_prompt(REPOSITORY, "from-scratch")
    hybrid = builder_prompt(REPOSITORY, "blackridge-hybrid")

    assert "grounded_researcher.py (exact eligible source)" not in baseline
    assert "grounded_researcher.py (exact eligible source)" in hybrid
    assert "selected_component_id to grounded-researcher-v1" in hybrid
