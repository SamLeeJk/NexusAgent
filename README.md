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
- Chinese/English lexical, vector or hybrid policy retrieval with explicit no-match handling.
- Local multilingual ONNX embeddings, Qdrant indexing, reciprocal-rank fusion and lexical fallback.
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
python backend/evaluate_hybrid.py --cache-dir backend/runtime/models
```

Set `NEXUS_TEST_DATABASE_URL` to a **dedicated PostgreSQL test database** to run `backend/tests/test_support.py` and `backend/tests/test_knowledge.py` against PostgreSQL. Tests create and remove their own random schemas. Without it, tests use temporary SQLite databases. Live-model tests are skipped unless `NEXUS_LIVE_EVAL=1` is explicitly enabled.

### Stage 3B hybrid retrieval

Lexical retrieval remains the default, so normal unit tests and a basic app startup do not require Qdrant or a model download. To run the measured hybrid mode locally from PowerShell:

```powershell
$env:NEXUS_HTTP_PORT='8001'
$env:NEXUS_RETRIEVAL_MODE='hybrid'
docker compose -p nexusagent-stage2 -f backend/docker-compose.yml --profile search up --build -d qdrant app
```

The first hybrid query downloads the FastEmbed runtime model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` at immutable ONNX repository revision `faf4aa4225822f3bc6376869cb1164e8e3feedd0` into the `embedding_cache` volume. Qdrant and its Python client are pinned to compatible 1.15 releases. PostgreSQL remains the source of truth: only chunks returned by the current active-version query are accepted, and Qdrant payloads are filtered by the active corpus hash. Re-indexing the same corpus is idempotent. Publishing or rolling back changes that hash and synchronizes the newly active chunks; drafts cannot enter search results.

Set `NEXUS_RETRIEVAL_MODE` to `lexical`, `vector` or `hybrid`. `NEXUS_QDRANT_URL`, `NEXUS_QDRANT_COLLECTION`, `NEXUS_EMBEDDING_CACHE_DIR` and `NEXUS_VECTOR_SCORE_THRESHOLD` are optional. If embedding or Qdrant access fails in vector/hybrid mode, the request completes with lexical results and returns `mode_used=lexical`, `fallback=true` and the controlled reason `vector_unavailable`; exception text is not returned. With observability enabled, `nexus_retrieval_mode_total{requested_mode,used_mode,fallback}` records the path without high-cardinality identifiers.

The frozen Stage 3B evaluation contains 48 bilingual paraphrases and 32 unsupported questions. Run Qdrant, then execute:

```powershell
python backend/evaluate_hybrid.py --cache-dir backend/runtime/models
```

The checked-in run achieved Hybrid Hit@3 95.8%, MRR@3 93.4% and 100% no-answer rejection on that synthetic set. The lexical mode intentionally scored 0% Hit@3 on these paraphrase-heavy cases while rejecting all negatives, making the comparison a hard regression set rather than a production-quality estimate. See [the Stage 3B acceptance record](docs/阶段3B-混合检索计划与验收.md) and [machine-readable report](docs/validation/stage3b-hybrid.json).

With the demo server running at port 8000, run `npm run test:e2e` in `frontend/`. This uses installed Chrome and writes only to demo accounts. Build with `npm run build` to check TypeScript and generate frontend assets.

## P0 observability (opt-in)

This slice adds OpenTelemetry request/node/retrieval/database spans, Prometheus RED and Agent metrics, and allowlisted JSON logs. It does not change approval, transaction, idempotency or checkpoint rules. No SSE or vector milestone is included.

From the repository root in PowerShell:

```powershell
$env:NEXUS_HTTP_PORT='8001'
$env:NEXUS_OBSERVABILITY_ENABLED='true'
docker compose -p nexusagent-stage2 -f backend/docker-compose.yml --profile observability up --build -d app otel-collector jaeger prometheus grafana
```

The app remains at http://127.0.0.1:8001; `/metrics` is exposed only when enabled. Open [Jaeger](http://127.0.0.1:16686) and select service `nexusagent`, [Prometheus targets](http://127.0.0.1:9090/targets), and [Grafana dashboard](http://127.0.0.1:3000/d/nexusagent-p0). Grafana's local demo login is `admin / nexus-local-demo`; set `NEXUS_GRAFANA_PASSWORD` before the first start to use another password. Existing Grafana volumes retain their previously initialized password.

