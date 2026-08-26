from __future__ import annotations

from copy import deepcopy

from blackridge.adaptation import AdapterExperiment, CompositionPairProbe, JsonPatchAdapterProbe


def experiment(patch: list[dict[str, object]]) -> AdapterExperiment:
    return AdapterExperiment(
        name="field-contract-adapter",
        description="Map one source field into a required target contract field.",
        source_contract="paper-record/v1",
        target_contract="document-record/v1",
        source_fixture={"paper": {"title": "Proof"}, "keep": {"id": 7}},
        target_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["document"],
            "properties": {
                "document": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                }
            },
        },
        patch=patch,
    )


def test_adapter_records_before_failure_after_success_and_preservation() -> None:
    subject = experiment(
        [
            {"op": "add", "path": "/document", "value": {}},
            {"op": "copy", "from": "/paper/title", "path": "/document/name"},
        ]
    )
    original_patch = deepcopy(subject.patch)

    probe = JsonPatchAdapterProbe().probe(subject)

    assert probe.observations["before_adapter"]["target_contract_valid"] is False
    assert probe.observations["after_adapter"]["target_contract_valid"] is True
    assert probe.observations["after_adapter"]["instance"]["document"]["name"] == "Proof"
    assert probe.observations["preservation"]["all_source_values_preserved"] is True
    assert subject.patch == original_patch
    assert probe.observations["patch"]["operations"] == original_patch
    assert probe.observations["patch"]["definition_mutated"] is False
    assert probe.observations["patch"]["working_copy_mutated"] is True
    assert "verdict" not in probe.model_dump()


def test_green_patch_application_does_not_hide_contract_failure() -> None:
    probe = JsonPatchAdapterProbe().probe(
        experiment([{"op": "add", "path": "/document", "value": {}}])
    )

    assert probe.observations["patch"]["error"] is None
    assert probe.observations["after_adapter"]["target_contract_valid"] is False
    assert "still violates" in probe.warnings[0]


def test_composition_pair_requires_same_workload_and_detects_removed_operation() -> None:
    working = experiment(
        [
            {"op": "add", "path": "/document", "value": {}},
            {"op": "copy", "from": "/paper/title", "path": "/document/name"},
        ]
    )
    broken = experiment([{"op": "add", "path": "/document", "value": {}}])

    probe = CompositionPairProbe().probe(working, broken)
    comparison = probe.observations["artifact_comparison"]

    assert comparison["both_patch_applications_returned_without_error"] is True
    assert comparison["working_target_contract_valid"] is True
    assert comparison["negative_target_contract_valid"] is False
    assert probe.observations["adapter_difference"]["removed_operations"] == [
        {"op": "copy", "from": "/paper/title", "path": "/document/name"}
    ]
    assert "verdict" not in probe.model_dump()
