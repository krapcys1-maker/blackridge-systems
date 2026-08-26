"""Deterministic known-good protocol fixture; not a scientific researcher product."""

import json
import sys

request = json.load(sys.stdin)
documents = request["documents"]
selected = [
    document for document in documents if document["document_id"].startswith("evidence-")
]
if len(selected) < request["minimum_sources"]:
    result = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "status": "insufficient-evidence",
        "answer": "The supplied corpus does not contain enough relevant evidence to answer.",
        "claims": [],
        "sources": [],
    }
else:
    result = {
        "schema_version": "1",
        "request_id": request["request_id"],
        "status": "answered",
        "answer": " ".join(document["full_text"] for document in selected),
        "claims": [
            {
                "text": document["full_text"],
                "citations": [
                    {
                        "document_id": document["document_id"],
                        "quote": document["full_text"],
                    }
                ],
            }
            for document in selected
        ],
        "sources": [
            {"document_id": document["document_id"], "title": document["title"]}
            for document in selected
        ],
    }
json.dump(result, sys.stdout)
