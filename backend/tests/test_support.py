import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from api import create_app
from configuration import Config
from database import proposals, refunds, sessions
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

HEADERS = {"X-Nexus-Request": "1"}


@pytest.fixture
def database_url(tmp_path):
    url = os.getenv("NEXUS_TEST_DATABASE_URL")
    if not url:
        yield "sqlite:///" + str(tmp_path / "support.db")
        return
    # PostgreSQL tests get an isolated, randomly named schema in the test DB.
    schema = "test_" + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    separator = "&" if "?" in url else "?"
    try:
        yield url + separator + "options=-csearch_path%3D" + schema
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def config(database_url):
    return Config(database_url=database_url, demo_mode=True, seed_demo=True)


@pytest.fixture
def client(config):
    with TestClient(
        create_app(config), headers=HEADERS, raise_server_exceptions=False
    ) as client:
        yield client


def login(client, username="alice"):
    response = client.post(
        "/api/login", json={"username": username, "password": f"demo-{username}-123"}
    )
    assert response.status_code == 200, response.text
    return response


def new_conversation(client):
    login(client)
    response = client.post("/api/conversations")
    assert response.status_code == 200, response.text
    return response.json()["id"]


def send(client, cid, message, rid=None):
    return client.post(
        f"/api/conversations/{cid}/messages",
        json={"request_id": rid or str(uuid4()), "message": message},
    )


def pending(client, cid, oid="O1002"):
    response = send(client, cid, f"帮我申请订单 {oid} 的退款")
    assert response.status_code == 200, response.text
    return response.json()["proposals"][-1]["id"]


def decide(client, cid, pid, decision="approve"):
    return client.post(
        f"/api/conversations/{cid}/proposals/{pid}/decision",
        json={"decision": decision},
    )


def count_refunds(db):
    with db.engine.connect() as conn:
        return len(conn.execute(select(refunds)).all())


