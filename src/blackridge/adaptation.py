"""Declarative component-contract adapters built from RFC 6902 and JSON Schema."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Literal
from uuid import uuid4

import jsonpatch
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field, field_validator

from blackridge.errors import BlackridgeError
from blackridge.evidence import ProbeEvidence

JSON_PATCH_SOURCE = "https://github.com/stefankoegl/python-json-patch/tree/v1.33"
JSON_SCHEMA_SOURCE = "https://github.com/python-jsonschema/jsonschema/tree/v4.26.0"


class AdapterExperiment(BaseModel):
    """A concrete source fixture, target contract, and reviewable JSON Patch adapter."""

    schema_version: Literal["1"] = "1"
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    description: str = Field(min_length=20)
    source_contract: str = Field(min_length=3)
    target_contract: str = Field(min_length=3)
    source_fixture: dict[str, object]
    target_schema: dict[str, object]
    patch: list[dict[str, object]] = Field(min_length=1)

    @field_validator("patch")
    @classmethod
    def patch_has_explicit_operations(cls, value: list[dict[str, object]]):
        allowed = {"add", "remove", "replace", "move", "copy", "test"}
        for index, operation in enumerate(value):
            if operation.get("op") not in allowed:
                raise ValueError(f"patch operation {index} has an unsupported op")
            path = operation.get("path")
            if not isinstance(path, str) or (path and not path.startswith("/")):
                raise ValueError(f"patch operation {index} has an invalid JSON Pointer path")
        return value


def _validation_errors(
    validator: Draft202012Validator, instance: object
) -> list[dict[str, object]]:
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    return [
        {
            "message": error.message,
            "instance_path": "/" + "/".join(str(part) for part in error.absolute_path),
            "schema_path": "/" + "/".join(str(part) for part in error.absolute_schema_path),
            "validator": error.validator,
        }
        for error in errors
    ]


def _source_differences(before: object, after: object, path: str = "") -> list[str]:
    """Find source values removed or changed by an adapter; added target fields are allowed."""

    if isinstance(before, dict):
        if not isinstance(after, dict):
            return [path or "/"]
        differences: list[str] = []
        for key, value in before.items():
            child = f"{path}/{key}"
            if key not in after:
                differences.append(child)
            else:
                differences.extend(_source_differences(value, after[key], child))
        return differences
    if isinstance(before, list):
        if not isinstance(after, list) or before != after:
            return [path or "/"]
        return []
    return [] if before == after else [path or "/"]


class JsonPatchAdapterProbe:
    """Apply a declarative adapter and retain before/after contract observations."""

    def probe(self, experiment: AdapterExperiment) -> ProbeEvidence:
        try:
            Draft202012Validator.check_schema(experiment.target_schema)
        except SchemaError as exc:
            raise BlackridgeError(f"invalid target JSON Schema: {exc.message}") from exc

        validator = Draft202012Validator(experiment.target_schema)
        original = deepcopy(experiment.source_fixture)
        original_patch = deepcopy(experiment.patch)
        working_patch = deepcopy(original_patch)
        before_errors = _validation_errors(validator, original)
        adapted: object | None = None
        patch_error: str | None = None
        after_errors: list[dict[str, object]] | None = None
        try:
            adapted = jsonpatch.JsonPatch(working_patch).apply(original, in_place=False)
            after_errors = _validation_errors(validator, adapted)
        except jsonpatch.JsonPatchException as exc:
            patch_error = f"{type(exc).__name__}: {exc}"

        source_mutated = original != experiment.source_fixture
        patch_definition_mutated = experiment.patch != original_patch
        patch_working_copy_mutated = working_patch != original_patch
        preservation_differences = (
            _source_differences(experiment.source_fixture, adapted) if adapted is not None else []
        )
        warnings: list[str] = []
        if not before_errors:
            warnings.append("The source fixture already satisfies the target contract.")
        if patch_error:
            warnings.append("The declarative patch failed and no adapted output was produced.")
        if after_errors:
            warnings.append("The adapted output still violates the target contract.")
        if source_mutated:
            warnings.append("The upstream adapter mutated the source fixture in place.")
        if patch_definition_mutated:
            warnings.append("The upstream adapter mutated the declarative patch definition.")
        if preservation_differences:
            warnings.append("At least one source value was removed or changed by the adapter.")

        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="jsonpatch-rfc6902+jsonschema-draft2020-12",
            subject=experiment.name,
            request=experiment.model_dump(),
            observations={
                "probe_completed": True,
                "libraries": {
                    "jsonpatch": version("jsonpatch"),
                    "jsonschema": version("jsonschema"),
                },
                "before_adapter": {
                    "instance": experiment.source_fixture,
                    "target_contract_valid": not before_errors,
                    "validation_errors": before_errors,
                },
                "patch": {
                    "operations": original_patch,
                    "error": patch_error,
                    "source_fixture_mutated": source_mutated,
                    "definition_mutated": patch_definition_mutated,
                    "working_copy_mutated": patch_working_copy_mutated,
                },
                "after_adapter": {
                    "instance": adapted,
                    "target_contract_valid": after_errors == [],
                    "validation_errors": after_errors,
                },
                "preservation": {
                    "all_source_values_preserved": not preservation_differences,
                    "changed_or_missing_source_paths": preservation_differences,
                },
            },
            sources=[JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
            warnings=warnings,
        )


class CompositionPairProbe:
    """Run the same workload through a working and deliberately broken adapter."""

    @staticmethod
    def _patch_difference(
        working: list[dict[str, object]], broken: list[dict[str, object]]
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        remaining = deepcopy(broken)
        removed: list[dict[str, object]] = []
        for operation in working:
            if operation in remaining:
                remaining.remove(operation)
            else:
                removed.append(operation)
        return removed, remaining

    def probe(
        self, working: AdapterExperiment, broken: AdapterExperiment
    ) -> ProbeEvidence:
        comparable = {
            "source_contract": working.source_contract == broken.source_contract,
            "target_contract": working.target_contract == broken.target_contract,
            "source_fixture": working.source_fixture == broken.source_fixture,
            "target_schema": working.target_schema == broken.target_schema,
        }
        mismatches = [name for name, matches in comparable.items() if not matches]
        if mismatches:
            raise BlackridgeError(
                "composition fixtures do not run the same workload: " + ", ".join(mismatches)
            )

        working_probe = JsonPatchAdapterProbe().probe(working)
        broken_probe = JsonPatchAdapterProbe().probe(broken)
        working_observations = working_probe.observations
        broken_observations = broken_probe.observations
        removed, added = self._patch_difference(working.patch, broken.patch)
        working_after = working_observations["after_adapter"]
        broken_after = broken_observations["after_adapter"]
        warnings: list[str] = []
        if working_after["target_contract_valid"] is not True:
            warnings.append("The nominal composition does not satisfy the target contract.")
        if broken_after["target_contract_valid"] is True:
            warnings.append("The deliberate negative composition was not detected.")
        if len(removed) != 1 or added:
            warnings.append(
                "The negative fixture does not differ by exactly one removed operation."
            )

        return ProbeEvidence(
            probe_id=uuid4().hex,
            observed_at=datetime.now(UTC),
            provider="blackridge-composition-pair/jsonpatch+jsonschema",
            subject=f"{working.name}::vs::{broken.name}",
            request={
                "working": working.model_dump(),
                "deliberate_negative": broken.model_dump(),
            },
            observations={
                "probe_completed": True,
                "same_workload": comparable,
                "adapter_difference": {
                    "removed_operations": removed,
                    "added_operations": added,
                },
                "working": working_observations,
                "deliberate_negative": broken_observations,
                "artifact_comparison": {
                    "both_patch_applications_returned_without_error": (
                        working_observations["patch"]["error"] is None
                        and broken_observations["patch"]["error"] is None
                    ),
                    "working_target_contract_valid": working_after[
                        "target_contract_valid"
                    ],
                    "negative_target_contract_valid": broken_after[
                        "target_contract_valid"
                    ],
                    "working_output": working_after["instance"],
                    "negative_output": broken_after["instance"],
                    "negative_validation_errors": broken_after["validation_errors"],
                },
            },
            sources=[JSON_PATCH_SOURCE, JSON_SCHEMA_SOURCE],
            warnings=warnings,
        )
