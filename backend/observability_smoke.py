"""Explicit local demo acceptance. Never runs a live model or prints credentials."""

import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx


def wait_for(check, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise AssertionError("Telemetry did not arrive within the acceptance timeout")


def run(base_url, jaeger_url, prometheus_url, grafana_url):
    with httpx.Client(
        base_url=base_url, headers={"X-Nexus-Request": "1"}, timeout=10
    ) as client:
        health = client.get("/api/health").json()
        assert health["mode"] == "demo", "Only the seeded rule demo is allowed"
        client.post(
            "/api/login", json={"username": "alice", "password": "demo-alice-123"}
        ).raise_for_status()
        cid = client.post("/api/conversations").json()["id"]
        rid = str(uuid4())
        policy = client.post(
            f"/api/conversations/{cid}/messages",
            json={"request_id": rid, "message": "确认有效期政策"},
        )
        policy.raise_for_status()
        assert policy.json()["messages"][-1]["sources"]
        trace_id = policy.headers["x-trace-id"]
        proposal = client.post(
            f"/api/conversations/{cid}/messages",
            json={"request_id": str(uuid4()), "message": "帮我申请 O1002"},
        )
        proposal.raise_for_status()
        pid = proposal.json()["proposals"][-1]["id"]
        waiting_trace_id = proposal.headers["x-trace-id"]
        decision = client.post(
            f"/api/conversations/{cid}/proposals/{pid}/decision",
            json={"decision": "reject"},
        )
        decision.raise_for_status()
        assert decision.json()["proposals"][-1]["status"] == "rejected"
        assert not decision.json()["refunds"]
        resume_trace_id = decision.headers["x-trace-id"]
        metrics = client.get("/metrics")
        metrics.raise_for_status()
        assert 'nexus_retrieval_total{outcome="found"}' in metrics.text
    with httpx.Client(timeout=10) as probe:

        def spans_for(tid):
            response = probe.get(f"{jaeger_url}/api/traces/{tid}")
            if response.status_code != 200:
                return None
            data = response.json().get("data", [])
            return data[0]["spans"] if data else None

        spans = wait_for(lambda: spans_for(trace_id))
        operations = sorted({s["operationName"] for s in spans})
        assert {
            "agent.understand",
            "agent.resolve",
            "knowledge.search",
            "db.knowledge.active_chunks",
        } <= set(operations)
        waiting = wait_for(lambda: spans_for(waiting_trace_id))
        assert any(
            s["operationName"] == "agent.approval"
            and any(
                t["key"] == "nexus.status" and t["value"] == "waiting"
                for t in s["tags"]
            )
            for s in waiting
        )
        resumed = wait_for(lambda: spans_for(resume_trace_id))
        assert {"agent.approval", "agent.execute", "db.execute"} <= {
            s["operationName"] for s in resumed
        }

        def scraped():
            values = probe.get(
                prometheus_url + "/api/v1/query",
                params={"query": 'up{job="nexusagent"}'},
            ).json()["data"]["result"]
            return values if values and values[0]["value"][1] == "1" else None

        wait_for(scraped)

        def found_metric():
            result = probe.get(
                prometheus_url + "/api/v1/query",
                params={"query": 'nexus_retrieval_total{outcome="found"}'},
            ).json()["data"]["result"]
            return result if result and float(result[0]["value"][1]) >= 1 else None

        wait_for(found_metric)
        dashboard = probe.get(
            grafana_url + "/api/dashboards/uid/nexusagent-p0",
            auth=("admin", os.getenv("NEXUS_GRAFANA_PASSWORD", "nexus-local-demo")),
        )
        dashboard.raise_for_status()
        assert len(dashboard.json()["dashboard"]["panels"]) == 10
        for panel in dashboard.json()["dashboard"]["panels"]:
            query = probe.get(
                prometheus_url + "/api/v1/query",
                params={"query": panel["targets"][0]["expr"]},
            )
            query.raise_for_status()
            assert query.json()["status"] == "success"
    return {
        "passed": True,
        "storage": health["storage"],
        "trace_id": trace_id,
        "waiting_trace_id": waiting_trace_id,
        "resume_trace_id": resume_trace_id,
        "conversation_id": cid,
        "operations": operations,
        "prometheus_up": True,
        "dashboard_panels": 10,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--jaeger-url", default="http://127.0.0.1:16686")
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:3000")
    parser.add_argument(
        "--output", type=Path, default=Path("backend/runtime/observability-smoke.json")
    )
    args = parser.parse_args()
    report = run(args.base_url, args.jaeger_url, args.prometheus_url, args.grafana_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
