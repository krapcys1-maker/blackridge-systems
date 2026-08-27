from __future__ import annotations

from typer.testing import CliRunner

from blackridge.cli import app
from blackridge.doctor import ToolCheck

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
