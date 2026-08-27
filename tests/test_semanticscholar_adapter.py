from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "components" / "semanticscholar_search_v1"
SPEC = importlib.util.spec_from_file_location(
    "semanticscholar_adapter_under_test", COMPONENT / "semanticscholar_adapter.py"
)
assert SPEC is not None and SPEC.loader is not None
ADAPTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ADAPTER
SPEC.loader.exec_module(ADAPTER)
RESPONSE_VALIDATOR = Draft202012Validator(
    __import__("json").loads((COMPONENT / "contracts" / "response.schema.json").read_text())
)


def paper(identifier: str) -> SimpleNamespace:
    return SimpleNamespace(
        paperId=identifier,
        title=f"Title {identifier}",
        abstract="Fixture abstract",
        year=2026,
        authors=[SimpleNamespace(authorId="author-1", name="Ada Example")],
        externalIds={"DOI": f"10.1/{identifier}"},
        citationCount=7,
        url=f"https://example.test/{identifier}",
        venue="Fixture Venue",
        publicationDate=None,
        isOpenAccess=True,
        openAccessPdf={"url": "https://example.test/paper.pdf"},
    )


class Results:
    total = 4

    def __iter__(self):
        yield from [paper("one"), paper("two"), paper("three"), paper("four")]


class Client:
    def search_paper(self, query: str, *, fields: list[str], limit: int) -> Results:
        assert query == "fixture query"
        assert fields == ADAPTER.FIELDS
        assert limit == 2
        return Results()

    def get_paper(self, paper_id: str, *, fields: list[str]) -> SimpleNamespace:
        assert fields == ADAPTER.FIELDS
        return paper(paper_id)


def test_search_normalizes_and_bounds_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ADAPTER, "_client", Client)

    output = ADAPTER.execute(
        {
            "request_id": "search-1",
            "operation": "search",
            "query": "fixture query",
            "page_size": 2,
            "max_results": 3,
        }
    )

    RESPONSE_VALIDATOR.validate(output)
    assert output["status"] == "ok"
    assert output["returned_count"] == 3
    assert output["total"] == 4
    assert [item["paper_id"] for item in output["papers"]] == ["one", "two", "three"]


def test_get_paper_uses_fixed_output_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ADAPTER, "_client", Client)

    output = ADAPTER.execute(
        {"request_id": "paper-1", "operation": "get-paper", "paper_id": "doi-fixture"}
    )

    RESPONSE_VALIDATOR.validate(output)
    assert output["papers"][0]["paper_id"] == "doi-fixture"
    assert output["papers"][0]["authors"] == [{"author_id": "author-1", "name": "Ada Example"}]


@pytest.mark.parametrize(
    "request_value",
    [
        None,
        {},
        {"request_id": "x", "operation": "search", "query": ""},
        {"request_id": "x", "operation": "search", "query": "q", "limit": 3},
        {"request_id": "x", "operation": "search", "query": "q", "page_size": True},
        {"request_id": "x", "operation": "get-paper", "paper_id": "x", "query": "q"},
    ],
)
def test_invalid_requests_fail_as_structured_contract_errors(request_value: object) -> None:
    output = ADAPTER.execute(request_value)

    RESPONSE_VALIDATOR.validate(output)
    assert output["status"] == "error"
    assert output["error"] == {"code": "invalid-request"}
    assert output["papers"] == []


def test_custom_api_url_is_restricted_to_explicit_loopback_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLACKRIDGE_SEMANTICSCHOLAR_API_URL", "https://attacker.example")

    with pytest.raises(ADAPTER.ContractError, match="requires BLACKRIDGE_EVALUATION_MODE"):
        ADAPTER._evaluation_api_url()

    monkeypatch.setenv("BLACKRIDGE_EVALUATION_MODE", "1")
    with pytest.raises(ADAPTER.ContractError, match="IPv4 loopback"):
        ADAPTER._evaluation_api_url()

    monkeypatch.setenv("BLACKRIDGE_SEMANTICSCHOLAR_API_URL", "http://127.0.0.1:8765")
    assert ADAPTER._evaluation_api_url() == "http://127.0.0.1:8765"


def test_upstream_failures_do_not_leak_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenClient(Client):
        def get_paper(self, paper_id: str, *, fields: list[str]) -> SimpleNamespace:
            raise RuntimeError("secret upstream response")

    monkeypatch.setattr(ADAPTER, "_client", BrokenClient)
    output = ADAPTER.execute(
        {"request_id": "paper-error", "operation": "get-paper", "paper_id": "x"}
    )

    RESPONSE_VALIDATOR.validate(output)
    assert output["error"] == {"code": "upstream-failure"}
    assert "secret" not in str(output)


def test_wrapped_rate_limit_is_classified_without_exposing_details() -> None:
    class Attempt:
        @staticmethod
        def exception() -> Exception:
            return ConnectionRefusedError("HTTP 429 with private response")

    wrapped = RuntimeError("retry wrapper")
    wrapped.last_attempt = Attempt()

    assert ADAPTER._upstream_error_code(wrapped) == "rate-limited"
