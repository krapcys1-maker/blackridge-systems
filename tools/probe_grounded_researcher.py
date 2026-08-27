"""Probe the grounded researcher and a green-exit broken control in Docker."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from blackridge.evidence import ProbeEvidence
from blackridge.io import write_probe
from blackridge.process_boundary import run_bounded

EXPECTED_SOURCES = {
    "policy-answerable.json": {
        "policy-identity",
        "policy-approval",
        "policy-rollback",
        "policy-negative",
        "policy-audit",
    },
    "backup-answerable.json": {
        "backup-snapshot",
        "backup-restore",
        "backup-offsite",
        "backup-review",
    },
    "museum-loan-answerable.json": {
        "loan-identity",
        "loan-approval",
        "loan-condition",
        "loan-custody",
        "loan-environment",
        "loan-packaging",
        "loan-insurance",
        "loan-incident",
        "loan-return",
        "loan-audit",
    },
    "astronomy-insufficient.json": set(),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _invoke(
    *, image: str, component_root: Path, script: str, request: dict[str, Any]
) -> dict[str, Any]:
    container_name = f"blackridge-grounded-researcher-{uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "65534:65534",
        "--pids-limit",
        "32",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "1",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",  # nosec B108 - isolated container tmpfs
        "--mount",
        f"type=bind,source={component_root},target=/workspace,readonly",
        "--workdir",
        "/workspace",
        image,
        "python",
        script,
    ]
    result = run_bounded(
        command,
        input_text=json.dumps(request),
        timeout_seconds=30,
        maximum_output_bytes_per_stream=250_000,
    )
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    try:
        value = json.loads(result.stdout)
        if isinstance(value, dict):
            parsed = value
        else:
            parse_error = "stdout JSON is not an object"
    except json.JSONDecodeError as exc:
        parse_error = str(exc)
    cleanup = run_bounded(
        ["docker", "rm", "--force", container_name],
        timeout_seconds=15,
        maximum_output_bytes_per_stream=20_000,
    )
    return {
        "command": command,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "output_limit_exceeded": result.output_limit_exceeded,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "parsed_output": parsed,
        "parse_error": parse_error,
        "cleanup_exit_code": cleanup.exit_code,
    }


def _checks(
    request: dict[str, Any],
    output: dict[str, Any] | None,
    expected: set[str],
    input_validator: Draft202012Validator,
    output_validator: Draft202012Validator,
) -> list[dict[str, Any]]:
    documents = {item["document_id"]: item for item in request["documents"]}
    checks: list[dict[str, Any]] = []

    def add(name: str, matched: bool, observed: object) -> None:
        checks.append({"check": name, "matched": matched, "observed": observed})

    input_errors = sorted(error.message for error in input_validator.iter_errors(request))
    add("public-input-contract", not input_errors, input_errors)
    add("output-object", output is not None, output is not None)
    if output is None:
        return checks
    output_errors = sorted(error.message for error in output_validator.iter_errors(output))
    add("public-output-contract", not output_errors, output_errors)
    status = output.get("status")
    expected_status = "answered" if expected else "insufficient-evidence"
    add("expected-status", status == expected_status, status)
    sources = output.get("sources")
    source_ids = {item.get("document_id") for item in sources or [] if isinstance(item, dict)}
    add("exact-source-selection", source_ids == expected, sorted(str(x) for x in source_ids))
    claims = output.get("claims")
    citations = [
        citation
        for claim in claims or []
        if isinstance(claim, dict)
        for citation in claim.get("citations", [])
        if isinstance(citation, dict)
    ]
    grounded = all(
        citation.get("document_id") in documents
        and isinstance(citation.get("quote"), str)
        and citation["quote"] in documents[citation["document_id"]]["full_text"]
        for citation in citations
    )
    add(
        "citation-quotes-grounded",
        bool(citations) and grounded if expected else not citations,
        grounded,
    )
    clean_abstention = not claims and not sources
    add(
        "clean-abstention",
        clean_abstention if not expected else True,
        {"claim_count": len(claims or []), "source_count": len(sources or [])},
    )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repository.resolve()
    component_root = repository / "components" / "grounded_researcher_v1"
    fixtures = component_root / "fixtures"
    candidate = component_root / "grounded_researcher.py"
    broken = fixtures / "broken_grounded_researcher.py"
    public_root = repository / "benchmarks" / "scientific-researcher-v1" / "public"
    input_contract = public_root / "research-input.schema.json"
    output_contract = public_root / "research-output.schema.json"
    input_validator = Draft202012Validator(json.loads(input_contract.read_text(encoding="utf-8")))
    output_validator = Draft202012Validator(json.loads(output_contract.read_text(encoding="utf-8")))

    observations: list[dict[str, Any]] = []
    for fixture_name, expected in EXPECTED_SOURCES.items():
        request_path = fixtures / fixture_name
        request = json.loads(request_path.read_text(encoding="utf-8"))
        candidate_run = _invoke(
            image=args.image,
            component_root=component_root,
            script="grounded_researcher.py",
            request=request,
        )
        broken_run = _invoke(
            image=args.image,
            component_root=component_root,
            script="fixtures/broken_grounded_researcher.py",
            request=request,
        )
        candidate_checks = _checks(
            request,
            candidate_run["parsed_output"],
            expected,
            input_validator,
            output_validator,
        )
        broken_checks = _checks(
            request,
            broken_run["parsed_output"],
            expected,
            input_validator,
            output_validator,
        )
        observations.append(
            {
                "fixture": fixture_name,
                "fixture_sha256": _sha256(request_path),
                "expected_source_ids": sorted(expected),
                "candidate": {**candidate_run, "checks": candidate_checks},
                "broken_control": {**broken_run, "checks": broken_checks},
            }
        )

    candidate_checks = [
        check for observation in observations for check in observation["candidate"]["checks"]
    ]
    broken_checks = [
        check for observation in observations for check in observation["broken_control"]["checks"]
    ]
    probe = ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-grounded-researcher-probe/1",
        subject=f"grounded-researcher-v1@{_sha256(candidate)}",
        request={
            "repository": str(repository),
            "repository_revision": run_bounded(
                ["git", "-C", str(repository), "rev-parse", "HEAD"]
            ).stdout.strip(),
            "component_file": str(candidate),
            "component_sha256": _sha256(candidate),
            "broken_control_file": str(broken),
            "broken_control_sha256": _sha256(broken),
            "input_contract_file": str(input_contract),
            "input_contract_sha256": _sha256(input_contract),
            "output_contract_file": str(output_contract),
            "output_contract_sha256": _sha256(output_contract),
            "image": args.image,
        },
        observations={
            "probe_completed": True,
            "case_count": len(observations),
            "candidate_all_checks_matched": all(
                bool(check["matched"]) for check in candidate_checks
            ),
            "broken_all_processes_exited_zero": all(
                observation["broken_control"]["exit_code"] == 0 for observation in observations
            ),
            "broken_detected_check_count": sum(
                1 for check in broken_checks if not check["matched"]
            ),
            "cases": observations,
        },
        sources=["blackridge://components/grounded-researcher-v1"],
        warnings=[
            "The probe covers three independent answerable domains and one unrelated corpus; "
            "it is not a universal relevance claim.",
            "A named review is required before evidence promotion.",
        ],
    )
    write_probe(probe, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
