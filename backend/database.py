"""Persistent business state. PostgreSQL is the deployment backend; SQLite is local-only."""

import hashlib
import hmac
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from filelock import FileLock, Timeout
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
    create_engine,
    event,
    select,
    text,
)

metadata = MetaData()
versions = Table(
    "schema_versions", metadata, Column("version", Integer, primary_key=True)
)
users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(80), unique=True, nullable=False),
    Column("password_hash", String(200), nullable=False),
)
sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("expires_at", Float, nullable=False),
)
orders = Table(
    "orders",
    metadata,
    Column("id", String(80), primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("status", String(32), nullable=False),
)
conversations = Table(
    "conversations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("created_at", Float, nullable=False),
)
turns = Table(
    "turns",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("request_id", String(36), nullable=False),
    Column("content", Text, nullable=False),
    Column("status", String(24), nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("conversation_id", "request_id"),
)
messages = Table(
    "messages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("turn_id", ForeignKey("turns.id"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("role", String(16), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("turn_id", "kind"),
)
proposals = Table(
    "proposals",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("turn_id", ForeignKey("turns.id"), unique=True, nullable=False),
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("order_id", ForeignKey("orders.id"), nullable=False),
    Column("action", String(32), nullable=False),
    Column("status", String(24), nullable=False),
    Column("decision", String(16)),
    Column("expires_at", Float, nullable=False),
    Column("result", Text),
    Column("created_at", Float, nullable=False),
)
refunds = Table(
    "refund_requests",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("order_id", ForeignKey("orders.id"), unique=True, nullable=False),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("proposal_id", ForeignKey("proposals.id"), unique=True, nullable=False),
    Column("status", String(24), nullable=False),
    Column("created_at", Float, nullable=False),
)


class BusinessError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), 310000
    )
    return f"{salt}${digest.hex()}"


def row(conn, table, condition):
    value = conn.execute(select(table).where(condition)).mappings().first()
    return dict(value) if value else None