def test_auth_and_retired_unsafe_entry(client):
    assert client.get("/api/orders").status_code == 401
    assert client.post("/chat", json={"message": "refund O1002"}).status_code == 410
    assert (
        client.post(
            "/api/login",
            headers={"X-Nexus-Request": ""},
            json={"username": "alice", "password": "bad"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/login", json={"username": "alice", "password": "bad"}
        ).status_code
        == 401
    )
    response = login(client)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    with client.app.state.db.engine.connect() as conn:
        assert conn.execute(
            select(sessions.c.token_hash)
        ).scalar() != client.cookies.get("nexus_session")
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_ownership_on_all_resources(client):
    cid = new_conversation(client)
    pid = pending(client, cid)
    login(client, "bob")
    assert [o["id"] for o in client.get("/api/orders").json()] == ["O2001"]
    assert client.get("/api/orders/O1002").status_code == 404
    assert client.get(f"/api/conversations/{cid}").status_code == 404
    assert send(client, cid, "hello").status_code == 404
    assert decide(client, cid, pid).status_code == 404
    assert client.get("/api/conversations").json() == []
    assert count_refunds(client.app.state.db) == 0


@pytest.mark.parametrize("message", ["", " \n\t", "x" * 10001])
def test_invalid_messages(client, message):
    cid = new_conversation(client)
    assert send(client, cid, message).status_code == 422


def test_multiturn_inquiry_then_proposal_no_early_write(client):
    cid = new_conversation(client)
    result = send(client, cid, "O1002 能退款吗？")
    assert result.status_code == 200, result.text
    assert result.json()["proposals"] == []
    result = send(client, cid, "帮我申请")
    assert result.status_code == 200, result.text
    assert result.json()["proposals"][0]["order_id"] == "O1002"
    assert count_refunds(client.app.state.db) == 0
    assert send(client, cid, "yes, confirm").status_code == 409


def test_conversation_context_isolated(client):
    cid = new_conversation(client)
    send(client, cid, "O1002 能退款吗")
    other = client.post("/api/conversations").json()["id"]
    result = send(client, other, "帮我申请")
    assert result.status_code == 200, result.text
    assert result.json()["proposals"] == []


def test_approve_is_idempotent_and_tampering_rejected(client):
    cid = new_conversation(client)
    pid = pending(client, cid)
    path = f"/api/conversations/{cid}/proposals/{pid}/decision"
    assert (
        client.post(path, json={"decision": "approve", "order_id": "O2001"}).status_code
        == 422
    )
    first = decide(client, cid, pid)
    assert first.status_code == 200, first.text
    second = decide(client, cid, pid)
    assert second.status_code == 200, second.text
    assert first.json()["refunds"] == second.json()["refunds"]
    assert len(first.json()["refunds"]) == 1
    assert first.json()["refunds"][0]["order_id"] == "O1002"
    assert count_refunds(client.app.state.db) == 1
    assert send(client, cid, "谢谢").status_code == 200


def test_rejected_cannot_later_be_approved(client):
    cid = new_conversation(client)
    pid = pending(client, cid)
    response = decide(client, cid, pid, "reject")
    assert response.status_code == 200, response.text
    assert response.json()["proposals"][0]["status"] == "rejected"
    assert decide(client, cid, pid).status_code == 409
    assert count_refunds(client.app.state.db) == 0


def test_expiry_blocks_write_and_unblocks_conversation(client):
    cid = new_conversation(client)
    pid = pending(client, cid)
    with client.app.state.db.engine.begin() as conn:
        conn.execute(
            proposals.update()
            .where(proposals.c.id == pid)
            .values(expires_at=time.time() - 1)
        )
    result = decide(client, cid, pid)
    assert result.status_code == 200, result.text
    assert result.json()["proposals"][0]["status"] == "expired"
    assert count_refunds(client.app.state.db) == 0
    assert send(client, cid, "O1002 能退款吗").status_code == 200


def test_status_rechecked_at_execution(client):
    from database import orders

    cid = new_conversation(client)
    pid = pending(client, cid)
    with client.app.state.db.engine.begin() as conn:
        conn.execute(
            orders.update().where(orders.c.id == "O1002").values(status="shipped")
        )
    result = decide(client, cid, pid)
    assert result.status_code == 200, result.text
    assert result.json()["proposals"][0]["status"] == "invalidated"
    assert count_refunds(client.app.state.db) == 0


@pytest.mark.parametrize("oid", ["O1001", "O2001", "O9999"])
def test_ineligible_or_unowned_order_never_proposed(client, oid):
    cid = new_conversation(client)
    result = send(client, cid, f"帮我申请 {oid}")
    assert result.status_code == 200, result.text
    assert result.json()["proposals"] == []
    assert count_refunds(client.app.state.db) == 0


def test_request_id_replay_and_mismatched_body(client):
    cid = new_conversation(client)
    rid = str(uuid4())
    result = send(client, cid, "O1002 能退款吗", rid)
    assert result.status_code == 200, result.text
    assert send(client, cid, "O1002 能退款吗", rid).json() == result.json()
    assert send(client, cid, "帮我申请 O1002", rid).status_code == 409


def test_pending_survives_fresh_app_and_database_instance(client, config):
    cid = new_conversation(client)
    send(client, cid, "O1002 能退款吗")
    with TestClient(create_app(config), headers=HEADERS) as restarted:
        login(restarted)
        result = send(restarted, cid, "帮我申请")
        assert result.status_code == 200, result.text
        pid = result.json()["proposals"][0]["id"]
    with TestClient(create_app(config), headers=HEADERS) as restarted:
        login(restarted)
        assert (
            restarted.get(f"/api/conversations/{cid}").json()["proposals"][0]["id"]
            == pid
        )
        response = decide(restarted, cid, pid)
        assert response.status_code == 200, response.text
        assert len(response.json()["refunds"]) == 1


def test_concurrent_confirmation_single_receipt(client):
    cid = new_conversation(client)
    pid = pending(client, cid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: decide(client, cid, pid), range(2)))
    assert all(r.status_code in (200, 409) for r in responses), [
        r.text for r in responses
    ]
    assert any(r.status_code == 200 for r in responses)
    assert decide(client, cid, pid).status_code == 200
    assert count_refunds(client.app.state.db) == 1


