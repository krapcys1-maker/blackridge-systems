"""Deliberately broken green-exit sink for artifact-level negative control."""

from __future__ import annotations

import json
import sys

document = json.load(sys.stdin)
json.dump(
    {
        "report": {"based_on": document["paper"]["identifier"]},
        "trace": {"sink": "broken-green-exit"},
    },
    sys.stdout,
)
