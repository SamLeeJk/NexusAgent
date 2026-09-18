"""Deterministic Markdown chunks and bilingual lexical ranking, not semantic search."""

import hashlib
import re
from functools import lru_cache

from rank_bm25 import BM25Okapi
from retrieval import STOP_WORDS

ALGORITHM = "bilingual-bm25-coverage-v1"
MIN_COVERAGE = 0.6
CHINESE_FILLERS = (
    "请问",
    "一下",
    "如何",
    "怎么",
    "是否",
    "可以",
    "什么",
    "政策",
    "规定",
    "规则",
    "请",
    "的",
    "吗",
    "呢",
    "是",
    "了",
)
ENGLISH_FILLERS = STOP_WORDS | {
    "policy",
    "policies",
    "rules",
    "tell",
    "want",
    "know",
    "via",
}


def tokenize_knowledge(value):
    value = value.lower()
    words = re.findall(r"[a-z0-9]+", value)
    words = [
        w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w
        for w in words
        if w not in ENGLISH_FILLERS
    ]
    for phrase in CHINESE_FILLERS:
        value = value.replace(phrase, "")
    for run in re.findall(r"[\u4e00-\u9fff]+", value):
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
    return words


def chunk_markdown(content, max_chars=800):
    """Keep heading paths and exact source line ranges; long lines split locally."""
    result, block, headings = [], [], []

    def flush():
        if not block:
            return
        result.append(
            {
                "heading": " / ".join(h[1] for h in headings),
                "text": "\n".join(t for _, t in block),
                "start_line": block[0][0],
                "end_line": block[-1][0],
            }
        )
        block.clear()

    in_fence = False
    for number, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped) if not in_fence else None
        if heading:
            flush()
            level = len(heading[1])
            headings = [h for h in headings if h[0] < level] + [(level, heading[2])]
        elif not stripped:
            flush()
        else:
            if re.match(r"^([-*+] |\d+\. )", stripped) and not in_fence:
                flush()
            for start in range(0, len(line), max_chars):
                part = line[start : start + max_chars]
                if sum(len(t) + 1 for _, t in block) + len(part) > max_chars:
                    flush()
                block.append((number, part))
    flush()
    return [c for c in result if tokenize_knowledge(c["text"])]


@lru_cache(maxsize=8)
def _index(entries):
    tokens = [tokenize_knowledge(heading + " " + body) for _, heading, body in entries]
    return tokens, BM25Okapi(tokens) if any(tokens) else None


def rank_chunks(sources, query, top_k=3):
    signature = hashlib.sha256(
        "\n".join(
            sorted({s["version_id"] + ":" + s["content_hash"] for s in sources})
        ).encode()
    ).hexdigest()
    response = {
        "query": query,
        "found": False,
        "sources": [],
        "index_version": signature,
        "algorithm": ALGORITHM,
        "reason": "no_match",
    }
    tokens = set(tokenize_knowledge(query))
    if not sources or not tokens:
        return response
    entries = tuple((s["chunk_id"], s["heading"], s["text"]) for s in sources)
    corpus, index = _index(entries)
    if index is None:
        return response
    scores = index.get_scores(sorted(tokens))
    candidates = []
    for i, terms in enumerate(corpus):
        coverage = len(tokens.intersection(terms)) / len(tokens)
        if coverage >= MIN_COVERAGE:
            candidates.append((i, coverage))
    candidates.sort(
        key=lambda item: (
            -item[1],
            -float(scores[item[0]]),
            sources[item[0]]["chunk_id"],
        )
    )
    selected = [
        {
            **sources[i],
            "score": float(scores[i]),
            "coverage": coverage,
            "is_current": True,
        }
        for i, coverage in candidates[:top_k]
    ]
    return {
        **response,
        "found": bool(selected),
        "sources": selected,
        "reason": "matched" if selected else "no_match",
    }
