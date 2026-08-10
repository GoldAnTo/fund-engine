"""Verify the default event API against a real local HTTP server.

This is intentionally separate from TestClient coverage: it starts Uvicorn,
uses an opaque tenant bearer token, creates a Case, and reads the event desk
back through the public V1 route.  No fixture or mock adapter participates.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "live-verifier-token"
TENANT = "live-verifier-team"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, dict]:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        method=method,
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fund-engine-live-") as directory:
        database_url = f"sqlite:///{Path(directory) / 'live.db'}"
        env = {
            **os.environ,
            "DATABASE_URL": database_url,
            "RESEARCH_TENANT_TOKENS": json.dumps({TOKEN: TENANT}),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.models.ledger import Base; from app.db import engine; Base.metadata.create_all(engine)",
            ],
            cwd=ROOT,
            env=env,
            check=True,
        )
        port = _free_port()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        base_url = f"http://127.0.0.1:{port}/api/v1"
        try:
            for _ in range(30):
                try:
                    status, _ = _request(f"{base_url}/event-research")
                except urllib.error.URLError:
                    time.sleep(0.1)
                    continue
                if status == 200:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("live API did not become ready")

            title = "真实 API 验收事件"
            created_status, created = _request(
                f"{base_url}/event-research",
                method="POST",
                body={
                    "raw_input": "公司披露新增经营数据，市场等待后续验证。",
                    "event_title": title,
                    "company_name": "示例公司",
                    "ticker": "000001",
                    "research_question": "新增经营数据是否改变收入与利润率预期？",
                    "candidate_factors": ["订单", "收入", "利润率"],
                    "research_protocol_required": False,
                    "created_by": "human:live-verifier",
                },
            )
            if created_status != 201 or not created.get("case_id"):
                raise RuntimeError(f"event creation failed: {created}")
            if created["lifecycle"]["status"] != "awaiting_key_review":
                raise RuntimeError(f"unexpected initial lifecycle: {created}")

            listed_status, listed = _request(f"{base_url}/event-research")
            if listed_status != 200 or not any(
                item.get("case_id") == created["case_id"]
                and item.get("event_title") == title
                for item in listed.get("items", [])
            ):
                raise RuntimeError(f"created Case missing from event desk: {listed}")
            print("PASS: authenticated live API created and listed the same Case")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
