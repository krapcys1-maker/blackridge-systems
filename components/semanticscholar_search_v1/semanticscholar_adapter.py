#!/usr/bin/env python3
"""Strict JSON adapter for the reviewed semanticscholar 0.12.0 client."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from typing import Any

FIELDS = [
    "paperId",
    "title",
    "abstract",
    "year",
    "authors",
    "externalIds",
    "citationCount",
    "url",
    "venue",
    "publicationDate",
    "isOpenAccess",
    "openAccessPdf",
]
COMMON_FIELDS = {"request_id", "operation"}
SEARCH_FIELDS = COMMON_FIELDS | {"query", "page_size", "max_results"}
GET_FIELDS = COMMON_FIELDS | {"paper_id"}


class ContractError(ValueError):
    """The caller supplied a request outside the frozen adapter contract."""


def _bounded_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractError(f"{name} must be a non-empty string of at most {maximum} characters")
    return value.strip()


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContractError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _validate_request(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError("request must be a JSON object")
    request_id = _bounded_text(value.get("request_id"), "request_id", 128)
    operation = value.get("operation")
    if operation == "search":
        unknown = set(value) - SEARCH_FIELDS
        if unknown:
            raise ContractError(f"unknown request fields: {', '.join(sorted(unknown))}")
        query = _bounded_text(value.get("query"), "query", 500)
        page_size = _bounded_integer(value.get("page_size", 20), "page_size", 1, 100)
        maximum = _bounded_integer(value.get("max_results", page_size), "max_results", 1, 1000)
        return {
            "request_id": request_id,
            "operation": operation,
            "query": query,
            "page_size": min(page_size, maximum),
            "max_results": maximum,
        }
    if operation == "get-paper":
        unknown = set(value) - GET_FIELDS
        if unknown:
            raise ContractError(f"unknown request fields: {', '.join(sorted(unknown))}")
        return {
            "request_id": request_id,
            "operation": operation,
            "paper_id": _bounded_text(value.get("paper_id"), "paper_id", 500),
        }
    raise ContractError("operation must be search or get-paper")


def _evaluation_api_url() -> str | None:
    value = os.environ.get("BLACKRIDGE_SEMANTICSCHOLAR_API_URL")
    if value and os.environ.get("BLACKRIDGE_EVALUATION_MODE") != "1":
        raise ContractError("custom API URL requires BLACKRIDGE_EVALUATION_MODE=1")
    if value and not value.startswith("http://127.0.0.1:"):
        raise ContractError("evaluation API URL must use IPv4 loopback")
    return value


def _client() -> Any:
    from semanticscholar import SemanticScholar

    return SemanticScholar(timeout=10, api_url=_evaluation_api_url(), retry=False)


def _json_value(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _paper(paper: Any) -> dict[str, object]:
    authors = getattr(paper, "authors", None) or []
    return {
        "paper_id": getattr(paper, "paperId", None),
        "title": getattr(paper, "title", None),
        "abstract": getattr(paper, "abstract", None),
        "year": getattr(paper, "year", None),
        "authors": [
            {
                "author_id": getattr(author, "authorId", None),
                "name": getattr(author, "name", None),
            }
            for author in authors
        ],
        "external_ids": _json_value(getattr(paper, "externalIds", None)),
        "citation_count": getattr(paper, "citationCount", None),
        "url": getattr(paper, "url", None),
        "venue": getattr(paper, "venue", None),
        "publication_date": _json_value(getattr(paper, "publicationDate", None)),
        "is_open_access": getattr(paper, "isOpenAccess", None),
        "open_access_pdf": _json_value(getattr(paper, "openAccessPdf", None)),
    }


def _error(request_id: str | None, operation: str | None, code: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "request_id": request_id,
        "operation": operation,
        "status": "error",
        "papers": [],
        "returned_count": 0,
        "total": None,
        "error": {"code": code},
    }


def _upstream_error_code(exc: Exception) -> str:
    if type(exc).__name__ == "ObjectNotFoundException":
        return "not-found"
    last_attempt = getattr(exc, "last_attempt", None)
    nested = last_attempt.exception() if last_attempt is not None else None
    if isinstance(exc, ConnectionRefusedError) or isinstance(nested, ConnectionRefusedError):
        return "rate-limited"
    return "upstream-failure"


def execute(raw: object) -> dict[str, object]:
    request_id = raw.get("request_id") if isinstance(raw, dict) else None
    operation = raw.get("operation") if isinstance(raw, dict) else None
    try:
        request = _validate_request(raw)
        client = _client()
        if request["operation"] == "get-paper":
            papers = [_paper(client.get_paper(str(request["paper_id"]), fields=FIELDS))]
            total: int | None = 1
        else:
            results = client.search_paper(
                str(request["query"]), fields=FIELDS, limit=int(request["page_size"])
            )
            papers = []
            for paper in results:
                papers.append(_paper(paper))
                if len(papers) >= request["max_results"]:
                    break
            total = results.total
        return {
            "schema_version": "1",
            "request_id": request["request_id"],
            "operation": request["operation"],
            "status": "ok",
            "papers": papers,
            "returned_count": len(papers),
            "total": total,
            "error": None,
        }
    except ContractError:
        return _error(
            request_id if isinstance(request_id, str) else None, operation, "invalid-request"
        )
    except ModuleNotFoundError:
        return _error(
            request_id if isinstance(request_id, str) else None, operation, "dependency-missing"
        )
    except Exception as exc:  # The upstream client exposes transport/parser exceptions directly.
        return _error(
            request_id if isinstance(request_id, str) else None,
            operation if isinstance(operation, str) else None,
            _upstream_error_code(exc),
        )


def main() -> int:
    try:
        raw = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError):
        raw = None
    print(json.dumps(execute(raw), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
