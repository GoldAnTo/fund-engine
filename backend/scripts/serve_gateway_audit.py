"""Serve a disposable, read-only Gateway fixture for browser acceptance.

Run from backend: .venv/bin/python scripts/serve_gateway_audit.py
Only binds 127.0.0.1:8019. No real provider or existing database is used.
"""
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    with TemporaryDirectory(prefix="fundclaw-browser-audit-") as directory:
        database_url = f"sqlite:///{directory}/audit.db"
        os.environ.update({
            "APP_ENV": "test", "DATABASE_URL": database_url,
            "GATEWAY_DATABASE_URL": database_url,
            "RESEARCH_TENANT_TOKENS": json.dumps({
                "fundclaw-audit-local-only": {"tenant_id": "team-a", "subject_id": "alice", "roles": []},
            }),
        })
        for key in ("LLM_API_KEY", "GILDATA_TOKEN", "TEST_DATABASE_URL", "NEO4J_URL"):
            os.environ.pop(key, None)
        import uvicorn
        from starlette.responses import JSONResponse

        from app.db import SessionLocal, engine
        from app.gateway_main import app
        from app.models.ledger import Base
        from app.models.research_gateway import ResearchConversation
        from tests.test_research_gateway_artifact_authorization import (
            complete_authorized_evidence,
        )
        from tests.test_research_gateway_automatic_adapter import seed_spec

        Base.metadata.create_all(engine)
        with SessionLocal() as session:
            complete, _, _ = complete_authorized_evidence(session)
            session.get(ResearchConversation, complete.conversation_id).title = "隔离验收样例：设备研究结果与证据"
            queued, _ = seed_spec(session)
            session.get(ResearchConversation, queued.conversation_id).title = "隔离验收样例：等待调度"
            session.commit()

        @app.middleware("http")
        async def read_only_fixture(request, call_next):
            if request.method not in {"GET", "HEAD"}:
                return JSONResponse({"detail": "This disposable audit fixture is read-only"}, status_code=405)
            return await call_next(request)

        print("Read-only synthetic Gateway audit: http://127.0.0.1:8019", flush=True)
        try:
            uvicorn.run(app, host="127.0.0.1", port=8019, log_level="warning")
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
