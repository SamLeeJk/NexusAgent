"""Two-phase, synthetic-data acceptance around a real application restart."""

import argparse
import http.cookiejar
import json
import urllib.request
from pathlib import Path
from uuid import uuid4


def run(base_url, phase, case_file):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def call(path, payload=None):
        request = urllib.request.Request(
            base_url + "/api" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "X-Nexus-Request": "1"},
        )
        with opener.open(request, timeout=30) as response:
            return json.load(response)

    assert call("/health")["mode"] == "demo", "Requires synthetic demo mode"
    call("/login", {"username": "bob", "password": "demo-bob-123"})
    if phase == "prepare":
        cid = call("/conversations", {})["id"]
        snapshot = call(
            f"/conversations/{cid}/messages",
            {"request_id": str(uuid4()), "message": "帮我申请 O2001 的退款"},
        )
        proposal = snapshot["proposals"][-1]
        assert proposal["status"] == "pending"
        case_file.parent.mkdir(parents=True, exist_ok=True)
        case_file.write_text(
            json.dumps({"cid": cid, "pid": proposal["id"]}), encoding="utf-8"
        )
        print(
            "PASS: pending proposal persisted; restart the application before finish."
        )
    else:
        saved = json.loads(case_file.read_text(encoding="utf-8"))
        cid, pid = saved["cid"], saved["pid"]
        snapshot = call(f"/conversations/{cid}")
        assert snapshot["proposals"][-1]["id"] == pid
        assert snapshot["proposals"][-1]["status"] == "pending"
        first = call(
            f"/conversations/{cid}/proposals/{pid}/decision", {"decision": "approve"}
        )
        second = call(
            f"/conversations/{cid}/proposals/{pid}/decision", {"decision": "approve"}
        )
        assert first["proposals"][-1]["status"] == "completed"
        assert first["proposals"][-1]["result"] == second["proposals"][-1]["result"]
        assert all(turn["status"] == "completed" for turn in second["turns"])
        print(
            "PASS: pending proposal resumed after restart; duplicate confirmation returned the same receipt."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["prepare", "finish"])
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--case-file",
        type=Path,
        default=Path(__file__).parents[1] / "runtime" / "restart-case.json",
    )
    args = parser.parse_args()
    run(args.base_url, args.phase, args.case_file)
