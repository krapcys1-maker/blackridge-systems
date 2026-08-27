from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

from blackridge.process_boundary import BoundedProcessResult
from blackridge.sandbox import SandboxExperiment, SwerexDockerProbe, WorkspaceSnapshot


def experiment_data() -> dict[str, object]:
    return {
        "name": "small-public-repository",
        "description": "Exercise a public Python repository at one immutable commit.",
        "repository_url": "https://github.com/pypa/sampleproject",
        "commit": "621e4974ca25ce531773def586ba3ed8e736b3fc",
        "image": "blackridge/swerex-runtime:1.4.0",
        "commands": [
            {
                "id": "real-check",
                "description": "Run a behavior-bearing repository command.",
                "argv": ["python", "-m", "unittest"],
            }
        ],
    }


def process_result(
    argv: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> BoundedProcessResult:
    return BoundedProcessResult(
        argv=tuple(argv),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.1,
        timed_out=False,
        output_limit_exceeded=False,
        stdout_bytes_seen=len(stdout.encode()),
        stderr_bytes_seen=len(stderr.encode()),
    )


def test_experiment_requires_an_exact_commit() -> None:
    data = experiment_data()
    data["commit"] = "main"

    with pytest.raises(ValidationError):
        SandboxExperiment.model_validate(data)


def test_source_setup_fetches_only_requested_commit_without_shell() -> None:
    experiment = SandboxExperiment.model_validate(experiment_data())

    commands = SwerexDockerProbe._setup_commands(experiment)
    fetch = next(item for item in commands if item["id"] == "source-fetch")

    assert fetch["argv"][-1] == experiment.commit
    assert all(isinstance(item["argv"], list) for item in commands)
    assert "shell" not in fetch


def test_production_experiment_requires_networkless_workload() -> None:
    data = experiment_data()
    data["execution_profile"] = "production"
    data["execution_network"] = "inherit"

    with pytest.raises(ValidationError, match="execution_network=none"):
        SandboxExperiment.model_validate(data)


def test_preparation_and_workload_ids_are_unique() -> None:
    data = experiment_data()
    data["preparation_commands"] = [data["commands"][0]]

    with pytest.raises(ValidationError, match="command ids must be unique"):
        SandboxExperiment.model_validate(data)


def test_sandbox_rejects_unknown_controls_and_workdir_escape() -> None:
    misspelled = experiment_data()
    misspelled["commands"][0]["timeout_secodns"] = 1
    with pytest.raises(ValidationError, match="timeout_secodns"):
        SandboxExperiment.model_validate(misspelled)

    escaped = experiment_data()
    escaped["workdir"] = "/workspace/../../etc"
    with pytest.raises(ValidationError, match="workdir must stay below"):
        SandboxExperiment.model_validate(escaped)


def test_preparation_commands_have_a_separate_phase() -> None:
    data = experiment_data()
    data["preparation_commands"] = [
        {
            "id": "install-package",
            "description": "Install the exact package before workload isolation.",
            "argv": ["python", "-m", "pip", "install", "."],
        }
    ]
    experiment = SandboxExperiment.model_validate(data)

    commands = SwerexDockerProbe._preparation_commands(experiment)

    assert commands[0]["id"] == "install-package"
    assert commands[0]["phase"] == "preparation"


def test_network_isolation_disconnects_every_observed_network(monkeypatch) -> None:
    snapshots = iter([{"bridge": {"IPAddress": "172.17.0.2"}}, {}])
    calls: list[list[str]] = []

    monkeypatch.setattr(
        SwerexDockerProbe,
        "_container_networks",
        staticmethod(lambda _name: next(snapshots)),
    )

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return process_result(argv)

    monkeypatch.setattr("blackridge.sandbox.run_bounded", fake_run)

    result = SwerexDockerProbe._isolate_execution_network("exact-container")

    assert result["applied"] is True
    assert result["networks_after"] == {}
    assert result["host_environment_forwarded"] == []
    assert calls == [
        [
            "docker",
            "network",
            "disconnect",
            "--force",
            "bridge",
            "exact-container",
        ]
    ]


def test_network_isolation_fails_closed_when_a_network_remains(monkeypatch) -> None:
    network = {"bridge": {"IPAddress": "172.17.0.2"}}
    snapshots = iter([network, network])
    monkeypatch.setattr(
        SwerexDockerProbe,
        "_container_networks",
        staticmethod(lambda _name: next(snapshots)),
    )
    monkeypatch.setattr(
        "blackridge.sandbox.run_bounded",
        lambda argv, **_kwargs: process_result(argv, 1, stderr="denied"),
    )

    result = SwerexDockerProbe._isolate_execution_network("exact-container")

    assert result["applied"] is False
    assert result["error"] == ("container network isolation did not remove every attached network")
    assert result["networks_after"] == network


def test_networkless_workload_uses_shell_free_docker_exec(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return process_result(argv, stdout="ok\n")

    monkeypatch.setattr("blackridge.sandbox.run_bounded", fake_run)
    result = SwerexDockerProbe._docker_exec_result(
        "exact-container",
        {
            "id": "real-check",
            "description": "Run one behavior-bearing command.",
            "argv": ["python", "-c", "print(42)"],
            "cwd": "/workspace/repository",
            "timeout_seconds": 30,
            "phase": "experiment",
        },
    )

    assert observed["argv"] == [
        "docker",
        "exec",
        "--user",
        "65534:65534",
        "--workdir",
        "/workspace/repository",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONIOENCODING=utf-8",
        "--env",
        "TMPDIR=/tmp",
        "exact-container",
        "timeout",
        "--verbose",
        "--signal=TERM",
        "--kill-after=1s",
        "30s",
        "python",
        "-c",
        "print(42)",
    ]
    assert observed["kwargs"] == {"timeout_seconds": 35.0}
    assert result["executor"] == "docker-exec-shell-free"
    assert result["user"] == "65534:65534"
    assert result["timed_out"] is False
    assert result["exit_code"] == 0


def test_container_timeout_escalation_is_retained(monkeypatch) -> None:
    ticks = iter([100.0, 102.1])
    monkeypatch.setattr("blackridge.sandbox.perf_counter", lambda: next(ticks))
    monkeypatch.setattr(
        "blackridge.sandbox.run_bounded",
        lambda argv, **_kwargs: process_result(argv, 137),
    )

    result = SwerexDockerProbe._docker_exec_result(
        "exact-container",
        {
            "id": "ignore-term",
            "description": "Ignore TERM until the timeout escalates to KILL.",
            "argv": ["python", "hanging.py"],
            "cwd": "/workspace/repository",
            "timeout_seconds": 1,
            "phase": "experiment",
        },
    )

    assert result["container_argv"][:5] == [
        "timeout",
        "--verbose",
        "--signal=TERM",
        "--kill-after=1s",
        "1s",
    ]
    assert result["timed_out"] is True
    assert result["exit_code"] == 137


def test_workspace_snapshot_detects_real_content_change(tmp_path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    source = tmp_path / "component.py"
    source.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "component.py"], cwd=tmp_path, check=True)
    before = WorkspaceSnapshot.capture(tmp_path)

    source.write_text("value = 2\n", encoding="utf-8")
    after = WorkspaceSnapshot.capture(tmp_path)

    assert before.digest != after.digest
    assert before.changed_paths(after) == ["component.py"]
