"""Optional Qdrant-backed semantic retrieval with deterministic lexical fallback."""

import hashlib
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

from knowledge_retrieval import rank_chunks

RRF_K = 60
VECTOR_SIZE = 384
MODEL_REPOSITORY = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
SUPPORTED_MODES = {"lexical", "vector", "hybrid"}
SCOPE_CUES = (
    "refund",
    "reimburse",
    "money back",
    "reverse",
    "undo",
    "return",
    "approve",
    "approval",
    "authorize",
    "confirmation",
    "action card",
    "request",
    "claim",
    "record",
    "filed",
    "back out",
    "automated flow",
    "click",
    "action",
    "retry",
    "decline",
    "reject",
    "cash",
    "money",
    "funds",
    "bank",
    "processor",
    "reference number",
    "退",
    "撤",
    "申请",
    "办理",
    "授权",
    "确认",
    "卡片",
    "记录",
    "登记",
    "提交",
    "同意",
    "拒绝",
    "反悔",
    "资金",
    "银行",
    "到账",
    "受理编号",
    "操作",
    "办过",
    "重试",
)
QUERY_EXPANSIONS = (
    (("preparing", "packed", "not left", "仓库准备", "备货", "没寄出"), " processing 处理中 eligible refund"),
    (("dispatched", "courier", "on its way", "left the warehouse", "出库", "运输中", "在路上", "交给快递"), " shipped 已发货 manual support"),
    (("approve", "approval", "authorize", "time out", "点击", "授权", "卡片", "失效"), " confirmation explicit confirmation expires"),
    (("twice", "retry", "another case", "multiple", "两次", "重试", "已经办过", "几次"), " duplicate request existing one request"),
    (("cash", "bank", "funds", "money arrived", "reference number", "银行卡", "到账", "受理编号", "资金处理"), " payment gateway money returned"),
    (("decline", "reject", "stop the pending", "changed my mind", "拒绝", "终止", "不同意", "反悔"), " cancellation cancel without creating request"),
)


def _index_version(sources):
    return hashlib.sha256(
        "\n".join(
            sorted({s["version_id"] + ":" + s["content_hash"] for s in sources})
        ).encode()
    ).hexdigest()


def _semantic_queries(query):
    lowered = query.lower()
    additions = [text for phrases, text in QUERY_EXPANSIONS if any(p in lowered for p in phrases)]
    return [query, *additions]


def _in_scope(query):
    lowered = query.lower()
    return any(cue in lowered for cue in SCOPE_CUES)


