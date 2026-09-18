import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from api import create_app
from configuration import Config
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from test_support import HEADERS, login, new_conversation, send

from observability import LOG_FIELDS, Observability

pytest_plugins = ["test_support"]


@pytest.fixture
def observed_app(config):
    config = replace(config, observability_enabled=True)
    exporter, logs = InMemorySpanExporter(), []
    telemetry = Observability(config, exporter=exporter, log_sink=logs.append)
    with TestClient(
        create_app(config, telemetry=telemetry),
        headers=HEADERS,
        raise_server_exceptions=False,
    ) as client:
        yield client, exporter, logs


def sample(client, name, labels):
    return client.app.state.telemetry.registry.get_sample_value(name, labels)


def test_traceparent_propagates_to_graph_retrieval_and_database(observed_app):
    client, exporter, logs = observed_app
    cid = new_conversation(client)
    exporter.clear()
    rid = str(uuid4())
    trace_id = "1234567890abcdef1234567890abcdef"
    response = client.post(
        f"/api/conversations/{cid}/messages",
        json={"request_id": rid, "message": "确认有效期政策"},
        headers={
            "traceparent": f"00-{trace_id}-1234567890abcdef-01",
            "baggage": "password=never-collect-baggage",
        },
    )
    assert response.status_code == 200
    assert response.headers["x-trace-id"] == trace_id
    spans = exporter.get_finished_spans()
    assert all(s.context.trace_id == int(trace_id, 16) for s in spans)
    by_name = {s.name: s for s in spans}
    server = next(s for s in spans if s.kind == SpanKind.SERVER)
    assert server.parent.span_id == int("1234567890abcdef", 16)
    assert server.name == "POST /api/conversations/{cid}/messages"
    assert by_name["agent.send"].parent.span_id == server.context.span_id
    assert (
        by_name["agent.resolve"].parent.span_id == by_name["agent.send"].context.span_id
    )
    assert (
        by_name["knowledge.search"].parent.span_id
        == by_name["agent.resolve"].context.span_id
    )
    assert (
        by_name["db.knowledge.active_chunks"].parent.span_id
        == by_name["knowledge.search"].context.span_id
    )
    assert "db.begin_turn" in by_name and "db.finish_turn" in by_name
    assert server.attributes["nexus.request_id"] == rid
    assert server.attributes["nexus.conversation_id"] == cid
    assert server.attributes["nexus.turn_id"]
    http_record = logs[-1]
    assert set(http_record) == set(LOG_FIELDS)
    assert http_record["turn_id"] == server.attributes["nexus.turn_id"]
    assert sample(client, "nexus_retrieval_total", {"outcome": "found"}) == 1
    assert (
        sample(
            client,
            "nexus_retrieval_mode_total",
            {
                "requested_mode": "lexical",
                "used_mode": "lexical",
                "fallback": "false",
            },
        )
        == 1
    )


def test_waiting_resume_and_replay_preserve_business_semantics(observed_app):
    client, exporter, _logs = observed_app
    cid = new_conversation(client)
    rid = str(uuid4())
    first = send(client, cid, "帮我申请 O1002", rid).json()
    assert first["refunds"] == []
    pid = first["proposals"][-1]["id"]
    approval = [s for s in exporter.get_finished_spans() if s.name == "agent.approval"][
        -1
    ]
    assert approval.attributes["nexus.status"] == "waiting"
    assert approval.status.status_code != StatusCode.ERROR
    assert not approval.events
    assert send(client, cid, "帮我申请 O1002", rid).status_code == 200
    path = f"/api/conversations/{cid}/proposals/{pid}/decision"
    response = client.post(path, json={"decision": "approve"})
    assert response.status_code == 200 and len(response.json()["refunds"]) == 1
    assert len(client.post(path, json={"decision": "approve"}).json()["refunds"]) == 1
    assert (
        sample(
            client,
            "nexus_agent_outcomes_total",
            {"action": "send", "outcome": "replay"},
        )
        == 1
    )
    assert (
        sample(
            client,
            "nexus_agent_outcomes_total",
            {"action": "decide", "outcome": "replay"},
        )
        == 1
    )
    assert (
        sample(
            client,
            "nexus_agent_outcomes_total",
            {"action": "decide", "outcome": "completed"},
        )
        == 1
    )
    assert any(s.name == "agent.execute" for s in exporter.get_finished_spans())
    assert any(s.name == "db.execute" for s in exporter.get_finished_spans())


