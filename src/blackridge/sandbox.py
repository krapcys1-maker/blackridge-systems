"""Disposable repository execution through the upstream SWE-ReX Docker backend."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence

SWEREX_VERSION = "1.4.0"
SWEREX_SOURCE = f"https://github.com/SWE-agent/SWE-ReX/tree/v{SWEREX_VERSION}"


class SandboxCommandSpec(BaseModel):
    """One explicit, shell-free command in a repository experiment."""

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=10)
    argv: list[str] = Field(min_length=1)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)

    @field_validator("argv")
    @classmethod
    def non_empty_argv(cls, value: list[str]) -> list[str]:
        if any(not argument for argument in value):
            raise ValueError("command arguments cannot be empty")
        return value


class SandboxExperiment(BaseModel):
    """Pinned source and commands that will run only inside a disposable container."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20)
    repository_url: str = Field(pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    image: str = Field(min_length=3)
    workdir: str = Field(default="/workspace/repository", pattern=r"^/[A-Za-z0-9_./-]+$")
    commands: list[SandboxCommandSpec] = Field(min_length=1)

    @field_validator("commands")
    @classmethod
    def unique_command_ids(cls, value: list[SandboxCommandSpec]) -> list[SandboxCommandSpec]:
        ids = [command.id for command in value]
        if len(ids) != len(set(ids)):
            raise ValueError("sandbox command ids must be unique")
        return value


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Content hashes for all tracked and non-ignored untracked source files."""

    digest: str
    files: dict[str, str]

    @classmethod
    def capture(cls, root: Path) -> WorkspaceSnapshot:
        completed = subprocess.run(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        names = [
            name
            for name in completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
            if name
        ]
        files: dict[str, str] = {}
        for name in sorted(set(names)):
            path = root / name
            files[name] = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
            )
        canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return cls(digest=hashlib.sha256(canonical).hexdigest(), files=files)

    def changed_paths(self, other: WorkspaceSnapshot) -> list[str]:
        return sorted(
            path
            for path in self.files.keys() | other.files.keys()
            if self.files.get(path) != other.files.get(path)
        )


def inspect_local_image(image: str) -> dict[str, object]:
    """Resolve a local image reference to immutable Docker metadata."""

    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", image],
            check=True,
            capture_output=True,
            text=True,
        )
        inspected = json.loads(completed.stdout)[0]
    except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise BlackridgeError(
            f"cannot inspect local Docker image {image!r}: {detail.strip()}"
        ) from exc

    image_id = inspected.get("Id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise BlackridgeError(f"Docker did not return an immutable image ID for {image!r}")
    config = inspected.get("Config") or {}
    return {
        "requested_reference": image,
        "resolved_id": image_id,
        "repo_digests": inspected.get("RepoDigests") or [],
        "created": inspected.get("Created"),
        "os": inspected.get("Os"),
        "architecture": inspected.get("Architecture"),
        "labels": config.get("Labels") or {},
    }


def _container_exists(name: str | None) -> bool | None:
    if not name:
        return None
    try:
        completed = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        return None
    return bool(completed.stdout.strip())


class SwerexDockerProbe:
    """Thin policy adapter; execution remains implemented by SWE-ReX and Docker."""

    def _runtime_types(self):
        try:
            from swerex.deployment.docker import DockerDeployment
            from swerex.runtime.abstract import Command
        except ImportError as exc:
            raise BlackridgeError(
                "SWE-ReX Docker support is unavailable; install blackridge-systems[sandbox]"
            ) from exc
        return DockerDeployment, Command

    @staticmethod
    def _logger() -> logging.Logger:
        # SWE-ReX creates emoji RichHandlers by default. Besides being noisy evidence,
        # they fail on a default Windows cp1252 console. Pre-seeding these logger names
        # keeps the adapter's output deterministic without changing upstream code.
        for name in ("free_port", "rex-runtime"):
            upstream_logger = logging.getLogger(name)
            upstream_logger.handlers.clear()
            upstream_logger.addHandler(logging.NullHandler())
            upstream_logger.propagate = False
        logger = logging.getLogger("blackridge.swerex")
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    @staticmethod
    def _setup_commands(experiment: SandboxExperiment) -> list[dict[str, object]]:
        repository = experiment.repository_url.removesuffix(".git") + ".git"
        return [
            {
                "id": "runtime-identity",
                "description": "Record the exact SWE-ReX server version inside the image.",
                "argv": ["swerex-remote", "--version"],
                "cwd": None,
                "timeout_seconds": 60,
                "phase": "runtime",
            },
            {
                "id": "runtime-boundary",
                "description": "Read effective Linux process and cgroup isolation limits.",
                "argv": [
                    "python",
                    "-c",
                    (
                        "import json,pathlib; "
                        "status=dict(line.split(':',1) for line in "
                        "pathlib.Path('/proc/self/status').read_text().splitlines() "
                        "if ':' in line); "
                        "paths=['/sys/fs/cgroup/memory.max','/sys/fs/cgroup/pids.max',"
                        "'/sys/fs/cgroup/cpu.max']; "
                        "result={'CapEff':status.get('CapEff','').strip(),"
                        "'NoNewPrivs':status.get('NoNewPrivs','').strip(),"
                        "**{p:pathlib.Path(p).read_text().strip() if pathlib.Path(p).exists() "
                        "else 'unavailable' for p in paths}}; "
                        "print(json.dumps(result,sort_keys=True))"
                    ),
                ],
                "cwd": None,
                "timeout_seconds": 60,
                "phase": "runtime",
            },
            {
                "id": "source-init",
                "description": "Create an empty repository directory inside the container.",
                "argv": ["git", "init", experiment.workdir],
                "cwd": None,
                "timeout_seconds": 60,
                "phase": "source",
            },
            {
                "id": "source-remote",
                "description": "Attach the explicitly requested public repository origin.",
                "argv": ["git", "-C", experiment.workdir, "remote", "add", "origin", repository],
                "cwd": None,
                "timeout_seconds": 60,
                "phase": "source",
            },
            {
                "id": "source-fetch",
                "description": "Fetch only the explicitly pinned commit from the public origin.",
                "argv": [
                    "git",
                    "-C",
                    experiment.workdir,
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    experiment.commit,
                ],
                "cwd": None,
                "timeout_seconds": 180,
                "phase": "source",
            },
            {
                "id": "source-checkout",
                "description": (
                    "Check out the fetched commit in detached mode without substitution."
                ),
                "argv": ["git", "-C", experiment.workdir, "checkout", "--detach", "FETCH_HEAD"],
                "cwd": None,
                "timeout_seconds": 60,
                "phase": "source",
            },
            {
                "id": "source-identity",
                "description": "Print the exact commit that the following commands will execute.",
                "argv": ["git", "-C", experiment.workdir, "rev-parse", "HEAD"],
                "cwd": None,
                "timeout_seconds": 60,
                "phase": "source",
            },
        ]

    @staticmethod
    def _experiment_commands(experiment: SandboxExperiment) -> list[dict[str, object]]:
        return [
            {
                "id": command.id,
                "description": command.description,
                "argv": command.argv,
                "cwd": experiment.workdir,
                "timeout_seconds": command.timeout_seconds,
                "phase": "experiment",
            }
            for command in experiment.commands
        ]

    async def _probe(self, experiment: SandboxExperiment, host_root: Path) -> ProbeEvidence:
        DockerDeployment, Command = self._runtime_types()
        image = inspect_local_image(experiment.image)
        before = WorkspaceSnapshot.capture(host_root)
        started = perf_counter()
        command_results: list[dict[str, object]] = []
        deployment_started = False
        deployment_error: str | None = None
        stop_error: str | None = None
        container_name: str | None = None
        all_commands = self._setup_commands(experiment) + self._experiment_commands(experiment)

        deployment = DockerDeployment(
            image=image["resolved_id"],
            pull="never",
            remove_container=True,
            startup_timeout=180,
            docker_args=[
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--pids-limit=256",
                "--memory=1g",
                "--cpus=2",
            ],
            logger=self._logger(),
        )
        attempted = 0
        try:
            await deployment.start()
            deployment_started = True
            container_name = deployment.container_name
            for item in all_commands:
                attempted += 1
                command_started = perf_counter()
                try:
                    response = await deployment.runtime.execute(
                        Command(
                            command=item["argv"],
                            timeout=item["timeout_seconds"],
                            cwd=item["cwd"],
                            shell=False,
                            check=False,
                        )
                    )
                    result = {
                        **item,
                        "duration_seconds": round(perf_counter() - command_started, 3),
                        "stdout": response.stdout,
                        "stderr": response.stderr,
                        "exit_code": response.exit_code,
                        "transport_error": None,
                    }
                except Exception as exc:  # upstream transports several remote exception types
                    result = {
                        **item,
                        "duration_seconds": round(perf_counter() - command_started, 3),
                        "stdout": "",
                        "stderr": "",
                        "exit_code": None,
                        "transport_error": f"{type(exc).__name__}: {exc}",
                    }
                command_results.append(result)
                if result["transport_error"] is not None or result["exit_code"] != 0:
                    break
        except Exception as exc:  # retain startup failure as evidence in the normal result
            deployment_error = f"{type(exc).__name__}: {exc}"
            container_name = deployment.container_name
        finally:
            try:
                await deployment.stop()
            except Exception as exc:
                stop_error = f"{type(exc).__name__}: {exc}"

        after = WorkspaceSnapshot.capture(host_root)
        changed_paths = before.changed_paths(after)
        not_run = [str(item["id"]) for item in all_commands[attempted:]]
        warnings: list[str] = []
        if deployment_error:
            warnings.append("The disposable deployment did not start; no repository commands ran.")
        if command_results and (
            command_results[-1]["transport_error"] is not None
            or command_results[-1]["exit_code"] != 0
        ):
            warnings.append(
                "Execution stopped at the first failed command; remaining commands were not run."
            )
        if changed_paths:
            warnings.append("The host source snapshot changed during the sandbox probe.")
        container_exists = _container_exists(container_name)
        if stop_error or container_exists:
            warnings.append("Container cleanup could not be confirmed.")

        repository_url = experiment.repository_url.removesuffix(".git")
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider=f"swe-rex-docker/{SWEREX_VERSION}",
            subject=f"{repository_url}@{experiment.commit}",
            request=experiment.model_dump(),
            observations={
                "probe_completed": deployment_started,
                "duration_seconds": round(perf_counter() - started, 3),
                "image": image,
                "deployment": {
                    "started": deployment_started,
                    "error": deployment_error,
                    "container_name": container_name,
                    "security_options": [
                        "cap-drop=ALL",
                        "no-new-privileges",
                        "pids-limit=256",
                        "memory=1g",
                        "cpus=2",
                    ],
                },
                "commands": command_results,
                "not_run_command_ids": not_run,
                "host_workspace": {
                    "before_sha256": before.digest,
                    "after_sha256": after.digest,
                    "unchanged": before.digest == after.digest,
                    "changed_paths": changed_paths,
                    "file_count_before": len(before.files),
                    "file_count_after": len(after.files),
                },
                "cleanup": {
                    "stop_error": stop_error,
                    "container_exists_after_stop": container_exists,
                },
            },
            sources=[
                f"{repository_url}/commit/{experiment.commit}",
                SWEREX_SOURCE,
            ],
            warnings=warnings,
        )

    def probe(self, experiment: SandboxExperiment, host_root: Path) -> ProbeEvidence:
        """Run one experiment from a synchronous CLI and retain observations, not a verdict."""

        try:
            return asyncio.run(self._probe(experiment, host_root.resolve()))
        except subprocess.CalledProcessError as exc:
            detail = (
                exc.stderr.decode(errors="replace")
                if isinstance(exc.stderr, bytes)
                else exc.stderr
            )
            raise BlackridgeError(f"cannot snapshot the host workspace: {detail or exc}") from exc
