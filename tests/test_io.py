from __future__ import annotations

import json
from pathlib import Path

import pytest

import blackridge.io as blackridge_io


class JsonRecord:
    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps({"kind": "json-record", "indent": indent})


class YamlRecord:
    def model_dump_json(self) -> str:
        return json.dumps({"kind": "yaml-record"})


@pytest.mark.parametrize(
    ("writer", "record"),
    [
        (blackridge_io.write_run, JsonRecord()),
        (blackridge_io.write_blueprint, YamlRecord()),
        (blackridge_io.write_composition_plan, YamlRecord()),
        (blackridge_io.write_probe, JsonRecord()),
        (blackridge_io.write_manual_review, JsonRecord()),
    ],
)
def test_persistent_writers_share_the_atomic_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer, record
) -> None:
    destination = tmp_path / "nested" / "evidence.json"
    calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        blackridge_io,
        "_atomic_write_text",
        lambda path, value: calls.append((path, value)),
    )

    writer(record, destination)

    assert calls[0][0] == destination
    assert calls[0][1]


def test_atomic_writer_preserves_previous_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "evidence.json"
    destination.write_text("trusted", encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(blackridge_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        blackridge_io._atomic_write_text(destination, "partial")

    assert destination.read_text(encoding="utf-8") == "trusted"
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob(".*.part")) == []


def test_atomic_writer_creates_parent_and_replaces_complete_file(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "evidence.json"

    blackridge_io._atomic_write_text(destination, "complete")

    assert destination.read_text(encoding="utf-8") == "complete"
    assert list(destination.parent.glob("*.part")) == []
    assert list(destination.parent.glob(".*.part")) == []
