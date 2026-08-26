from __future__ import annotations

from pathlib import Path

import pytest

from blackridge.errors import ConfigurationError
from blackridge.formats import load_yaml


def test_malformed_yaml_is_reported_as_a_stable_configuration_error(
    tmp_path: Path,
) -> None:
    control = tmp_path / "broken.yaml"
    control.write_text("goal: [unterminated\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="invalid YAML"):
        load_yaml(control)
