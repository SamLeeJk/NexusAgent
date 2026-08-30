# NexusAgent

An AI customer-support agent built with **FastAPI**, **LangGraph**, and **OpenAI**.

NexusAgent is a learning-and-portfolio project focused on a **safe, extensible agent workflow**: the model can choose tools, but **deterministic Python** decides whether a business action is allowed. Current scenarios include order lookup, refund eligibility, refund request creation, and policy Q&A grounded in a local markdown document.

RAG, a vector database, session memory, and human-in-the-loop interrupts are **planned**, not implemented.

---

## Key features (implemented)

- FastAPI `POST /chat` with Swagger UI (`/docs`)
- Single LangGraph agent loop with tool calling
- OpenAI chat model for reasoning and tool selection
- Structured output to classify refund confirmation
- LangGraph `InjectedState` so refund confirmation is not an LLM-supplied tool argument
- Tool-level guardrails (order exists, eligibility, duplicate refund, user confirmation)
- In-memory mock orders and refund requests
- Local markdown refund policy as the first knowledge source
- Server-side debug tracing of graph messages

## Not implemented yet

Vector search, hybrid RAG, BM25 retrieval, Qdrant usage, persistent DB, cross-request memory, HITL interrupt/resume, and SSE streaming. Related packages (for example `qdrant-client`, `rank-bm25`, `sse-starlette`) may be installed for later stages; they are **not** used in the current agent path.

---

## Architecture

```
Client  →  FastAPI /chat  →  LangGraph  →  OpenAI (reason + tools)
                                  ↓
                         ToolNode (Python)
                                  ↓
                    mock orders / refunds / policy.md
```

| Layer | Role |
| --- | --- |
| FastAPI | HTTP API; invokes the compiled graph; prints a debug transcript of messages |
| LangGraph | Workflow: confirmation → agent → (tools ↔ agent) → end |
| OpenAI | Tool selection, natural-language answers, structured confirmation classification |
| LangChain tools | Business actions (`get_order_status`, refund checks, create refund, policy read) |
| `AgentState` | Message history plus `refund_confirmed` for one request |
| Mock data | In-memory `ORDERS` and `REFUND_REQUEST` |
| Policy file | `backend/data/knowledge/refund_policy.md` |

---

## Current agent workflow

```
START
  → confirmation   # structured bool: did this user message authorize a refund?
  → agent          # LLM + bound tools
  → tools?         # LangGraph tools_condition
       yes → ToolNode → agent
       no  → END
```

- Recursion is capped (`RECURSION_LIMIT = 10`) so a tool loop cannot run forever.
- Each HTTP request starts a **new** graph state (`messages` + `refund_confirmed=False`). There is no checkpointer and no session across requests.

---

## Tools

| Tool | What it does |
| --- | --- |
| `get_order_status` | Looks up mock order `O1001` (shipped) or `O1002` (processing). Unknown IDs return not-found. |
| `check_refund_policy` | Eligibility from status: `processing` eligible, `shipped` not eligible. |
| `create_refund_request` | Creates an in-memory refund **only** if confirmation, order, uniqueness, and status checks pass. |
| `search_refund_policy` | Reads the local refund policy markdown and returns it with the query. No chunking or ranking yet. |

---

## Safety / guardrails

The LLM may **request** `create_refund_request`. Python **enforces** whether it succeeds.

1. **Confirmation is not a model argument.** `refund_confirmed` is set by a dedicated structured-output classifier, stored on `AgentState`, and injected into the tool with LangGraph `InjectedState`. The model cannot pass `refund_confirmed=true` in the tool call.
2. **Eligibility** is re-checked in Python (`processing` only).
3. **Duplicates** are blocked via in-memory `REFUND_REQUEST`.
4. **Missing orders** return a structured error instead of creating a refund.

Questions about policy or eligibility are treated as **not** confirmation.

---

## Project structure

```
NexusAgent/
├── LICENSE
├── README.md
└── backend/
    ├── main.py                 # FastAPI app, POST /chat
    ├── agent.py                # LangGraph graph, LLM, confirmation node
    ├── state.py                # AgentState (messages + refund_confirmed)
    ├── tools.py                # LangChain tools + InjectedState
    ├── requirements.txt
    ├── docker-compose.yml      # Qdrant ports 6333/6334 (not used by the agent yet)
    └── data/
        ├── orders.py           # Mock orders
        ├── refunds.py          # In-memory refund store
        ├── confirm.py
        └── knowledge/
            └── refund_policy.md
```

---

## Setup

Requirements: **Python 3.12**, pip, an OpenAI API key.

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Create `backend/.env` (do not commit secrets):

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

`OPENAI_MODEL` is optional and defaults to `gpt-4o-mini`.

---

## Run

From `backend/` with the venv active:

```bash
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

- API: `http://127.0.0.1:8000/chat`
- Swagger: `http://127.0.0.1:8000/docs`

If `OPENAI_API_KEY` is missing, `/chat` returns **503**.

Optional: `docker compose -f backend/docker-compose.yml up` starts a local Qdrant for later RAG work. The current agent does not call it.

---

## Example requests

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"What is the status of order O1001?\"}"
```

```json
{ "message": "Is order O1002 eligible for a refund?" }
```

```json
{ "message": "Please create a refund for order O1002." }
```

```json
{ "message": "What is the refund policy for shipped orders?" }
```

`POST /chat` currently returns a **debug transcript** of graph messages (user turn, tool calls, tool results, final model text), not a production chat payload.

Mock data: **O1001** shipped / not refundable; **O1002** processing / refundable.

---

## Roadmap

1. Document chunking  
2. Basic lexical retrieval  
3. BM25 retrieval  
4. Qdrant vector retrieval  
5. Hybrid RAG  
6. Reranking  
7. Persistent database  
8. Session memory / checkpointer  
9. Human-in-the-loop interrupt and resume  
10. SSE streaming  
11. Evaluation dataset and agent eval harness  

---

## Project status

**Stage:** single-agent customer support with tool calling, confirmation classification, injected-state guardrails, and file-based policy grounding.

**Not production-ready:** in-memory data, no auth, no durable sessions, debug-oriented `/chat` response.

---

## License

MIT. See [LICENSE](LICENSE).
