"""Calibration component that consumes an adapted document record."""

from __future__ import annotations

import json
import sys

document = json.load(sys.stdin)
json.dump(
    {
        "report": {
            "title": document["document"]["name"],
            "based_on": document["paper"]["identifier"],
        },
        "trace": {
            "source": document["trace"]["source"],
            "sink": "report-sink-calibration",
        },
    },
    sys.stdout,
)
