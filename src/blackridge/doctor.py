"""Local prerequisites and optional upstream tool availability."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCheck:
    name: str
    required_for_mvp: bool
    available: bool
    purpose: str


TOOLS = (
    ("git", True, "source control"),
    ("gh", True, "official GitHub metadata and authentication"),
    ("node", True, "Octocode runtime"),
    ("npx", True, "pinned Octocode invocation"),
    ("docker", False, "candidate sandbox and later experiment stages"),
    ("dagger", False, "reproducible experiment DAGs"),
    ("scorecard", False, "offline OpenSSF checks; MVP uses its public API"),
    ("syft", False, "SBOM generation in the inspector milestone"),
    ("ort", False, "license and policy analysis in the inspector milestone"),
)


def check_tools() -> list[ToolCheck]:
    return [
        ToolCheck(
            name=name,
            required_for_mvp=required,
            available=shutil.which(name) is not None,
            purpose=purpose,
        )
        for name, required, purpose in TOOLS
    ]

