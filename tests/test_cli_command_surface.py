"""Cover the CLI commands that had no test at all.

Nineteen of the thirty registered commands were never invoked by a test, so a broken Typer
signature or an unhandled exception on a malformed input would have shipped silently. These
tests assert the two properties that matter for every command: it is registered and its help
renders, and a bad input fails closed with a usable message instead of a traceback.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from blackridge.cli import app

runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[1]

UNTESTED_COMMANDS = [
    "benchmark-calibrate",
    "benchmark-compare",
    "benchmark-evaluate",
    "check-provenance",
    "compliance-notices",
    "compose-generate",
    "materialize-proposal",
    "plan",
    "probe-adapter",
    "probe-composer",
    "probe-composition",
    "probe-environment",
    "probe-package",
    "probe-source-provenance",
    "probe-supply-chain",
    "propose-challenger",
    "propose-gap",
    "repair-challenger-interfaces",
    "select-champion",
]


def test_every_registered_command_is_reachable() -> None:
    registered = set(get_command(app).commands)
    missing = sorted(set(UNTESTED_COMMANDS) - registered)
    assert missing == [], f"commands disappeared from the CLI: {missing}"


@pytest.mark.parametrize("command", UNTESTED_COMMANDS)
def test_command_help_renders(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output
    # A truncated or empty help body means the signature failed to render, not that the
    # command is simple.
    assert command in result.output
    assert "Usage:" in result.output


@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("plan", ["missing-brief.md"]),
        ("benchmark-evaluate", ["missing-definition.yaml", "missing-plan.json"]),
        ("select-champion", ["missing-round.json"]),
        ("probe-adapter", ["missing-adapter.yaml"]),
        ("probe-composition", ["missing-composition.yaml"]),
        ("probe-environment", ["missing-environment.yaml"]),
        ("probe-supply-chain", ["missing-supply-chain.yaml"]),
        ("check-provenance", ["missing-provenance.yaml"]),
    ],
)
def test_missing_input_file_is_rejected_before_any_work(
    tmp_path: Path, command: str, arguments: list[str]
) -> None:
    output = tmp_path / "should-not-exist.json"
    result = runner.invoke(app, [command, *arguments, "--output", str(output)])
    assert result.exit_code != 0
    assert not output.exists(), "a rejected invocation must not leave a partial artifact"


def test_select_champion_rejects_malformed_round_evidence(tmp_path: Path) -> None:
    round_file = tmp_path / "round.json"
    round_file.write_text("{ not json", encoding="utf-8")
    output = tmp_path / "selection.json"
    result = runner.invoke(app, ["select-champion", str(round_file), "--output", str(output)])
    assert result.exit_code != 0
    assert not output.exists()


def test_select_champion_rejects_schema_valid_json_that_is_not_a_round(tmp_path: Path) -> None:
    round_file = tmp_path / "round.json"
    round_file.write_text(json.dumps({"schema_version": "1"}), encoding="utf-8")
    output = tmp_path / "selection.json"
    result = runner.invoke(app, ["select-champion", str(round_file), "--output", str(output)])
    assert result.exit_code != 0
    assert not output.exists()


def test_compliance_notices_check_matches_the_committed_notices() -> None:
    result = runner.invoke(app, ["compliance-notices", "--check"])
    assert result.exit_code == 0, result.output


def test_compliance_notices_detects_a_drifted_notice_file(tmp_path: Path) -> None:
    drifted = tmp_path / "THIRD_PARTY_NOTICES.md"
    drifted.write_text("# Not the generated notices\n", encoding="utf-8")
    result = runner.invoke(app, ["compliance-notices", "--check", "--output", str(drifted)])
    assert result.exit_code != 0


def test_probe_package_rejects_an_unsupported_ecosystem(tmp_path: Path) -> None:
    output = tmp_path / "package.json"
    result = runner.invoke(
        app, ["probe-package", "not-an-ecosystem", "example", "--output", str(output)]
    )
    assert result.exit_code != 0
    assert not output.exists()


def test_propose_gap_requires_both_request_and_discovery(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("Build a deterministic fixture system.\n", encoding="utf-8")
    result = runner.invoke(app, ["propose-gap", str(brief)])
    assert result.exit_code != 0
