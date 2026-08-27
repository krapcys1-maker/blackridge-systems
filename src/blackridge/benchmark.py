"""Frozen benchmark contracts and artifact-first candidate evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Literal
from uuid import uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence
from blackridge.formats import load_yaml
from blackridge.process_boundary import run_bounded

BENCHMARK_SOURCE = (
    "https://github.com/krapcys1-maker/blackridge-systems/"
    "tree/main/benchmarks/scientific-researcher-v1"
)


class ResearchDocument(BaseModel):
    """One document supplied to a benchmark candidate."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=3)
    full_text: str = Field(min_length=20)


class ResearchRequest(BaseModel):
    """Public input contract for the first scientific-researcher benchmark."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    question: str = Field(min_length=10)
    minimum_sources: StrictInt = Field(ge=1)
    documents: list[ResearchDocument] = Field(min_length=1)

    @field_validator("documents")
    @classmethod
    def unique_document_ids(cls, value: list[ResearchDocument]) -> list[ResearchDocument]:
        identifiers = [item.document_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("document IDs must be unique")
        return value


class ResearchCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    quote: str = Field(min_length=1)


class ResearchClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    citations: list[ResearchCitation] = Field(default_factory=list)


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    title: str = Field(min_length=1)


class ResearchOutput(BaseModel):
    """Strict output artifact; command completion is deliberately insufficient."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    request_id: str
    status: Literal["answered", "insufficient-evidence"]
    answer: str = Field(min_length=1)
    claims: list[ResearchClaim]
    sources: list[ResearchSource]


class CaseExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_status: Literal["answered", "insufficient-evidence"]
    minimum_unique_sources: int = Field(default=0, ge=0)
    maximum_unique_sources: int | None = Field(default=None, ge=0)
    maximum_answer_characters: int | None = Field(default=None, ge=1)
    required_concept_groups: list[list[str]] = Field(default_factory=list)
    required_document_ids: list[str] = Field(default_factory=list)
    allowed_document_ids: list[str] = Field(default_factory=list)
    minimum_required_document_coverage: int = Field(default=0, ge=0)

    @field_validator("required_concept_groups")
    @classmethod
    def non_empty_concept_groups(cls, value: list[list[str]]) -> list[list[str]]:
        if any(not group or any(not term.strip() for term in group) for group in value):
            raise ValueError("concept groups require non-empty alternative terms")
        return value

    @model_validator(mode="after")
    def consistent_bounds(self) -> CaseExpectation:
        if (
            self.maximum_unique_sources is not None
            and self.maximum_unique_sources < self.minimum_unique_sources
        ):
            raise ValueError("maximum unique sources cannot be below the minimum")
        if self.minimum_required_document_coverage > len(self.required_document_ids):
            raise ValueError("required document coverage exceeds the required support set")
        if self.allowed_document_ids and not set(self.required_document_ids).issubset(
            self.allowed_document_ids
        ):
            raise ValueError("required documents must belong to the allowed support set")
        return self


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    category: Literal["functional", "robustness", "change"]
    critical: bool = True
    request_file: str
    expectation: CaseExpectation


class BenchmarkDefinition(BaseModel):
    """Frozen evaluator inputs that must exist before either builder starts."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    task_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^[0-9]+(?:\.[0-9]+)*$")
    description: str = Field(min_length=20)
    public_spec_file: str
    input_contract_file: str
    output_contract_file: str
    cases: list[BenchmarkCase] = Field(min_length=1)

    @field_validator("cases")
    @classmethod
    def unique_case_ids(cls, value: list[BenchmarkCase]) -> list[BenchmarkCase]:
        identifiers = [item.id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("benchmark case IDs must be unique")
        return value


class BenchmarkMethod(StrEnum):
    FROM_SCRATCH = "from-scratch"
    REUSE_ONLY = "reuse-only"
    BLACKRIDGE_HYBRID = "blackridge-hybrid"
    CALIBRATION_REFERENCE = "calibration-reference"
    CALIBRATION_BROKEN = "calibration-broken"


class CandidateInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["host", "docker"] = "host"
    argv: list[str] = Field(min_length=1)
    cwd: str = "."
    workspace: str | None = None
    docker_image: str | None = None
    network: Literal["none"] = "none"
    read_only_root: bool = True
    memory_mib: int = Field(default=256, ge=64, le=4096)
    cpus: float = Field(default=1.0, gt=0, le=8)
    pids_limit: int = Field(default=64, ge=16, le=1024)
    tmpfs_mib: int = Field(default=64, ge=16, le=1024)
    timeout_seconds_per_case: float = Field(default=60, gt=0, le=3600)
    maximum_output_bytes_per_stream: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    environment_allowlist: list[str] = Field(default_factory=list)

    @field_validator("argv")
    @classmethod
    def non_empty_argv(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("candidate argv entries must not be empty")
        return value

    @field_validator("environment_allowlist")
    @classmethod
    def valid_environment_names(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("candidate environment allowlist entries must be unique")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) for item in value):
            raise ValueError("candidate environment allowlist contains an invalid name")
        return value

    @model_validator(mode="after")
    def valid_backend_configuration(self) -> CandidateInvocation:
        if self.backend == "host":
            if self.workspace is not None or self.docker_image is not None:
                raise ValueError("host candidates cannot declare Docker workspace or image")
            return self
        if self.workspace is None:
            raise ValueError("docker candidates require a workspace")
        if self.docker_image is None:
            raise ValueError("docker candidates require an immutable image")
        immutable_image = bool(
            re.fullmatch(r"sha256:[a-f0-9]{64}", self.docker_image)
            or re.search(r"@sha256:[a-f0-9]{64}$", self.docker_image)
        )
        if not immutable_image:
            raise ValueError("docker image must be an immutable image ID or repository digest")
        if not self.read_only_root:
            raise ValueError("docker benchmark root filesystem must be read-only")
        return self


class BenchmarkTelemetry(BaseModel):
    """Externally measured or explicitly reported raw metrics; never silently inferred."""

    model_config = ConfigDict(extra="forbid")

    builder_wall_seconds: float | None = Field(default=None, ge=0)
    model_cost_usd: float | None = Field(default=None, ge=0)
    repair_iterations: int | None = Field(default=None, ge=0)
    generated_source_lines: int | None = Field(default=None, ge=0)
    reused_source_lines: int | None = Field(default=None, ge=0)
    clean_install: Literal["pass", "fail", "not-run"] = "not-run"
    measurement_source: Literal["orchestrator", "builder-reported", "unavailable"]
    notes: list[str] = Field(default_factory=list)


class BenchmarkRunPlan(BaseModel):
    """Controls that must be equal across compared A/B runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    run_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    task_id: str
    method: BenchmarkMethod
    run_kind: Literal["harness-control", "experiment"] = "harness-control"
    model_identifier: str = Field(min_length=2)
    model_configuration: dict[str, object]
    attempt: int = Field(ge=1)
    builder_time_budget_seconds: int = Field(gt=0)
    candidate: CandidateInvocation
    telemetry: BenchmarkTelemetry


