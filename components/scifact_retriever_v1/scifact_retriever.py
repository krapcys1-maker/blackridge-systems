#!/usr/bin/env python3
"""Deterministic BM25 retrieval over a separately locked JSONL abstract corpus."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "for",
        "from",
        "had",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "than",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    ]
)


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in _TOKEN.findall(value.casefold())
        if len(token) > 1 and token not in _STOPWORDS
    ]


def _load_corpus(path: Path) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen: set[int] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"corpus line {line_number} is not an object")
            document_id = value.get("doc_id")
            title = value.get("title")
            abstract = value.get("abstract")
            if (
                not isinstance(document_id, int)
                or isinstance(document_id, bool)
                or document_id in seen
                or not isinstance(title, str)
                or not title
                or not isinstance(abstract, list)
                or not all(isinstance(sentence, str) and sentence for sentence in abstract)
            ):
                raise ValueError(f"corpus line {line_number} is invalid")
            seen.add(document_id)
            documents.append(value)
    if not documents:
        raise ValueError("corpus is empty")
    return documents


def _sentence_candidates(
    claim_terms: list[str], document: dict[str, Any], inverse_frequency: dict[str, float]
) -> list[dict[str, Any]]:
    claim_counts = Counter(claim_terms)
    claim_norm = math.sqrt(
        sum((count * inverse_frequency.get(term, 0.0)) ** 2 for term, count in claim_counts.items())
    )
    scored: list[tuple[float, int, str]] = []
    for index, sentence in enumerate(document["abstract"]):
        sentence_counts = Counter(_tokens(sentence))
        dot = sum(
            claim_counts[term] * sentence_counts[term] * inverse_frequency.get(term, 0.0) ** 2
            for term in claim_counts
        )
        sentence_norm = math.sqrt(
            sum(
                (count * inverse_frequency.get(term, 0.0)) ** 2
                for term, count in sentence_counts.items()
            )
        )
        score = dot / (claim_norm * sentence_norm) if claim_norm and sentence_norm else 0.0
        scored.append((score, index, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"sentence_index": index, "quote": sentence, "score": round(score, 8)}
        for score, index, sentence in scored[:8]
        if score > 0
    ]


class Bm25Index:
    """Immutable in-memory index reusable across requests in a long-lived process."""

    def __init__(self, corpus_path: Path) -> None:
        self._documents = _load_corpus(corpus_path)
        self._term_frequencies: list[Counter[str]] = []
        document_frequency: Counter[str] = Counter()
        self._lengths: list[int] = []
        for document in self._documents:
            counts = Counter(_tokens(" ".join(document["abstract"])))
            self._term_frequencies.append(counts)
            document_frequency.update(counts)
            self._lengths.append(sum(counts.values()))
        document_count = len(self._documents)
        self._average_length = sum(self._lengths) / document_count
        self._inverse_frequency = {
            term: math.log(1 + (document_count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def retrieve(self, request: dict[str, Any]) -> dict[str, Any]:
        claim = request["claim"]
        maximum_candidates = request["maximum_candidates"]
        claim_terms = _tokens(claim)
        if not claim_terms:
            raise ValueError("claim has no searchable terms")

        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for document, frequencies, length in zip(
            self._documents, self._term_frequencies, self._lengths, strict=True
        ):
            score = 0.0
            for term in claim_terms:
                frequency = frequencies[term]
                if not frequency:
                    continue
                numerator = frequency * 2.2
                denominator = frequency + 1.2 * (
                    0.25 + 0.75 * length / self._average_length
                )
                score += self._inverse_frequency.get(term, 0.0) * numerator / denominator
            ranked.append((score, document["doc_id"], document))
        ranked.sort(key=lambda item: (-item[0], item[1]))

        candidates = []
        for score, document_id, document in ranked[:maximum_candidates]:
            candidates.append(
                {
                    "document_id": str(document_id),
                    "title": document["title"],
                    "abstract": document["abstract"],
                    "retrieval_score": round(score, 8),
                    "sentence_candidates": _sentence_candidates(
                        claim_terms, document, self._inverse_frequency
                    ),
                }
            )
        return {
            "schema_version": "1",
            "request_id": request["request_id"],
            "claim": claim,
            "candidates": candidates,
        }


def retrieve(request: dict[str, Any], corpus_path: Path) -> dict[str, Any]:
    return Bm25Index(corpus_path).retrieve(request)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: scifact_retriever.py CORPUS.jsonl")
    request = json.load(sys.stdin)
    print(json.dumps(retrieve(request, Path(sys.argv[1])), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
