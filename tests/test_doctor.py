from __future__ import annotations

from blackridge.doctor import check_tools
from blackridge.process_boundary import BoundedProcessResult


def test_doctor_requires_a_functional_command_not_only_a_path(monkeypatch) -> None:
    monkeypatch.setattr("blackridge.doctor.TOOLS", (("git", True, ("--version",), "source"),))
    monkeypatch.setattr("blackridge.doctor.shutil.which", lambda _name: "/tools/git")
    monkeypatch.setattr(
        "blackridge.doctor.run_bounded",
        lambda _argv, **_kwargs: BoundedProcessResult(
            argv=("/tools/git", "--version"),
            returncode=1,
            stdout="",
            stderr="broken installation",
            duration_seconds=0.1,
            timed_out=False,
            output_limit_exceeded=False,
            stdout_bytes_seen=0,
            stderr_bytes_seen=19,
        ),
    )

    check = check_tools()[0]

    assert check.path == "/tools/git"
    assert check.available is False
    assert check.detail == "broken installation"
