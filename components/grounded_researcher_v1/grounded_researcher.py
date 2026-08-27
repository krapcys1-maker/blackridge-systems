#!/usr/bin/env python3
"""Offline, extractive research synthesis with grounded citations."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)?")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_STOPWORDS = frozenset(
    """
    a across after all an and are as at be been before being by can could did do does
    during every for from had has have how if in into is it its may might must no not of
    on only or other our same should than that the their then there these they this those
    through to under was we were what when where which who why will with would you your
    """.split()  # noqa: SIM905 - compact, reviewable fixed vocabulary
)


@dataclass(frozen=True)
class Document:
    document_id: str
    title: str
    full_text: str


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in _TOKEN.findall(text.casefold())
        if token not in _STOPWORDS and len(token) > 2
    ]


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text.strip()) if part.strip()]


def _idf(documents: list[Document]) -> dict[str, float]:
    frequencies: Counter[str] = Counter()
    for document in documents:
        frequencies.update(set(_tokens(f"{document.title} {document.full_text}")))
    count = len(documents)
    return {
        token: math.log((count + 1) / (frequency + 0.5)) + 1
        for token, frequency in frequencies.items()
    }


def _vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    # Binary term frequency prevents keyword repetition from gaming relevance.
    return {token: idf.get(token, 1.0) for token in set(tokens)}


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    overlap = left.keys() & right.keys()
    numerator = sum(left[token] * right[token] for token in overlap)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _query_aligned_group(
    documents: list[Document],
    document_tokens: dict[str, list[str]],
    direct: dict[str, float],
    minimum_sources: int,
) -> list[Document]:
    """Choose a sufficiently large lexical community that the query actually reaches."""

    token_sets = {identifier: set(tokens) for identifier, tokens in document_tokens.items()}
    remaining = {document.document_id for document in documents}
    groups: list[set[str]] = []
    while remaining:
        pending = [remaining.pop()]
        group: set[str] = set()
        while pending:
            identifier = pending.pop()
            if identifier in group:
                continue
            group.add(identifier)
            neighbors = {other for other in remaining if token_sets[identifier] & token_sets[other]}
            remaining.difference_update(neighbors)
            pending.extend(neighbors)
        groups.append(group)
    eligible = [
        group
        for group in groups
        if len(group) >= minimum_sources and any(direct[item] > 0 for item in group)
    ]
    if not eligible:
        return []
    chosen = max(eligible, key=lambda group: (sum(direct[item] for item in group), len(group)))
    return [document for document in documents if document.document_id in chosen]


def _rank_documents(
    question: str, documents: list[Document], minimum_sources: int
) -> tuple[list[Document], dict[str, float], list[str]]:
    idf = _idf(documents)
    vectors = {
        document.document_id: _vector(_tokens(f"{document.title} {document.full_text}"), idf)
        for document in documents
    }
    question_vector = _vector(_tokens(question), idf)
    direct = {
        document.document_id: _cosine(question_vector, vectors[document.document_id])
        for document in documents
    }
    document_tokens = {
        document.document_id: _tokens(f"{document.title} {document.full_text}")
        for document in documents
    }
    aligned = _query_aligned_group(documents, document_tokens, direct, minimum_sources)
    if not aligned:
        return [], {}, []
    aligned_ids = {document.document_id for document in aligned}
    seed_ids = {document.document_id for document in documents if direct[document.document_id] > 0}
    feedback_frequency: Counter[str] = Counter(
        token for identifier in seed_ids for token in set(document_tokens[identifier])
    )
    feedback_weight = {
        token: sum(
            direct[identifier] for identifier in seed_ids if token in document_tokens[identifier]
        )
        * idf.get(token, 1.0)
        for token, frequency in feedback_frequency.items()
        if frequency >= 2 and token not in question_vector
    }
    feedback_terms = sorted(
        feedback_weight, key=lambda token: (feedback_weight[token], token), reverse=True
    )[:20]
    total_feedback_weight = sum(feedback_weight[token] for token in feedback_terms)

    scores: dict[str, float] = {}
    neighbor_count = max(1, min(minimum_sources - 1, len(documents) - 1))
    for document in documents:
        identifier = document.document_id
        other_similarities = sorted(
            (
                _cosine(vectors[identifier], vectors[other.document_id])
                for other in documents
                if other.document_id != identifier
            ),
            reverse=True,
        )
        centrality = _mean(other_similarities[:neighbor_count])
        feedback_coverage = (
            sum(
                feedback_weight[token]
                for token in feedback_terms
                if token in document_tokens[identifier]
            )
            / total_feedback_weight
            if total_feedback_weight
            else 0.0
        )
        query_hits = [token for token in document_tokens[identifier] if token in question_vector]
        repetition_penalty = 1.0
        if len(query_hits) >= 4:
            repetition_penalty = (len(set(query_hits)) / len(query_hits)) ** 2
        scores[identifier] = repetition_penalty * (
            0.35 * direct[identifier] + 0.50 * feedback_coverage + 0.15 * centrality
        )

    ranked = sorted(aligned, key=lambda item: scores[item.document_id], reverse=True)
    selected = ranked[:minimum_sources]
    if not seed_ids & aligned_ids or max(direct.values(), default=0.0) == 0:
        return [], scores, feedback_terms
    if _mean([scores[item.document_id] for item in selected]) <= 0:
        return [], scores, feedback_terms
    return selected, scores, feedback_terms


def _best_sentence(
    document: Document,
    question: str,
    feedback_terms: list[str],
    idf: dict[str, float],
) -> str | None:
    target = _vector([*_tokens(question), *feedback_terms], idf)
    candidates = _sentences(document.full_text)
    if not candidates:
        return None
    scored = [
        (_cosine(target, _vector(_tokens(sentence), idf)), index, sentence)
        for index, sentence in enumerate(candidates)
    ]
    return max(scored, key=lambda item: (item[0], -item[1]))[2]


def _abstention(request_id: str, reason: str) -> dict[str, object]:
    return {
        "schema_version": "1",
        "request_id": request_id,
        "status": "insufficient-evidence",
        "answer": reason,
        "claims": [],
        "sources": [],
    }


def synthesize(request: dict[str, object]) -> dict[str, object]:
    request_id = request.get("request_id")
    question = request.get("question")
    minimum_sources = request.get("minimum_sources")
    raw_documents = request.get("documents")
    if (
        not isinstance(request_id, str)
        or not isinstance(question, str)
        or not isinstance(minimum_sources, int)
        or minimum_sources < 1
        or not isinstance(raw_documents, list)
    ):
        return _abstention(
            request_id if isinstance(request_id, str) else "unknown",
            "The request does not satisfy the research-input contract.",
        )

    documents: list[Document] = []
    for item in raw_documents:
        if not isinstance(item, dict):
            continue
        identifier = item.get("document_id")
        title = item.get("title")
        text = item.get("full_text")
        if isinstance(identifier, str) and isinstance(title, str) and isinstance(text, str):
            documents.append(Document(identifier, title, text))
    if len(documents) < minimum_sources:
        return _abstention(
            request_id, "The corpus has fewer documents than the required source count."
        )

    selected, _, feedback_terms = _rank_documents(question, documents, minimum_sources)
    if len(selected) < minimum_sources:
        return _abstention(request_id, "The corpus does not contain enough relevant evidence.")

    idf = _idf(selected)
    claims: list[dict[str, object]] = []
    sources: list[dict[str, str]] = []
    for document in selected:
        sentence = _best_sentence(document, question, feedback_terms, idf)
        if sentence is None or sentence not in document.full_text:
            continue
        claims.append(
            {
                "text": sentence,
                "citations": [{"document_id": document.document_id, "quote": sentence}],
            }
        )
        sources.append({"document_id": document.document_id, "title": document.title})
    if len(sources) < minimum_sources:
        return _abstention(request_id, "The selected sources did not yield enough grounded claims.")

    answer = " ".join(str(claim["text"]) for claim in claims)
    if len(answer) > 2400:
        answer = answer[:2400].rsplit(" ", 1)[0] + "…"
    return {
        "schema_version": "1",
        "request_id": request_id,
        "status": "answered",
        "answer": answer,
        "claims": claims,
        "sources": sources,
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(json.dumps(_abstention("unknown", f"Invalid input JSON: {exc}")))
        return 0
    if not isinstance(request, dict):
        print(json.dumps(_abstention("unknown", "Input must be one JSON object.")))
        return 0
    print(json.dumps(synthesize(request), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
