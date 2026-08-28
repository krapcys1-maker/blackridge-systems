"""Green-exit negative control that violates the published output schema."""

import json
import sys

request = json.load(sys.stdin)
json.dump(
    {
        "schema_version": "1",
        "request_id": request["request_id"],
        "status": "insufficient-evidence",
        "answer": "No answer was produced.",
        "claims": [],
        "sources": [],
        "unchecked_payload": True,
    },
    sys.stdout,
)
