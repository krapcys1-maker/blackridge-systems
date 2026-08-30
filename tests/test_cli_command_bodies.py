"""Execute the bodies of previously untested CLI commands.

`--help` proves a command is registered; it does not run a single line of the command body.
These tests drive the hermetic commands end to end with real repository fixtures, and assert
the property the project actually cares about: evidence is retained, and no probe assigns
itself a verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from _terminal import plain
from test_evolution import _round
from typer.testing import CliRunner

from blackridge.cli import app

runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[1]


def _evidence(path: Path) -> dict[str, object]:
    payload: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def test_probe_adapter_retains_evidence_without_assigning_a_verdict(tmp_path: Path) -> None:
    output = tmp_path / "adapter.json"
    result = runner.invoke(
        app,
        [
            "probe-adapter",
            str(REPOSITORY / "examples" / "adapter-paper-title-to-document-name.yaml"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    evidence = _evidence(output)
    assert evidence["observations"]["probe_completed"] is True
    assert "manual review" in plain(result.output).lower()


def test_probe_adapter_retains_the_broken_control_as_evidence(tmp_path: Path) -> None:
    output = tmp_path / "adapter-broken.json"
    result = runner.invoke(
        app,
        [
            "probe-adapter",
            str(REPOSITORY / "examples" / "adapter-paper-title-broken.yaml"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    evidence = _evidence(output)
    # The broken control must be retained as an observation, never silently normalized.
    assert output.is_file()
    assert evidence["observations"]


def test_check_provenance_passes_on_the_committed_registry(tmp_path: Path) -> None:
    output = tmp_path / "provenance.json"
    result = runner.invoke(app, ["check-provenance", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert "Provenance issues: 0" in plain(result.output)
    assert output.is_file()


def test_check_provenance_fails_closed_on_the_invalid_registry(tmp_path: Path) -> None:
    output = tmp_path / "provenance-invalid.json"
    result = runner.invoke(
        app,
        [
            "check-provenance",
            str(REPOSITORY / "provenance" / "derived-code-invalid-example.yaml"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0


def test_probe_source_provenance_retains_raw_matches(tmp_path: Path) -> None:
    output = tmp_path / "source-provenance.json"
    result = runner.invoke(
        app,
        [
            "probe-source-provenance",
            str(REPOSITORY / "provenance" / "source-audit.yaml"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "zero matches is not proof of originality" in plain(result.output)
    assert _evidence(output)["observations"]


def test_probe_composer_solves_generates_and_runs_a_reused_component(tmp_path: Path) -> None:
    case = REPOSITORY / "benchmarks" / "composition-reuse-v1" / "cases" / "reuse-complete.yaml"
    request = (
        REPOSITORY / "components" / "grounded_researcher_v1" / "fixtures" / "policy-answerable.json"
    )
    output = tmp_path / "composer.json"
    result = runner.invoke(
        app,
        [
            "probe-composer",
            str(case),
            str(request),
            str(tmp_path / "bundle"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    observations = _evidence(output)["observations"]
    assert observations["plan"]["complete"] is True
    assert observations["plan"]["selected_component_ids"] == ["grounded-researcher-v1"]
    assert observations["runtime"]["observations"]["all_steps_completed"] is True
    assert "No manual PASS/FAIL was assigned." in plain(result.output)


def test_compose_generate_writes_a_provenance_locked_bundle(tmp_path: Path) -> None:
    case = REPOSITORY / "benchmarks" / "composition-reuse-v1" / "cases" / "reuse-complete.yaml"
    plan = tmp_path / "plan.yaml"
    solved = runner.invoke(app, ["compose-solve", str(case), "--output", str(plan)])
    assert solved.exit_code == 0, solved.output

    bundle = tmp_path / "bundle"
    generated = runner.invoke(app, ["compose-generate", str(case), str(plan), str(bundle)])
    assert generated.exit_code == 0, generated.output
    assert (bundle / "provenance.json").is_file()
    assert "Trusted provenance SHA-256:" in plain(generated.output)


def test_select_champion_writes_a_deterministic_selection(tmp_path: Path) -> None:
    round_file = tmp_path / "round.json"
    round_file.write_text(json.dumps(_round()), encoding="utf-8")
    output = tmp_path / "selection.json"
    result = runner.invoke(app, ["select-champion", str(round_file), "--output", str(output)])
    assert result.exit_code == 0, result.output
    selection = _evidence(output)
    assert selection["selected_candidate_id"]
    # The candidate that failed a critical safety gate must never win on metrics alone.
    assert selection["selected_candidate_id"] != "candidate-b"


def test_select_champion_is_reproducible_for_the_same_round(tmp_path: Path) -> None:
    round_file = tmp_path / "round.json"
    round_file.write_text(json.dumps(_round()), encoding="utf-8")
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        result = runner.invoke(app, ["select-champion", str(round_file), "--output", str(output)])
        assert result.exit_code == 0, result.output
    assert _evidence(first)["selected_candidate_id"] == _evidence(second)["selected_candidate_id"]


def test_benchmark_calibrate_retains_failure_evidence_for_a_wrong_file_kind(
    tmp_path: Path,
) -> None:
    definition = (
        REPOSITORY / "benchmarks" / "scientific-researcher-v1" / "calibration-reference.yaml"
    )
    output = tmp_path / "calibration.json"
    result = runner.invoke(
        app,
        [
            "benchmark-calibrate",
            str(definition),
            str(definition),
            str(definition),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0
    # A rejected calibration still has to leave retained evidence rather than vanish.
    assert output.is_file()
    assert _evidence(output)["observations"] is not None