Compose sends spans over internal OTLP/HTTP to Collector, then Jaeger. Prometheus scrapes the app directly. Grafana provisions both data sources and ten dashboard panels. All published ports are loopback-only; Collector's ingest port is internal. These components are a local demo, not a hardened production monitoring deployment. Jaeger storage is in-memory; Prometheus keeps seven days in its volume.

The [dashboard JSON](observability/grafana/dashboards/nexusagent.json) is also importable via Grafana **Dashboards → New → Import**. Choose your Prometheus data source using the dashboard's `Prometheus` selector; the provisioned instance chooses `nexus-prometheus`. A direct Jaeger link uses the local port above.

Configuration:

- `NEXUS_OBSERVABILITY_ENABLED=false` by default: no exporter, spans, metrics endpoint or structured telemetry records are created. Explicit test `Config` objects remain disabled unless the test opts in.
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`: full HTTP endpoint, e.g. `http://otel-collector:4318/v1/traces`. An empty endpoint enables local metrics/logs and spans without exporting. Outside Docker, use an endpoint reachable by that process.
- `NEXUS_OBS_FAULT_NODE=understand`: deterministic fault at node entry, active only with observability enabled and `NEXUS_DEMO_MODE=true`. Leave empty normally; no request header can enable it.

The server accepts valid W3C `traceparent`, returns `X-Trace-ID`, and propagates context into sync handlers and graph nodes. Resuming an approval creates a new request trace, correlated by `conversation_id` and `turn_id`; trace state is never added to checkpoints. Message `request_id` is the client's idempotency UUID; other requests get a generated correlation UUID. Unknown IDs remain empty in logs.

Log fields are `trace_id`, `conversation_id`, `turn_id`, `request_id`, `node`, `status`, `error_type`. Only validated UUIDs and controlled operation/error categories are recorded. Passwords, Cookie/Authorization headers, raw URLs/query strings, model prompts, answers, knowledge bodies, SQL parameters, exception messages/stacks and baggage are excluded rather than regex-redacted after collection. Unexpected request exceptions produce a generic 500; nodes keep their original exceptions for graph failure/retry. Container access logs are disabled to avoid query-string logging. For local Uvicorn use `--no-access-log` too. Do not enable third-party debug/body logging when checking this guarantee.

Metrics use bounded labels; no user/conversation/turn/request/trace ID is a metric label:

- `nexus_http_requests_total{method,route,status_code}` and `nexus_http_request_duration_seconds{method,route}`: HTTP rate, status-based error rate and duration. Route templates replace IDs; unknown routes and early CSRF rejection use `unmatched`; `/metrics` itself is excluded.
- `nexus_operations_total{category,operation,status}` and `nexus_operation_duration_seconds{category,operation}`: graph nodes and database/service/retrieval operation attempts. Approval interrupt is `waiting`, not `error`.
- `nexus_agent_outcomes_total{action,outcome}`: completed/waiting/replay/error sends and decision outcomes, including rejected, expired and invalidated proposals.
- `nexus_retrieval_total{outcome}`: found/no_match/error retrievals.

Counters count attempts and response outcomes, **not unique refund transactions**; retries are explicitly counted. Database spans wrap business methods, including commit, rather than exporting individual SQL. This P0 uses one app worker and process-local counters (reset on restart). Multi-worker metrics aggregation, durable trace storage, log search and production alerting are outside this slice. Trace export uses a bounded asynchronous batch queue and a two-second exporter timeout; an unavailable Collector must not become a business dependency. Export failures can lose telemetry. Structured logs are available through `docker compose ... logs app`; no log storage service was added.

### Demo and acceptance

1. Wait for `/api/health` to return `mode=demo`, `storage=postgresql` and for the Prometheus target to be UP.
2. Sign in as `alice`, start a conversation and ask `确认有效期政策`. Use the response's `X-Trace-ID` in Jaeger to inspect `agent.send → agent.understand / agent.resolve → knowledge.search → db.knowledge.active_chunks`.
3. Send `帮我申请 O1002`, then explicitly confirm or cancel. Check `agent.approval` with `nexus.status=waiting`, followed by a separate resume request with `agent.approval`, `agent.execute` and `db.execute`. Repeat the same decision to inspect a `replay` outcome.
4. Run the automated local-stack smoke test (it creates a synthetic conversation and **cancels** its proposal):

