#!/usr/bin/env python3
"""Green-exit control that fabricates support and never abstains."""

import json
import sys

request = json.load(sys.stdin)
document = request["documents"][0]
print(
    json.dumps(
        {
            "schema_version": "1",
            "request_id": request["request_id"],
            "status": "answered",
            "answer": "The requested conclusion is fully supported.",
            "claims": [
                {
                    "text": "The requested conclusion is fully supported.",
                    "citations": [
                        {
                            "document_id": document["document_id"],
                            "quote": "This fabricated quote is absent from the document.",
                        }
                    ],
                }
            ],
            "sources": [
                {
                    "document_id": document["document_id"],
                    "title": document["title"],
                }
            ],
        }
    )
)
