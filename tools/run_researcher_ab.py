"""Run replicated, contamination-aware scientific-researcher A/B experiments.

The builder API receives the public contract bytes only.  The Blackridge arm also
receives reviewed registry metadata, which is the experimental treatment.  Hidden
evaluator cases never enter a builder request or workspace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from blackridge.benchmark import BenchmarkComparisonProbe
from blackridge.composition_evidence import EvidenceReference, verify_evidence
from blackridge.models import EvidenceLevel

MODEL = "deepseek-v4-flash"
CANDIDATE_MAX_LINES = 320
CANDIDATE_MAX_CHARACTERS = 20_000
MODEL_CONFIGURATION = {
    "temperature": 0.2,
    "max_tokens": 8192,
    "response_format": "json_object",
    "thinking": "disabled",
    "builder_policy": "one-shot-no-repair",
    "candidate_max_lines": CANDIDATE_MAX_LINES,
    "candidate_max_characters": CANDIDATE_MAX_CHARACTERS,
}
BUILDER_BUDGET_SECONDS = 300
RUNTIME_IMAGE = "sha256:a03f1852c1c437df005ee33b01a26d5e55714c670d3e2273e007c56fd16a5903"
ALLOWED_FILES = {"candidate.py", "README.md", "requirements.lock", "BUILD.json"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_api_key(repo: Path) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    env_file = repo / ".env"
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("DEEPSEEK_API_KEY="):
            return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("DEEPSEEK_API_KEY is unavailable")


def public_material(repo: Path) -> str:
    public = repo / "benchmarks" / "scientific-researcher-v1" / "public"
    sections = []
    for name in (
        "benchmark-spec.md",
        "research-input.schema.json",
        "research-output.schema.json",
    ):
        sections.append(f"--- {name} ---\n{(public / name).read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


def eligible_component(repo: Path) -> tuple[dict[str, Any], Path, dict[str, object]]:
    """Load one hash-bound component and independently enforce its L3 evidence gate."""

    manifest_path = repo / "components" / "grounded_researcher_v1" / "component.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise RuntimeError("component manifest is not an object")
    source_name = manifest.get("source_file")
    if not isinstance(source_name, str) or Path(source_name).name != source_name:
        raise RuntimeError("component source_file is unsafe")
    source_path = manifest_path.parent / source_name
    artifact_hash = manifest.get("artifact_sha256")
    if (
        not isinstance(artifact_hash, str)
        or sha256_bytes(source_path.read_bytes()) != artifact_hash
    ):
        raise RuntimeError("component source SHA-256 does not match its manifest")
    evidence = EvidenceReference.model_validate(manifest.get("evidence"))
    if evidence.level < EvidenceLevel.CONTRACT_TESTED:
        raise RuntimeError("component does not reach the frozen L3 reuse gate")
    public_root = repo / "benchmarks" / "scientific-researcher-v1" / "public"
    contract_bindings = {
        "input_contract_sha256": public_root / "research-input.schema.json",
        "output_contract_sha256": public_root / "research-output.schema.json",
    }
    for field, contract_path in contract_bindings.items():
        if manifest.get(field) != sha256_bytes(contract_path.read_bytes()):
            raise RuntimeError(f"component {field} does not match the public contract")
    reasons, observations = verify_evidence(
        evidence,
        definition_directory=manifest_path.parent,
        mode="calibration",
        subject_type="component",
        subject_revision=str(manifest.get("revision", "")),
        subject_license_spdx=str(manifest.get("license_spdx", "")),
        artifact_sha256=artifact_hash,
    )
    if reasons:
        raise RuntimeError("component evidence gate failed: " + "; ".join(reasons))
    return manifest, source_path, observations


def treatment_material(repo: Path) -> str:
    manifest, source_path, observations = eligible_component(repo)
    retained = {
        "gate": "eligible-at-L3-with-exact-contract-hashes",
        "reviewer": observations.get("reviewer"),
        "review_verdict": observations.get("review_verdict"),
        "review_hash_matches": observations.get("review_hash_matches"),
        "probe_completed": observations.get("probe_completed"),
        "probe_subject_matches": observations.get("probe_subject_matches"),
    }
    return "\n\n".join(
        (
            "--- component.yaml ---\n" + yaml.safe_dump(manifest, sort_keys=False),
            "--- independently-verified-gate.json ---\n"
            + json.dumps(retained, indent=2, sort_keys=True),
            f"--- {source_path.name} (exact eligible source) ---\n"
            + source_path.read_text(encoding="utf-8"),
        )
    )


def builder_prompt(repo: Path, method: str) -> str:
    treatment = ""
    if method == "from-scratch":
        treatment = (
            "Build the implementation from scratch. You have no Blackridge catalog, retained "
            "component evidence, or reuse workflow."
        )
    else:
        treatment = (
            "Use the Blackridge reuse-first policy represented by the registry material below. "
            "Reuse source only when its retained evidence actually reaches the stated gate. "
            "Do not import or claim reuse of provisional, blocked, or merely discovered entries. "
            "The deterministic orchestrator has verified the listed component's source hash, "
            "named L3 review, and exact public input/output contract hashes. It has therefore "
            "preselected grounded-researcher-v1 and will install the exact retained bytes as "
            "candidate.py. Record that selected_component_id and do not retranscribe or replace "
            "candidate.py. Your files may contain only supporting README.md, requirements.lock, "
            "or BUILD.json metadata.\n\nBLACKRIDGE REGISTRY MATERIAL:\n" + treatment_material(repo)
        )
    return f"""You are one isolated software builder in a controlled experiment.

