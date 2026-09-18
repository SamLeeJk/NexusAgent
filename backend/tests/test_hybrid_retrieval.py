from math import sqrt
from types import SimpleNamespace
from uuid import uuid4

from hybrid_retrieval import HybridRetriever


def config(mode="hybrid", threshold=0.45):
    return SimpleNamespace(
        retrieval_mode=mode,
        qdrant_url="http://unused",
        qdrant_collection="test",
        embedding_model="test-model",
        embedding_revision="test-revision",
        embedding_cache_dir=None,
        vector_score_threshold=threshold,
    )


def vector(value):
    return [value[0], value[1], value[2]] + [0.0] * 381


class FakeEncoder:
    def __init__(self):
        self.document_calls = 0

    @staticmethod
    def encode(text):
        text = text.lower()
        if "15 minutes" in text or "approval window" in text:
            return vector((1.0, 0.0, 0.0))
        if "one request" in text or "sent twice" in text:
            return vector((0.0, 1.0, 0.0))
        return vector((0.0, 0.0, 1.0))

    def embed(self, documents):
        self.document_calls += 1
        return iter([self.encode(document) for document in documents])

    def query_embed(self, queries):
        return iter([self.encode(query) for query in queries])


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.points = {}
        self.upsert_calls = 0
        self.created = False

    def collection_exists(self, name):
        if self.fail:
            raise ConnectionError("qdrant unavailable")
        return self.created

    def create_collection(self, **kwargs):
        self.created = True

    def upsert(self, points, **kwargs):
        self.upsert_calls += 1
        for point in points:
            self.points[str(point.id)] = point

    def query_points(self, query, limit, **kwargs):
        def cosine(left, right):
            denominator = sqrt(sum(v * v for v in left)) * sqrt(
                sum(v * v for v in right)
            )
            return sum(a * b for a, b in zip(left, right, strict=True)) / denominator

        ranked = sorted(
            (
                SimpleNamespace(
                    id=point.id,
                    payload=point.payload,
                    score=cosine(query, point.vector),
                )
                for point in self.points.values()
            ),
            key=lambda point: -point.score,
        )
        return SimpleNamespace(points=ranked[:limit])


def source(heading, text, digest="hash"):
    return {
        "chunk_id": str(uuid4()),
        "version_id": str(uuid4()),
        "content_hash": digest,
        "heading": heading,
        "text": text,
        "title": "Policy",
        "version": 1,
        "start_line": 1,
        "end_line": 2,
    }


def corpus():
    return [
        source("confirmation", "Confirmation expires after 15 minutes."),
        source("duplicates", "Only one request per order is allowed."),
    ]


def test_hybrid_recovers_semantic_match_and_reports_mode():
    result = HybridRetriever(config(), FakeEncoder(), FakeClient()).search(
        corpus(), "How long is the approval window?"
    )
    assert result["found"]
    assert result["sources"][0]["heading"] == "confirmation"
    assert result["mode_requested"] == result["mode_used"] == "hybrid"
    assert result["fallback"] is False


def test_vector_failure_falls_back_without_exposing_exception():
    result = HybridRetriever(config(), FakeEncoder(), FakeClient(fail=True)).search(
        corpus(), "confirmation expires"
    )
    assert result["found"]
    assert result["mode_requested"] == "hybrid"
    assert result["mode_used"] == "lexical"
    assert result["fallback"] is True
    assert result["fallback_reason"] == "vector_unavailable"


def test_index_sync_is_idempotent_and_new_active_set_excludes_old_chunk():
    encoder, client = FakeEncoder(), FakeClient()
    retriever = HybridRetriever(config(), encoder, client)
    first = corpus()
    retriever.search(first, "approval window")
    retriever.search(first, "approval window")
    assert client.upsert_calls == 1
    assert encoder.document_calls == 1

    replacement = [source("payment", "A submitted request does not move money.", "new")]
    result = retriever.search(replacement, "approval window")
    assert client.upsert_calls == 2
    assert all(item["chunk_id"] == replacement[0]["chunk_id"] for item in result["sources"])


def test_lost_remote_collection_is_rebuilt_from_authoritative_sources():
    encoder, client = FakeEncoder(), FakeClient()
    retriever = HybridRetriever(config(), encoder, client)
    sources = corpus()
    retriever.search(sources, "approval window")
    client.created = False
    client.points.clear()

    result = retriever.search(sources, "approval window")
    assert client.upsert_calls == 2
    assert result["found"]


def test_vector_threshold_rejects_unrelated_question():
    result = HybridRetriever(
        config(mode="vector", threshold=0.8), FakeEncoder(), FakeClient()
    ).search(corpus(), "Where is my parcel?")
    assert result["found"] is False
    assert result["sources"] == []
