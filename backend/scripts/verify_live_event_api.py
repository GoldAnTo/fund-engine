"""Verify the default event API with signed OIDC over real local HTTP.

This is intentionally separate from TestClient coverage: it starts a local
RS256 JWKS endpoint and Uvicorn, creates a Case, and reads the event desk back
through the public V1 route. No opaque bearer fallback, fixture, or mock
adapter participates.
"""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Protocol

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


ROOT = Path(__file__).resolve().parents[1]
TENANT = "live-verifier-team"
AUDIENCE = "fund-engine-live-verifier"
KEY_ID = "live-verifier-rs256-key"
STARTUP_TIMEOUT_SECONDS = 10.0
READINESS_REQUEST_TIMEOUT_SECONDS = 0.5
READINESS_SLEEP_SECONDS = 0.1
MAX_REQUEST_TIMEOUT_SECONDS = 2.0


class _ProcessPoller(Protocol):
    def poll(self) -> int | None: ...


class _ReadinessRequest(Protocol):
    def __call__(
        self,
        url: str,
        *,
        token: str,
        timeout: float,
    ) -> tuple[int, dict]: ...


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    url: str,
    *,
    token: str,
    timeout: float = MAX_REQUEST_TIMEOUT_SECONDS,
    method: str = "GET",
    body: dict | None = None,
) -> tuple[int, dict]:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("request timeout must be a positive finite value")
    bounded_timeout = min(timeout, MAX_REQUEST_TIMEOUT_SECONDS)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        method=method,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=bounded_timeout) as response:
            return response.status, json.loads(response.read())
    except TimeoutError:
        # Uvicorn can accept a socket before the application is ready to
        # respond. Treat that exactly like a not-yet-listening server so the
        # bounded readiness loop retries the real API check.
        return 0, {}
    except urllib.error.HTTPError as exc:
        # A server can return an empty, non-JSON response while Uvicorn is
        # still starting. The readiness loop needs the status to continue
        # probing; it must not turn that transient response into a JSON error.
        raw = exc.read()
        try:
            decoded = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            decoded = {}
        return exc.code, decoded if isinstance(decoded, dict) else {}


def _wait_until_ready(
    *,
    base_url: str,
    token: str,
    process: _ProcessPoller,
    startup_timeout: float = STARTUP_TIMEOUT_SECONDS,
    monotonic_clock: Callable[[], float] = time.monotonic,
    request_func: _ReadinessRequest = _request,
    sleep_func: Callable[[float], None] = time.sleep,
) -> int:
    deadline = monotonic_clock() + max(0.0, startup_timeout)
    last_status = 0
    while process.poll() is None:
        remaining = deadline - monotonic_clock()
        if remaining <= 0:
            break
        try:
            last_status, _ = request_func(
                f"{base_url}/event-research",
                token=token,
                timeout=min(READINESS_REQUEST_TIMEOUT_SECONDS, remaining),
            )
        except urllib.error.URLError:
            last_status = 0
        if last_status == 200:
            return last_status
        remaining = deadline - monotonic_clock()
        if remaining <= 0:
            break
        sleep_func(min(READINESS_SLEEP_SECONDS, remaining))
    return last_status


def _signing_material() -> tuple[rsa.RSAPrivateKey, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(
        jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key())
    )
    public_jwk.update({"kid": KEY_ID, "use": "sig", "alg": "RS256"})
    return private_key, {"keys": [public_jwk]}


def _jwks_handler(document: dict[str, object]) -> type[BaseHTTPRequestHandler]:
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")

    class JWKSHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/jwks":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return JWKSHandler


def _start_jwks_server(
    document: dict[str, object],
) -> tuple[ThreadingHTTPServer, Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _jwks_handler(document))
    server.daemon_threads = True
    thread = Thread(
        target=server.serve_forever,
        name="live-verifier-jwks",
        daemon=True,
    )
    try:
        thread.start()
    except Exception:
        server.server_close()
        raise
    return server, thread