{treatment}

Implement the public task below as a clean Python 3.12 repository. Use only the Python standard
library so the immutable offline evaluator image needs no network installation. The program must
be general: do not hardcode request IDs, document IDs, titles, quotations, expected concepts, or
any guessed hidden case. It must read exactly one JSON request from stdin, write exactly one JSON
artifact to stdout, and put diagnostics only on stderr.

Keep the implementation deliberately small and reviewable: candidate.py must contain at most 320
physical lines and at most 20,000 characters. Do not emit giant stopword, synonym, phrase, or
domain-vocabulary tables. Prefer a compact general algorithm over enumerating possible content.

Return one JSON object, without Markdown fences. In the from-scratch arm use this shape:
{{
  "files": [{{"path": "candidate.py", "content": "..."}}, ...],
  "candidate_command": ["python", "/workspace/candidate.py"],
  "selected_component_id": null,
  "architecture_notes": ["..."],
  "reused_source_lines": 0,
  "reuse_evidence": ["..."]
}}

In the Blackridge arm use selected_component_id "grounded-researcher-v1", omit candidate.py from
files, report 293 reused_source_lines, and identify the retained L3 review and artifact SHA-256.

Allowed output files are candidate.py, README.md, requirements.lock, and BUILD.json. candidate.py
is mandatory when selected_component_id is null and must be omitted when a retained component is
selected. A selected component is allowed only in the Blackridge arm. requirements.lock must state
that there are no third-party runtime dependencies.
Do not include tests derived from guessed hidden inputs. This is a one-shot build with no repair
after evaluator feedback.

