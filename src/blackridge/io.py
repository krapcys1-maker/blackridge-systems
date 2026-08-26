"""Strict file formats for requests, discovery runs, and blueprints."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from blackridge.models import DiscoveryRun, SystemBlueprint, SystemRequest


def load_request(path: Path) -> SystemRequest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return SystemRequest.model_validate(data)


def load_run(path: Path) -> DiscoveryRun:
    return DiscoveryRun.model_validate_json(path.read_text(encoding="utf-8"))


def write_run(run: DiscoveryRun, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")


def write_blueprint(blueprint: SystemBlueprint, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    primitive = json.loads(blueprint.model_dump_json())
    path.write_text(yaml.safe_dump(primitive, sort_keys=False), encoding="utf-8")

