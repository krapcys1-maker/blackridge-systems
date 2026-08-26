"""Green-exit negative control with fabricated and ungrounded output."""

import json
import sys

request = json.load(sys.stdin)
json.dump(
    {
        "schema_version": "1",
        "request_id": request["request_id"],
        "status": "answered",
        "answer": "Automation is reliable because every command completed successfully.",
        "claims": [{"text": "Every automated result is trustworthy.", "citations": []}],
        "sources": [{"document_id": "invented-paper", "title": "Imaginary Evidence"}],
    },
    sys.stdout,
)