PUBLIC TASK AND CONTRACTS:
{public_material(repo)}
"""


def call_builder(
    api_key: str, prompt: str, response_path: Path
) -> tuple[dict[str, Any], dict[str, Any], float]:
    body = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Follow the experiment instructions exactly. Never claim access to files or "
                    "evaluator cases not present in the user message."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": MODEL_CONFIGURATION["temperature"],
        "max_tokens": MODEL_CONFIGURATION["max_tokens"],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "stream": False,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        # The request URL is a fixed HTTPS API endpoint, never caller-controlled.
        with urllib.request.urlopen(  # nosec B310
            request, timeout=BUILDER_BUDGET_SECONDS
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"builder HTTP {exc.code}: {detail[:1000]}") from exc
    duration = time.perf_counter() - started
    envelope = json.loads(raw)
    write_json(response_path, envelope)
    if envelope.get("model") != MODEL:
        raise RuntimeError(f"model resolved to {envelope.get('model')!r}, expected {MODEL!r}")
    content = envelope["choices"][0]["message"]["content"]
    if not content or not content.strip():
        finish_reason = envelope["choices"][0].get("finish_reason")
        raise RuntimeError(f"builder returned empty content (finish_reason={finish_reason!r})")
    bundle = json.loads(content)
    return envelope, bundle, duration


def validate_and_write_bundle(
    repo: Path, workspace: Path, bundle: dict[str, Any], method: str
) -> dict[str, Any] | None:
    if bundle.get("candidate_command") != ["python", "/workspace/candidate.py"]:
        raise RuntimeError("builder returned a non-frozen candidate command")
    files = bundle.get("files")
    if not isinstance(files, list):
        raise RuntimeError("builder returned no files")
    selected_id = bundle.get("selected_component_id")
    if selected_id is not None and not isinstance(selected_id, str):
        raise RuntimeError("builder returned an invalid selected_component_id")
    selected: dict[str, Any] | None = None
    selected_source: Path | None = None
    if method == "blackridge-hybrid":
        selected, selected_source, _ = eligible_component(repo)
    elif selected_id is not None:
        raise RuntimeError("from-scratch builder attempted component reuse")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise RuntimeError("builder returned a malformed file record")
        name = item.get("path")
        if not isinstance(name, str) or name not in ALLOWED_FILES:
            raise RuntimeError(f"builder returned a forbidden path: {name!r}")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or name in seen:
            raise RuntimeError(f"builder returned an unsafe or duplicate path: {name!r}")
        if len(item["content"].encode("utf-8")) > 250_000:
            raise RuntimeError(f"builder file is unexpectedly large: {name}")
        if name == "candidate.py" and (
            len(item["content"]) > CANDIDATE_MAX_CHARACTERS
            or len(item["content"].splitlines()) > CANDIDATE_MAX_LINES
        ):
            raise RuntimeError("builder exceeded the frozen candidate size budget")
        seen.add(name)
        if not (selected is not None and name == "candidate.py"):
            (workspace / name).write_text(item["content"], encoding="utf-8", newline="\n")
    if selected is None and "candidate.py" not in seen:
        raise RuntimeError("builder omitted candidate.py")
    if selected is not None:
        assert selected_source is not None
        shutil.copyfile(selected_source, workspace / "candidate.py")
    return selected


def measure_reuse(
    workspace: Path,
    selected: dict[str, Any] | None,
    builder_claim: object,
) -> dict[str, Any]:
    """Measure reuse from installed bytes; never use builder self-report as telemetry."""

    if not isinstance(builder_claim, int) or isinstance(builder_claim, bool) or builder_claim < 0:
        raise RuntimeError("builder returned invalid reused_source_lines")
    candidate = workspace / "candidate.py"
    candidate_hash = sha256_bytes(candidate.read_bytes())
    total = source_lines(workspace)
    if selected is None:
        reused = 0
        selected_hash = None
        exact_artifact_match = False
    else:
        selected_hash = selected["artifact_sha256"]
        exact_artifact_match = candidate_hash == selected_hash
        if not exact_artifact_match:
            raise RuntimeError("installed candidate does not match the selected component hash")
        reused = len(candidate.read_text(encoding="utf-8").splitlines())
    return {
        "candidate_sha256": candidate_hash,
        "selected_artifact_sha256": selected_hash,
        "exact_artifact_match": exact_artifact_match,
        "total_source_lines": total,
        "generated_source_lines": total - reused,
        "reused_source_lines": reused,
        "builder_claimed_reused_source_lines": builder_claim,
        "builder_claim_matches_measurement": builder_claim == reused,
        "measurement_source": "orchestrator-sha256-and-physical-lines",
    }


def run_checked(argv: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {argv!r}\n{result.stdout}\n{result.stderr}"
        )
    return result


def commit_workspace(workspace: Path) -> str:
    run_checked(["git", "init", "--initial-branch=main"], workspace)
    run_checked(["git", "config", "user.name", "Blackridge Experiment Orchestrator"], workspace)
    run_checked(["git", "config", "user.email", "experiment@blackridge.invalid"], workspace)
    run_checked(["git", "add", "."], workspace)
    run_checked(["git", "commit", "-m", "Freeze builder deliverable"], workspace)
    return run_checked(["git", "rev-parse", "HEAD"], workspace).stdout.strip()


def clean_install_probe(workspace: Path) -> dict[str, Any]:
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "65534:65534",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",  # nosec B108 - container tmpfs, not host temp
        "--env",
        "PYTHONPYCACHEPREFIX=/tmp/pycache",
        "--mount",
        f"type=bind,source={workspace},target=/workspace,readonly",
        "--workdir",
        "/workspace",
        RUNTIME_IMAGE,
        "python",
        "-m",
        "py_compile",
        "candidate.py",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    return {
        "pass": result.returncode == 0,
        "argv": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def source_lines(workspace: Path) -> int:
    return sum(
        len(path.read_text(encoding="utf-8").splitlines())
        for path in workspace.rglob("*.py")
        if ".git" not in path.parts
    )


def run_plan(
    *,
    method: str,
    attempt: int,
    workspace: Path,
    duration: float,
    reuse: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "run_id": f"{method}-attempt-{attempt}",
        "task_id": "scientific-researcher-v1",
        "method": method,
        "run_kind": "experiment",
        "model_identifier": MODEL,
        "model_configuration": MODEL_CONFIGURATION,
        "attempt": attempt,
        "builder_time_budget_seconds": BUILDER_BUDGET_SECONDS,
        "candidate": {
            "backend": "docker",
            "argv": ["python", "/workspace/candidate.py"],
            "cwd": "/workspace",
            "workspace": str(workspace.resolve()),
            "docker_image": RUNTIME_IMAGE,
            "network": "none",
            "read_only_root": True,
            "memory_mib": 256,
            "cpus": 1.0,
            "pids_limit": 64,
            "tmpfs_mib": 64,
            "timeout_seconds_per_case": 60,
            "maximum_output_bytes_per_stream": 1_000_000,
            "environment_allowlist": [],
        },
        "telemetry": {
            "builder_wall_seconds": round(duration, 3),
            "model_cost_usd": None,
            "repair_iterations": 0,
            "generated_source_lines": reuse["generated_source_lines"],
            "reused_source_lines": reuse["reused_source_lines"],
            "clean_install": "pass",
            "measurement_source": "orchestrator",
            "notes": [
                "API usage retained in builder-response.json; provider did not report USD cost.",
                "Reuse is counted only when installed candidate bytes match the retained "
                "artifact SHA-256.",
                "Source lines count nonblank and blank physical Python lines in the deliverable.",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.attempts < 1:
        parser.error("--attempts must be positive")

    repo = Path(__file__).resolve().parents[1]
    definition = repo / "benchmarks" / "scientific-researcher-v1" / "evaluator" / "benchmark.yaml"
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    api_key = read_api_key(repo)
    public_hashes = {
        path.name: sha256_bytes(path.read_bytes())
        for path in (repo / "benchmarks" / "scientific-researcher-v1" / "public").iterdir()
        if path.is_file()
    }
    manifest: dict[str, Any] = {
        "schema_version": "1",
        "started_at": datetime.now(UTC).isoformat(),
        "repository_commit": run_checked(["git", "rev-parse", "HEAD"], repo).stdout.strip(),
        "definition_sha256": sha256_bytes(definition.read_bytes()),
        "public_hashes": public_hashes,
        "model": MODEL,
        "model_configuration": MODEL_CONFIGURATION,
        "runtime_image": RUNTIME_IMAGE,
        "attempts": args.attempts,
        "excluded_pilots": [
            {
                "path": str(output_root.parent / "scientific-researcher-v1-replication-20260827"),
                "reason": (
                    "Invalid orchestration pilot: the API envelope was parsed before being "
                    "retained and default thinking exhausted the usable response channel."
                ),
            },
            {
                "path": str(
                    output_root.parent / "scientific-researcher-v1-replication-20260827-v2"
                ),
                "reason": (
                    "Invalid orchestration pilot: builder JSON reached finish_reason=length "
                    "before a candidate bundle existed; the response was retained."
                ),
            },
        ],
        "runs": [],
    }
    component, component_source, component_gate = eligible_component(repo)
    manifest["eligible_component"] = {
        "component_id": component["component_id"],
        "revision": component["revision"],
        "artifact_sha256": component["artifact_sha256"],
        "source_file": str(component_source.relative_to(repo)),
        "evidence_level": component["evidence"]["level"],
        "input_contract_sha256": component["input_contract_sha256"],
        "output_contract_sha256": component["output_contract_sha256"],
        "reviewer": component_gate.get("reviewer"),
        "review_verdict": component_gate.get("review_verdict"),
        "probe_completed": component_gate.get("probe_completed"),
    }
    write_json(output_root / "manifest.json", manifest)

    for attempt in range(1, args.attempts + 1):
        plans: dict[str, Path] = {}
        for method in ("from-scratch", "blackridge-hybrid"):
            run_root = output_root / f"attempt-{attempt}" / method
            workspace = run_root / "workspace"
            workspace.mkdir(parents=True)
            prompt = builder_prompt(repo, method)
            (run_root / "builder-prompt.txt").write_text(prompt, encoding="utf-8", newline="\n")
            envelope: dict[str, Any] | None = None
            duration: float | None = None
            try:
                envelope, bundle, duration = call_builder(
                    api_key, prompt, run_root / "builder-response.json"
                )
                write_json(run_root / "builder-bundle.json", bundle)
                selected = validate_and_write_bundle(repo, workspace, bundle, method)
                install = clean_install_probe(workspace)
                write_json(run_root / "clean-install.json", install)
                if not install["pass"]:
                    raise RuntimeError(f"clean install failed for {method} attempt {attempt}")
                commit = commit_workspace(workspace)
                reuse = measure_reuse(workspace, selected, bundle.get("reused_source_lines"))
                plan = run_plan(
                    method=method,
                    attempt=attempt,
                    workspace=workspace,
                    duration=duration,
                    reuse=reuse,
                )
                plan_path = run_root / "run-plan.yaml"
                plan_path.write_text(
                    yaml.safe_dump(plan, sort_keys=False),
                    encoding="utf-8",
                    newline="\n",
                )
                plans[method] = plan_path
                run_record = {
                    "attempt": attempt,
                    "method": method,
                    "status": "candidate-ready",
                    "workspace_commit": commit,
                    "builder_prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                    "builder_wall_seconds": round(duration, 3),
                    "generated_source_lines": reuse["generated_source_lines"],
                    "reused_source_lines": reuse["reused_source_lines"],
                    "reuse_measurement": reuse,
                    "api_usage": envelope.get("usage"),
                }
            except Exception as exc:
                run_record = {
                    "attempt": attempt,
                    "method": method,
                    "status": "builder-failed",
                    "builder_prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                    "builder_wall_seconds": (round(duration, 3) if duration is not None else None),
                    "api_usage": envelope.get("usage") if envelope else None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                write_json(run_root / "builder-failure.json", run_record)
            manifest["runs"].append(run_record)
            write_json(output_root / "manifest.json", manifest)

        if set(plans) == {"from-scratch", "blackridge-hybrid"}:
            probe = BenchmarkComparisonProbe().probe(
                definition,
                plans["from-scratch"],
                plans["blackridge-hybrid"],
            )
            write_json(
                output_root / f"attempt-{attempt}" / "comparison-probe.json",
                probe.model_dump(mode="json"),
            )
        else:
            write_json(
                output_root / f"attempt-{attempt}" / "comparison-blocked.json",
                {
                    "attempt": attempt,
                    "status": "blocked-by-builder-failure",
                    "candidate_ready_methods": sorted(plans),
                    "task_success_for_missing_arm": False,
                    "automatic_winner": None,
                },
            )

    manifest["completed_at"] = datetime.now(UTC).isoformat()
    write_json(output_root / "manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
