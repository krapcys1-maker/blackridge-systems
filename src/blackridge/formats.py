"""Fail-closed readers for human-authored control files."""

from __future__ import annotations

from pathlib import Path

import yaml

from blackridge.errors import ConfigurationError


def load_yaml(path: Path) -> object:
    """Read one YAML document and turn parser failures into a stable domain error."""

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {path}: {exc}") from exc
