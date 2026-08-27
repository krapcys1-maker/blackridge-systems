"""Shared pristine-checkout inspection for scanners and provenance audits."""

from __future__ import annotations

from pathlib import Path

from blackridge.errors import BlackridgeError
from blackridge.process_boundary import run_bounded


def _git_observation(argv: list[str]) -> dict[str, object]:
    completed = run_bounded(argv, timeout_seconds=30)
    observation = {
        "argv": argv,
        "duration_seconds": completed.duration_seconds,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "timed_out": completed.timed_out,
        "output_limit_exceeded": completed.output_limit_exceeded,
        "stdout_bytes_seen": completed.stdout_bytes_seen,
        "stderr_bytes_seen": completed.stderr_bytes_seen,
    }
    if completed.timed_out:
        raise BlackridgeError("Git checkout inspection timed out")
    if completed.output_limit_exceeded:
        raise BlackridgeError("Git checkout inspection exceeded the output limit")
    if completed.returncode != 0:
        raise BlackridgeError(
            f"Git checkout inspection failed with exit {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    return observation


def inspect_pristine_checkout(
    directory: Path,
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
    context: str = "Git checkout",
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Prove HEAD/tree identity and reject tracked, untracked, or ignored residue."""

    commands = [
        _git_observation(["git", "-C", str(directory), "rev-parse", "HEAD"]),
        _git_observation(["git", "-C", str(directory), "rev-parse", "HEAD^{tree}"]),
        _git_observation(
            [
                "git",
                "-C",
                str(directory),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ]
        ),
        _git_observation(["git", "-C", str(directory), "clean", "-ndx"]),
    ]
    commit = str(commands[0]["stdout"]).strip()
    tree = str(commands[1]["stdout"]).strip()
    status = str(commands[2]["stdout"])
    cleanup_preview = str(commands[3]["stdout"])
    state = {
        "commit": commit,
        "tree": tree,
        "status_porcelain": status,
        "ignored_or_untracked_cleanup_preview": cleanup_preview,
        "pristine": not status.strip() and not cleanup_preview.strip(),
    }
    if not state["pristine"]:
        raise BlackridgeError(f"{context} is not pristine")
    if expected_commit is not None and commit != expected_commit:
        raise BlackridgeError(
            f"{context} identity mismatch: requested {expected_commit}, observed {commit}"
        )
    if expected_tree is not None and tree != expected_tree:
        raise BlackridgeError(
            f"{context} tree mismatch: requested {expected_tree}, observed {tree}"
        )
    return commands, state
