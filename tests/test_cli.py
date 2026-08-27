from __future__ import annotations

import json
from datetime import UTC, datetime

from typer.testing import CliRunner

from blackridge.cli import app
from blackridge.doctor import ToolCheck
from blackridge.evidence import ProbeEvidence

runner = CliRunner()


def test_cli_help_is_runnable() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Evidence-driven" in result.stdout


def test_review_probe_help_is_runnable() -> None:
    result = runner.invoke(app, ["review-probe", "--help"])

    assert result.exit_code == 0
    assert "--subject-type" in result.stdout


def test_doctor_exits_nonzero_for_a_nonfunctional_required_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        "blackridge.cli.check_tools",
        lambda: [
            ToolCheck(
                name="git",
                required_for_mvp=True,
                available=False,
                purpose="source control",
                path="/tools/git",
                detail="broken installation",
            )
        ],
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "broken installation" in result.stdout


def test_compose_run_retains_evidence_but_does_not_publish_failed_output(
    tmp_path, monkeypatch
) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    input_file = tmp_path / "input.json"
    input_file.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"
    evidence = tmp_path / "evidence.json"
    probe = ProbeEvidence(
        probe_id="a" * 32,
        observed_at=datetime(2026, 8, 27, tzinfo=UTC),
        provider="fixture-runtime",
        subject="fixture-bundle",
        request={},
        observations={
            "all_steps_completed": False,
            "final_artifact": {"partial": True},
        },
        sources=["https://example.test/runtime"],
    )
    monkeypatch.setattr("blackridge.cli.run_generated_system", lambda *_args, **_kwargs: probe)

    result = runner.invoke(
        app,
        [
            "compose-run",
            str(bundle),
            str(input_file),
            "--provenance-sha256",
            "b" * 64,
            "--output",
            str(output),
            "--evidence",
            str(evidence),
        ],
    )

    assert result.exit_code == 1
    assert "not published" in result.stdout
    assert output.exists() is False
    assert json.loads(evidence.read_text(encoding="utf-8"))["probe_id"] == "a" * 32