class Database:
    def __init__(self, url: str):
        self.url = url
        self.sqlite = url.startswith("sqlite")
        if self.sqlite:
            location = url.removeprefix("sqlite:///")
            if location == ":memory:":
                raise ValueError("Use a file-backed database for durable checkpoints")
            self.path = Path(location).resolve()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(
                url, connect_args={"check_same_thread": False, "timeout": 30}
            )

            @event.listens_for(self.engine, "connect")
            def sqlite_options(connection, record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
        else:
            if not url.startswith("postgresql+psycopg://"):
                raise ValueError(
                    "DATABASE_URL must use postgresql+psycopg:// or sqlite:///"
                )
            self.engine = create_engine(url, pool_pre_ping=True)

    @contextmanager
    def lock(self, key: str):
        """Cross-process, crash-released lock held for the whole graph invocation."""
        lock_id = hashlib.sha256(key.encode()).hexdigest()
        if self.sqlite:
            try:
                with FileLock(str(self.path) + f".{lock_id}.lock", timeout=0):
                    yield
            except Timeout:
                raise BusinessError(409, "该会话正在处理请求，请稍后重试。") from None
        else:
            number = int(lock_id[:15], 16)
            with self.engine.connect() as conn:
                acquired = conn.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": number}
                ).scalar()
                conn.commit()
                if not acquired:
                    raise BusinessError(409, "该会话正在处理请求，请稍后重试。")
                try:
                    yield
                finally:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": number}
                    )
                    conn.commit()

    def migrate(self):
        with self.lock("schema-migration"), self.engine.begin() as conn:
            # Migration 001: initial schema. Later schema changes require a new revision.
            metadata.create_all(conn)
            if not row(conn, versions, versions.c.version == 1):
                conn.execute(versions.insert().values(version=1))

    def seed_demo(self):
        with self.lock("seed-demo"), self.engine.begin() as conn:
            for username in ("alice", "bob"):
                if not row(conn, users, users.c.username == username):
                    conn.execute(
                        users.insert().values(
                            id=username,
                            username=username,
                            password_hash=password_hash(f"demo-{username}-123"),
                        )
                    )
            for oid, uid, status in (
                ("O1001", "alice", "shipped"),
                ("O1002", "alice", "processing"),
                ("O2001", "bob", "processing"),
            ):
                if not row(conn, orders, orders.c.id == oid):
                    conn.execute(
                        orders.insert().values(id=oid, user_id=uid, status=status)
                    )

    def login(self, username, password):
        with self.engine.begin() as conn:
            user = row(conn, users, users.c.username == username)
            stored = (
                user["password_hash"] if user else password_hash("unknown", "00" * 16)
            )
            valid = hmac.compare_digest(
                stored, password_hash(password, stored.split("$")[0])
            )
            if not user or not valid:
                raise BusinessError(401, "用户名或密码不正确。")
            token = secrets.token_urlsafe(32)
            conn.execute(
                sessions.insert().values(
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    user_id=user["id"],
                    expires_at=time.time() + 86400,
                )
            )
            return token, {"id": user["id"], "username": user["username"]}

    def authenticate(self, token):
        with self.engine.connect() as conn:
            session = row(
                conn,
                sessions,
                sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest(),
            )
            if not session or session["expires_at"] <= time.time():
                raise BusinessError(401, "请先登录。")
            user = row(conn, users, users.c.id == session["user_id"])
            return {"id": user["id"], "username": user["username"]}

    def logout(self, token):
        with self.engine.begin() as conn:
            conn.execute(
                sessions.delete().where(
                    sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()
                )
            )

    def own_conversation(self, uid, cid):
        with self.engine.connect() as conn:
            value = row(
                conn,
                conversations,
                (conversations.c.id == cid) & (conversations.c.user_id == uid),
            )
            if not value:
                raise BusinessError(404, "会话不存在。")
            return value

    def new_conversation(self, uid):
        value = {"id": str(uuid4()), "user_id": uid, "created_at": time.time()}
        with self.engine.begin() as conn:
            conn.execute(conversations.insert().values(**value))
        return value

    def list_conversations(self, uid):
        with self.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(conversations)
                    .where(conversations.c.user_id == uid)
                    .order_by(conversations.c.created_at.desc())
                ).mappings()
            ]

    def get_order(self, uid, oid):
        with self.engine.connect() as conn:
            return row(conn, orders, (orders.c.id == oid) & (orders.c.user_id == uid))

    def list_orders(self, uid):
        with self.engine.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(orders).where(orders.c.user_id == uid).order_by(orders.c.id)
                ).mappings()
            ]

    def snapshot(self, uid, cid):
        self.own_conversation(uid, cid)
        with self.engine.connect() as conn:
            result = {"id": cid}
            for name, table in (
                ("messages", messages),
                ("proposals", proposals),
                ("turns", turns),
            ):
                result[name] = [
                    dict(r)
                    for r in conn.execute(
                        select(table)
                        .where(table.c.conversation_id == cid)
                        .order_by(table.c.created_at, table.c.id)
                    ).mappings()
                ]
            result["refunds"] = [
                dict(r)
                for r in conn.execute(
                    select(refunds)
                    .join(proposals, refunds.c.proposal_id == proposals.c.id)
                    .where(proposals.c.conversation_id == cid)
                ).mappings()
            ]
            return result

    @staticmethod
    def message(conn, cid, tid, kind, content):
        if not row(
            conn, messages, (messages.c.turn_id == tid) & (messages.c.kind == kind)
        ):
            conn.execute(
                messages.insert().values(
                    id=str(uuid4()),
                    conversation_id=cid,
                    turn_id=tid,
                    kind=kind,
                    role="user" if kind == "user" else "assistant",
                    content=content,
                    created_at=time.time(),
                )
            )

    def begin_turn(self, uid, cid, request_id, content):
        self.own_conversation(uid, cid)
        with self.engine.begin() as conn:
            existing = row(
                conn,
                turns,
                (turns.c.conversation_id == cid) & (turns.c.request_id == request_id),
            )
            if existing:
                if existing["content"] != content:
                    raise BusinessError(409, "同一请求编号不能用于不同消息。")
                return existing
            active = row(
                conn,
                turns,
                (turns.c.conversation_id == cid) & (turns.c.status != "completed"),
            )
            if active:
                raise BusinessError(409, "请先完成待确认操作，或重试尚未完成的消息。")
            value = {
                "id": str(uuid4()),
                "conversation_id": cid,
                "request_id": request_id,
                "content": content,
                "status": "running",
                "created_at": time.time(),
            }
            conn.execute(turns.insert().values(**value))
            self.message(conn, cid, value["id"], "user", content)
            return value

    def finish_turn(self, cid, tid, reply, waiting=False, decision=False):
        with self.engine.begin() as conn:
            conn.execute(
                turns.update()
                .where(turns.c.id == tid)
                .values(status="waiting" if waiting else "completed")
            )
            self.message(conn, cid, tid, "decision" if decision else "reply", reply)

    def propose(self, uid, cid, tid, oid, ttl):
        pid = str(uuid5(NAMESPACE_URL, "nexus-refund:" + tid))
        with self.engine.begin() as conn:
            existing = row(conn, proposals, proposals.c.id == pid)
            if existing:
                return existing
            order = row(conn, orders, (orders.c.id == oid) & (orders.c.user_id == uid))
            if not order or order["status"] != "processing":
                raise BusinessError(409, "订单当前不能申请退款。")
            value = {
                "id": pid,
                "turn_id": tid,
                "conversation_id": cid,
                "user_id": uid,
                "order_id": oid,
                "action": "request_refund",
                "status": "pending",
                "expires_at": time.time() + ttl,
                "created_at": time.time(),
            }
            conn.execute(proposals.insert().values(**value))
            return value

    def get_proposal(self, uid, cid, pid):
        with self.engine.connect() as conn:
            value = row(
                conn,
                proposals,
                (proposals.c.id == pid)
                & (proposals.c.user_id == uid)
                & (proposals.c.conversation_id == cid),
            )
            if not value:
                raise BusinessError(404, "操作提案不存在。")
            return value

    def decide(self, uid, cid, pid, decision):
        self.get_proposal(uid, cid, pid)
        with self.engine.begin() as conn:
            value = row(conn, proposals, proposals.c.id == pid)
            if value["decision"] and value["decision"] != decision:
                raise BusinessError(409, "该提案已作出不同决定。")
            if value["decision"]:
                return value
            status = "approved" if decision == "approve" else "rejected"
            if value["expires_at"] <= time.time():
                status = "expired"
            conn.execute(
                proposals.update()
                .where(proposals.c.id == pid)
                .values(status=status, decision=decision)
            )
        return self.get_proposal(uid, cid, pid)

    def execute(self, uid, cid, pid):
        proposal = self.get_proposal(uid, cid, pid)
        with self.lock("order:" + proposal["order_id"]):
            return self._execute(uid, cid, pid)

    def _execute(self, uid, cid, pid):
        """Authoritative approval + row lock + unique order + receipt in one transaction."""
        self.get_proposal(uid, cid, pid)
        with self.engine.begin() as conn:
            proposal = row(conn, proposals, proposals.c.id == pid)
            if proposal["result"]:
                return proposal["result"]
            status = proposal["status"]
            if status == "pending":
                raise BusinessError(409, "尚未收到用户确认。")
            order = (
                conn.execute(
                    select(orders)
                    .where(orders.c.id == proposal["order_id"])
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if status == "rejected":
                reply = "已取消本次申请，没有创建退款申请。"
            elif status == "expired" or proposal["expires_at"] <= time.time():
                status, reply = "expired", "确认已过期，没有创建退款申请。请重新发起。"
            elif (
                not order or order["user_id"] != uid or order["status"] != "processing"
            ):
                status, reply = (
                    "invalidated",
                    "订单状态或归属已变化，没有创建退款申请。",
                )
            elif (
                status != "approved"
                or proposal["decision"] != "approve"
                or proposal["action"] != "request_refund"
            ):
                raise BusinessError(409, "无有效批准，不能执行该操作。")
            else:
                receipt = row(conn, refunds, refunds.c.order_id == order["id"])
                if not receipt:
                    receipt = {
                        "id": str(uuid4()),
                        "order_id": order["id"],
                        "user_id": uid,
                        "proposal_id": pid,
                        "status": "requested",
                        "created_at": time.time(),
                    }
                    conn.execute(refunds.insert().values(**receipt))
                status = "completed"
                reply = f"订单 {order['id']} 的退款申请已登记，申请编号 {receipt['id']}。这表示申请已提交，并非款项已退回。"
            conn.execute(
                proposals.update()
                .where(proposals.c.id == pid)
                .values(status=status, result=reply)
            )
            return reply

    @contextmanager
    def checkpoint(self):
        if self.sqlite:
            from langgraph.checkpoint.sqlite import SqliteSaver

            with SqliteSaver.from_conn_string(str(self.path) + ".checkpoints") as saver:
                yield saver
        else:
            from langgraph.checkpoint.postgres import PostgresSaver

            with PostgresSaver.from_conn_string(
                self.url.replace("postgresql+psycopg://", "postgresql://", 1)
            ) as saver:
                yield saver

    def setup_checkpoints(self):
        with self.lock("checkpoint-migration"), self.checkpoint() as saver:
            saver.setup()
