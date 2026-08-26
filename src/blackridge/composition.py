"""Compatibility solving, provenance-locked generation, and linear system execution."""

from __future__ import annotations

import heapq
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from itertools import product
from pathlib import Path
from typing import Literal
from uuid import uuid4

import jsonpatch
import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ManualReview, ManualVerdict, ProbeEvidence
from blackridge.models import EvidenceLevel

COMPOSITION_SOURCE = (
    "https://github.com/krapcys1-maker/blackridge-systems/tree/main/src/blackridge/composition.py"
)
JSON_PATCH_SOURCE = "https://github.com/stefankoegl/python-json-patch/tree/v1.33"
JSON_SCHEMA_SOURCE = "https://github.com/python-jsonschema/jsonschema/tree/v4.26.0"


class StrictModel(BaseModel):
    """Reject undeclared fields in composition control files."""

    model_config = ConfigDict(extra="forbid")


class ContractDefinition(StrictModel):
    contract_id: str = Field(min_length=3)
    schema_definition: dict[str, object] = Field(alias="schema")


class EvidenceReference(StrictModel):
    """A claimed evidence level plus the exact named review supporting it."""

    level: EvidenceLevel
    review_file: str | None = None
    review_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    capability_id: str | None = None
    scenario_id: str | None = None
    probe_subject: str | None = None

    @model_validator(mode="after")
    def review_fields_are_complete(self) -> EvidenceReference:
        fields = [
            self.review_file,
            self.review_sha256,
            self.capability_id,
            self.scenario_id,
            self.probe_subject,
        ]
        if any(value is not None for value in fields) and not all(
            value is not None for value in fields
        ):
            raise ValueError("all review fields must be supplied together")
        return self


class ComponentLaunch(StrictModel):
    argv: list[str] = Field(min_length=1)
    artifact_file: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    working_directory: str = "{definition_dir}"
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    environment_allowlist: list[str] = Field(default_factory=list)


class ComponentOption(StrictModel):
    """One replaceable implementation behind a single-stream contract boundary."""

    component_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    capability_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_uri: str = Field(min_length=3)
    revision: str = Field(min_length=7)
    license_spdx: str = Field(min_length=2)
    integration: Literal["command-json", "python-library", "cli", "api", "oci"]
    accepts: list[str] = Field(min_length=1, max_length=1)
    produces: list[str] = Field(min_length=1, max_length=1)
    launch: ComponentLaunch | None = None
    evidence: EvidenceReference
    selection_priority: int = Field(default=100, ge=0)
    blocked_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def command_integration_has_launch(self) -> ComponentOption:
        if self.integration == "command-json" and self.launch is None:
            raise ValueError("command-json components require a launch definition")
        if self.integration != "command-json" and self.launch is not None:
            raise ValueError("only command-json components may declare a launch definition")
        return self


class AdapterOption(StrictModel):
    adapter_id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    source_contract: str = Field(min_length=3)
    target_contract: str = Field(min_length=3)
    source_uri: str = Field(min_length=3)
    revision: str = Field(min_length=7)
    license_spdx: str = Field(min_length=2)
    operations: list[dict[str, object]] = Field(min_length=1)
    operations_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: EvidenceReference
    blocked_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def operations_are_explicit_json_patch(self) -> AdapterOption:
        allowed = {"add", "remove", "replace", "move", "copy", "test"}
        for index, operation in enumerate(self.operations):
            if operation.get("op") not in allowed:
                raise ValueError(f"adapter operation {index} has an unsupported op")
            path = operation.get("path")
            if not isinstance(path, str) or (path and not path.startswith("/")):
                raise ValueError(f"adapter operation {index} has an invalid path")
        return self