```powershell
python backend/observability_smoke.py --base-url http://127.0.0.1:8001
```

It checks actual Jaeger spans, Prometheus UP/metrics, Grafana's ten panels and all their PromQL expressions. Non-sensitive IDs and results are written to ignored `backend/runtime/observability-smoke.json`. It refuses a non-demo model mode.

### Fault injection and recovery

With the same HTTP port and observability environment variables still set:

```powershell
$env:NEXUS_OBS_FAULT_NODE='understand'
docker compose -p nexusagent-stage2 -f backend/docker-compose.yml --profile observability up -d --no-build app
```

Wait for app health, start a new customer conversation and send a policy question. The response is 500; `agent.understand`, `agent.send` and its server span have `error.type=InjectedFailure`. The node error counter and HTTP 5xx series increase. Keep the original conversation/request UUID (the UI retains it behind **重试原消息（保持请求编号）**). The failure happens before understanding or any refund action.

Disable the fault immediately after observing it:

```powershell
$env:NEXUS_OBS_FAULT_NODE=''
docker compose -p nexusagent-stage2 -f backend/docker-compose.yml --profile observability up -d --no-build app
```

Wait for health, then click the retry button **without refreshing the page**, or resend the same message with its original `request_id` to the original conversation endpoint. The same persisted turn resumes; no duplicate user message or business action is required. Metrics reset on app recreation, so use a Grafana time range including the failure; Jaeger keeps the error trace until its own restart. Do not run startup fault demonstrations concurrently with PostgreSQL tests against the same database: their existing advisory migration locks are shared even across test schemas.

To demonstrate exporter independence, stop only Collector, send another policy question, then start Collector again. The request should still succeed; a sufficiently long outage may drop queued spans. To disable observability entirely, set `NEXUS_OBSERVABILITY_ENABLED=false` and recreate app. Stop monitoring services explicitly if desired; omitting the Compose profile does not stop containers already running.

### Automated checks

```powershell
python -m pytest backend/tests -q
$env:NEXUS_TEST_DATABASE_URL='postgresql+psycopg://nexus:nexus_local@127.0.0.1:55432/nexus?connect_timeout=5'
python -m pytest backend/tests/test_support.py backend/tests/test_knowledge.py backend/tests/test_observability.py -q
Remove-Item Env:NEXUS_TEST_DATABASE_URL
npm --prefix frontend run build
```

Observability tests use in-memory span exporters and per-app Prometheus registries; they require no Collector, Jaeger, Prometheus, Grafana or model service. They cover trace parentage, request isolation, RED labels, sensitive input/exception exclusion, normal interrupt, explicit decision/replay, fault recovery, logging sink failure, default-off behavior and independent app registries. PostgreSQL tests use random schemas; use a dedicated test database in shared environments. See [P0 acceptance record](docs/P0-可观测性验收.md) for the actual run results and limitations.

Instrumentation follows [OpenTelemetry Python's manual instrumentation API](https://opentelemetry.io/docs/languages/python/instrumentation/), [Prometheus Python histograms](https://prometheus.github.io/client_python/instrumenting/histogram/) and [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/).

## Scope and next work

No payment gateway, production identity lifecycle, SSE, full operator console or load certification is included. The knowledge admin page and optional vector/hybrid retrieval are implemented. Authentication and rule-based demo mode are suitable for the local portfolio environment, not an assertion of production readiness.

Stage 3A delivers versioned knowledge ingestion and a lexical evaluation baseline. Stage 3B adds the measured optional hybrid retrieval path while keeping PostgreSQL publication semantics authoritative. Migration 002 adds roles, message sources and knowledge tables without resetting existing conversations or receipts. Seed data never overwrites a published policy. Streaming and operator handoff remain future milestones.

- [Current progress](docs/PROGRESS.md)
- [Deployment and acceptance walkthrough](docs/阶段2-验收与部署.md)
- [Stage 3A plan, acceptance results and walkthrough](docs/阶段3A-计划与验收.md)
- [Target-state project/resume material](docs/NexusAgent-项目完成形态与简历素材.md)

MIT license; see [LICENSE](LICENSE).