class HybridRetriever:
    """Keeps PostgreSQL authoritative; Qdrant stores only searchable active chunks."""

    def __init__(self, config, encoder=None, client=None):
        if config.retrieval_mode not in SUPPORTED_MODES:
            raise ValueError("NEXUS_RETRIEVAL_MODE must be lexical, vector or hybrid")
        self.mode = config.retrieval_mode
        self.url = config.qdrant_url
        self.collection = config.qdrant_collection
        self.model_name = config.embedding_model
        self.model_revision = config.embedding_revision
        self.cache_dir = config.embedding_cache_dir or None
        self.threshold = config.vector_score_threshold
        self._encoder = encoder
        self._client = client
        self._lock = RLock()
        self._vector_cache = {}
        self._synced_index = ""

    def _dependencies(self):
        with self._lock:
            if self._encoder is None:
                from fastembed import TextEmbedding
                from huggingface_hub import snapshot_download

                model_path = snapshot_download(
                    repo_id=MODEL_REPOSITORY,
                    revision=self.model_revision,
                    cache_dir=self.cache_dir,
                )
                self._encoder = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=self.cache_dir,
                    specific_model_path=model_path,
                )
            if self._client is None:
                from qdrant_client import QdrantClient

                self._client = QdrantClient(url=self.url, timeout=3)
        return self._encoder, self._client

    def _vector_search(self, sources, query, limit):
        from qdrant_client.models import (
            Distance,
            FieldCondition,
            Filter,
            MatchValue,
            PointStruct,
            VectorParams,
        )

        if not _in_scope(query):
            return []
        encoder, client = self._dependencies()
        signature = _index_version(sources)
        rows = []
        for source in sources:
            heading_parts = [
                part.strip()
                for part in source["heading"].split(" / ")[-2:]
                if part.strip()
            ]
            text_parts = [
                line.strip() for line in source["text"].splitlines() if line.strip()
            ] or [source["text"]]
            segments = text_parts + [
                heading + ": " + line
                for heading in heading_parts
                for line in text_parts
            ]
            rows.extend(
                (source, index, segment)
                for index, segment in enumerate(dict.fromkeys(segments))
            )
        missing = [
            row
            for row in rows
            if (row[0]["chunk_id"], row[0]["content_hash"], row[1])
            not in self._vector_cache
        ]
        if missing:
            values = encoder.embed([row[2] for row in missing])
            for (source, index, _), value in zip(missing, values, strict=True):
                self._vector_cache[
                    (source["chunk_id"], source["content_hash"], index)
                ] = list(map(float, value))
        query_vectors = [
            list(map(float, value))
            for value in encoder.query_embed(_semantic_queries(query))
        ]
        if not client.collection_exists(self.collection):
            client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
            )
            # A remote reset can remove the collection while this process still
            # remembers the last synchronized corpus.
            self._synced_index = ""
        if signature != self._synced_index:
            client.upsert(
                collection_name=self.collection,
                wait=True,
                points=[
                    PointStruct(
                        id=str(
                            uuid5(
                                NAMESPACE_URL,
                                f"nexus-vector:{source['chunk_id']}:{index}",
                            )
                        ),
                        vector=self._vector_cache[
                            (source["chunk_id"], source["content_hash"], index)
                        ],
                        payload={
                            "chunk_id": source["chunk_id"],
                            "version_id": source["version_id"],
                            "content_hash": source["content_hash"],
                            "index_version": signature,
                        },
                    )
                    for source, index, _ in rows
                ],
            )
            self._synced_index = signature
        allowed = {s["chunk_id"]: s for s in sources}
        points = []
        for query_vector in query_vectors:
            response = client.query_points(
                collection_name=self.collection,
                query=query_vector,
                limit=max(limit * 4, len(rows)),
                with_payload=True,
                query_filter=Filter(
                    must=[
                        FieldCondition(
                            key="index_version", match=MatchValue(value=signature)
                        )
                    ]
                ),
            )
            points.extend(response.points)
        points.sort(key=lambda point: (-float(point.score), str(point.id)))
        ranked, seen = [], set()
        for point in points:
            chunk_id = str((point.payload or {}).get("chunk_id", point.id))
            if (
                chunk_id in allowed
                and chunk_id not in seen
                and float(point.score) >= self.threshold
            ):
                seen.add(chunk_id)
                ranked.append(
                    {
                        **allowed[chunk_id],
                        "vector_score": float(point.score),
                        "is_current": True,
                    }
                )
        return ranked[:limit]

    def search(self, sources, query, top_k=3):
        lexical = rank_chunks(sources, query, max(top_k, len(sources)))
        base = {
            "query": query,
            "index_version": _index_version(sources),
            "mode_requested": self.mode,
            "fallback": False,
            "fallback_reason": None,
        }
        if self.mode == "lexical":
            return {
                **lexical,
                **base,
                "mode_used": "lexical",
            }
        if not sources or not query.strip():
            return {
                **lexical,
                **base,
                "mode_used": self.mode,
                "algorithm": self.mode + "-rrf-v1",
            }
        try:
            with self._lock:
                vector = self._vector_search(
                    sources, query, max(top_k, len(sources))
                )
        except Exception:  # noqa: BLE001 -- search dependency cannot break support flow
            return {
                **lexical,
                **base,
                "mode_used": "lexical",
                "fallback": True,
                "fallback_reason": "vector_unavailable",
            }
        if self.mode == "vector":
            selected = vector[:top_k]
        else:
            by_id = {s["chunk_id"]: s for s in sources}
            scores = {}
            lexical_rank = {
                s["chunk_id"]: rank for rank, s in enumerate(lexical["sources"], 1)
            }
            vector_rank = {s["chunk_id"]: rank for rank, s in enumerate(vector, 1)}
            for chunk_id in lexical_rank.keys() | vector_rank.keys():
                scores[chunk_id] = (
                    (1 / (RRF_K + lexical_rank[chunk_id]))
                    if chunk_id in lexical_rank
                    else 0
                ) + (
                    (1 / (RRF_K + vector_rank[chunk_id]))
                    if chunk_id in vector_rank
                    else 0
                )
            selected = [
                {
                    **by_id[chunk_id],
                    "score": score,
                    "lexical_rank": lexical_rank.get(chunk_id),
                    "vector_rank": vector_rank.get(chunk_id),
                    "is_current": True,
                }
                for chunk_id, score in sorted(
                    scores.items(), key=lambda item: (-item[1], item[0])
                )[:top_k]
            ]
        return {
            **base,
            "found": bool(selected),
            "sources": selected,
            "reason": "matched" if selected else "no_match",
            "mode_used": self.mode,
            "algorithm": self.mode + "-rrf-v1",
        }
