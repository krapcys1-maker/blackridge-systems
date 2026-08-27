"""Disposable repository execution through the upstream SWE-ReX Docker backend."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.process_boundary import run_bounded

SWEREX_VERSION = "1.4.0"
SWEREX_SOURCE = f"https://github.com/SWE-agent/SWE-ReX/tree/v{SWEREX_VERSION}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SandboxCommandSpec(StrictModel):
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


class SandboxExperiment(StrictModel):
    """Pinned source and commands that will run only inside a disposable container."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20)
    repository_url: str = Field(
        pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$"
    )
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    image: str = Field(min_length=3)
    workdir: str = Field(default="/workspace/repository", pattern=r"^/[A-Za-z0-9_./-]+$")
    execution_profile: Literal["calibration", "production"] = "calibration"
    execution_network: Literal["inherit", "none"] = "inherit"
    preparation_commands: list[SandboxCommandSpec] = Field(default_factory=list)
    commands: list[SandboxCommandSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def production_policy_and_unique_ids(self) -> SandboxExperiment:
        ids = [command.id for command in [*self.preparation_commands, *self.commands]]
        if len(ids) != len(set(ids)):
            raise ValueError("sandbox command ids must be unique")
        if self.execution_profile == "production" and self.execution_network != "none":
            raise ValueError("production sandbox experiments require execution_network=none")
        return self

    @field_validator("workdir")
    @classmethod
    def workdir_stays_in_workspace(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.parts[:2] != ("/", "workspace") or any(part in {".", ".."} for part in path.parts):
            raise ValueError("workdir must stay below /workspace without dot segments")
        return value


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Content hashes for all tracked and non-ignored untracked source files."""

    digest: str
    files: dict[str, str]

    @classmethod
    def capture(cls, root: Path) -> WorkspaceSnapshot:
        completed = run_bounded(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            timeout_seconds=30,
        )
        if completed.returncode != 0 or completed.timed_out:
            raise BlackridgeError(f"cannot snapshot workspace: {completed.stderr.strip()}")
        if completed.output_limit_exceeded:
            raise BlackridgeError("workspace file list exceeded the output limit")
        names = [name for name in completed.stdout.split("\0") if name]
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
        completed = run_bounded(["docker", "image", "inspect", image], timeout_seconds=30)
        if completed.returncode != 0 or completed.timed_out:
            raise BlackridgeError(completed.stderr.strip() or "Docker inspect failed")
        if completed.output_limit_exceeded:
            raise BlackridgeError("Docker image metadata exceeded the output limit")
        inspected = json.loads(completed.stdout)[0]
    except (BlackridgeError, json.JSONDecodeError, IndexError) as exc:
        detail = str(exc)
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
        completed = run_bounded(
            ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}"],
            timeout_seconds=15,
        )
    except OSError:
        return None
    if completed.returncode != 0 or completed.timed_out or completed.output_limit_exceeded:
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
                        "paths=['/sys/fs/cgroup/memory.max',"
                        "'/sys/fs/cgroup/memory.swap.max','/sys/fs/cgroup/pids.max',"
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

    @staticmethod
    def _preparation_commands(experiment: SandboxExperiment) -> list[dict[str, object]]:
        return [
            {
                "id": command.id,
                "description": command.description,
                "argv": command.argv,
                "cwd": experiment.workdir,
                "timeout_seconds": command.timeout_seconds,
                "phase": "preparation",
            }
            for command in experiment.preparation_commands
        ]

    @staticmethod
    def _container_networks(container_name: str) -> dict[str, object]:
        completed = run_bounded(
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Networks}}",
                container_name,
            ],
            timeout_seconds=15,
        )
        if completed.returncode != 0 or completed.timed_out:
            raise BlackridgeError(f"cannot inspect container networks: {completed.stderr}")
        if completed.output_limit_exceeded:
            raise BlackridgeError("container network metadata exceeded the output limit")
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise BlackridgeError("Docker returned invalid container network metadata")
        return value

    @classmethod
    def _isolate_execution_network(cls, container_name: str) -> dict[str, object]:
        before = cls._container_networks(container_name)
        commands: list[dict[str, object]] = []
        for network in sorted(before):
            argv = [
                "docker",
                "network",
                "disconnect",
                "--force",
                network,
                container_name,
            ]
            completed = run_bounded(argv, timeout_seconds=15)
            commands.append(
                {
                    "argv": argv,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            )
        after = cls._container_networks(container_name)
        failures = [item for item in commands if item["exit_code"] != 0]
        error = None
        if failures or after:
            error = "container network isolation did not remove every attached network"
        return {
            "requested": "none",
            "applied": error is None,
            "error": error,
            "networks_before": before,
            "disconnect_commands": commands,
            "networks_after": after,
            "workload_executor": "docker-exec-shell-free-non-root",
            "host_environment_forwarded": [],
        }

    @staticmethod
    def _docker_exec_result(container_name: str, item: dict[str, Any]) -> dict[str, object]:
        argv = [
            "docker",
            "exec",
            "--user",
            "65534:65534",
            "--workdir",
            str(item["cwd"]),
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--env",
            "TMPDIR=/tmp",
            container_name,
            "timeout",
            "--verbose",
            "--signal=TERM",
            "--kill-after=1s",
            f"{float(item['timeout_seconds']):g}s",
            *[str(argument) for argument in item["argv"]],
        ]
        started = perf_counter()
        completed = run_bounded(
            argv,
            timeout_seconds=float(item["timeout_seconds"]) + 5,
        )
        duration_seconds = round(perf_counter() - started, 3)
        timed_out = completed.timed_out or (
            completed.returncode in {124, 137}
            and duration_seconds >= float(item["timeout_seconds"]) * 0.9
        )
        transport_error = None
        if completed.timed_out:
            transport_error = f"TimeoutExpired: exceeded {item['timeout_seconds']} seconds"
        elif completed.output_limit_exceeded:
            transport_error = "OutputLimitExceeded: process output exceeded the retained limit"
        return {
            **item,
            "executor": "docker-exec-shell-free",
            "container_argv": argv[argv.index(container_name) + 1 :],
            "user": "65534:65534",
            "environment_names": ["HOME", "PYTHONIOENCODING", "TMPDIR"],
            "timeout_enforcer": "coreutils-timeout-term-then-kill",
            "timed_out": timed_out,
            "output_limit_exceeded": completed.output_limit_exceeded,
            "duration_seconds": duration_seconds,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "stdout_bytes_seen": completed.stdout_bytes_seen,
            "stderr_bytes_seen": completed.stderr_bytes_seen,
            "exit_code": completed.returncode,
            "transport_error": transport_error,
        }

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
        force_remove: dict[str, object] | None = None
        control_commands = self._setup_commands(experiment) + self._preparation_commands(experiment)
        workload_commands = self._experiment_commands(experiment)
        all_commands = control_commands + workload_commands
        execution_boundary: dict[str, object] = {
            "requested": experiment.execution_network,
            "applied": experiment.execution_network == "inherit",
            "error": None,
            "networks_before": None,
            "disconnect_commands": [],
            "networks_after": None,
            "workload_executor": "docker-exec-shell-free-non-root",
            "host_environment_forwarded": [],
        }

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
                "--memory-swap=1g",
                "--cpus=2",
            ],
            logger=self._logger(),
        )
        attempted = 0

        async def execute_through_swerex(item: dict[str, object]) -> dict[str, object]:
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
                return {
                    **item,
                    "executor": "swe-rex",
                    "duration_seconds": round(perf_counter() - command_started, 3),
                    "stdout": response.stdout,
                    "stderr": response.stderr,
                    "exit_code": response.exit_code,
                    "transport_error": None,
                }
            except Exception as exc:  # upstream transports several remote exception types
                return {
                    **item,
                    "executor": "swe-rex",
                    "duration_seconds": round(perf_counter() - command_started, 3),
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "transport_error": f"{type(exc).__name__}: {exc}",
                }

        try:
            await deployment.start()
            deployment_started = True
            container_name = deployment.container_name
            control_failed = False
            for item in control_commands:
                attempted += 1
                result = await execute_through_swerex(item)
                command_results.append(result)
                if result["transport_error"] is not None or result["exit_code"] != 0:
                    control_failed = True
                    break
            if not control_failed and experiment.execution_network == "none":
                try:
                    execution_boundary = self._isolate_execution_network(container_name)
                except Exception as exc:
                    execution_boundary["applied"] = False
                    execution_boundary["error"] = f"{type(exc).__name__}: {exc}"
            if not control_failed and execution_boundary["applied"]:
                for item in workload_commands:
                    attempted += 1
                    result = self._docker_exec_result(container_name, item)
                    command_results.append(result)
                    if result["transport_error"] is not None or result["exit_code"] != 0:
                        break
        except Exception as exc:  # retain startup failure as evidence in the normal result
            deployment_error = f"{type(exc).__name__}: {exc}"
            container_name = deployment.container_name
        finally:
            if deployment_started and experiment.execution_network == "none" and container_name:
                argv = ["docker", "rm", "--force", container_name]
                completed = run_bounded(
                    argv,
                    timeout_seconds=15,
                    maximum_output_bytes_per_stream=65_536,
                )
                force_remove = {
                    "argv": argv,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
            else:
                try:
                    await deployment.stop()
                except Exception as exc:
                    stop_error = f"{type(exc).__name__}: {exc}"

        after = WorkspaceSnapshot.capture(host_root)
        changed_paths = before.changed_paths(after)
        not_run = [str(item["id"]) for item in all_commands[attempted:]]
        warnings: list[str] = []
        if deployment_error:
            warnings.append(
                "The disposable deployment did not complete; inspect retained commands."
            )
        if execution_boundary["error"]:
            warnings.append(
                "Execution network isolation failed closed; workload commands did not run."
            )
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
        if (
            stop_error
            or container_exists is not False
            or (force_remove is not None and force_remove["exit_code"] != 0)
        ):
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
                        "memory-swap=1g",
                        "cpus=2",
                        "workload-user=65534:65534",
                    ],
                },
                "execution_boundary": execution_boundary,
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
                    "force_remove": force_remove,
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

        return asyncio.run(self._probe(experiment, host_root.resolve()))
