from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

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
