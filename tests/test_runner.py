from __future__ import annotations

from pathlib import Path

import pytest

from blackridge.errors import ExternalToolError
from blackridge.process_boundary import BoundedProcessResult
from blackridge.runner import CommandRunner


def _process(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    output_limit_exceeded: bool = False,
) -> BoundedProcessResult:
    return BoundedProcessResult(
        argv=("/tools/demo",),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=0.01,
        timed_out=timed_out,
        output_limit_exceeded=output_limit_exceeded,
        stdout_bytes_seen=len(stdout.encode()),
        stderr_bytes_seen=len(stderr.encode()),
    )


def test_resolve_returns_an_absolute_tool_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("blackridge.runner.shutil.which", lambda _name: "/tools/demo")

    assert CommandRunner().resolve("demo") == str(Path("/tools/demo"))


def test_resolve_rejects_a_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("blackridge.runner.shutil.which", lambda _name: None)

    with pytest.raises(ExternalToolError, match="required executable not found: missing"):
        CommandRunner().resolve("missing")


def test_run_rejects_an_empty_argument_vector() -> None:
    with pytest.raises(ValueError, match="argv cannot be empty"):
        CommandRunner().run([])


def test_run_resolves_the_executable_and_retains_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(CommandRunner, "resolve", lambda _self, _name: "/tools/demo")

    def bounded(argv: list[str], *, timeout_seconds: int) -> BoundedProcessResult:
        observed.update(argv=argv, timeout_seconds=timeout_seconds)
        return _process(stdout="ready\n", stderr="diagnostic\n")

    monkeypatch.setattr("blackridge.runner.run_bounded", bounded)

    result = CommandRunner().run(["demo", "--json"], timeout_seconds=7)

    assert observed == {"argv": ["/tools/demo", "--json"], "timeout_seconds": 7}
    assert result.argv == ("demo", "--json")
    assert result.stdout == "ready\n"
    assert result.stderr == "diagnostic\n"


@pytest.mark.parametrize(
    ("process", "message"),
    [
        (_process(timed_out=True), "upstream command timed out after 3s: demo"),
        (_process(output_limit_exceeded=True), "upstream command exceeded the output limit: demo"),
        (_process(returncode=9, stderr="failed safely"), "demo failed (9): failed safely"),
        (_process(returncode=4, stdout="stdout fallback"), "demo failed (4): stdout fallback"),
        (_process(returncode=2), "demo failed (2): no output"),
    ],
)
def test_run_fails_closed_for_abnormal_processes(
    monkeypatch: pytest.MonkeyPatch,
    process: BoundedProcessResult,
    message: str,
) -> None:
    monkeypatch.setattr(CommandRunner, "resolve", lambda _self, _name: "/tools/demo")
    monkeypatch.setattr("blackridge.runner.run_bounded", lambda *_args, **_kwargs: process)

    with pytest.raises(ExternalToolError, match=message.replace("(", r"\(").replace(")", r"\)")):
        CommandRunner().run(["demo"], timeout_seconds=3)


def test_run_bounds_the_error_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CommandRunner, "resolve", lambda _self, _name: "/tools/demo")
    monkeypatch.setattr(
        "blackridge.runner.run_bounded",
        lambda *_args, **_kwargs: _process(returncode=1, stderr="x" * 2000),
    )

    with pytest.raises(ExternalToolError) as caught:
        CommandRunner().run(["demo"])

    assert str(caught.value).endswith("x" * 1000)
    assert len(str(caught.value)) < 1100
