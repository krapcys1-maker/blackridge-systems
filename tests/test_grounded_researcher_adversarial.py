from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from blackridge.process_boundary import run_bounded

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "grounded_researcher_v1" / "grounded_researcher.py"
PUBLIC = ROOT / "benchmarks" / "scientific-researcher-v1" / "public"
SPEC = importlib.util.spec_from_file_location(
    "adversarial_probe_under_test",
    ROOT / "tools" / "probe_grounded_researcher_adversarial.py",
)
assert SPEC is not None and SPEC.loader is not None
PROBE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROBE
SPEC.loader.exec_module(PROBE)
CASES = PROBE._build_cases(ROOT)
INPUT_VALIDATOR = Draft202012Validator(
    json.loads((PUBLIC / "research-input.schema.json").read_text(encoding="utf-8"))
)
OUTPUT_VALIDATOR = Draft202012Validator(
    json.loads((PUBLIC / "research-output.schema.json").read_text(encoding="utf-8"))
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_adversarial_case_matches_every_check(case: object) -> None:
    result = run_bounded(
        [sys.executable, str(COMPONENT)],
        input_text=json.dumps(case.request),
        timeout_seconds=10,
        maximum_output_bytes_per_stream=500_000,
    )
    output = json.loads(result.stdout)
    execution = {
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "timed_out": result.timed_out,
    }

    checks = PROBE._inspect(
        case,
        execution,
        output,
        INPUT_VALIDATOR,
        OUTPUT_VALIDATOR,
    )

    assert [check for check in checks if not check["matched"]] == []
