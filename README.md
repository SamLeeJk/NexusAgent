# NexusAgent

A full-stack customer-support demo with **React / TypeScript, FastAPI, PostgreSQL and LangGraph**. Customers can look up their orders, ask about policy, create an order-bound refund proposal, and explicitly approve or reject it. Conversations, pending approvals and refund receipts survive application restarts.

The application creates **refund requests**, not financial transfers. It is a portfolio project using seeded users and simulated orders.

## Run the full stack

```bash
docker compose -p nexusagent-stage2 -f backend/docker-compose.yml up --build -d app
```

Open [the local workspace](http://127.0.0.1:8000). Demo accounts:

- `alice` / `demo-alice-123`: O1001 (shipped), O1002 (processing).
- `bob` / `demo-bob-123`: O2001 (processing).
- `admin` / `demo-admin-123`: knowledge import, preview, publication and rollback.

Compose explicitly enables the **deterministic rule demo**, so it does not require or call an external model. The UI labels this mode. PostgreSQL is exposed on loopback port `55432`; the application binds loopback port `8000`. Set `NEXUS_HTTP_PORT` before running Compose if that HTTP port is occupied.

Ask “O1002 能退款吗？”, then “帮我申请”. Refresh the page while the confirmation card is pending, approve it, and inspect the saved receipt. Repeated approvals return the same result. Existing seed records and receipts are not reset on startup.

## Implemented

- Login with hashed passwords, expiring opaque sessions and HttpOnly / SameSite cookies.
- User ownership checks on orders, conversations, proposals and their results.
- Persistent conversation history and LangGraph checkpoints.
- Explicit order-bound proposals with a 15-minute expiry and separate approval/rejection API.
- Durable `interrupt` / `Command(resume=...)` workflow; model text is never approval.
- Transactional refund request creation, order uniqueness, idempotent request IDs and receipt recovery.
- Cross-process conversation locking; execution rechecks the latest order state and ownership.
- Chinese/English lexical policy retrieval with BM25, query coverage filtering and no-match handling.
- Admin Markdown import, immutable drafts, atomic publication, stale-update protection and rollback.
- Persisted citations with source lines and historical-version access; unpublished drafts remain private.
- A fixed synthetic retrieval regression set with metric gates and reproducible JSON/Markdown reports.
- React chat, saved conversation navigation, confirmation cards, error recovery and mobile layout.
- Offline and PostgreSQL integration tests, browser end-to-end tests, and opt-in live-model smoke test.

## Runtime architecture

```text
React UI → authenticated FastAPI → SupportService → LangGraph + durable checkpoints
                                     │              understand → resolve
                                     │                  ↓ proposal
                                     │              approval (interrupt)
                                     │                  ↓ authenticated decision
                                     └────────────── execute → saved receipt
                                                      │
                                                  PostgreSQL
```

The model classifies intent and resolves conversational references. Business nodes retrieve authoritative data, create proposals and enforce execution rules. The runtime does not let a model supply its own approval. Approval decisions, business writes and checkpoint updates are separate commits; persistent receipts and idempotent retries reconcile interruption between them.

The original `agent.py`, `tools.py`, `state.py` and in-memory data modules remain as **stage-one experiments**, not the active web execution path. The old anonymous `POST /chat` returns 410.

## Local development

Use Python 3.12 and a working virtual environment:

```bash
pip install -r backend/requirements.txt
cd frontend
npm ci
npm run build
```

Start PostgreSQL using the Compose `postgres` service. Configure `backend/.env` using `backend/.env.example`. From `backend/`, run:

```bash
python migrate.py
uvicorn main:app --host 127.0.0.1 --port 8000
```

Alternatively, `python run_local.py` explicitly runs a file-backed SQLite / rule demo, storing data under `backend/runtime/`. It does not replace PostgreSQL by default and is not a PostgreSQL validation substitute.

To use a live model, set `NEXUS_DEMO_MODE=false`, configure `OPENAI_API_KEY` and optionally `OPENAI_MODEL`, then restart. Only enable real external calls when approved for the data involved. The default model name is `gpt-4o-mini`. Compose does not copy or automatically mount `.env` secrets into its image.

## API

Write requests require the `X-Nexus-Request: 1` header. Sign in using `POST /api/login`, then retain the session cookie.

- `GET /api/me`, `POST /api/logout`
- `GET /api/orders`, `GET /api/orders/{order_id}`
- `POST /api/conversations`, `GET /api/conversations`
- `GET /api/conversations/{cid}`
- `POST /api/conversations/{cid}/messages` with `{ "request_id": "UUID", "message": "..." }`
- `POST /api/conversations/{cid}/proposals/{pid}/decision` with `{ "decision": "approve" }` or `reject`
- `GET /api/health`
- `GET/POST /api/admin/knowledge/documents` (admin only)
- `GET /api/admin/knowledge/versions/{version_id}` (admin preview)
- `POST /api/admin/knowledge/documents/{document_id}/publish` with `version_id` and `expected_active_version_id` (admin; null for first publication)
- `POST /api/knowledge/search` with `query` and optional `top_k`
- `GET /api/knowledge/sources/{chunk_id}` (published versions only)

Preserve request IDs on retries. HTTP 200 may represent an expired, rejected or invalidated proposal; inspect the business state. Only a completed proposal and saved refund request indicate successful registration.

## Verification

From the repository root with development dependencies installed:

```bash
python -m pytest backend/tests -q
python -m ruff check backend
python backend/evaluate_knowledge.py
```

Set `NEXUS_TEST_DATABASE_URL` to a **dedicated PostgreSQL test database** to run `backend/tests/test_support.py` and `backend/tests/test_knowledge.py` against PostgreSQL. Tests create and remove their own random schemas. Without it, tests use temporary SQLite databases. Live-model tests are skipped unless `NEXUS_LIVE_EVAL=1` is explicitly enabled.

With the demo server running at port 8000, run `npm run test:e2e` in `frontend/`. This uses installed Chrome and writes only to demo accounts. Build with `npm run build` to check TypeScript and generate frontend assets.

## Scope and next work

No payment gateway, production identity lifecycle, SSE, vector/hybrid search, full operator console or load certification is included. The knowledge admin page is implemented. Authentication and rule-based demo mode are suitable for the local portfolio environment, not an assertion of production readiness.

Stage 3A delivers versioned knowledge ingestion and a lexical evaluation baseline. Migration 002 adds roles, message sources and knowledge tables without resetting existing conversations or receipts. Seed data never overwrites a published policy. The next milestone is a measured vector/hybrid retrieval comparison, followed by streaming and operator handoff.

- [Current progress](docs/PROGRESS.md)
- [Deployment and acceptance walkthrough](docs/阶段2-验收与部署.md)
- [Stage 3A plan, acceptance results and walkthrough](docs/阶段3A-计划与验收.md)
- [Target-state project/resume material](docs/NexusAgent-项目完成形态与简历素材.md)

MIT license; see [LICENSE](LICENSE).
