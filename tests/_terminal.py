"""Compare CLI help text without depending on how the environment styles it.

Rich decides whether to emit ANSI sequences from the environment it finds, so the same
command renders as plain text locally and as styled text on CI. Assertions against raw
output therefore pass on one machine and fail on another, which says nothing about the CLI.

Strip the sequences and assert on the text a reader actually sees.
"""

from __future__ import annotations

import re

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    """Return the visible text of a rendered terminal string."""

    return ANSI.sub("", text)