def test_errors_red_metrics_and_sensitive_data_never_exported(observed_app):
    client, exporter, logs = observed_app
    secret = "SENSITIVE_PASSWORD_COOKIE_PROMPT_SQL_KEY"
    client.post("/api/login", json={"username": secret, "password": secret})
    cid = new_conversation(client)

    class BrokenModel:
        def classify(self, *args):
            raise ValueError(secret)

    client.app.state.service.model = BrokenModel()
    rid = str(uuid4())
    response = client.post(
        f"/api/conversations/{cid}/messages?token={secret}",
        json={"request_id": rid, "message": secret},
        headers={
            "Authorization": secret,
            "Cookie": f"nexus_session={client.cookies.get('nexus_session')}; private={secret}",
        },
    )
    assert response.status_code == 500 and secret not in response.text
    error_spans = [
        s
        for s in exporter.get_finished_spans()
        if s.status.status_code == StatusCode.ERROR
        and s.context.trace_id == int(response.headers["x-trace-id"], 16)
    ]
    assert {
        "agent.understand",
        "agent.send",
        "POST /api/conversations/{cid}/messages",
    } <= {s.name for s in error_spans}
    assert all(s.attributes["error.type"] == "ValueError" for s in error_spans)
    serialized = json.dumps(logs) + "".join(
        s.to_json() for s in exporter.get_finished_spans()
    )
    assert secret not in serialized
    assert (
        "exception.stacktrace" not in serialized
        and "exception.message" not in serialized
    )
    assert (
        "db.statement" not in serialized and "never-collect-baggage" not in serialized
    )
    assert logs[-1]["error_type"] == "ValueError" and logs[-1]["request_id"] == rid
    assert (
        sample(
            client,
            "nexus_http_requests_total",
            {
                "method": "POST",
                "route": "/api/conversations/{cid}/messages",
                "status_code": "500",
            },
        )
        == 1
    )
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "nexus_http_request_duration_seconds_bucket" in metrics.text
    assert (
        secret not in metrics.text
        and cid not in metrics.text
        and rid not in metrics.text
    )
    assert "conversation_id=" not in metrics.text and "trace_id=" not in metrics.text


def test_routes_csrf_and_context_isolation(observed_app):
    client, _exporter, logs = observed_app
    assert client.post("/api/login", headers={"X-Nexus-Request": ""}).status_code == 403
    for _ in range(3):
        assert client.get("/missing-" + str(uuid4())).status_code == 404
    assert (
        sample(
            client,
            "nexus_http_requests_total",
            {"method": "GET", "route": "unmatched", "status_code": "404"},
        )
        == 3
    )
    login(client)
    cids = [client.post("/api/conversations").json()["id"] for _ in range(2)]

    def run(cid):
        return send(client, cid, "确认有效期政策")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(run, cids))
    assert all(r.status_code == 200 for r in responses)
    traces = {r.headers["x-trace-id"] for r in responses}
    assert len(traces) == 2
    for tid in traces:
        correlated = [r for r in logs if r["trace_id"] == tid and r["conversation_id"]]
        assert len({r["conversation_id"] for r in correlated}) == 1
    client.get("/api/health", headers={"traceparent": "not-a-valid-trace"})
    assert logs[-1]["conversation_id"] == "" and logs[-1]["turn_id"] == ""


def test_default_disabled_and_app_registries_are_isolated(config):
    exporter = InMemorySpanExporter()
    disabled = Observability(config, exporter=exporter)
    with TestClient(create_app(config, telemetry=disabled)) as client:
        assert client.get("/metrics").status_code == 404
        assert "x-trace-id" not in client.get("/api/health").headers
    assert not exporter.get_finished_spans()
    a = Observability(replace(config, observability_enabled=True))
    b = Observability(replace(config, observability_enabled=True))
    try:
        a.outcomes.labels("send", "completed").inc()
        assert (
            b.registry.get_sample_value(
                "nexus_agent_outcomes_total", {"action": "send", "outcome": "completed"}
            )
            is None
        )
    finally:
        a.shutdown()
        b.shutdown()


def test_fault_injection_can_retry_same_turn_and_logging_failure_is_nonfatal(config):
    config = replace(
        config, observability_enabled=True, observability_fault_node="understand"
    )
    exporter = InMemorySpanExporter()

    def failing_sink(record):
        raise RuntimeError("log sink unavailable")

    obs = Observability(config, exporter=exporter, log_sink=failing_sink)
    with TestClient(create_app(config, telemetry=obs), headers=HEADERS) as client:
        cid = new_conversation(client)
        rid = str(uuid4())
        assert send(client, cid, "帮我申请 O1002", rid).status_code == 500
        obs.fault_node = ""
        result = send(client, cid, "帮我申请 O1002", rid).json()
        assert len(result["proposals"]) == 1 and not result["refunds"]
        assert len(result["turns"]) == 1
    assert any(
        s.attributes.get("error.type") == "InjectedFailure"
        for s in exporter.get_finished_spans()
    )


def test_environment_switches_and_non_demo_fault_guard(monkeypatch):
    monkeypatch.setenv("NEXUS_OBSERVABILITY_ENABLED", "true")
    monkeypatch.setenv("NEXUS_OBS_FAULT_NODE", "understand")
    monkeypatch.setenv("NEXUS_DEMO_MODE", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
    obs = Observability(Config.environment())
    try:
        assert obs.enabled and obs.fault_node == ""
    finally:
        obs.shutdown()