class CheckObservation(BaseModel):
    """One objective comparison; it is not a human approval verdict."""

    check_id: str
    critical: bool
    expected: object
    observed: object
    matched: bool
    detail: str


@dataclass(frozen=True)
class _CandidateCommand:
    argv: list[str]
    cwd: Path
    cleanup_argv: list[str] | None = None


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _snapshot_file(path: Path, *, content: bytes | None = None) -> dict[str, object]:
    raw = path.read_bytes() if content is None else content
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_bytes(raw),
        "size_bytes": len(raw),
    }


def _snapshot_changes(snapshot: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for name, before in snapshot.items():
        path = Path(str(before["path"]))
        try:
            after = _snapshot_file(path)
        except OSError as exc:
            changes.append(
                {
                    "input": name,
                    "before": before,
                    "after": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        if after["sha256"] != before["sha256"]:
            changes.append({"input": name, "before": before, "after": after})
    return changes


def load_benchmark_definition(path: Path) -> BenchmarkDefinition:
    return BenchmarkDefinition.model_validate(load_yaml(path))


def load_benchmark_run_plan(path: Path) -> BenchmarkRunPlan:
    return BenchmarkRunPlan.model_validate(load_yaml(path))


class BenchmarkEvaluator:
    """Execute a candidate protocol and inspect its artifacts case by case."""

    safe_environment_names = ("HOME", "PATH", "SYSTEMROOT", "TEMP", "TMP")

    def _resolve_definition_file(self, definition_path: Path, value: str) -> Path:
        benchmark_root = definition_path.parent.parent.resolve()
        resolved = (definition_path.parent / value).resolve()
        if not resolved.is_relative_to(benchmark_root):
            raise BlackridgeError(f"benchmark file resolves outside its root: {value}")
        if not resolved.is_file():
            raise BlackridgeError(f"benchmark file does not exist: {resolved}")
        return resolved

    def _candidate_command(
        self, definition_path: Path, run_plan_path: Path, run: BenchmarkRunPlan
    ) -> _CandidateCommand:
        benchmark_root = definition_path.parent.parent.resolve()
        run_dir = run_plan_path.parent.resolve()

        def expand_host(value: str) -> str:
            return (
                value.replace("{python}", sys.executable)
                .replace("{benchmark_root}", str(benchmark_root))
                .replace("{run_dir}", str(run_dir))
            )

        if run.candidate.backend == "host":
            argv = [expand_host(value) for value in run.candidate.argv]
            cwd = Path(expand_host(run.candidate.cwd))
            if not cwd.is_absolute():
                cwd = (run_dir / cwd).resolve()
            if not cwd.is_dir():
                raise BlackridgeError(f"candidate working directory does not exist: {cwd}")
            return _CandidateCommand(argv=argv, cwd=cwd)

        workspace_value = run.candidate.workspace
        image = run.candidate.docker_image
        if workspace_value is None or image is None:  # Guard the validated model boundary.
            raise BlackridgeError("docker candidate configuration is incomplete")
        workspace = Path(expand_host(workspace_value))
        if not workspace.is_absolute():
            workspace = (run_dir / workspace).resolve()
        if not workspace.is_dir():
            raise BlackridgeError(f"candidate workspace does not exist: {workspace}")
        if benchmark_root.is_relative_to(workspace):
            raise BlackridgeError("candidate workspace cannot contain the private benchmark root")
        symlinks = [path for path in workspace.rglob("*") if path.is_symlink()]
        if symlinks:
            raise BlackridgeError(f"candidate workspace contains a symbolic link: {symlinks[0]}")
        if not run.candidate.cwd.startswith("/") or ".." in Path(run.candidate.cwd).parts:
            raise BlackridgeError("docker candidate cwd must be an absolute contained path")
        forbidden_placeholders = ("{benchmark_root}", "{run_dir}")
        if any(
            placeholder in value
            for value in run.candidate.argv
            for placeholder in forbidden_placeholders
        ):
            raise BlackridgeError("docker candidate argv cannot expose host benchmark or run paths")
        candidate_argv = [
            value.replace("{python}", "python").replace("{workspace}", "/workspace")
            for value in run.candidate.argv
        ]
        container_name = f"blackridge-benchmark-{uuid4().hex}"
        docker_argv = [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--name",
            container_name,
            "--network",
            run.candidate.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "65534:65534",
            "--pids-limit",
            str(run.candidate.pids_limit),
            "--memory",
            f"{run.candidate.memory_mib}m",
            "--memory-swap",
            f"{run.candidate.memory_mib}m",
            "--cpus",
            str(run.candidate.cpus),
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={run.candidate.tmpfs_mib}m",  # nosec B108
            "--mount",
            f"type=bind,source={workspace},target=/workspace,readonly",
            "--workdir",
            run.candidate.cwd,
        ]
        for name in sorted(run.candidate.environment_allowlist):
            docker_argv.extend(["--env", name])
        docker_argv.extend([image, *candidate_argv])
        return _CandidateCommand(
            argv=docker_argv,
            cwd=run_dir,
            cleanup_argv=["docker", "rm", "--force", container_name],
        )

    def _environment(self, run: BenchmarkRunPlan) -> dict[str, str]:
        names = set(self.safe_environment_names) | set(run.candidate.environment_allowlist)
        return {name: os.environ[name] for name in sorted(names) if name in os.environ}

    @staticmethod
    def _check(
        checks: list[CheckObservation],
        *,
        check_id: str,
        critical: bool,
        expected: object,
        observed: object,
        matched: bool,
        detail: str,
    ) -> None:
        checks.append(
            CheckObservation(
                check_id=check_id,
                critical=critical,
                expected=expected,
                observed=observed,
                matched=matched,
                detail=detail,
            )
        )

    def _inspect_output(
        self,
        case: BenchmarkCase,
        request: ResearchRequest,
        output: ResearchOutput,
        checks: list[CheckObservation],
    ) -> None:
        critical = case.critical
        expectation = case.expectation
        documents = {item.document_id: item for item in request.documents}
        self._check(
            checks,
            check_id="request-identity",
            critical=critical,
            expected=request.request_id,
            observed=output.request_id,
            matched=output.request_id == request.request_id,
            detail="Output must belong to the exact benchmark request.",
        )
        self._check(
            checks,
            check_id="expected-status",
            critical=critical,
            expected=expectation.expected_status,
            observed=output.status,
            matched=output.status == expectation.expected_status,
            detail="The candidate must answer or abstain as required by available evidence.",
        )

        source_ids = [item.document_id for item in output.sources]
        unique_source_ids = set(source_ids)
        sources_valid = len(source_ids) == len(unique_source_ids) and all(
            item.document_id in documents and item.title == documents[item.document_id].title
            for item in output.sources
        )
        self._check(
            checks,
            check_id="source-identities",
            critical=critical,
            expected="unique document IDs with exact corpus titles",
            observed={"ids": source_ids, "valid": sources_valid},
            matched=sources_valid,
            detail="Fabricated, duplicate, or title-mismatched bibliography entries are rejected.",
        )

        citations = [citation for claim in output.claims for citation in claim.citations]
        citation_ids = {citation.document_id for citation in citations}
        citations_valid = bool(citations) and all(
            citation.document_id in documents
            and citation.quote.strip() in documents[citation.document_id].full_text
            for citation in citations
        )
        every_claim_cited = bool(output.claims) and all(
            bool(claim.citations) for claim in output.claims
        )

        if expectation.expected_status == "answered":
            self._check(
                checks,
                check_id="minimum-source-count",
                critical=critical,
                expected=expectation.minimum_unique_sources,
                observed=len(unique_source_ids),
                matched=len(unique_source_ids) >= expectation.minimum_unique_sources,
                detail="Source count uses unique bibliography identities, not repeated citations.",
            )
            if expectation.maximum_unique_sources is not None:
                self._check(
                    checks,
                    check_id="maximum-source-count",
                    critical=critical,
                    expected=expectation.maximum_unique_sources,
                    observed=len(unique_source_ids),
                    matched=(len(unique_source_ids) <= expectation.maximum_unique_sources),
                    detail="Citing the complete corpus cannot substitute for source selection.",
                )
            self._check(
                checks,
                check_id="every-claim-cited",
                critical=critical,
                expected=True,
                observed=every_claim_cited,
                matched=every_claim_cited,
                detail="Every emitted factual claim requires at least one citation.",
            )
            self._check(
                checks,
                check_id="citation-quotes-grounded",
                critical=critical,
                expected=True,
                observed=citations_valid,
                matched=citations_valid,
                detail="Every citation ID must exist and its quote must occur in that document.",
            )
            bibliography_matches_citations = unique_source_ids == citation_ids
            self._check(
                checks,
                check_id="bibliography-citation-consistency",
                critical=critical,
                expected="bibliography document IDs equal cited document IDs",
                observed={
                    "bibliography_only": sorted(unique_source_ids - citation_ids),
                    "citation_only": sorted(citation_ids - unique_source_ids),
                },
                matched=bibliography_matches_citations,
                detail=(
                    "A bibliography cannot inflate source count, and every citation must have "
                    "a bibliography entry."
                ),
            )
            coverage = len(citation_ids & set(expectation.required_document_ids))
            self._check(
                checks,
                check_id="required-evidence-coverage",
                critical=critical,
                expected=expectation.minimum_required_document_coverage,
                observed=coverage,
                matched=coverage >= expectation.minimum_required_document_coverage,
                detail="The answer must use enough of the independently selected support set.",
            )
            if expectation.allowed_document_ids:
                allowed_ids = set(expectation.allowed_document_ids)
                disallowed_ids = sorted((unique_source_ids | citation_ids) - allowed_ids)
                self._check(
                    checks,
                    check_id="irrelevant-source-exclusion",
                    critical=critical,
                    expected="sources and citations limited to the hidden support set",
                    observed={"disallowed_document_ids": disallowed_ids},
                    matched=not disallowed_ids,
                    detail="Distractors cannot be used to inflate source or citation coverage.",
                )
            haystack = " ".join(
                [output.answer, *(claim.text for claim in output.claims)]
            ).casefold()
            missing_groups = [
                group
                for group in expectation.required_concept_groups
                if not any(term.casefold() in haystack for term in group)
            ]
            self._check(
                checks,
                check_id="required-concepts",
                critical=critical,
                expected=expectation.required_concept_groups,
                observed={"missing_groups": missing_groups},
                matched=not missing_groups,
                detail=(
                    "Semantic expectations are groups of accepted alternatives, not exact prose."
                ),
            )
            if expectation.maximum_answer_characters is not None:
                self._check(
                    checks,
                    check_id="answer-length-bound",
                    critical=critical,
                    expected={"maximum_characters": expectation.maximum_answer_characters},
                    observed={"characters": len(output.answer)},
                    matched=len(output.answer) <= expectation.maximum_answer_characters,
                    detail="Copying the complete corpus is not accepted as concise synthesis.",
                )
        else:
            abstention_clean = not output.claims and not output.sources
            self._check(
                checks,
                check_id="clean-abstention",
                critical=critical,
                expected={"claims": [], "sources": []},
                observed={
                    "claim_count": len(output.claims),
                    "source_count": len(output.sources),
                },
                matched=abstention_clean,
                detail="An insufficient-evidence response must not invent claims or sources.",
            )

    @staticmethod
    def _deeper_check_ids(case: BenchmarkCase) -> list[str]:
        result = ["request-identity", "expected-status", "source-identities"]
        if case.expectation.expected_status == "answered":
            result.extend(
                [
                    "minimum-source-count",
                    "every-claim-cited",
                    "citation-quotes-grounded",
                    "bibliography-citation-consistency",
                    "required-evidence-coverage",
                    "required-concepts",
                ]
            )
            if case.expectation.maximum_unique_sources is not None:
                result.append("maximum-source-count")
            if case.expectation.allowed_document_ids:
                result.append("irrelevant-source-exclusion")
            if case.expectation.maximum_answer_characters is not None:
                result.append("answer-length-bound")
        else:
            result.append("clean-abstention")
        return result

    def _record_blocked_output_checks(
        self, case: BenchmarkCase, checks: list[CheckObservation]
    ) -> None:
        for check_id in self._deeper_check_ids(case):
            self._check(
                checks,
                check_id=check_id,
                critical=case.critical,
                expected="check evaluated after a valid research-output/v1 artifact",
                observed="blocked by invalid output contract",
                matched=False,
                detail=(
                    "The check remains in the fixed denominator even though output parsing "
                    "could not continue."
                ),
            )

    def evaluate(self, definition_path: Path, run_plan_path: Path) -> ProbeEvidence:
        definition_path = definition_path.resolve()
        run_plan_path = run_plan_path.resolve()
        definition = load_benchmark_definition(definition_path)
        run = load_benchmark_run_plan(run_plan_path)
        if run.task_id != definition.task_id:
            raise BlackridgeError(
                f"run task {run.task_id} does not match benchmark {definition.task_id}"
            )

        public_spec = self._resolve_definition_file(definition_path, definition.public_spec_file)
        input_contract = self._resolve_definition_file(
            definition_path, definition.input_contract_file
        )
        output_contract = self._resolve_definition_file(
            definition_path, definition.output_contract_file
        )
        definition_bytes = definition_path.read_bytes()
        run_plan_bytes = run_plan_path.read_bytes()
        public_spec_bytes = public_spec.read_bytes()
        input_contract_bytes = input_contract.read_bytes()
        output_contract_bytes = output_contract.read_bytes()
        try:
            input_schema = json.loads(input_contract_bytes)
            output_schema = json.loads(output_contract_bytes)
            Draft202012Validator.check_schema(input_schema)
            Draft202012Validator.check_schema(output_schema)
        except Exception as exc:
            raise BlackridgeError(f"invalid public benchmark schema: {exc}") from exc
        input_validator = Draft202012Validator(input_schema)
        output_validator = Draft202012Validator(output_schema)
        command = self._candidate_command(definition_path, run_plan_path, run)
        environment = self._environment(run)
        frozen_inputs: dict[str, dict[str, object]] = {
            "definition": _snapshot_file(definition_path, content=definition_bytes),
            "evaluator_module": _snapshot_file(Path(__file__).resolve()),
            "public_spec": _snapshot_file(public_spec, content=public_spec_bytes),
            "input_contract": _snapshot_file(input_contract, content=input_contract_bytes),
            "output_contract": _snapshot_file(output_contract, content=output_contract_bytes),
            "run_plan": _snapshot_file(run_plan_path, content=run_plan_bytes),
        }
        prepared_cases: list[tuple[BenchmarkCase, Path, bytes, ResearchRequest]] = []
        for case in definition.cases:
            request_path = self._resolve_definition_file(definition_path, case.request_file)
            try:
                request_bytes = request_path.read_bytes()
                request_data = json.loads(request_bytes)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise BlackridgeError(
                    f"invalid benchmark case {case.id}: {type(exc).__name__}: {exc}"
                ) from exc
            input_errors = sorted(
                input_validator.iter_errors(request_data),
                key=lambda item: tuple(str(part) for part in item.path),
            )
            if input_errors:
                messages = "; ".join(error.message for error in input_errors)
                raise BlackridgeError(
                    f"benchmark case {case.id} violates the public input contract: {messages}"
                )
            request = ResearchRequest.model_validate(request_data)
            frozen_inputs[f"case:{case.id}"] = _snapshot_file(request_path, content=request_bytes)
            prepared_cases.append((case, request_path, request_bytes, request))

        case_observations: list[dict[str, object]] = []
        all_checks: list[CheckObservation] = []
        started = perf_counter()

        for case, request_path, request_bytes, request in prepared_cases:
            case_started = perf_counter()
            try:
                capture = run_bounded(
                    command.argv,
                    cwd=command.cwd,
                    env=environment,
                    input_text=request_bytes.decode("utf-8"),
                    timeout_seconds=run.candidate.timeout_seconds_per_case,
                    maximum_output_bytes_per_stream=(run.candidate.maximum_output_bytes_per_stream),
                )
            finally:
                if command.cleanup_argv is not None:
                    run_bounded(
                        command.cleanup_argv,
                        cwd=command.cwd,
                        env=environment,
                        timeout_seconds=10,
                        maximum_output_bytes_per_stream=65_536,
                    )

            checks: list[CheckObservation] = []
            process_matched = (
                not capture.timed_out
                and not capture.output_limit_exceeded
                and capture.exit_code == 0
            )
            self._check(
                checks,
                check_id="process-completed",
                critical=case.critical,
                expected={
                    "exit_code": 0,
                    "timed_out": False,
                    "output_limit_exceeded": False,
                },
                observed={
                    "exit_code": capture.exit_code,
                    "timed_out": capture.timed_out,
                    "output_limit_exceeded": capture.output_limit_exceeded,
                },
                matched=process_matched,
                detail="Process completion is retained but is not sufficient artifact proof.",
            )
            frozen_changes = _snapshot_changes(frozen_inputs)
            self._check(
                checks,
                check_id="frozen-input-integrity",
                critical=case.critical,
                expected="all pre-run benchmark and run-plan bytes unchanged",
                observed={"changed_inputs": frozen_changes},
                matched=not frozen_changes,
                detail=("Candidate execution must not alter either arm's frozen evaluator inputs."),
            )

            parsed_output: ResearchOutput | None = None
            parse_error: str | None = None
            try:
                if capture.output_limit_exceeded:
                    raise ValueError("candidate output exceeded the retained byte limit")
                output_data = json.loads(capture.stdout)
                output_errors = sorted(
                    output_validator.iter_errors(output_data),
                    key=lambda item: tuple(str(part) for part in item.path),
                )
                if output_errors:
                    messages = "; ".join(error.message for error in output_errors)
                    raise ValueError(messages)
                parsed_output = ResearchOutput.model_validate(output_data)
            except Exception as exc:  # Pydantic exposes several structured parse errors.
                parse_error = f"{type(exc).__name__}: {exc}"
            self._check(
                checks,
                check_id="output-contract",
                critical=case.critical,
                expected="research-output/v1 JSON",
                observed={"parsed": parsed_output is not None, "error": parse_error},
                matched=parsed_output is not None,
                detail="Stdout must contain exactly one strict output artifact.",
            )
            if parsed_output is not None:
                self._inspect_output(case, request, parsed_output, checks)
            else:
                self._record_blocked_output_checks(case, checks)

            all_checks.extend(checks)
            case_observations.append(
                {
                    "case_id": case.id,
                    "category": case.category,
                    "critical": case.critical,
                    "request_file": str(request_path),
                    "request_sha256": _sha256_bytes(request_bytes),
                    "command": {
                        "backend": run.candidate.backend,
                        "argv": command.argv,
                        "cwd": str(command.cwd),
                        "environment_names": sorted(environment),
                        "timeout_seconds": run.candidate.timeout_seconds_per_case,
                        "maximum_output_bytes_per_stream": (
                            run.candidate.maximum_output_bytes_per_stream
                        ),
                    },
                    "duration_seconds": round(perf_counter() - case_started, 3),
                    "exit_code": capture.exit_code,
                    "timed_out": capture.timed_out,
                    "output_limit_exceeded": capture.output_limit_exceeded,
                    "stdout_bytes_seen": capture.stdout_bytes_seen,
                    "stderr_bytes_seen": capture.stderr_bytes_seen,
                    "stdout": capture.stdout,
                    "stderr": capture.stderr,
                    "parsed_output": (
                        parsed_output.model_dump() if parsed_output is not None else None
                    ),
                    "checks": [item.model_dump() for item in checks],
                }
            )

        critical_checks = [item for item in all_checks if item.critical]
        matched_checks = [item for item in all_checks if item.matched]
        matched_critical = [item for item in critical_checks if item.matched]
        warnings = [
            "The evaluator records objective matches; a named manual review is still required.",
            "Builder telemetry keeps its declared measurement source and is not auto-trusted.",
        ]
        if run.telemetry.measurement_source != "orchestrator":
            warnings.append("At least one run metric was not measured by the orchestrator.")
        if run.candidate.backend != "docker":
            warnings.append(
                "Host execution is a harness control only and is not eligible "
                "for experiment claims."
            )
        frozen_input_changes = _snapshot_changes(frozen_inputs)
        all_critical_matched = bool(critical_checks) and len(matched_critical) == len(
            critical_checks
        )
        experiment_eligible = (
            run.run_kind == "experiment"
            and run.candidate.backend == "docker"
            and run.telemetry.measurement_source == "orchestrator"
            and not frozen_input_changes
            and all_critical_matched
        )
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="blackridge-benchmark-harness/2",
            subject=f"{definition.task_id}@{definition.version}::{run.run_id}",
            request={
                "definition": definition.model_dump(),
                "definition_file": str(definition_path),
                "definition_sha256": frozen_inputs["definition"]["sha256"],
                "evaluator_module_sha256": frozen_inputs["evaluator_module"]["sha256"],
                "public_spec_sha256": frozen_inputs["public_spec"]["sha256"],
                "input_contract_sha256": frozen_inputs["input_contract"]["sha256"],
                "output_contract_sha256": frozen_inputs["output_contract"]["sha256"],
                "case_manifest": {
                    name.removeprefix("case:"): value
                    for name, value in frozen_inputs.items()
                    if name.startswith("case:")
                },
                "run_plan": run.model_dump(),
                "run_plan_file": str(run_plan_path),
                "run_plan_sha256": frozen_inputs["run_plan"]["sha256"],
            },
            observations={
                "probe_completed": True,
                "duration_seconds": round(perf_counter() - started, 3),
                "case_count": len(definition.cases),
                "check_count": len(all_checks),
                "matched_check_count": len(matched_checks),
                "critical_check_count": len(critical_checks),
                "matched_critical_check_count": len(matched_critical),
                "critical_match_rate": (
                    len(matched_critical) / len(critical_checks) if critical_checks else None
                ),
                "all_critical_matched": all_critical_matched,
                "frozen_inputs_before": frozen_inputs,
                "frozen_input_changes": frozen_input_changes,
                "frozen_inputs_unchanged": not frozen_input_changes,
                "experiment_eligible": experiment_eligible,
                "cases": case_observations,
                "telemetry": run.telemetry.model_dump(),
                "weighted_success_score": None,
            },
            sources=[BENCHMARK_SOURCE],
            warnings=warnings,
        )


class BenchmarkCalibrationProbe:
    """Prove the evaluator accepts a reference and catches a green broken control."""

    def probe(
        self,
        definition_path: Path,
        reference_plan_path: Path,
        broken_plan_path: Path,
    ) -> ProbeEvidence:
        evaluator = BenchmarkEvaluator()
        reference = evaluator.evaluate(definition_path, reference_plan_path)
        broken = evaluator.evaluate(definition_path, broken_plan_path)
        reference_run = reference.request["run_plan"]
        broken_run = broken.request["run_plan"]
        if not isinstance(reference_run, dict) or not isinstance(broken_run, dict):
            raise BlackridgeError("calibration run plans are malformed")
        if reference_run.get("method") != BenchmarkMethod.CALIBRATION_REFERENCE:
            raise BlackridgeError("reference run must use calibration-reference method")
        if broken_run.get("method") != BenchmarkMethod.CALIBRATION_BROKEN:
            raise BlackridgeError("broken run must use calibration-broken method")

        same_controls = {
            "definition_sha256": (
                reference.request["definition_sha256"] == broken.request["definition_sha256"]
            ),
            "evaluator_module_sha256": (
                reference.request["evaluator_module_sha256"]
                == broken.request["evaluator_module_sha256"]
            ),
            "public_spec_sha256": (
                reference.request["public_spec_sha256"] == broken.request["public_spec_sha256"]
            ),
            "input_contract_sha256": (
                reference.request["input_contract_sha256"]
                == broken.request["input_contract_sha256"]
            ),
            "output_contract_sha256": (
                reference.request["output_contract_sha256"]
                == broken.request["output_contract_sha256"]
            ),
            "model_identifier": (
                reference_run.get("model_identifier") == broken_run.get("model_identifier")
            ),
            "model_configuration": (
                reference_run.get("model_configuration") == broken_run.get("model_configuration")
            ),
            "builder_time_budget_seconds": (
                reference_run.get("builder_time_budget_seconds")
                == broken_run.get("builder_time_budget_seconds")
            ),
            "candidate_backend": (
                reference_run.get("candidate", {}).get("backend")
                == broken_run.get("candidate", {}).get("backend")
            ),
            "maximum_output_bytes_per_stream": (
                reference_run.get("candidate", {}).get("maximum_output_bytes_per_stream")
                == broken_run.get("candidate", {}).get("maximum_output_bytes_per_stream")
            ),
        }
        broken_cases = broken.observations.get("cases")
        if not isinstance(broken_cases, list):
            raise BlackridgeError("broken calibration did not retain case observations")
        broken_processes_green = all(
            isinstance(case, dict) and case.get("exit_code") == 0 for case in broken_cases
        )
        reference_checks = {
            (str(case.get("case_id")), str(check.get("check_id"))): check.get("matched")
            for case in reference.observations.get("cases", [])
            if isinstance(case, dict)
            for check in case.get("checks", [])
            if isinstance(check, dict)
        }
        detected_broken_checks = [
            {
                "case_id": case.get("case_id"),
                "check_id": check.get("check_id"),
                "detail": check.get("detail"),
            }
            for case in broken_cases
            if isinstance(case, dict)
            for check in case.get("checks", [])
            if isinstance(check, dict)
            and not check.get("matched")
            and reference_checks.get((str(case.get("case_id")), str(check.get("check_id")))) is True
        ]
        warnings = [
            "Calibration is a harness test, not evidence that either A/B method is superior.",
            "Hidden cases share this repository for the smoke test and are procedurally, not "
            "cryptographically, isolated from future builders.",
        ]
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="blackridge-benchmark-calibration/1",
            subject=f"{reference.subject}::vs::{broken.subject}",
            request={
                "definition_file": str(definition_path.resolve()),
                "reference_plan_file": str(reference_plan_path.resolve()),
                "broken_plan_file": str(broken_plan_path.resolve()),
            },
            observations={
                "probe_completed": True,
                "same_controls": same_controls,
                "all_controls_identical": all(same_controls.values()),
                "reference": reference.model_dump(),
                "broken": broken.model_dump(),
                "comparison": {
                    "reference_all_critical_matched": reference.observations.get(
                        "all_critical_matched"
                    ),
                    "broken_all_processes_exited_zero": broken_processes_green,
                    "broken_all_critical_matched": broken.observations.get("all_critical_matched"),
                    "detected_broken_check_count": len(detected_broken_checks),
                    "detected_broken_checks": detected_broken_checks,
                    "weighted_success_score_used": False,
                },
            },
            sources=[BENCHMARK_SOURCE],
            warnings=warnings,
        )


class BenchmarkComparisonProbe:
    """Run a controlled two-arm experiment without assigning a winner."""

    @staticmethod
    def _controls(run: BenchmarkRunPlan) -> dict[str, object]:
        return {
            "task_id": run.task_id,
            "run_kind": run.run_kind,
            "model_identifier": run.model_identifier,
            "model_configuration": run.model_configuration,
            "attempt": run.attempt,
            "builder_time_budget_seconds": run.builder_time_budget_seconds,
            "candidate_backend": run.candidate.backend,
            "timeout_seconds_per_case": run.candidate.timeout_seconds_per_case,
            "maximum_output_bytes_per_stream": (run.candidate.maximum_output_bytes_per_stream),
            "environment_allowlist": sorted(run.candidate.environment_allowlist),
            "docker_image": run.candidate.docker_image,
            "network": run.candidate.network,
            "read_only_root": run.candidate.read_only_root,
            "memory_mib": run.candidate.memory_mib,
            "cpus": run.candidate.cpus,
            "pids_limit": run.candidate.pids_limit,
            "tmpfs_mib": run.candidate.tmpfs_mib,
            "telemetry_measurement_source": run.telemetry.measurement_source,
        }

    @staticmethod
    def _category_summary(probe: ProbeEvidence) -> dict[str, object]:
        cases = probe.observations.get("cases")
        if not isinstance(cases, list):
            raise BlackridgeError("benchmark probe contains no case observations")
        result: dict[str, object] = {}
        categories = sorted({str(case.get("category")) for case in cases if isinstance(case, dict)})
        for category in categories:
            selected = [
                case
                for case in cases
                if isinstance(case, dict) and case.get("category") == category
            ]
            checks = [
                check
                for case in selected
                for check in case.get("checks", [])
                if isinstance(check, dict) and check.get("critical")
            ]
            result[category] = {
                "case_count": len(selected),
                "critical_check_count": len(checks),
                "matched_critical_check_count": sum(1 for check in checks if check.get("matched")),
                "all_critical_matched": bool(checks)
                and all(bool(check.get("matched")) for check in checks),
            }
        return result

    @staticmethod
    def _arm(probe: ProbeEvidence) -> dict[str, object]:
        run_plan = probe.request.get("run_plan")
        if not isinstance(run_plan, dict):
            raise BlackridgeError("benchmark probe contains no run plan")
        return {
            "run_id": run_plan.get("run_id"),
            "method": run_plan.get("method"),
            "model_identifier": run_plan.get("model_identifier"),
            "task_success": probe.observations.get("all_critical_matched"),
            "experiment_eligible": probe.observations.get("experiment_eligible"),
            "critical_match_rate": probe.observations.get("critical_match_rate"),
            "matched_critical_check_count": probe.observations.get("matched_critical_check_count"),
            "critical_check_count": probe.observations.get("critical_check_count"),
            "evaluator_duration_seconds": probe.observations.get("duration_seconds"),
            "categories": BenchmarkComparisonProbe._category_summary(probe),
            "telemetry": probe.observations.get("telemetry"),
            "probe_id": probe.probe_id,
        }

    def probe(
        self,
        definition_path: Path,
        baseline_plan_path: Path,
        blackridge_plan_path: Path,
    ) -> ProbeEvidence:
        baseline_run = load_benchmark_run_plan(baseline_plan_path)
        blackridge_run = load_benchmark_run_plan(blackridge_plan_path)
        if baseline_run.method != BenchmarkMethod.FROM_SCRATCH:
            raise BlackridgeError("baseline run must use from-scratch method")
        if blackridge_run.method != BenchmarkMethod.BLACKRIDGE_HYBRID:
            raise BlackridgeError("Blackridge run must use blackridge-hybrid method")
        baseline_controls = self._controls(baseline_run)
        blackridge_controls = self._controls(blackridge_run)
        control_matches = {
            name: baseline_controls[name] == blackridge_controls[name] for name in baseline_controls
        }
        mismatches = [name for name, matched in control_matches.items() if not matched]
        if mismatches:
            raise BlackridgeError(f"A/B controls differ before execution: {', '.join(mismatches)}")
        is_experiment = baseline_run.run_kind == "experiment"
        if is_experiment:
            if baseline_run.candidate.backend != "docker":
                raise BlackridgeError(
                    "experiment comparisons require the structured Docker backend"
                )
            if baseline_run.telemetry.measurement_source != "orchestrator":
                raise BlackridgeError(
                    "experiment comparisons require orchestrator-measured telemetry"
                )
            if baseline_run.telemetry.clean_install != "pass" or (
                blackridge_run.telemetry.clean_install != "pass"
            ):
                raise BlackridgeError(
                    "experiment comparisons require a passing clean-install observation"
                )

        evaluator = BenchmarkEvaluator()
        baseline = evaluator.evaluate(definition_path, baseline_plan_path)
        if baseline.observations.get("frozen_inputs_unchanged") is not True:
            raise BlackridgeError("baseline candidate altered frozen benchmark inputs")
        blackridge = evaluator.evaluate(definition_path, blackridge_plan_path)
        if blackridge.observations.get("frozen_inputs_unchanged") is not True:
            raise BlackridgeError("Blackridge candidate altered frozen benchmark inputs")
        immutable_matches = {
            name: baseline.request[name] == blackridge.request[name]
            for name in (
                "definition_sha256",
                "evaluator_module_sha256",
                "public_spec_sha256",
                "input_contract_sha256",
                "output_contract_sha256",
                "case_manifest",
            )
        }
        if not all(immutable_matches.values()):
            raise BlackridgeError("A/B benchmark or public contract bytes differ")

        baseline_arm = self._arm(baseline)
        blackridge_arm = self._arm(blackridge)
        baseline_rate = baseline_arm["critical_match_rate"]
        blackridge_rate = blackridge_arm["critical_match_rate"]
        rate_delta = (
            float(blackridge_rate) - float(baseline_rate)
            if isinstance(baseline_rate, int | float) and isinstance(blackridge_rate, int | float)
            else None
        )
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="blackridge-benchmark-comparison/2",
            subject=(
                f"{baseline_run.run_id}::vs::{blackridge_run.run_id}::"
                f"attempt-{baseline_run.attempt}"
            ),
            request={
                "definition_file": str(definition_path.resolve()),
                "baseline_plan_file": str(baseline_plan_path.resolve()),
                "blackridge_plan_file": str(blackridge_plan_path.resolve()),
            },
            observations={
                "probe_completed": True,
                "comparison_controls": baseline_controls,
                "control_matches": control_matches,
                "immutable_input_matches": immutable_matches,
                "valid_two_arm_comparison": is_experiment,
                "harness_control_only": not is_experiment,
                "baseline": baseline_arm,
                "blackridge": blackridge_arm,
                "raw_deltas_blackridge_minus_baseline": {
                    "critical_match_rate": rate_delta,
                },
                "weighted_success_score": None,
                "automatic_winner": None,
                "baseline_probe": baseline.model_dump(),
                "blackridge_probe": blackridge.model_dump(),
            },
            sources=[BENCHMARK_SOURCE],
            warnings=[
                (
                    "One paired experiment is a smoke result and cannot establish general "
                    "superiority."
                    if is_experiment
                    else "This pair exercises comparator plumbing only; it is not an experiment."
                ),
                "A named reviewer must inspect raw arms and measurement sources before reporting.",
            ],
        )
