"""Run deterministic adversarial and metamorphic checks against the grounded researcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator

from blackridge.evidence import ProbeEvidence
from blackridge.io import write_probe
from blackridge.process_boundary import run_bounded


@dataclass(frozen=True)
class AdversarialCase:
    case_id: str
    request: dict[str, Any]
    expected_status: str
    allowed_sources: frozenset[str] = frozenset()
    exact_sources: frozenset[str] | None = None
    maximum_duration_seconds: float = 5.0


def _document(identifier: str, title: str, text: str) -> dict[str, str]:
    return {"document_id": identifier, "title": title, "full_text": text}


def _request(
    request_id: str,
    question: object,
    minimum_sources: object,
    documents: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "request_id": request_id,
        "question": question,
        "minimum_sources": minimum_sources,
        "documents": documents,
    }


def _cluster_documents(
    *, prefix: str, topic: str, count: int, sentence: str
) -> list[dict[str, str]]:
    return [
        _document(
            f"{prefix}-{index:02d}",
            f"{topic.title()} Record {index}",
            (
                f"{topic.title()} evidence record {index} retains {sentence} "
                f"The {topic} audit links this observation to item {index}."
            ),
        )
        for index in range(1, count + 1)
    ]


def _build_cases(repository: Path) -> list[AdversarialCase]:
    fixture_root = repository / "components" / "grounded_researcher_v1" / "fixtures"
    policy = json.loads((fixture_root / "policy-answerable.json").read_text(encoding="utf-8"))
    policy_sources = frozenset(
        {
            "policy-identity",
            "policy-approval",
            "policy-rollback",
            "policy-negative",
            "policy-audit",
        }
    )
    reversed_policy = deepcopy(policy)
    reversed_policy["request_id"] = "policy-order-reversed"
    reversed_policy["documents"] = list(reversed(reversed_policy["documents"]))
    shuffled_policy = deepcopy(policy)
    shuffled_policy["request_id"] = "policy-order-seeded"
    random.Random(20260827).shuffle(shuffled_policy["documents"])

    calibration = _cluster_documents(
        prefix="cal",
        topic="calibration",
        count=5,
        sentence="an immutable measurement and a named instrument owner.",
    )
    kitchen = _cluster_documents(
        prefix="kitchen",
        topic="kitchen",
        count=4,
        sentence="a recipe temperature and a clean preparation surface.",
    )
    bridge = _document(
        "bridge-label",
        "Calibration Instrument Kitchen Labels",
        (
            "Calibration instrument evidence record reproducible calibration instrument. "
            "These words are decorative labels for kitchen storage jars; the note contains "
            "no measurement, test result, or instrument observation."
        ),
    )
    bridge_request = _request(
        "bridge-distractor",
        "Which calibration instrument evidence records make the measurement reproducible?",
        5,
        [*calibration, bridge, *kitchen],
    )

    release = _cluster_documents(
        prefix="release",
        topic="checksum",
        count=3,
        sentence="a verified package digest and a retained release decision.",
    )
    negated = _document(
        "negated-near-duplicate",
        "Checksum Package Release Evidence",
        (
            "This memo does not contain checksum evidence for a package release and must not be "
            "used as a release record. It discusses the font used on empty archive labels."
        ),
    )
    release_request = _request(
        "negated-near-duplicate",
        "Which checksum evidence records support the package release decision?",
        3,
        [release[0], negated, release[1], release[2]],
    )

    partial = _cluster_documents(
        prefix="spectral",
        topic="spectral",
        count=2,
        sentence="a wavelength observation from the calibrated detector.",
    )
    unrelated = _cluster_documents(
        prefix="orchard",
        topic="orchard",
        count=5,
        sentence="a pruning date and seasonal fruit count.",
    )
    partial_request = _request(
        "partial-insufficient",
        "Which spectral observations establish detector calibration?",
        4,
        [*partial, *unrelated],
    )

    variable = _cluster_documents(
        prefix="custody",
        topic="custody",
        count=6,
        sentence="a signed handoff receipt and immutable sample identifier.",
    )
    travel = _cluster_documents(
        prefix="travel",
        topic="travel",
        count=4,
        sentence="a walking route and an estimated arrival time.",
    )
    variable_cases = [
        AdversarialCase(
            case_id=f"variable-minimum-{minimum}",
            request=_request(
                f"variable-minimum-{minimum}",
                "Which custody records prove the sample handoff?",
                minimum,
                [*variable, *travel],
            ),
            expected_status="answered",
            allowed_sources=frozenset(item["document_id"] for item in variable),
        )
        for minimum in (1, 3, 6)
    ]

    large_relevant = _cluster_documents(
        prefix="seismic",
        topic="seismic",
        count=30,
        sentence="a timestamped waveform and calibrated station identity.",
    )
    large_distractors = [
        *_cluster_documents(
            prefix="garden-large",
            topic="garden",
            count=85,
            sentence="a planting date and measured row spacing.",
        ),
        *_cluster_documents(
            prefix="recipe-large",
            topic="recipe",
            count=85,
            sentence="an ingredient quantity and oven temperature.",
        ),
    ]
    random.Random(17).shuffle(large_distractors)
    large_request = _request(
        "large-corpus",
        "Which seismic evidence records retain calibrated waveform observations?",
        20,
        [*large_relevant, *large_distractors],
    )

    invalid_bool = _request(
        "invalid-boolean-minimum",
        "Which checksum record supports this release?",
        True,
        [release[0]],
    )
    invalid_duplicate = _request(
        "invalid-duplicate-identifiers",
        "Which custody records prove the sample handoff?",
        2,
        [variable[0], {**variable[1], "document_id": variable[0]["document_id"]}],
    )

    return [
        AdversarialCase(
            "policy-order-reversed",
            reversed_policy,
            "answered",
            policy_sources,
            policy_sources,
        ),
        AdversarialCase(
            "policy-order-seeded",
            shuffled_policy,
            "answered",
            policy_sources,
            policy_sources,
        ),
        AdversarialCase(
            "bridge-distractor",
            bridge_request,
            "answered",
            frozenset(item["document_id"] for item in calibration),
            frozenset(item["document_id"] for item in calibration),
        ),
        AdversarialCase(
            "negated-near-duplicate",
            release_request,
            "answered",
            frozenset(item["document_id"] for item in release),
            frozenset(item["document_id"] for item in release),
        ),
        AdversarialCase(
            "partial-insufficient",
            partial_request,
            "insufficient-evidence",
        ),
        *variable_cases,
        AdversarialCase(
            "large-corpus",
            large_request,
            "answered",
            frozenset(item["document_id"] for item in large_relevant),
            maximum_duration_seconds=8.0,
        ),
        AdversarialCase(
            "invalid-boolean-minimum",
            invalid_bool,
            "insufficient-evidence",
        ),
        AdversarialCase(
            "invalid-duplicate-identifiers",
            invalid_duplicate,
            "insufficient-evidence",
        ),
    ]


def _invoke(
    *, image: str, component_root: Path, request: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    container = f"blackridge-researcher-adversarial-{uuid4().hex}"
    command = [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--name",
        container,
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
        "/tmp:rw,noexec,nosuid,size=16m",  # nosec B108 - container tmpfs
        "--mount",
        f"type=bind,source={component_root},target=/workspace,readonly",
        "--workdir",
        "/workspace",
        image,
        "python",
        "grounded_researcher.py",
    ]
    result = run_bounded(
        command,
        input_text=json.dumps(request),
        timeout_seconds=15,
        maximum_output_bytes_per_stream=500_000,
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
        ["docker", "rm", "--force", container],
        timeout_seconds=15,
        maximum_output_bytes_per_stream=20_000,
    )
    execution = {
        "command": command,
        "exit_code": result.exit_code,
        "duration_seconds": result.duration_seconds,
        "timed_out": result.timed_out,
        "output_limit_exceeded": result.output_limit_exceeded,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "parse_error": parse_error,
        "cleanup_exit_code": cleanup.exit_code,
    }
    return execution, parsed


def _inspect(
    case: AdversarialCase,
    execution: dict[str, Any],
    output: dict[str, Any] | None,
    input_validator: Draft202012Validator,
    output_validator: Draft202012Validator,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(check: str, matched: bool, observed: object) -> None:
        checks.append({"check": check, "matched": matched, "observed": observed})

    input_errors = sorted(error.message for error in input_validator.iter_errors(case.request))
    expected_input_valid = not case.case_id.startswith("invalid-")
    add("input-validity-classification", (not input_errors) == expected_input_valid, input_errors)
    add("process-exit", execution["exit_code"] == 0, execution["exit_code"])
    add("no-timeout", execution["timed_out"] is False, execution["timed_out"])
    add(
        "duration-budget",
        execution["duration_seconds"] <= case.maximum_duration_seconds,
        execution["duration_seconds"],
    )
    add("output-object", output is not None, output is not None)
    if output is None:
        return checks
    output_errors = sorted(error.message for error in output_validator.iter_errors(output))
    add("public-output-contract", not output_errors, output_errors)
    add(
        "request-identity",
        output.get("request_id") == case.request["request_id"],
        output.get("request_id"),
    )
    add("expected-status", output.get("status") == case.expected_status, output.get("status"))
    claims = output.get("claims") if isinstance(output.get("claims"), list) else []
    sources = output.get("sources") if isinstance(output.get("sources"), list) else []
    if case.expected_status == "insufficient-evidence":
        add(
            "clean-abstention",
            not claims and not sources,
            {"claims": len(claims), "sources": len(sources)},
        )
        return checks

    documents = {
        item["document_id"]: item
        for item in case.request["documents"]
        if isinstance(item, dict) and isinstance(item.get("document_id"), str)
    }
    source_ids = [item.get("document_id") for item in sources if isinstance(item, dict)]
    unique_sources = set(source_ids)
    minimum_sources = case.request["minimum_sources"]
    add("unique-source-identities", len(source_ids) == len(unique_sources), source_ids)
    add("minimum-source-count", len(unique_sources) >= minimum_sources, len(unique_sources))
    add("allowed-source-selection", unique_sources <= case.allowed_sources, sorted(unique_sources))
    if case.exact_sources is not None:
        add("exact-source-selection", unique_sources == case.exact_sources, sorted(unique_sources))
    add(
        "source-title-integrity",
        all(
            item.get("document_id") in documents
            and item.get("title") == documents[item["document_id"]]["title"]
            for item in sources
            if isinstance(item, dict)
        ),
        True,
    )
    citations = [
        citation
        for claim in claims
        if isinstance(claim, dict)
        for citation in claim.get("citations", [])
        if isinstance(citation, dict)
    ]
    add(
        "every-claim-cited",
        bool(claims)
        and all(isinstance(claim, dict) and bool(claim.get("citations")) for claim in claims),
        {"claims": len(claims), "citations": len(citations)},
    )
    grounded = all(
        citation.get("document_id") in documents
        and isinstance(citation.get("quote"), str)
        and citation["quote"] in documents[citation["document_id"]]["full_text"]
        for citation in citations
    )
    add("citation-quotes-grounded", bool(citations) and grounded, grounded)
    add(
        "answer-length",
        len(str(output.get("answer", ""))) <= 2400,
        len(str(output.get("answer", ""))),
    )
    return checks


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository = args.repository.resolve()
    component_root = repository / "components" / "grounded_researcher_v1"
    component = component_root / "grounded_researcher.py"
    public = repository / "benchmarks" / "scientific-researcher-v1" / "public"
    input_contract = public / "research-input.schema.json"
    output_contract = public / "research-output.schema.json"
    input_validator = Draft202012Validator(json.loads(input_contract.read_text(encoding="utf-8")))
    output_validator = Draft202012Validator(json.loads(output_contract.read_text(encoding="utf-8")))

    observations: list[dict[str, Any]] = []
    for case in _build_cases(repository):
        execution, output = _invoke(
            image=args.image,
            component_root=component_root,
            request=case.request,
        )
        checks = _inspect(case, execution, output, input_validator, output_validator)
        observations.append(
            {
                "case_id": case.case_id,
                "request": case.request,
                "expected_status": case.expected_status,
                "allowed_sources": sorted(case.allowed_sources),
                "exact_sources": sorted(case.exact_sources) if case.exact_sources else None,
                "execution": execution,
                "parsed_output": output,
                "checks": checks,
            }
        )

    all_checks = [check for case in observations for check in case["checks"]]
    probe = ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-grounded-researcher-adversarial/1",
        subject=f"grounded-researcher-v1@{_sha256(component)}",
        request={
            "repository": str(repository),
            "repository_revision": run_bounded(
                ["git", "-C", str(repository), "rev-parse", "HEAD"]
            ).stdout.strip(),
            "component_sha256": _sha256(component),
            "input_contract_sha256": _sha256(input_contract),
            "output_contract_sha256": _sha256(output_contract),
            "image": args.image,
            "seed": 20260827,
        },
        observations={
            "probe_completed": True,
            "case_count": len(observations),
            "check_count": len(all_checks),
            "matched_check_count": sum(1 for check in all_checks if check["matched"]),
            "all_checks_matched": all(bool(check["matched"]) for check in all_checks),
            "cases": observations,
        },
        sources=["blackridge://components/grounded-researcher-v1/adversarial-suite"],
        warnings=[
            "This deterministic suite is adversarial remediation evidence, not a blinded holdout.",
            "Passing synthetic lexical attacks does not prove universal semantic relevance.",
        ],
    )
    write_probe(probe, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
