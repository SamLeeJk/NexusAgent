"""Small English-policy BM25 baseline; lexical matches are not confidence scores."""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "it",
        "its",
        "they",
        "their",
        "this",
        "that",
        "these",
        "those",
        "what",
        "which",
        "how",
        "when",
        "where",
        "why",
        "with",
        "for",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "and",
        "or",
        "as",
        "about",
        "please",
    ]
)


def tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"\b\w+\b", text.lower())
        if token not in STOP_WORDS
    ]


@lru_cache(maxsize=8)
def _index(policy_text: str):
    # Content keys also invalidate the index for same-size document edits.
    chunks = []
    corpus = []
    for line_number, line in enumerate(policy_text.splitlines(), start=1):
        text = line.strip()
        tokens = tokenize(text)
        if tokens:
            chunks.append({"line": line_number, "text": text})
            corpus.append(tokens)
    return chunks, corpus, BM25Okapi(corpus) if corpus else None


def search_policy(path: Path, query: str, top_k: int = 3) -> dict:
    result = {"query": query, "found": False, "best_chunk": None, "sources": []}
    try:
        policy_text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {**result, "reason": "policy_unavailable"}

    chunks, corpus, index = _index(policy_text)
    if index is None:
        return {**result, "reason": "empty_policy"}
    tokens = tokenize(query)
    if not tokens:
        return {**result, "reason": "no_match"}

    # Scores can be zero/negative in small corpora. Check overlap independently.
    candidates = [
        i for i, words in enumerate(corpus) if set(tokens).intersection(words)
    ]
    if not candidates or top_k < 1:
        return {**result, "reason": "no_match"}
    scores = index.get_scores(tokens)
    ranked = sorted(candidates, key=lambda i: (-float(scores[i]), i))[:top_k]
    version = hashlib.sha256(policy_text.encode("utf-8")).hexdigest()
    sources = [
        {
            "source": path.name,
            "version": version,
            "chunk_id": f"{path.name}:{version}:L{chunks[i]['line']}",
            "line": chunks[i]["line"],
            "text": chunks[i]["text"],
            "score": float(scores[i]),
        }
        for i in ranked
    ]
    return {
        **result,
        "found": True,
        "best_chunk": sources[0]["text"],
        "sources": sources,
    }
