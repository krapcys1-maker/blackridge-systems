"""Local prerequisites and optional upstream tool availability."""

from __future__ import annotations

from dataclasses import dataclass

from blackridge.process_boundary import resolve_executable, run_bounded


@dataclass(frozen=True)
class ToolCheck:
    name: str
    required_for_mvp: bool
    available: bool
    purpose: str
    path: str | None
    detail: str


TOOLS = (
    ("git", True, ("--version",), "source control"),
    ("gh", True, ("auth", "status", "--hostname", "github.com"), "GitHub authentication"),
    ("node", True, ("--version",), "Octocode runtime"),
    ("npx", True, ("--version",), "pinned Octocode invocation"),
    (
        "docker",
        False,
        ("version", "--format", "{{.Server.Version}}"),
        "candidate sandbox with a reachable daemon",
    ),
    ("dagger", False, ("version",), "reproducible experiment DAGs"),
    ("scorecard", False, ("version",), "offline OpenSSF checks; MVP uses its public API"),
    ("syft", False, ("version",), "SBOM generation in the inspector milestone"),
    ("ort", False, ("--version",), "license and policy analysis in the inspector milestone"),
    (
        "pypi-attestations",
        False,
        ("--version",),
        "cryptographic PyPI provenance verification",
    ),
)


def check_tools() -> list[ToolCheck]:
    checks: list[ToolCheck] = []
    for name, required, version_args, purpose in TOOLS:
        resolved = resolve_executable(name)
        if resolved is None:
            checks.append(ToolCheck(name, required, False, purpose, None, "executable not found"))
            continue
        try:
            result = run_bounded(
                [resolved, *version_args],
                timeout_seconds=10,
                maximum_output_bytes_per_stream=65_536,
            )
        except OSError as exc:
            checks.append(
                ToolCheck(
                    name,
                    required,
                    False,
                    purpose,
                    resolved,
                    f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        output = (result.stdout.strip() or result.stderr.strip()).splitlines()
        functional = (
            result.returncode == 0 and not result.timed_out and not result.output_limit_exceeded
        )
        detail = output[0][:160] if output else f"exit {result.returncode}"
        checks.append(ToolCheck(name, required, functional, purpose, resolved, detail))
    return checks