def _issue_token(private_key: rsa.RSAPrivateKey, *, issuer: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": issuer,
            "aud": AUDIENCE,
            "sub": "live-verifier-user",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=10)).timestamp()),
            "tenant_id": TENANT,
            "name": "Live API Verifier",
            "roles": ["case_administrator"],
        },
        private_key,
        algorithm="RS256",
        headers={"kid": KEY_ID, "typ": "JWT"},
    )


def _child_environment(
    *,
    database_url: str,
    issuer: str,
    jwks_url: str,
) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("RESEARCH_TENANT_TOKENS", None)
    env.update(
        {
            "DATABASE_URL": database_url,
            "OIDC_ISSUER": issuer,
            "OIDC_AUDIENCE": AUDIENCE,
            "OIDC_JWKS_URL": jwks_url,
            "OIDC_TENANT_CLAIM": "tenant_id",
            "OIDC_ALLOW_INSECURE_HTTP": "true",
            "APP_ENV": "local",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    return env


def _stop_process(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _stop_jwks_server(server: ThreadingHTTPServer, thread: Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _startup_failure(process: subprocess.Popen[str], *, last_status: int) -> str:
    return_code = process.poll()
    if return_code is None:
        return f"live API did not become ready (last HTTP status: {last_status})"
    stderr = process.stderr.read() if process.stderr is not None else ""
    stderr_tail = stderr[-2000:].strip()
    detail = f"; uvicorn stderr: {stderr_tail}" if stderr_tail else ""
    return (
        "live API process exited before readiness "
        f"(return code: {return_code}, last HTTP status: {last_status}{detail})"
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fund-engine-live-") as directory:
        private_key, jwks = _signing_material()
        jwks_server, jwks_thread = _start_jwks_server(jwks)
        process: subprocess.Popen[str] | None = None
        try:
            jwks_port = int(jwks_server.server_address[1])
            issuer = f"http://127.0.0.1:{jwks_port}"
            token = _issue_token(private_key, issuer=issuer)
            database_url = f"sqlite:///{Path(directory) / 'live.db'}"
            env = _child_environment(
                database_url=database_url,
                issuer=issuer,
                jwks_url=f"{issuer}/jwks",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "from app.models.ledger import Base; "
                        "from app.db import engine; "
                        "Base.metadata.create_all(engine)"
                    ),
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
            last_status = _wait_until_ready(
                base_url=base_url,
                token=token,
                process=process,
            )
            if last_status != 200:
                raise RuntimeError(_startup_failure(process, last_status=last_status))

            title = "真实 API 验收事件"
            created_status, created = _request(
                f"{base_url}/event-research",
                token=token,
                method="POST",
                body={
                    "raw_input": "公司披露新增经营数据，市场等待后续验证。",
                    "event_title": title,
                    "company_name": "示例公司",
                    "ticker": "000001",
                    "research_question": "新增经营数据是否改变收入与利润率预期？",
                    "candidate_factors": ["订单", "收入", "利润率"],
                    "research_protocol_required": False,
                },
            )
            if created_status != 201 or not created.get("case_id"):
                raise RuntimeError(f"event creation failed: {created}")
            if created["lifecycle"]["status"] != "awaiting_key_review":
                raise RuntimeError(f"unexpected initial lifecycle: {created}")

            listed_status, listed = _request(
                f"{base_url}/event-research",
                token=token,
            )
            if listed_status != 200 or not any(
                item.get("case_id") == created["case_id"]
                and item.get("event_title") == title
                for item in listed.get("items", [])
            ):
                raise RuntimeError(f"created Case missing from event desk: {listed}")
            print("PASS: authenticated live API created and listed the same Case")
            return 0
        finally:
            _stop_process(process)
            _stop_jwks_server(jwks_server, jwks_thread)


if __name__ == "__main__":
    raise SystemExit(main())
