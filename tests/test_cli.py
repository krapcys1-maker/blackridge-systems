from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from _terminal import plain
from typer.main import get_command
from typer.testing import CliRunner

from blackridge.cli import _environment_probe_gate_failures, app
from blackridge.doctor import ToolCheck
from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.io import load_request, write_run
from blackridge.models import (
    Candidate,
    CapabilityResult,
    DiscoveryRun,
    RepositoryMetadata,
    ScoreBreakdown,
    SearchQuery,
)

runner = CliRunner()
REPOSITORY = Path(__file__).resolve().parents[1]


def _request_file(tmp_path: Path) -> Path:
    path = tmp_path / "request.yaml"
    path.write_text(
        """schema_version: "1"
name: fixture-system
goal: Build a deterministic fixture system with retained evidence.
capabilities:
  - id: first-capability
    description: Discover the first reusable fixture capability.
    searches:
      - keywords: [first, fixture]
  - id: second-capability
    description: Discover the second reusable fixture capability.
    searches:
      - keywords: [second, fixture]
""",
        encoding="utf-8",
    )
    return path


def _empty_run(request_file: Path) -> DiscoveryRun:
    request = load_request(request_file)
    return DiscoveryRun(
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        provider="fixture-discovery/1",
        request=request,
        results=[
            CapabilityResult(capability=capability, candidates=[])
            for capability in request.capabilities
        ],
    )


def test_cli_help_is_runnable() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Evidence-driven" in plain(result.stdout)


def _environment_probe_observations() -> dict[str, object]:
    return {
        "probe_completed": True,
        "execution_boundary": {"requested": "none", "applied": True},
        "commands": [{"id": "behavior-test", "exit_code": 0, "transport_error": None}],
        "not_run_command_ids": [],
        "host_workspace": {"unchanged": True},
        "cleanup": {
            "stop_error": None,
            "force_remove": {"exit_code": 0},
            "container_exists_after_stop": False,
        },
    }


def test_environment_probe_completion_gate_accepts_complete_evidence() -> None:
    probe = ProbeEvidence(
        probe_id="a" * 32,
        observed_at=datetime(2026, 8, 29, tzinfo=UTC),
        provider="fixture",
        subject="fixture",
        request={},
        observations=_environment_probe_observations(),
        sources=["https://example.test/source"],
    )

    assert _environment_probe_gate_failures(probe) == []


def test_environment_probe_completion_gate_reports_each_critical_boundary() -> None:
    observations = _environment_probe_observations()
    observations["probe_completed"] = False
    observations["execution_boundary"] = {"requested": "none", "applied": False}
    observations["commands"] = [{"id": "behavior-test", "exit_code": 9, "transport_error": None}]
    observations["not_run_command_ids"] = ["second-test"]
    observations["host_workspace"] = {"unchanged": False}
    observations["cleanup"] = {
        "stop_error": "stop failed",
        "force_remove": {"exit_code": 7},
        "container_exists_after_stop": True,
    }
    probe = ProbeEvidence(
        probe_id="b" * 32,
        observed_at=datetime(2026, 8, 29, tzinfo=UTC),
        provider="fixture",
        subject="fixture",
        request={},
        observations=observations,
        sources=["https://example.test/source"],
    )

    failures = _environment_probe_gate_failures(probe)

    assert "probe did not complete" in failures
    assert "requested network isolation was not applied" in failures
    assert "command behavior-test exited with code 9" in failures
    assert "commands were not run: second-test" in failures
    assert "host workspace integrity was not confirmed" in failures
    assert "container stop failed: stop failed" in failures
    assert "forced container cleanup exited with code 7" in failures
    assert "container cleanup was not confirmed" in failures


def test_release_help_does_not_render_a_truncated_image_digest() -> None:
    for command in ("probe-wheel-release", "probe-image-release"):
        result = runner.invoke(app, [command, "--help"])

        assert result.exit_code == 0
        assert "--syft-image" in plain(result.stdout)
        assert "…" not in plain(result.stdout)
        assert "\ufffd" not in plain(result.stdout)
        assert "678bfa565b60" not in plain(result.stdout)


