"""Run the installed-wheel system path and fail-closed controls against Docker."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


class Harness:
    def __init__(self, *, repository: Path, work_directory: Path, blackridge: Path) -> None:
        self.repository = repository.resolve()
        self.work_directory = work_directory.resolve()
        self.blackridge = blackridge.resolve()
        self.logs = self.work_directory / "command-logs"
        self.logs.mkdir(parents=True)

    def command(
        self,
        name: str,
        argv: list[str | Path],
        *,
        expected_exit_codes: set[int] | None = None,
        timeout_seconds: float = 180,
    ) -> CommandResult:
        expected_exit_codes = {0} if expected_exit_codes is None else expected_exit_codes
        normalized = [str(value) for value in argv]
        started = perf_counter()
        completed = subprocess.run(
            normalized,
            cwd=self.repository,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        result = CommandResult(
            argv=normalized,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=round(perf_counter() - started, 3),
        )
        (self.logs / f"{name}.json").write_text(
            json.dumps(result.__dict__, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"[{name}] exit={result.returncode} duration={result.duration_seconds}s")
        if result.stdout.strip():
            print(result.stdout.rstrip())
        if result.stderr.strip():
            print(result.stderr.rstrip(), file=sys.stderr)
        if result.returncode not in expected_exit_codes:
            expected = ", ".join(str(code) for code in sorted(expected_exit_codes))
            raise AssertionError(
                f"{name} returned {result.returncode}; expected one of: {expected}"
            )
        return result

    def cli(
        self,
        name: str,
        arguments: list[str | Path],
        *,
        expected_exit_codes: set[int] | None = None,
    ) -> CommandResult:
        return self.command(
            name,
            [self.blackridge, *arguments],
            expected_exit_codes=expected_exit_codes,
        )


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected a JSON object in {path}")
    return value


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _generate_bundle(
    harness: Harness,
    *,
    name: str,
    definition: Path,
    destination: Path,
) -> tuple[Path, str]:
    plan = destination / "plan.yaml"
    bundle = destination / "bundle"
    destination.mkdir(parents=True)
    harness.cli(f"{name}-solve", ["compose-solve", definition, "--output", plan])
    harness.cli(f"{name}-generate", ["compose-generate", definition, plan, bundle])
    provenance = bundle / "provenance.json"
    _expect(provenance.is_file(), f"{name} did not generate provenance.json")
    return bundle, _sha256_file(provenance)


def _docker_container_ids(harness: Harness, name: str, docker: str) -> set[str]:
    result = harness.command(name, [docker, "ps", "-aq"])
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _run_system(harness: Harness, *, image: str, docker: str) -> dict[str, object]:
    repository = harness.repository
    work = harness.work_directory
    examples = repository / "examples"
    input_file = examples / "composition-input.json"
    source_status_before = harness.command(
        "source-status-before",
        ["git", "-C", repository, "status", "--porcelain=v1", "--untracked-files=all"],
    )
    source_clean_before = harness.command(
        "source-clean-before", ["git", "-C", repository, "clean", "-ndx"]
    )
    _expect(not source_status_before.stdout.strip(), "source checkout is dirty before E2E")
    _expect(
        not source_clean_before.stdout.strip(),
        "source checkout has ignored residue before E2E",
    )
    containers_before = _docker_container_ids(harness, "containers-before", docker)

    positive = work / "positive"
    bundle, provenance_sha256 = _generate_bundle(
        harness,
        name="positive",
        definition=examples / "composition-linear-calibration.yaml",
        destination=positive,
    )
    host_output = positive / "host-output.json"
    host_evidence = positive / "host-evidence.json"
    harness.cli(
        "positive-host",
        [
            "compose-run",
            bundle,
            input_file,
            "--provenance-sha256",
            provenance_sha256,
            "--output",
            host_output,
            "--evidence",
            host_evidence,
        ],
    )
    sandbox_output = positive / "sandbox-output.json"
    sandbox_evidence = positive / "sandbox-evidence.json"
    harness.cli(
        "positive-sandbox",
        [
            "compose-run-sandbox",
            bundle,
            input_file,
            "--provenance-sha256",
            provenance_sha256,
            "--image",
            image,
            "--output",
            sandbox_output,
            "--evidence",
            sandbox_evidence,
        ],
    )
    host_probe = _load_json(host_evidence)
    sandbox_probe = _load_json(sandbox_evidence)
    host_observations = host_probe["observations"]
    sandbox_observations = sandbox_probe["observations"]
    _expect(isinstance(host_observations, dict), "host evidence has invalid observations")
    _expect(isinstance(sandbox_observations, dict), "sandbox evidence has invalid observations")
    sandbox = sandbox_observations["sandbox"]
    _expect(isinstance(sandbox, dict), "sandbox evidence has no sandbox boundary")
    preflight = sandbox["preflight"]
    cleanup = sandbox["cleanup"]
    _expect(isinstance(preflight, dict), "sandbox preflight evidence is invalid")
    _expect(isinstance(cleanup, dict), "sandbox cleanup evidence is invalid")
    checks = preflight["checks"]
    _expect(isinstance(checks, dict), "sandbox preflight checks are invalid")
    _expect(host_observations["all_steps_completed"] is True, "host path did not complete")
    _expect(sandbox_observations["all_steps_completed"] is True, "sandbox path did not complete")
    _expect(
        len(checks) == 13 and all(value is True for value in checks.values()),
        "sandbox preflight did not pass 13/13 checks",
    )
    _expect(cleanup["container_exists_after"] is False, "positive sandbox container remained")
    _expect(
        _sha256_file(host_output) == _sha256_file(sandbox_output),
        "host and sandbox outputs differ",
    )

    controls = work / "controls"
    controls.mkdir()
    wrong_output = controls / "wrong-provenance-output.json"
    wrong_evidence = controls / "wrong-provenance-evidence.json"
    harness.cli(
        "wrong-provenance",
        [
            "compose-run",
            bundle,
            input_file,
            "--provenance-sha256",
            "0" * 64,
            "--output",
            wrong_output,
            "--evidence",
            wrong_evidence,
        ],
        expected_exit_codes={2},
    )
    wrong_probe = _load_json(wrong_evidence)
    wrong_observations = wrong_probe["observations"]
    _expect(isinstance(wrong_observations, dict), "wrong-root evidence is invalid")
    _expect(wrong_observations["probe_completed"] is False, "wrong trust root completed")
    _expect(not wrong_output.exists(), "wrong trust root published output")

    broken = controls / "broken-contract"
    broken_bundle, broken_hash = _generate_bundle(
        harness,
        name="broken-contract",
        definition=examples / "composition-linear-broken-output.yaml",
        destination=broken,
    )
    broken_output = broken / "output.json"
    broken_evidence = broken / "evidence.json"
    harness.cli(
        "broken-contract-run",
        [
            "compose-run",
            broken_bundle,
            input_file,
            "--provenance-sha256",
            broken_hash,
            "--output",
            broken_output,
            "--evidence",
            broken_evidence,
        ],
        expected_exit_codes={1},
    )
    broken_probe = _load_json(broken_evidence)
    broken_observations = broken_probe["observations"]
    _expect(isinstance(broken_observations, dict), "broken-contract evidence is invalid")
    broken_steps = broken_observations["steps"]
    _expect(isinstance(broken_steps, list) and broken_steps, "broken-contract steps missing")
    broken_last = broken_steps[-1]
    _expect(isinstance(broken_last, dict), "broken-contract final step is invalid")
    broken_process = broken_last["process"]
    _expect(isinstance(broken_process, dict), "broken-contract process evidence is invalid")
    _expect(broken_process["exit_code"] == 0, "broken fixture did not retain green exit")
    _expect(broken_last["output_contract_valid"] is False, "broken output passed contract")
    _expect(not broken_output.exists(), "broken contract published output")

    timeout = controls / "timeout"
    timeout_bundle, timeout_hash = _generate_bundle(
        harness,
        name="timeout",
        definition=examples / "composition-timeout-calibration.yaml",
        destination=timeout,
    )
    timeout_output = timeout / "output.json"
    timeout_evidence = timeout / "evidence.json"
    harness.cli(
        "timeout-run",
        [
            "compose-run-sandbox",
            timeout_bundle,
            examples / "composition-timeout-input.json",
            "--provenance-sha256",
            timeout_hash,
            "--image",
            image,
            "--output",
            timeout_output,
            "--evidence",
            timeout_evidence,
        ],
        expected_exit_codes={1},
    )
    timeout_probe = _load_json(timeout_evidence)
    timeout_observations = timeout_probe["observations"]
    _expect(isinstance(timeout_observations, dict), "timeout evidence is invalid")
    timeout_steps = timeout_observations["steps"]
    _expect(isinstance(timeout_steps, list) and timeout_steps, "timeout steps missing")
    timeout_step = timeout_steps[0]
    _expect(isinstance(timeout_step, dict), "timeout step is invalid")
    timeout_process = timeout_step["process"]
    _expect(isinstance(timeout_process, dict), "timeout process evidence is invalid")
    _expect(timeout_process["timed_out"] is True, "hostile component did not time out")
    _expect(timeout_process["exit_code"] == 137, "timeout did not escalate to exit 137")
    timeout_sandbox = timeout_observations["sandbox"]
    _expect(isinstance(timeout_sandbox, dict), "timeout sandbox evidence is invalid")
    timeout_cleanup = timeout_sandbox["cleanup"]
    _expect(isinstance(timeout_cleanup, dict), "timeout cleanup evidence is invalid")
    _expect(timeout_cleanup["container_exists_after"] is False, "timeout container remained")
    _expect(not timeout_output.exists(), "timeout published output")

    tamper_examples = controls / "tamper-examples"
    shutil.copytree(examples, tamper_examples)
    tamper = controls / "component-tamper"
    tamper_bundle, tamper_hash = _generate_bundle(
        harness,
        name="component-tamper",
        definition=tamper_examples / "composition-linear-calibration.yaml",
        destination=tamper,
    )
    sink = tamper_examples / "fixtures" / "report_sink.py"
    with sink.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write("\n# Deliberate post-generation E2E tamper.\n")
    tamper_output = tamper / "output.json"
    tamper_evidence = tamper / "evidence.json"
    harness.cli(
        "component-tamper-run",
        [
            "compose-run",
            tamper_bundle,
            tamper_examples / "composition-input.json",
            "--provenance-sha256",
            tamper_hash,
            "--output",
            tamper_output,
            "--evidence",
            tamper_evidence,
        ],
        expected_exit_codes={1},
    )
    tamper_probe = _load_json(tamper_evidence)
    tamper_observations = tamper_probe["observations"]
    _expect(isinstance(tamper_observations, dict), "tamper evidence is invalid")
    tamper_steps = tamper_observations["steps"]
    _expect(isinstance(tamper_steps, list), "tamper steps are invalid")
    statuses = [step.get("status") for step in tamper_steps if isinstance(step, dict)]
    processes = [step for step in tamper_steps if isinstance(step, dict) and "process" in step]
    _expect(statuses == ["skipped", "skipped", "failed"], "tamper preflight statuses changed")
    _expect(not processes, "a component executed after preflight found a tampered artifact")
    _expect(not tamper_output.exists(), "tampered component published output")

    production_plan = controls / "production-unreviewed-plan.yaml"
    production_definition = examples / "composition-production-unreviewed.yaml"
    harness.cli(
        "production-unreviewed-solve",
        ["compose-solve", production_definition, "--output", production_plan],
        expected_exit_codes={1},
    )
    production_text = production_plan.read_text(encoding="utf-8")
    _expect("complete: false" in production_text, "unreviewed production plan completed")
    _expect(
        "claimed evidence level has no named manual review" in production_text,
        "unreviewed production reason was not retained",
    )
    production_bundle = controls / "production-unreviewed-bundle"
    harness.cli(
        "production-unreviewed-generate",
        ["compose-generate", production_definition, production_plan, production_bundle],
        expected_exit_codes={2},
    )
    _expect(not production_bundle.exists(), "unreviewed production bundle was generated")

    containers_after = _docker_container_ids(harness, "containers-after", docker)
    new_containers = sorted(containers_after - containers_before)
    _expect(not new_containers, f"E2E left Docker containers behind: {new_containers}")
    source_status_after = harness.command(
        "source-status-after",
        ["git", "-C", repository, "status", "--porcelain=v1", "--untracked-files=all"],
    )
    source_clean_after = harness.command(
        "source-clean-after", ["git", "-C", repository, "clean", "-ndx"]
    )
    _expect(not source_status_after.stdout.strip(), "source checkout changed during E2E")
    _expect(not source_clean_after.stdout.strip(), "E2E left ignored residue in source checkout")
    return {
        "commit": harness.command(
            "tested-commit", ["git", "-C", repository, "rev-parse", "HEAD"]
        ).stdout.strip(),
        "positive": {
            "output_sha256": _sha256_file(host_output),
            "step_count": len(host_observations["steps"]),
            "sandbox_preflight_checks": len(checks),
        },
        "controls": {
            "wrong_provenance_rejected": True,
            "green_exit_invalid_contract_rejected": True,
            "timeout_exit_code": timeout_process["exit_code"],
            "tamper_processes_executed": len(processes),
            "unreviewed_production_rejected": True,
        },
        "new_containers_after": new_containers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--work-directory", type=Path, required=True)
    parser.add_argument("--blackridge", type=Path, required=True)
    parser.add_argument("--image", default="blackridge/swerex-runtime:1.4.0")
    args = parser.parse_args()

    repository = args.repository.resolve()
    work_directory = args.work_directory.resolve()
    blackridge = args.blackridge.resolve()
    if not (repository / ".git").exists():
        raise SystemExit(f"repository is not a Git checkout: {repository}")
    if not blackridge.is_file():
        raise SystemExit(f"installed blackridge entrypoint does not exist: {blackridge}")
    if work_directory.exists() and any(work_directory.iterdir()):
        raise SystemExit(f"work directory must be empty: {work_directory}")
    work_directory.mkdir(parents=True, exist_ok=True)
    docker = shutil.which("docker")
    if docker is None:
        raise SystemExit("docker executable is required for the system E2E")

    summary: dict[str, object] = {
        "schema_version": "1",
        "observed_at": datetime.now(UTC).isoformat(),
        "probe_completed": False,
        "image": args.image,
    }
    summary_file = work_directory / "system-e2e-summary.json"
    harness = Harness(
        repository=repository,
        work_directory=work_directory,
        blackridge=blackridge,
    )
    try:
        summary.update(_run_system(harness, image=args.image, docker=docker))
        summary["probe_completed"] = True
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        raise
    finally:
        summary_file.write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    print(f"System E2E evidence written to {summary_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