class CompositionDefinition(StrictModel):
    """A frozen single-stream composition problem and all permitted choices."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    goal: str = Field(min_length=20)
    mode: Literal["calibration", "production"] = "production"
    external_input: str
    required_output: str
    required_capabilities: list[str] = Field(min_length=1)
    allowed_licenses: list[str] = Field(min_length=1)
    allowed_integrations: list[str] = Field(min_length=1)
    minimum_evidence_level: EvidenceLevel = EvidenceLevel.CONTRACT_TESTED
    max_combinations: int = Field(default=10_000, ge=1, le=100_000)
    contracts: list[ContractDefinition] = Field(min_length=2)
    components: list[ComponentOption] = Field(min_length=1)
    adapters: list[AdapterOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_and_contracts_are_consistent(self) -> CompositionDefinition:
        def require_unique(values: list[str], label: str) -> None:
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")

        require_unique(self.required_capabilities, "required capabilities")
        require_unique([item.contract_id for item in self.contracts], "contract ids")
        require_unique([item.component_id for item in self.components], "component ids")
        require_unique([item.adapter_id for item in self.adapters], "adapter ids")
        contract_ids = {item.contract_id for item in self.contracts}
        referenced = {self.external_input, self.required_output}
        for component in self.components:
            referenced.update(component.accepts)
            referenced.update(component.produces)
        for adapter in self.adapters:
            referenced.add(adapter.source_contract)
            referenced.add(adapter.target_contract)
        missing = sorted(referenced - contract_ids)
        if missing:
            raise ValueError("undefined contracts: " + ", ".join(missing))
        component_capabilities = {item.capability_id for item in self.components}
        absent = sorted(set(self.required_capabilities) - component_capabilities)
        if absent:
            raise ValueError("capabilities without component options: " + ", ".join(absent))
        if (
            self.mode == "production"
            and self.minimum_evidence_level < EvidenceLevel.CONTRACT_TESTED
        ):
            raise ValueError("production compositions require at least L3 contract evidence")
        for contract in self.contracts:
            try:
                Draft202012Validator.check_schema(contract.schema_definition)
            except SchemaError as exc:
                raise ValueError(
                    f"invalid schema for {contract.contract_id}: {exc.message}"
                ) from exc
        return self


class QualificationObservation(StrictModel):
    subject_type: Literal["component", "adapter"]
    subject_id: str
    eligible: bool
    reasons: list[str]
    evidence_observations: dict[str, object]


class PlanStep(StrictModel):
    index: int = Field(ge=1)
    step_type: Literal["component", "adapter"]
    subject_id: str
    input_contract: str
    output_contract: str


class CombinationObservation(StrictModel):
    component_ids: list[str]
    complete: bool
    adapter_count: int
    steps: list[PlanStep]
    unresolved_capabilities: list[str]
    terminal_contract: str


class CompositionPlan(StrictModel):
    schema_version: Literal["1"] = "1"
    generated_at: datetime
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    solver_module_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    system_name: str
    mode: Literal["calibration", "production"]
    complete: bool
    release_ready: bool = False
    selected_component_ids: list[str]
    selected_adapter_ids: list[str]
    steps: list[PlanStep]
    unresolved_capabilities: list[str]
    qualifications: list[QualificationObservation]
    evaluated_combinations: list[CombinationObservation]
    warnings: list[str]


class GeneratedSystem(StrictModel):
    output_directory: str
    definition_sha256: str
    plan_sha256: str
    execution_ready: bool
    release_ready: bool
    artifact_sha256: dict[str, str]


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(canonical).hexdigest()


def _repository_root(start: Path) -> Path:
    for candidate in [start.resolve(), *start.resolve().parents]:
        if (candidate / ".git").exists():
            return candidate
    return start.resolve()


def _is_immutable_revision(value: str) -> bool:
    normalized = value.removeprefix("sha256:")
    if len(normalized) in {40, 64}:
        try:
            int(normalized, 16)
        except ValueError:
            return False
        return True
    if "@sha256:" in value:
        digest = value.rsplit("@sha256:", 1)[1]
        return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)
    return False


def _verify_evidence(
    evidence: EvidenceReference,
    *,
    definition_directory: Path,
    mode: Literal["calibration", "production"],
) -> tuple[list[str], dict[str, object]]:
    reasons: list[str] = []
    observations: dict[str, object] = {"claimed_level": int(evidence.level)}
    if evidence.review_file is None:
        observations["review_supplied"] = False
        if mode == "production" or evidence.level > EvidenceLevel.DISCOVERED:
            reasons.append("claimed evidence level has no named manual review")
        return reasons, observations

    review_path = (definition_directory / evidence.review_file).resolve()
    observations.update(
        {
            "review_supplied": True,
            "review_file": str(review_path),
            "review_exists": review_path.is_file(),
        }
    )
    if not review_path.is_file():
        reasons.append("manual review file does not exist")
        return reasons, observations
    actual_review_hash = _sha256_file(review_path)
    observations["review_sha256"] = actual_review_hash
    observations["review_hash_matches"] = actual_review_hash == evidence.review_sha256
    if actual_review_hash != evidence.review_sha256:
        reasons.append("manual review SHA-256 does not match")
        return reasons, observations
    try:
        review = ManualReview.model_validate_json(review_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        reasons.append(f"manual review is invalid: {type(exc).__name__}: {exc}")
        return reasons, observations

    observations.update(
        {
            "review_verdict": review.verdict.value,
            "reviewer": review.reviewer,
            "review_capability_id": review.capability_id,
            "review_scenario_id": review.scenario_id,
        }
    )
    if review.verdict != ManualVerdict.PASS:
        reasons.append("manual review verdict is not pass")
    if review.capability_id != evidence.capability_id:
        reasons.append("manual review capability does not match evidence reference")
    if review.scenario_id != evidence.scenario_id:
        reasons.append("manual review scenario does not match evidence reference")

    repository_root = _repository_root(definition_directory)
    probe_path = Path(review.probe_file)
    if not probe_path.is_absolute():
        probe_path = (repository_root / probe_path).resolve()
    observations["probe_file"] = str(probe_path)
    observations["probe_exists"] = probe_path.is_file()
    if not probe_path.is_file():
        reasons.append("probe referenced by manual review does not exist")
    else:
        actual_probe_hash = _sha256_file(probe_path)
        observations["probe_sha256"] = actual_probe_hash
        observations["probe_hash_matches_review"] = actual_probe_hash == review.probe_sha256
        if actual_probe_hash != review.probe_sha256:
            reasons.append("probe SHA-256 no longer matches the manual review")
        else:
            try:
                probe = ProbeEvidence.model_validate_json(
                    probe_path.read_text(encoding="utf-8")
                )
            except (ValueError, OSError) as exc:
                reasons.append(f"reviewed probe is invalid: {type(exc).__name__}: {exc}")
            else:
                observations["probe_id"] = probe.probe_id
                observations["probe_subject"] = probe.subject
                observations["probe_id_matches_review"] = probe.probe_id == review.probe_id
                observations["probe_subject_matches"] = probe.subject == evidence.probe_subject
                if probe.probe_id != review.probe_id:
                    reasons.append("reviewed probe id does not match the manual review")
                if probe.subject != evidence.probe_subject:
                    reasons.append("reviewed probe subject does not match evidence reference")
    return reasons, observations


def _qualify_component(
    component: ComponentOption,
    definition: CompositionDefinition,
    definition_directory: Path,
) -> QualificationObservation:
    reasons = list(component.blocked_reasons)
    if component.license_spdx not in definition.allowed_licenses:
        reasons.append(f"license {component.license_spdx} is not allowed")
    if component.integration not in definition.allowed_integrations:
        reasons.append(f"integration {component.integration} is not allowed")
    if component.integration != "command-json":
        reasons.append(f"v1 runtime does not implement {component.integration} components")
    if component.evidence.level < definition.minimum_evidence_level:
        reasons.append(
            f"evidence L{int(component.evidence.level)} is below required "
            f"L{int(definition.minimum_evidence_level)}"
        )
    if definition.mode == "production" and not _is_immutable_revision(component.revision):
        reasons.append("production component revision is not an immutable commit or digest")
    evidence_reasons, observations = _verify_evidence(
        component.evidence,
        definition_directory=definition_directory,
        mode=definition.mode,
    )
    reasons.extend(evidence_reasons)
    if component.launch is not None:
        artifact_value = component.launch.artifact_file.replace(
            "{definition_dir}", str(definition_directory.resolve())
        )
        artifact_path = Path(artifact_value).resolve()
        observations["launch_artifact_file"] = str(artifact_path)
        observations["launch_artifact_exists"] = artifact_path.is_file()
        if not artifact_path.is_file():
            reasons.append("command launch artifact does not exist")
        else:
            actual_hash = _sha256_file(artifact_path)
            expected_hash = component.launch.artifact_sha256
            observations["launch_artifact_sha256"] = actual_hash
            observations["launch_artifact_hash_matches"] = actual_hash == expected_hash
            if actual_hash != expected_hash:
                reasons.append("command launch artifact SHA-256 does not match its lock")
        expanded_working_directory = component.launch.working_directory.replace(
            "{definition_dir}", str(definition_directory.resolve())
        )
        expanded_argv = [
            value.replace("{python}", sys.executable).replace(
                "{definition_dir}", str(definition_directory.resolve())
            )
            for value in component.launch.argv
        ]
        working_directory = Path(expanded_working_directory).resolve()
        argv_paths = {
            (Path(value) if Path(value).is_absolute() else working_directory / value).resolve()
            for value in expanded_argv
        }
        observations["launch_artifact_referenced_by_argv"] = artifact_path in argv_paths
        if artifact_path not in argv_paths:
            reasons.append("locked command artifact is not referenced by argv")
    return QualificationObservation(
        subject_type="component",
        subject_id=component.component_id,
        eligible=not reasons,
        reasons=reasons,
        evidence_observations=observations,
    )


def _qualify_adapter(
    adapter: AdapterOption,
    definition: CompositionDefinition,
    definition_directory: Path,
) -> QualificationObservation:
    reasons = list(adapter.blocked_reasons)
    if adapter.license_spdx not in definition.allowed_licenses:
        reasons.append(f"license {adapter.license_spdx} is not allowed")
    if adapter.evidence.level < definition.minimum_evidence_level:
        reasons.append(
            f"evidence L{int(adapter.evidence.level)} is below required "
            f"L{int(definition.minimum_evidence_level)}"
        )
    if definition.mode == "production" and not _is_immutable_revision(adapter.revision):
        reasons.append("production adapter revision is not an immutable commit or digest")
    evidence_reasons, observations = _verify_evidence(
        adapter.evidence,
        definition_directory=definition_directory,
        mode=definition.mode,
    )
    reasons.extend(evidence_reasons)
    operations_hash = _sha256_json(adapter.operations)
    observations["operations_sha256"] = operations_hash
    observations["operations_hash_matches"] = operations_hash == adapter.operations_sha256
    if operations_hash != adapter.operations_sha256:
        reasons.append("adapter operations SHA-256 does not match its lock")
    return QualificationObservation(
        subject_type="adapter",
        subject_id=adapter.adapter_id,
        eligible=not reasons,
        reasons=reasons,
        evidence_observations=observations,
    )


def _route_combination(
    definition: CompositionDefinition,
    components: tuple[ComponentOption, ...],
    adapters: list[AdapterOption],
) -> CombinationObservation:
    selected = {component.component_id: component for component in components}
    required_ids = frozenset(selected)
    queue: list[
        tuple[int, int, tuple[str, ...], str, frozenset[str], tuple[PlanStep, ...]]
    ] = [(0, 0, (), definition.external_input, frozenset(), ())]
    visited: dict[tuple[str, frozenset[str]], tuple[int, int, tuple[str, ...]]] = {}
    furthest = (definition.external_input, frozenset(), ())

    while queue:
        adapter_count, step_count, path_key, current, executed, steps = heapq.heappop(queue)
        state = (current, executed)
        cost = (adapter_count, step_count, path_key)
        if state in visited and visited[state] <= cost:
            continue
        visited[state] = cost
        if len(executed) > len(furthest[1]) or (
            len(executed) == len(furthest[1]) and len(steps) < len(furthest[2])
        ):
            furthest = (current, executed, steps)
        if executed == required_ids and current == definition.required_output:
            return CombinationObservation(
                component_ids=sorted(selected),
                complete=True,
                adapter_count=adapter_count,
                steps=list(steps),
                unresolved_capabilities=[],
                terminal_contract=current,
            )

        actions: list[tuple[str, str, str, str]] = []
        for component in components:
            if component.component_id not in executed and component.accepts[0] == current:
                actions.append(
                    (
                        "component",
                        component.component_id,
                        component.accepts[0],
                        component.produces[0],
                    )
                )
        for adapter in adapters:
            if adapter.source_contract == current and adapter.target_contract != current:
                actions.append(
                    (
                        "adapter",
                        adapter.adapter_id,
                        adapter.source_contract,
                        adapter.target_contract,
                    )
                )
        actions.sort(key=lambda item: (item[0], item[1]))
        for step_type, subject_id, input_contract, output_contract in actions:
            next_executed = executed
            next_adapter_count = adapter_count
            if step_type == "component":
                next_executed = executed | {subject_id}
            else:
                next_adapter_count += 1
            next_step = PlanStep(
                index=step_count + 1,
                step_type=step_type,
                subject_id=subject_id,
                input_contract=input_contract,
                output_contract=output_contract,
            )
            next_path = (*path_key, f"{step_type}:{subject_id}")
            heapq.heappush(
                queue,
                (
                    next_adapter_count,
                    step_count + 1,
                    next_path,
                    output_contract,
                    next_executed,
                    (*steps, next_step),
                ),
            )

    unresolved = sorted(
        {
            selected[component_id].capability_id
            for component_id in required_ids - furthest[1]
        }
    )
    return CombinationObservation(
        component_ids=sorted(selected),
        complete=False,
        adapter_count=sum(step.step_type == "adapter" for step in furthest[2]),
        steps=list(furthest[2]),
        unresolved_capabilities=unresolved,
        terminal_contract=furthest[0],
    )


def solve_composition(
    definition: CompositionDefinition,
    *,
    definition_file: Path,
    now: datetime | None = None,
) -> CompositionPlan:
    """Select qualified components and the fewest verified contract adapters."""

    definition_directory = definition_file.resolve().parent
    definition_hash = _sha256_file(definition_file)
    component_qualifications = [
        _qualify_component(component, definition, definition_directory)
        for component in definition.components
    ]
    adapter_qualifications = [
        _qualify_adapter(adapter, definition, definition_directory)
        for adapter in definition.adapters
    ]
    qualifications = [*component_qualifications, *adapter_qualifications]
    component_status = {item.subject_id: item for item in component_qualifications}
    adapter_status = {item.subject_id: item for item in adapter_qualifications}
    options_by_capability: list[list[ComponentOption]] = []
    missing_capabilities: list[str] = []
    for capability_id in definition.required_capabilities:
        options = [
            component
            for component in definition.components
            if component.capability_id == capability_id
            and component_status[component.component_id].eligible
        ]
        options.sort(key=lambda item: (item.selection_priority, item.component_id))
        options_by_capability.append(options)
        if not options:
            missing_capabilities.append(capability_id)

    warnings = [
        "A complete solver plan is generation evidence, not an L4 system-verification verdict."
    ]
    if definition.mode == "calibration":
        warnings.append(
            "Calibration mode may use L0 fixtures without manual promotion and is never "
            "release-ready."
        )
    if missing_capabilities:
        warnings.append("No eligible implementation exists for at least one required capability.")
        return CompositionPlan(
            generated_at=(now or datetime.now(UTC)).astimezone(UTC),
            definition_sha256=definition_hash,
            solver_module_sha256=_sha256_file(Path(__file__)),
            system_name=definition.name,
            mode=definition.mode,
            complete=False,
            selected_component_ids=[],
            selected_adapter_ids=[],
            steps=[],
            unresolved_capabilities=missing_capabilities,
            qualifications=qualifications,
            evaluated_combinations=[],
            warnings=warnings,
        )

    combination_count = 1
    for options in options_by_capability:
        combination_count *= len(options)
    if combination_count > definition.max_combinations:
        raise BlackridgeError(
            f"composition search would evaluate {combination_count} combinations; "
            f"limit is {definition.max_combinations}"
        )

    eligible_adapters = [
        adapter
        for adapter in definition.adapters
        if adapter_status[adapter.adapter_id].eligible
    ]
    evaluated = [
        _route_combination(definition, combination, eligible_adapters)
        for combination in product(*options_by_capability)
    ]
    complete = [item for item in evaluated if item.complete]
    if not complete:
        best_partial = max(
            evaluated,
            key=lambda item: (
                len(definition.required_capabilities) - len(item.unresolved_capabilities),
                -item.adapter_count,
                tuple(item.component_ids),
            ),
        )
        warnings.append("Eligible components exist, but no complete contract route was found.")
        return CompositionPlan(
            generated_at=(now or datetime.now(UTC)).astimezone(UTC),
            definition_sha256=definition_hash,
            solver_module_sha256=_sha256_file(Path(__file__)),
            system_name=definition.name,
            mode=definition.mode,
            complete=False,
            selected_component_ids=best_partial.component_ids,
            selected_adapter_ids=[
                step.subject_id for step in best_partial.steps if step.step_type == "adapter"
            ],
            steps=best_partial.steps,
            unresolved_capabilities=best_partial.unresolved_capabilities,
            qualifications=qualifications,
            evaluated_combinations=evaluated,
            warnings=warnings,
        )

    component_by_id = {item.component_id: item for item in definition.components}

    def objective(item: CombinationObservation) -> tuple[object, ...]:
        priority = sum(
            component_by_id[component_id].selection_priority
            for component_id in item.component_ids
        )
        adapter_ids = tuple(
            step.subject_id for step in item.steps if step.step_type == "adapter"
        )
        return item.adapter_count, priority, tuple(item.component_ids), adapter_ids

    chosen = min(complete, key=objective)
    return CompositionPlan(
        generated_at=(now or datetime.now(UTC)).astimezone(UTC),
        definition_sha256=definition_hash,
        solver_module_sha256=_sha256_file(Path(__file__)),
        system_name=definition.name,
        mode=definition.mode,
        complete=True,
        selected_component_ids=chosen.component_ids,
        selected_adapter_ids=[
            step.subject_id for step in chosen.steps if step.step_type == "adapter"
        ],
        steps=chosen.steps,
        unresolved_capabilities=[],
        qualifications=qualifications,
        evaluated_combinations=evaluated,
        warnings=warnings,
    )


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8", newline="\n")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _primitive(model: BaseModel) -> dict[str, object]:
    return json.loads(model.model_dump_json(by_alias=True))


def _plan_signature(plan: CompositionPlan) -> dict[str, object]:
    return plan.model_dump(mode="json", exclude={"generated_at"})


def _expand_control_value(value: str, definition_directory: Path) -> str:
    return value.replace("{python}", sys.executable).replace(
        "{definition_dir}", str(definition_directory.resolve())
    )


def generate_system(
    definition: CompositionDefinition,
    plan: CompositionPlan,
    *,
    definition_file: Path,
    output_directory: Path,
    now: datetime | None = None,
) -> GeneratedSystem:
    """Render a complete, locked system bundle without silently overwriting an existing one."""

    if output_directory.exists():
        raise BlackridgeError(f"generated system target already exists: {output_directory}")
    if not plan.complete:
        raise BlackridgeError("cannot generate a system from an incomplete compatibility plan")
    fresh_plan = solve_composition(
        definition, definition_file=definition_file, now=plan.generated_at
    )
    if _plan_signature(plan) != _plan_signature(fresh_plan):
        raise BlackridgeError("composition plan no longer matches the frozen definition and solver")

    component_by_id = {item.component_id: item for item in definition.components}
    adapter_by_id = {item.adapter_id: item for item in definition.adapters}
    contract_by_id = {item.contract_id: item for item in definition.contracts}
    definition_directory = definition_file.resolve().parent
    output_directory = output_directory.resolve()
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}-", dir=output_directory.parent)
    )
    try:
        (temporary / "composition.definition.yaml").write_bytes(definition_file.read_bytes())
        _write_json(temporary / "composition.plan.json", _primitive(plan))
        contract_files: dict[str, str] = {}
        for contract_id, contract in sorted(contract_by_id.items()):
            filename = contract_id.replace("/", "--") + ".schema.json"
            relative = f"contracts/{filename}"
            _write_json(temporary / relative, contract.schema_definition)
            contract_files[contract_id] = relative

        selected_components = [
            component_by_id[component_id] for component_id in plan.selected_component_ids
        ]
        selected_adapters = [
            adapter_by_id[adapter_id] for adapter_id in plan.selected_adapter_ids
        ]
        component_locks = [_primitive(component) for component in selected_components]
        _write_yaml(
            temporary / "components.lock.yaml",
            {
                "schema_version": "1",
                "system_name": definition.name,
                "components": component_locks,
            },
        )
        for adapter in selected_adapters:
            _write_yaml(temporary / "adapters" / f"{adapter.adapter_id}.yaml", _primitive(adapter))

        runtime_steps: list[dict[str, object]] = []
        for step in plan.steps:
            runtime_step = _primitive(step)
            if step.step_type == "component":
                component = component_by_id[step.subject_id]
                if component.launch is None:
                    raise BlackridgeError(
                        "selected component is not executable by the v1 runtime: "
                        f"{component.component_id}"
                    )
                runtime_step["launch"] = {
                    "argv": [
                        _expand_control_value(value, definition_directory)
                        for value in component.launch.argv
                    ],
                    "working_directory": _expand_control_value(
                        component.launch.working_directory, definition_directory
                    ),
                    "artifact_file": _expand_control_value(
                        component.launch.artifact_file, definition_directory
                    ),
                    "artifact_sha256": component.launch.artifact_sha256,
                    "timeout_seconds": component.launch.timeout_seconds,
                    "environment_allowlist": component.launch.environment_allowlist,
                }
            else:
                runtime_step["operations"] = adapter_by_id[step.subject_id].operations
            runtime_steps.append(runtime_step)

        runtime = {
            "schema_version": "1",
            "system_name": definition.name,
            "mode": definition.mode,
            "external_input": definition.external_input,
            "required_output": definition.required_output,
            "contract_files": contract_files,
            "steps": runtime_steps,
        }
        _write_yaml(temporary / "runtime.yaml", runtime)
        _write_yaml(
            temporary / "blackridge.blueprint.yaml",
            {
                "schema_version": "1",
                "system_name": definition.name,
                "goal": definition.goal,
                "mode": definition.mode,
                "generation_ready": plan.complete,
                "execution_ready": definition.mode == "calibration",
                "release_ready": False,
                "external_input": definition.external_input,
                "required_output": definition.required_output,
                "steps": [_primitive(step) for step in plan.steps],
                "warnings": plan.warnings,
            },
        )
        _write_yaml(
            temporary / "evidence" / "evidence.lock.yaml",
            {
                "schema_version": "1",
                "components": [
                    {
                        "component_id": component.component_id,
                        "evidence": _primitive(component.evidence),
                    }
                    for component in selected_components
                ],
                "adapters": [
                    {
                        "adapter_id": adapter.adapter_id,
                        "evidence": _primitive(adapter.evidence),
                    }
                    for adapter in selected_adapters
                ],
            },
        )
        _write_yaml(
            temporary / "compose.yaml",
            {
                "services": {},
                "x-blackridge": {
                    "runtime": "runtime.yaml",
                    "status": "no-oci-services",
                    "reason": "v1 executes locked command-json components without a shell",
                },
            },
        )
        _write_text(
            temporary / "tests" / "README.md",
            "# System verification\n\n"
            "No L4 verdict is generated automatically. Run representative workloads, retain raw "
            "evidence, and add a named manual review here.",
        )
        _write_text(
            temporary / "sbom" / "README.md",
            "# SBOM gate\n\n"
            "The component lock is not an SBOM. Generate and review an SBOM for every distributed "
            "wheel, binary, or OCI image before release.",
        )
        _write_text(
            temporary / "README.md",
            f"# {definition.name}\n\n"
            f"{definition.goal}\n\n"
            "This bundle has a complete compatibility route, but it is not release-ready until "
            "representative L4 workloads receive named manual review.\n\n"
            "Run: `blackridge compose-run . INPUT.json --output OUTPUT.json "
            "--evidence evidence/run-probe.json`",
        )

        plan_hash = _sha256_file(temporary / "composition.plan.json")
        artifact_hashes = {
            path.relative_to(temporary).as_posix(): _sha256_file(path)
            for path in sorted(temporary.rglob("*"))
            if path.is_file()
        }
        provenance = {
            "schema_version": "1",
            "generated_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
            "generator": "blackridge-composer/1",
            "definition_file": str(definition_file.resolve()),
            "definition_sha256": plan.definition_sha256,
            "solver_module_sha256": plan.solver_module_sha256,
            "plan_sha256": plan_hash,
            "selected_components": plan.selected_component_ids,
            "selected_adapters": plan.selected_adapter_ids,
            "artifact_sha256": artifact_hashes,
            "release_ready": False,
        }
        _write_json(temporary / "provenance.json", provenance)
        artifact_hashes["provenance.json"] = _sha256_file(temporary / "provenance.json")
        temporary.replace(output_directory)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    return GeneratedSystem(
        output_directory=str(output_directory),
        definition_sha256=plan.definition_sha256,
        plan_sha256=plan_hash,
        execution_ready=definition.mode == "calibration",
        release_ready=False,
        artifact_sha256=artifact_hashes,
    )


def _validation_errors(schema: dict[str, object], value: object) -> list[dict[str, object]]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    return [
        {
            "message": error.message,
            "instance_path": "/" + "/".join(str(part) for part in error.absolute_path),
            "schema_path": "/" + "/".join(str(part) for part in error.absolute_schema_path),
        }
        for error in errors
    ]


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BlackridgeError(f"expected a mapping in {path}")
    return value


def _resolve_bundle_file(bundle: Path, relative: str) -> Path:
    resolved = (bundle / relative).resolve()
    try:
        resolved.relative_to(bundle.resolve())
    except ValueError as exc:
        raise BlackridgeError(f"generated bundle path escapes its root: {relative}") from exc
    if not resolved.is_file():
        raise BlackridgeError(f"generated bundle file is missing: {relative}")
    return resolved


def _normalized_process_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_generated_system(bundle_directory: Path, input_artifact: object) -> ProbeEvidence:
    """Execute a generated calibration bundle and retain every boundary observation."""

    bundle = bundle_directory.resolve()
    provenance_file = _resolve_bundle_file(bundle, "provenance.json")
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or not isinstance(
        provenance.get("artifact_sha256"), dict
    ):
        raise BlackridgeError("generated provenance manifest is invalid")
    integrity_mismatches: list[dict[str, object]] = []
    for relative, expected_hash in provenance["artifact_sha256"].items():
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise BlackridgeError("generated provenance artifact mapping is invalid")
        artifact_path = _resolve_bundle_file(bundle, relative)
        actual_hash = _sha256_file(artifact_path)
        if actual_hash != expected_hash:
            integrity_mismatches.append(
                {
                    "file": relative,
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                }
            )
    if integrity_mismatches:
        raise BlackridgeError(
            "generated bundle integrity failed: "
            + ", ".join(item["file"] for item in integrity_mismatches)
        )
    runtime_file = _resolve_bundle_file(bundle, "runtime.yaml")
    runtime = _load_yaml_mapping(runtime_file)
    if runtime.get("mode") != "calibration":
        raise BlackridgeError(
            "v1 host runner only executes explicit calibration bundles; production uses a sandbox"
        )
    contract_files = runtime.get("contract_files")
    steps = runtime.get("steps")
    if not isinstance(contract_files, dict) or not isinstance(steps, list):
        raise BlackridgeError("generated runtime is missing contract files or steps")
    schemas: dict[str, dict[str, object]] = {}
    for contract_id, relative in contract_files.items():
        if not isinstance(contract_id, str) or not isinstance(relative, str):
            raise BlackridgeError("generated runtime contract mapping is invalid")
        schema = json.loads(_resolve_bundle_file(bundle, relative).read_text(encoding="utf-8"))
        if not isinstance(schema, dict):
            raise BlackridgeError(f"contract schema is not an object: {contract_id}")
        Draft202012Validator.check_schema(schema)
        schemas[contract_id] = schema

    current_contract = runtime.get("external_input")
    required_output = runtime.get("required_output")
    if not isinstance(current_contract, str) or not isinstance(required_output, str):
        raise BlackridgeError("generated runtime contract endpoints are invalid")
    artifact = input_artifact
    initial_errors = _validation_errors(schemas[current_contract], artifact)
    step_observations: list[dict[str, object]] = []
    failed = bool(initial_errors)
    failure_reason = "initial artifact violates its public contract" if failed else None

    for raw_step in steps:
        if not isinstance(raw_step, dict):
            raise BlackridgeError("generated runtime step is not an object")
        subject_id = raw_step.get("subject_id")
        step_type = raw_step.get("step_type")
        input_contract = raw_step.get("input_contract")
        output_contract = raw_step.get("output_contract")
        if not all(
            isinstance(value, str)
            for value in [subject_id, step_type, input_contract, output_contract]
        ):
            raise BlackridgeError("generated runtime step fields are invalid")
        if failed:
            step_observations.append(
                {
                    "subject_id": subject_id,
                    "step_type": step_type,
                    "status": "skipped",
                    "reason": "an earlier boundary failed",
                }
            )
            continue
        if input_contract != current_contract:
            failed = True
            failure_reason = (
                f"step {subject_id} expected {input_contract} but runtime held {current_contract}"
            )
            step_observations.append(
                {
                    "subject_id": subject_id,
                    "step_type": step_type,
                    "status": "failed",
                    "reason": failure_reason,
                }
            )
            continue
        before_errors = _validation_errors(schemas[current_contract], artifact)
        if before_errors:
            failed = True
            failure_reason = f"step {subject_id} received an invalid input artifact"
            step_observations.append(
                {
                    "subject_id": subject_id,
                    "step_type": step_type,
                    "status": "failed",
                    "input_validation_errors": before_errors,
                    "reason": failure_reason,
                }
            )
            continue

        observation: dict[str, object] = {
            "subject_id": subject_id,
            "step_type": step_type,
            "status": "failed",
            "input_contract": input_contract,
            "output_contract": output_contract,
            "input_artifact": artifact,
        }
        produced: object | None = None
        artifact_integrity_failed = False
        if step_type == "adapter":
            operations = raw_step.get("operations")
            observation["operations"] = operations
            try:
                produced = jsonpatch.JsonPatch(operations).apply(artifact, in_place=False)
                observation["patch_error"] = None
            except (jsonpatch.JsonPatchException, TypeError) as exc:
                observation["patch_error"] = f"{type(exc).__name__}: {exc}"
                failure_reason = f"adapter {subject_id} failed"
        elif step_type == "component":
            launch = raw_step.get("launch")
            if not isinstance(launch, dict):
                raise BlackridgeError(f"component step has no launch control: {subject_id}")
            argv = launch.get("argv")
            cwd = launch.get("working_directory")
            timeout = launch.get("timeout_seconds")
            allowlist = launch.get("environment_allowlist", [])
            artifact_file = launch.get("artifact_file")
            artifact_hash = launch.get("artifact_sha256")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(value, str) for value in argv)
                or not isinstance(cwd, str)
                or not isinstance(timeout, (int, float))
                or not isinstance(allowlist, list)
                or not isinstance(artifact_file, str)
                or not isinstance(artifact_hash, str)
            ):
                raise BlackridgeError(f"component launch control is invalid: {subject_id}")
            launch_artifact = Path(artifact_file).resolve()
            actual_artifact_hash = (
                _sha256_file(launch_artifact) if launch_artifact.is_file() else None
            )
            observation["artifact_integrity"] = {
                "file": str(launch_artifact),
                "exists": launch_artifact.is_file(),
                "expected_sha256": artifact_hash,
                "actual_sha256": actual_artifact_hash,
                "matches": actual_artifact_hash == artifact_hash,
            }
            if actual_artifact_hash != artifact_hash:
                failure_reason = f"component {subject_id} launch artifact failed integrity"
                observation["reason"] = failure_reason
                failed = True
                step_observations.append(observation)
                continue
            environment = {
                key: os.environ[key]
                for key in allowlist
                if isinstance(key, str) and key in os.environ
            }
            environment["PYTHONIOENCODING"] = "utf-8"
            started = datetime.now(UTC)
            try:
                completed = subprocess.run(
                    argv,
                    input=json.dumps(artifact),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    cwd=cwd,
                    env=environment,
                    timeout=float(timeout),
                    check=False,
                    shell=False,
                )
                duration = (datetime.now(UTC) - started).total_seconds()
                observation["process"] = {
                    "argv": argv,
                    "working_directory": cwd,
                    "environment_names": sorted(environment),
                    "duration_seconds": duration,
                    "timed_out": False,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                }
                if completed.returncode == 0:
                    try:
                        produced = json.loads(completed.stdout)
                    except json.JSONDecodeError as exc:
                        observation["parse_error"] = f"JSONDecodeError: {exc}"
                        failure_reason = f"component {subject_id} emitted invalid JSON"
                else:
                    failure_reason = f"component {subject_id} exited nonzero"
            except subprocess.TimeoutExpired as exc:
                duration = (datetime.now(UTC) - started).total_seconds()
                observation["process"] = {
                    "argv": argv,
                    "working_directory": cwd,
                    "environment_names": sorted(environment),
                    "duration_seconds": duration,
                    "timed_out": True,
                    "exit_code": None,
                    "stdout": _normalized_process_text(exc.stdout),
                    "stderr": _normalized_process_text(exc.stderr),
                }
                failure_reason = f"component {subject_id} timed out"
            after_artifact_hash = (
                _sha256_file(launch_artifact) if launch_artifact.is_file() else None
            )
            observation["artifact_integrity"]["after_sha256"] = after_artifact_hash
            observation["artifact_integrity"]["unchanged_after_execution"] = (
                after_artifact_hash == artifact_hash
            )
            artifact_integrity_failed = after_artifact_hash != artifact_hash
            if artifact_integrity_failed:
                failure_reason = f"component {subject_id} changed its locked launch artifact"
        else:
            raise BlackridgeError(f"unsupported generated runtime step: {step_type}")

        if produced is not None:
            after_errors = _validation_errors(schemas[output_contract], produced)
            observation["output_artifact"] = produced
            observation["output_validation_errors"] = after_errors
            observation["output_contract_valid"] = not after_errors
            if not after_errors and not artifact_integrity_failed:
                observation["status"] = "completed"
                artifact = produced
                current_contract = output_contract
            elif after_errors:
                failure_reason = f"step {subject_id} produced an invalid output artifact"
        if observation["status"] != "completed":
            failed = True
            observation["reason"] = failure_reason
        step_observations.append(observation)

    final_errors = (
        _validation_errors(schemas[required_output], artifact)
        if current_contract == required_output
        else []
    )
    completed = not failed and current_contract == required_output and not final_errors
    warnings: list[str] = []
    if not completed:
        warnings.append("The generated system did not produce a valid required output artifact.")
    return ProbeEvidence(
        probe_id=uuid4().hex,
        observed_at=datetime.now(UTC),
        provider="blackridge-generated-linear-runtime/1",
        subject=str(runtime.get("system_name")),
        request={
            "bundle_directory": str(bundle),
            "runtime_sha256": _sha256_file(runtime_file),
            "runtime_module_sha256": _sha256_file(Path(__file__)),
            "provenance_sha256": _sha256_file(provenance_file),
            "bundle_integrity_mismatches": integrity_mismatches,
            "input_artifact": input_artifact,
        },
        observations={
            "probe_completed": True,
            "runtime_mode": runtime.get("mode"),
            "initial_contract": runtime.get("external_input"),
            "initial_validation_errors": initial_errors,
            "steps": step_observations,
            "all_steps_completed": completed,
            "failure_reason": failure_reason,
            "final_contract": current_contract,
            "required_output": required_output,
            "final_artifact": artifact,
            "final_validation_errors": final_errors,
        },
        sources=[COMPOSITION_SOURCE, JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
        warnings=warnings,
    )


class CompositionSystemProbe:
    """Solve, generate, and execute one frozen composition workload."""

    def probe(
        self,
        definition: CompositionDefinition,
        *,
        definition_file: Path,
        output_directory: Path,
        input_artifact: object,
    ) -> ProbeEvidence:
        plan = solve_composition(definition, definition_file=definition_file)
        generation: GeneratedSystem | None = None
        runtime_probe: ProbeEvidence | None = None
        warnings = list(plan.warnings)
        if plan.complete:
            generation = generate_system(
                definition,
                plan,
                definition_file=definition_file,
                output_directory=output_directory,
            )
            if definition.mode == "calibration":
                runtime_probe = run_generated_system(output_directory, input_artifact)
                warnings.extend(runtime_probe.warnings)
            else:
                warnings.append(
                    "Production execution was not attempted because v1 has no sandbox runtime."
                )
        else:
            warnings.append("Generation and execution were not attempted for an incomplete plan.")
        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="blackridge-composition-system-probe/1",
            subject=definition.name,
            request={
                "definition_file": str(definition_file.resolve()),
                "definition_sha256": _sha256_file(definition_file),
                "output_directory": str(output_directory.resolve()),
                "input_artifact": input_artifact,
            },
            observations={
                "probe_completed": True,
                "plan": _primitive(plan),
                "generation": _primitive(generation) if generation else None,
                "runtime": _primitive(runtime_probe) if runtime_probe else None,
            },
            sources=[COMPOSITION_SOURCE, JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
            warnings=warnings,
        )
