"""Immutable policy versions, atomic publication and historical source access."""

import hashlib
import re
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from database import BusinessError, row, users
from knowledge_retrieval import chunk_markdown, rank_chunks
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)

from observability import observed

knowledge_metadata = MetaData()
documents = Table(
    "knowledge_documents",
    knowledge_metadata,
    Column("id", String(36), primary_key=True),
    Column("slug", String(80), nullable=False, unique=True),
    Column("active_version_id", String(36)),
    Column("created_at", Float, nullable=False),
)
revisions = Table(
    "knowledge_versions",
    knowledge_metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", ForeignKey("knowledge_documents.id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("title", String(200), nullable=False),
    Column("content", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("created_by", String(36), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("published_at", Float),
    UniqueConstraint("document_id", "content_hash"),
    UniqueConstraint("document_id", "number"),
)
chunks = Table(
    "knowledge_chunks",
    knowledge_metadata,
    Column("id", String(36), primary_key=True),
    Column("version_id", ForeignKey("knowledge_versions.id"), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("heading", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("start_line", Integer, nullable=False),
    Column("end_line", Integer, nullable=False),
    UniqueConstraint("version_id", "ordinal"),
)
publications = Table(
    "knowledge_publications",
    knowledge_metadata,
    Column("id", String(36), primary_key=True),
    Column("document_id", ForeignKey("knowledge_documents.id"), nullable=False),
    Column("previous_version_id", String(36)),
    Column("version_id", ForeignKey("knowledge_versions.id"), nullable=False),
    Column("actor_id", String(36), nullable=False),
    Column("created_at", Float, nullable=False),
)


class KnowledgeStore:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def require_admin(conn, uid):
        actor = row(conn, users, users.c.id == uid)
        if not actor or actor["role"] != "admin":
            raise BusinessError(403, "仅知识库管理员可以执行此操作。")

    def import_document(self, uid, slug, title, content):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug):
            raise BusinessError(422, "文档标识仅支持小写字母、数字和连字符。")
        title, content = (
            title.strip(),
            content.replace("\r\n", "\n").replace("\r", "\n").strip("\ufeff"),
        )
        if not title or len(title) > 200 or len(content) > 200000 or "\x00" in content:
            raise BusinessError(422, "标题或文档内容不符合限制。")
        pieces = chunk_markdown(content)
        if not pieces or len(pieces) > 500:
            raise BusinessError(422, "需要包含正文，且分块数不能超过 500。")
        digest = hashlib.sha256(
            ("markdown-v1\0" + title + "\0" + content).encode()
        ).hexdigest()
        with self.db.lock("knowledge-write"), self.db.engine.begin() as conn:
            self.require_admin(conn, uid)
            doc = row(conn, documents, documents.c.slug == slug)
            if not doc:
                doc = {
                    "id": str(uuid4()),
                    "slug": slug,
                    "active_version_id": None,
                    "created_at": time.time(),
                }
                conn.execute(documents.insert().values(**doc))
            existing = row(
                conn,
                revisions,
                (revisions.c.document_id == doc["id"])
                & (revisions.c.content_hash == digest),
            )
            if existing:
                return {
                    "document_id": doc["id"],
                    "version_id": existing["id"],
                    "number": existing["number"],
                    "deduplicated": True,
                }
            number = (
                len(
                    conn.execute(
                        select(revisions.c.id).where(
                            revisions.c.document_id == doc["id"]
                        )
                    ).all()
                )
                + 1
            )
            vid = str(uuid5(NAMESPACE_URL, "nexus-policy:" + doc["id"] + ":" + digest))
            conn.execute(
                revisions.insert().values(
                    id=vid,
                    document_id=doc["id"],
                    number=number,
                    title=title,
                    content=content,
                    content_hash=digest,
                    created_by=uid,
                    created_at=time.time(),
                )
            )
            conn.execute(
                chunks.insert(),
                [
                    dict(
                        piece,
                        id=str(uuid5(NAMESPACE_URL, vid + ":" + str(i))),
                        ordinal=i,
                        version_id=vid,
                    )
                    for i, piece in enumerate(pieces)
                ],
            )
            return {
                "document_id": doc["id"],
                "version_id": vid,
                "number": number,
                "deduplicated": False,
            }

    def list_documents(self, uid):
        with self.db.engine.connect() as conn:
            self.require_admin(conn, uid)
            result = []
            for value in conn.execute(
                select(documents).order_by(documents.c.created_at)
            ).mappings():
                doc = dict(value)
                doc["versions"] = [
                    dict(v)
                    for v in conn.execute(
                        select(
                            revisions.c.id,
                            revisions.c.title,
                            revisions.c.number,
                            revisions.c.content_hash,
                            revisions.c.created_at,
                            revisions.c.published_at,
                        )
                        .where(revisions.c.document_id == doc["id"])
                        .order_by(revisions.c.number.desc())
                    ).mappings()
                ]
                result.append(doc)
            return result

    def preview(self, uid, vid):
        with self.db.engine.connect() as conn:
            self.require_admin(conn, uid)
            version = row(conn, revisions, revisions.c.id == vid)
            if not version:
                raise BusinessError(404, "知识版本不存在。")
            version["chunks"] = [
                dict(c)
                for c in conn.execute(
                    select(chunks)
                    .where(chunks.c.version_id == vid)
                    .order_by(chunks.c.ordinal)
                ).mappings()
            ]
            return version

    def publish(self, uid, doc_id, vid, expected_active):
        with self.db.lock("knowledge-write"), self.db.engine.begin() as conn:
            self.require_admin(conn, uid)
            doc = row(conn, documents, documents.c.id == doc_id)
            version = row(
                conn,
                revisions,
                (revisions.c.id == vid) & (revisions.c.document_id == doc_id),
            )
            if not doc or not version:
                raise BusinessError(404, "文档或版本不存在。")
            if doc["active_version_id"] == vid:
                return {"active_version_id": vid}
            if doc["active_version_id"] != expected_active:
                raise BusinessError(409, "活动版本已更新，请刷新后重新核对。")
            if not version["published_at"]:
                conn.execute(
                    revisions.update()
                    .where(revisions.c.id == vid)
                    .values(published_at=time.time())
                )
            conn.execute(
                documents.update()
                .where(documents.c.id == doc_id)
                .values(active_version_id=vid)
            )
            conn.execute(
                publications.insert().values(
                    id=str(uuid4()),
                    document_id=doc_id,
                    previous_version_id=expected_active,
                    version_id=vid,
                    actor_id=uid,
                    created_at=time.time(),
                )
            )
            return {"active_version_id": vid}

    @staticmethod
    def source_query():
        return select(
            chunks.c.id.label("chunk_id"),
            chunks.c.heading,
            chunks.c.text,
            chunks.c.start_line,
            chunks.c.end_line,
            revisions.c.id.label("version_id"),
            revisions.c.number.label("version"),
            revisions.c.content_hash,
            revisions.c.title,
            revisions.c.published_at,
            documents.c.id.label("document_id"),
            documents.c.slug,
            documents.c.active_version_id,
        ).select_from(chunks.join(revisions).join(documents))

    @observed("db.knowledge.active_chunks", "database")
    def active_chunks(self):
        with self.db.engine.connect() as conn:
            # One statement takes a coherent view of publication pointers and content.
            return [
                dict(c)
                for c in conn.execute(
                    self.source_query()
                    .where(revisions.c.id == documents.c.active_version_id)
                    .order_by(documents.c.slug, chunks.c.ordinal)
                ).mappings()
            ]

    @observed("knowledge.search", "retrieval")
    def search(self, query, top_k=3):
        return rank_chunks(self.active_chunks(), query, top_k)

    def source(self, chunk_id):
        with self.db.engine.connect() as conn:
            source = (
                conn.execute(
                    self.source_query().where(
                        (chunks.c.id == chunk_id)
                        & revisions.c.published_at.is_not(None)
                    )
                )
                .mappings()
                .first()
            )
            if not source:
                raise BusinessError(404, "引用不存在或尚未发布。")
            return {
                **source,
                "is_current": source["active_version_id"] == source["version_id"],
            }

    def seed_demo(self):
        with self.db.engine.connect() as conn:
            if row(conn, documents, documents.c.slug == "refund-policy"):
                return
            actor = row(conn, users, users.c.id == "admin")
            if not actor or actor["role"] != "admin":
                return
        content = (
            Path(__file__).parent / "data" / "knowledge" / "support_policy.md"
        ).read_text(encoding="utf-8")
        created = self.import_document(
            "admin", "refund-policy", "售后政策 / Support policy", content
        )
        self.publish("admin", created["document_id"], created["version_id"], None)