def test_review_probe_help_is_runnable() -> None:
    result = runner.invoke(app, ["review-probe", "--help"])

    assert result.exit_code == 0
    root_command = get_command(app)
    assert hasattr(root_command, "commands")
    review_command = root_command.commands["review-probe"]
    assert any("--subject-type" in parameter.opts for parameter in review_command.params)


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
    assert "broken installation" in plain(result.stdout)


def test_doctor_preserves_complete_tool_names_and_diagnostics(monkeypatch) -> None:
    monkeypatch.setattr(
        "blackridge.cli.check_tools",
        lambda: [
            ToolCheck(
                name="pypi-attestations",
                required_for_mvp=False,
                available=True,
                purpose="cryptographic PyPI provenance verification",
                path="/tools/pypi-attestations",
                detail="pypi-attestations 0.0.30 on windows",
            )
        ],
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "…" not in plain(result.stdout)
    assert "\ufffd" not in plain(result.stdout)
    assert "pypi-attestations" in plain(result.stdout)
    assert "pypi-attestations 0.0.30 on windows" in plain(result.stdout)


def test_report_preserves_complete_repository_and_decision(tmp_path: Path) -> None:
    request_file = _request_file(tmp_path)
    request = load_request(request_file)
    candidate = Candidate(
        capability_id="first-capability",
        metadata=RepositoryMetadata(
            full_name="zongmin-yu/semantic-scholar-fastmcp-mcp-server",
            url="https://github.com/zongmin-yu/semantic-scholar-fastmcp-mcp-server",
            license_spdx="MIT",
        ),
        search_query=SearchQuery(keywords=["semantic", "scholar"]),
        search_position=1,
        decision="eligible-for-inspection",
        score=ScoreBreakdown(
            search_fit=1,
            maintenance=1,
            adoption=1,
            community=1,
            issue_health=1,
            license_confidence=1,
            security_posture=1,
            total=7,
        ),
    )
    run = DiscoveryRun(
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        provider="fixture-discovery/1",
        request=request,
        results=[CapabilityResult(capability=request.capabilities[0], candidates=[candidate])],
    )
    run_file = tmp_path / "discovery.json"
    write_run(run, run_file)

    result = runner.invoke(app, ["report", str(run_file)])

    assert result.exit_code == 0
    assert "zongmin-yu/semantic-scholar-fastmcp-mcp-server" in plain(result.stdout)
    assert "decision=eligible-for-inspection" in plain(result.stdout)
    assert "…" not in plain(result.stdout)
    assert "\ufffd" not in plain(result.stdout)


def test_discover_filters_capability_and_writes_a_strict_run(tmp_path, monkeypatch) -> None:
    request_file = _request_file(tmp_path)
    output = tmp_path / "nested" / "discovery.json"
    observed: dict[str, object] = {}

    def fake_discovery(request, **kwargs):
        observed.update(
            capability_ids=[item.id for item in request.capabilities],
            limit=kwargs["limit"],
            workers=kwargs["workers"],
            provider=kwargs["discovery"].provider_name,
        )
        return DiscoveryRun(
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
            provider="fixture-discovery/1",
            request=request,
            results=[CapabilityResult(capability=request.capabilities[0], candidates=[])],
        )

    monkeypatch.setattr("blackridge.cli.run_discovery", fake_discovery)

    result = runner.invoke(
        app,
        [
            "discover",
            str(request_file),
            "--output",
            str(output),
            "--capability",
            "second-capability",
            "--limit",
            "3",
            "--workers",
            "2",
            "--octocode-package",
            "octocode@fixture",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "capability_ids": ["second-capability"],
        "limit": 3,
        "workers": 2,
        "provider": "octocode-cli:octocode@fixture",
    }
    written = DiscoveryRun.model_validate_json(output.read_text(encoding="utf-8"))
    assert [item.id for item in written.request.capabilities] == ["second-capability"]
    assert "0 candidates written" in plain(result.stdout)
    assert "not approved components" in plain(result.stdout)


def test_discover_rejects_an_unknown_capability_without_output(tmp_path) -> None:
    request_file = _request_file(tmp_path)
    output = tmp_path / "discovery.json"

    result = runner.invoke(
        app,
        [
            "discover",
            str(request_file),
            "--output",
            str(output),
            "--capability",
            "missing-capability",
        ],
    )

    assert result.exit_code == 2
    assert "capability not found in request: missing-capability" in plain(result.stdout)
    assert output.exists() is False


def test_discover_can_use_official_github_provider_and_denied_policy(tmp_path, monkeypatch) -> None:
    request_file = _request_file(tmp_path)
    output = tmp_path / "discovery.json"
    observed: dict[str, object] = {}

    def fake_discovery(request, **kwargs):
        observed.update(
            provider=kwargs["discovery"].provider_name,
            max_queries=kwargs["discovery"].max_queries,
            denied=kwargs["denied_repositories"],
        )
        return DiscoveryRun(
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
            provider=kwargs["discovery"].provider_name,
            request=request,
            results=[
                CapabilityResult(capability=item, candidates=[]) for item in request.capabilities
            ],
        )

    monkeypatch.setattr("blackridge.cli.run_discovery", fake_discovery)
    result = runner.invoke(
        app,
        [
            "discover",
            str(request_file),
            "--output",
            str(output),
            "--provider",
            "github",
            "--max-queries",
            "12",
            "--deny-repository",
            "blocked/repository",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "provider": "github-cli:search-repos",
        "max_queries": 12,
        "denied": {"blocked/repository"},
    }


def test_report_and_blueprint_handle_a_capability_without_candidates(tmp_path) -> None:
    request_file = _request_file(tmp_path)
    run_file = tmp_path / "run.json"
    blueprint_file = tmp_path / "blueprint.yaml"
    write_run(_empty_run(request_file), run_file)

    report_result = runner.invoke(app, ["report", str(run_file), "--top", "2"])
    blueprint_result = runner.invoke(
        app, ["blueprint", str(run_file), "--output", str(blueprint_file)]
    )

    assert report_result.exit_code == 0
    assert "fixture-system" in plain(report_result.stdout)
    assert "fixture-system - Build a deterministic" in plain(report_result.stdout)
    assert "first-capability" in plain(report_result.stdout)
    assert "no candidates" in plain(report_result.stdout)
    assert "\ufffd" not in plain(report_result.stdout)
    assert "—" not in plain(report_result.stdout)
    assert blueprint_result.exit_code == 0
    blueprint = yaml.safe_load(blueprint_file.read_text(encoding="utf-8"))
    assert [item["status"] for item in blueprint["components"]] == [
        "no-candidate",
        "no-candidate",
    ]
    assert blueprint["release_ready"] is False
    assert "No component is release-ready" in plain(blueprint_result.stdout)


def test_compose_solve_cli_writes_complete_and_incomplete_plans(tmp_path) -> None:
    complete_plan = tmp_path / "complete.yaml"
    incomplete_plan = tmp_path / "incomplete.yaml"

    complete = runner.invoke(
        app,
        [
            "compose-solve",
            str(REPOSITORY / "examples" / "composition-linear-calibration.yaml"),
            "--output",
            str(complete_plan),
        ],
    )
    incomplete = runner.invoke(
        app,
        [
            "compose-solve",
            str(REPOSITORY / "examples" / "composition-production-unreviewed.yaml"),
            "--output",
            str(incomplete_plan),
        ],
    )

    assert complete.exit_code == 0
    assert "Compatibility plan complete: True" in plain(complete.stdout)
    assert complete_plan.is_file()
    assert incomplete.exit_code == 1
    assert "Compatibility plan complete: False" in plain(incomplete.stdout)
    assert incomplete_plan.is_file()


def test_compose_run_invalid_json_retains_failure_evidence(tmp_path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    input_file = tmp_path / "invalid.json"
    input_file.write_text("{", encoding="utf-8")
    output = tmp_path / "output.json"
    evidence = tmp_path / "evidence.json"

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

    assert result.exit_code == 2
    assert "Generated system execution failed" in plain(result.stdout)
    assert output.exists() is False
    failure = json.loads(evidence.read_text(encoding="utf-8"))
    assert failure["observations"]["probe_completed"] is False
    assert failure["observations"]["error_type"] == "JSONDecodeError"


def test_compose_run_publishes_only_a_completed_artifact(tmp_path, monkeypatch) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    input_file = tmp_path / "input.json"
    input_file.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"
    evidence = tmp_path / "evidence.json"
    probe = ProbeEvidence(
        probe_id="c" * 32,
        observed_at=datetime(2026, 8, 27, tzinfo=UTC),
        provider="fixture-runtime",
        subject="fixture-bundle",
        request={},
        observations={"all_steps_completed": True, "final_artifact": {"answer": 42}},
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
            "d" * 64,
            "--output",
            str(output),
            "--evidence",
            str(evidence),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {"answer": 42}
    assert json.loads(evidence.read_text(encoding="utf-8"))["probe_id"] == "c" * 32
    assert "All generated steps completed: True" in plain(result.stdout)
    assert "Output artifact written" in plain(result.stdout)


def test_compose_run_sandbox_reports_isolation_and_cleanup(tmp_path, monkeypatch) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    input_file = tmp_path / "input.json"
    input_file.write_text("{}", encoding="utf-8")
    output = tmp_path / "output.json"
    evidence = tmp_path / "evidence.json"
    probe = ProbeEvidence(
        probe_id="e" * 32,
        observed_at=datetime(2026, 8, 27, tzinfo=UTC),
        provider="fixture-sandbox",
        subject="fixture-bundle",
        request={},
        observations={
            "all_steps_completed": True,
            "final_artifact": {"sandboxed": True},
            "sandbox": {
                "image": {"resolved_id": "sha256:" + "f" * 64},
                "cleanup": {"container_exists_after": False},
            },
        },
        sources=["https://example.test/runtime"],
    )
    monkeypatch.setattr(
        "blackridge.cli.run_generated_system_sandboxed", lambda *_args, **_kwargs: probe
    )

    result = runner.invoke(
        app,
        [
            "compose-run-sandbox",
            str(bundle),
            str(input_file),
            "--provenance-sha256",
            "a" * 64,
            "--image",
            "sha256:" + "f" * 64,
            "--output",
            str(output),
            "--evidence",
            str(evidence),
        ],
    )

    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {"sandboxed": True}
    assert "Container remaining after cleanup: False" in plain(result.stdout)
    assert "Calibration only" in plain(result.stdout)


def test_verify_holdout_cli_retains_success_and_failure_evidence(tmp_path, monkeypatch) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    manifest = suite / "holdout-manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    success_output = tmp_path / "success.json"
    failure_output = tmp_path / "failure.json"
    probe = ProbeEvidence(
        probe_id="f" * 32,
        observed_at=datetime(2026, 8, 27, tzinfo=UTC),
        provider="blackridge-sealed-holdout-verifier/1",
        subject="external-suite@1",
        request={},
        observations={"probe_completed": True, "file_count": 3, "case_file_count": 1},
        sources=[str(manifest)],
    )
    monkeypatch.setattr("blackridge.cli.verify_sealed_holdout", lambda *_args, **_kwargs: probe)

    success = runner.invoke(
        app,
        [
            "verify-holdout",
            str(suite),
            "--manifest-sha256",
            "a" * 64,
            "--system-revision",
            "b" * 40,
            "--output",
            str(success_output),
        ],
    )

    assert success.exit_code == 0
    assert "Sealed holdout verified: external-suite@1" in plain(success.stdout)
    assert json.loads(success_output.read_text(encoding="utf-8"))["probe_id"] == "f" * 32

    def fail(*_args, **_kwargs):
        raise BlackridgeError("fixture seal mismatch")

    monkeypatch.setattr("blackridge.cli.verify_sealed_holdout", fail)
    failure = runner.invoke(
        app,
        [
            "verify-holdout",
            str(suite),
            "--manifest-sha256",
            "a" * 64,
            "--system-revision",
            "b" * 40,
            "--output",
            str(failure_output),
        ],
    )

    assert failure.exit_code == 2
    assert "fixture seal mismatch" in plain(failure.stdout)
    retained = json.loads(failure_output.read_text(encoding="utf-8"))
    assert retained["observations"]["probe_completed"] is False
    assert retained["observations"]["error_type"] == "BlackridgeError"


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
    assert "not published" in plain(result.stdout)
    assert output.exists() is False
    assert json.loads(evidence.read_text(encoding="utf-8"))["probe_id"] == "a" * 32