@pytest.mark.parametrize("crash_point", ["decide", "execute", "finish_turn"])
def test_restart_after_partial_commit(client, config, monkeypatch, crash_point):
    cid = new_conversation(client)
    pid = pending(client, cid)
    db = client.app.state.db
    original = getattr(db, crash_point)

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Simulated process loss after commit")

    monkeypatch.setattr(db, crash_point, crash)
    assert decide(client, cid, pid).status_code == 500
    with TestClient(create_app(config), headers=HEADERS) as restarted:
        login(restarted)
        response = decide(restarted, cid, pid)
        assert response.status_code == 200, response.text
        assert len(response.json()["refunds"]) == 1
        assert count_refunds(restarted.app.state.db) == 1
        assert send(restarted, cid, "谢谢").status_code == 200


def test_direct_resume_payload_is_not_approval(client):
    from database import BusinessError
    from langgraph.types import Command

    cid = new_conversation(client)
    pending(client, cid)
    service = client.app.state.service
    with service.db.checkpoint() as saver:
        graph = service.graph(saver)
        with pytest.raises(BusinessError):
            graph.invoke(Command(resume={"approved": True}), service.config(cid))
    assert count_refunds(service.db) == 0


def test_same_conversation_lock_rejects_overlapping_work(client):
    cid = new_conversation(client)
    with client.app.state.db.lock("conversation:" + cid):
        assert send(client, cid, "hello").status_code == 409


def test_runtime_unavailable_and_retry_same_request(client, monkeypatch):
    from database import BusinessError

    cid = new_conversation(client)
    rid = str(uuid4())
    model = client.app.state.service.model
    original = model.classify
    monkeypatch.setattr(
        model,
        "classify",
        lambda *args: (_ for _ in ()).throw(BusinessError(503, "model unavailable")),
    )
    assert send(client, cid, "O1002 能退款吗", rid).status_code == 503
    monkeypatch.setattr(model, "classify", original)
    result = send(client, cid, "O1002 能退款吗", rid)
    assert result.status_code == 200, result.text
    assert len(result.json()["messages"]) == 2


def test_same_order_across_conversations_does_not_duplicate(client):
    first = new_conversation(client)
    second = client.post("/api/conversations").json()["id"]
    p1 = pending(client, first)
    p2 = pending(client, second)
    assert decide(client, first, p1).status_code == 200
    assert decide(client, second, p2).status_code == 200
    assert count_refunds(client.app.state.db) == 1


def test_proposal_cannot_be_used_in_another_owned_conversation(client):
    cid = new_conversation(client)
    pid = pending(client, cid)
    other = client.post("/api/conversations").json()["id"]
    assert decide(client, other, pid).status_code == 404
    assert count_refunds(client.app.state.db) == 0


def test_ambiguous_orders_require_clarification(client):
    cid = new_conversation(client)
    result = send(client, cid, "帮我申请 O1001 或 O1002")
    assert result.status_code == 200
    assert result.json()["proposals"] == []


@pytest.mark.skipif(
    os.getenv("NEXUS_LIVE_EVAL") != "1", reason="Explicit live-model opt-in required"
)
def test_live_model_two_turns_and_explicit_approval(config):
    from dataclasses import replace

    actual = Config.environment()
    if not actual.api_key:
        pytest.skip("No model key configured")
    live = replace(config, demo_mode=False, api_key=actual.api_key, model=actual.model)
    with TestClient(create_app(live), headers=HEADERS) as browser:
        cid = new_conversation(browser)
        result = send(browser, cid, "订单 O1002 能退款吗？我现在只想咨询资格。")
        assert result.status_code == 200, result.text
        assert result.json()["proposals"] == []
        assert "processing" in result.json()["messages"][-1]["content"]
        result = send(browser, cid, "帮我为刚才那张订单申请退款。")
        assert result.status_code == 200, result.text
        proposal = result.json()["proposals"][-1]
        assert proposal["order_id"] == "O1002"
        assert count_refunds(browser.app.state.db) == 0
        final = decide(browser, cid, proposal["id"])
        assert final.status_code == 200, final.text
        assert len(final.json()["refunds"]) == 1
