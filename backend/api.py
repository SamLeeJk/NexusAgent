from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from configuration import Config
from database import BusinessError, Database
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from knowledge import KnowledgeStore
from langgraph.errors import GraphRecursionError
from openai import OpenAIError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from service import SupportService
from sqlalchemy import text
from workflow import IntentModel

from observability import Observability, TelemetryMiddleware, mark_error


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    message: str = Field(min_length=1, max_length=10000)

    @field_validator("message")
    @classmethod
    def trim(cls, value):
        if not value.strip():
            raise ValueError("Message is empty")
        return value.strip()


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


class KnowledgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200000)


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
    expected_active_version_id: UUID | None


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


def create_app(config=None, db=None, model=None, telemetry=None):
    config = config or Config.environment()
    telemetry = telemetry or Observability(config)

    @asynccontextmanager
    async def lifespan(app):
        database = db or Database(config.database_url)
        database.migrate()
        database.setup_checkpoints()
        if config.seed_demo:
            database.seed_demo()
        app.state.db = database
        app.state.service = SupportService(
            database,
            model or IntentModel(config.demo_mode, config.api_key, config.model),
            config.proposal_ttl,
        )
        try:
            yield
        finally:
            database.engine.dispose()
            telemetry.shutdown()

    app = FastAPI(title="NexusAgent", lifespan=lifespan)
    app.state.telemetry = telemetry

    if telemetry.enabled:
        @app.get("/metrics", include_in_schema=False)
        def metrics():
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
            return Response(generate_latest(telemetry.registry), headers={"Content-Type": CONTENT_TYPE_LATEST})

    @app.exception_handler(BusinessError)
    async def business_error(request, exc):
        mark_error(exc)
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})

    @app.exception_handler(OpenAIError)
    async def model_error(request, exc):
        mark_error(exc)
        return JSONResponse(
            status_code=502, content={"detail": "模型请求失败，请用原请求重试。"}
        )

    @app.exception_handler(GraphRecursionError)
    async def graph_error(request, exc):
        mark_error(exc)
        return JSONResponse(
            status_code=504, content={"detail": "运行达到步数上限，请联系维护者。"}
        )

    @app.middleware("http")
    async def csrf_and_headers(request, call_next):
        # No CORS permission is granted. Custom headers require a preflight cross-site.
        if (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            and request.headers.get("x-nexus-request") != "1"
        ):
            return JSONResponse(status_code=403, content={"detail": "缺少请求校验头。"})
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def current_user(request: Request):
        return request.app.state.db.authenticate(
            request.cookies.get("nexus_session", "")
        )

    CurrentUser = Annotated[dict, Depends(current_user)]

    def admin_user(user: CurrentUser):
        if user["role"] != "admin":
            raise HTTPException(403, "仅知识库管理员可以执行此操作。")
        return user

    AdminUser = Annotated[dict, Depends(admin_user)]

    @app.get("/api/admin/knowledge/documents")
    def knowledge_documents(request: Request, user: AdminUser):
        return KnowledgeStore(request.app.state.db).list_documents(user["id"])

    @app.post("/api/admin/knowledge/documents")
    def import_knowledge(payload: KnowledgeInput, request: Request, user: AdminUser):
        return KnowledgeStore(request.app.state.db).import_document(user["id"], payload.slug, payload.title, payload.content)

    @app.get("/api/admin/knowledge/versions/{vid}")
    def preview_knowledge(vid: UUID, request: Request, user: AdminUser):
        return KnowledgeStore(request.app.state.db).preview(user["id"], str(vid))

    @app.post("/api/admin/knowledge/documents/{did}/publish")
    def publish_knowledge(did: UUID, payload: PublishInput, request: Request, user: AdminUser):
        return KnowledgeStore(request.app.state.db).publish(user["id"], str(did), str(payload.version_id),
            str(payload.expected_active_version_id) if payload.expected_active_version_id else None)

    @app.post("/api/knowledge/search")
    def search_knowledge(payload: SearchInput, request: Request, user: CurrentUser):
        return KnowledgeStore(request.app.state.db).search(payload.query, payload.top_k)

    @app.get("/api/knowledge/sources/{chunk_id}")
    def knowledge_source(chunk_id: UUID, request: Request, user: CurrentUser):
        return KnowledgeStore(request.app.state.db).source(str(chunk_id))

    @app.get("/api/health")
    def health(request: Request):
        with request.app.state.db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "mode": "demo" if config.demo_mode else "model",
            "storage": "sqlite-local" if request.app.state.db.sqlite else "postgresql",
        }

    @app.post("/api/login")
    def login(payload: LoginInput, request: Request, response: Response):
        token, user = request.app.state.db.login(payload.username, payload.password)
        response.set_cookie(
            "nexus_session",
            token,
            httponly=True,
            secure=config.secure_cookie,
            samesite="strict",
            max_age=86400,
            path="/",
        )
        return user

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        request.app.state.db.logout(request.cookies.get("nexus_session", ""))
        response.delete_cookie("nexus_session", path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user: CurrentUser):
        return user

    @app.get("/api/orders")
    def own_orders(request: Request, user: CurrentUser):
        return request.app.state.db.list_orders(user["id"])

    @app.get("/api/orders/{order_id}")
    def order(order_id: str, request: Request, user: CurrentUser):
        value = request.app.state.db.get_order(user["id"], order_id)
        if not value:
            raise HTTPException(404, "订单不存在。")
        return value

    @app.get("/api/conversations")
    def list_conversations(request: Request, user: CurrentUser):
        return request.app.state.db.list_conversations(user["id"])

    @app.post("/api/conversations")
    def create_conversation(request: Request, user: CurrentUser):
        return request.app.state.db.new_conversation(user["id"])

    @app.get("/api/conversations/{cid}")
    def conversation(cid: UUID, request: Request, user: CurrentUser):
        return request.app.state.db.snapshot(user["id"], str(cid))

    @app.post("/api/conversations/{cid}/messages")
    def send(cid: UUID, payload: MessageInput, request: Request, user: CurrentUser):
        return request.app.state.service.send(
            user["id"], str(cid), str(payload.request_id), payload.message
        )

    @app.post("/api/conversations/{cid}/proposals/{pid}/decision")
    def decide(
        cid: UUID,
        pid: UUID,
        payload: DecisionInput,
        request: Request,
        user: CurrentUser,
    ):
        return request.app.state.service.decide(
            user["id"], str(cid), str(pid), payload.decision
        )

    @app.post("/chat", deprecated=True)
    def retired_chat():
        raise HTTPException(410, "旧单请求接口已停用，请登录后使用会话接口。")

    frontend = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(frontend / "index.html")

    # Outermost user middleware also measures CSRF rejections. No request bodies,
    # cookies, raw paths, SQL or exception messages are collected.
    app.add_middleware(TelemetryMiddleware, telemetry=telemetry)
    return app
