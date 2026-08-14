"""Fail-closed contract tests for the live acquisition smoke command."""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.acquisition.sources import (
    RetrievedEnvelope,
    RetrievedSearchResult,
    SourceDescriptor,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.datasources.exchanges.http import ExchangeHttpTransport
from app.datasources.exchanges.sse import SSEAnnouncementSource
from app.datasources.exchanges.szse import SZSEAnnouncementSource
from app.scripts import smoke_acquisition_sources


BACKEND_ROOT = Path(__file__).parents[1]
FIXED_NOW = datetime(2026, 8, 13, 4, 5, 6, tzinfo=UTC)
GENERATOR = "app.scripts.smoke_acquisition_sources"


class StubAdapter:
    def __init__(
        self,
        *,
        key: str = "sse",
        search_results: tuple[SourceReferenceValue, ...] = (),
        search_error: Exception | None = None,
        fetch_errors: dict[str, Exception] | None = None,
    ) -> None:
        self._descriptor = SourceDescriptor(
            adapter_key=key,
            provider_identity="Test Official Source",
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=frozenset({"example.test"}),
            allowed_source_roles=frozenset({"company_disclosure"}),
        )
        self.search_results = search_results
        self.search_error = search_error
        self.fetch_errors = fetch_errors or {}
        self.search_calls = 0
        self.fetch_calls: list[str] = []
        self.close_calls = 0

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._descriptor

    def search(self, query: str, cutoff: datetime):
        self.search_calls += 1
        if self.search_error is not None:
            raise self.search_error
        return self.search_results

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        self.fetch_calls.append(reference.external_record_id)
        if reference.external_record_id in self.fetch_errors:
            raise self.fetch_errors[reference.external_record_id]
        return RetrievedEnvelope(
            content=f"safe bytes {reference.external_record_id}".encode(),
            mime_type="application/pdf",
            final_url=reference.canonical_url,
            etag=None,
            last_modified=None,
            provider_request_id="request\nunsafe",
            metadata={"provider_identity": "Test Official Source"},
        )

    def close(self) -> None:
        self.close_calls += 1


def reference(number: int) -> SourceReferenceValue:
    return SourceReferenceValue(
        adapter_key="sse",
        external_record_id=f"sse:record-{number}",
        external_version="published:2026-08-13",
        canonical_url=f"https://example.test/document-{number}",
        title=f"Sensitive title {number}",
        published_at=FIXED_NOW,
        source_role="company_disclosure",
        fetch_locator={"record_id": f"sse:record-{number}"},
        metadata={"provider_identity": "Test Official Source"},
    )


def test_live_smoke_consumes_inline_search_envelope_without_adapter_fetch(tmp_path):
    value = reference(1)
    envelope = RetrievedEnvelope(
        content=b"inline provider body",
        mime_type="text/plain; charset=utf-8",
        final_url=value.canonical_url,
        etag=None,
        last_modified=None,
        provider_request_id=None,
        metadata={
            "adapter_key": "sse",
            "external_record_id": value.external_record_id,
            "provider_identity": "Test Official Source",
        },
    )
    adapter = StubAdapter(
        search_results=(RetrievedSearchResult(reference=value, envelope=envelope),)
    )

    exit_code, _output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--security-code",
        "600000",
        "--days",
        "2",
        adapter=adapter,
    )

    assert exit_code == 0
    assert adapter.fetch_calls == []
    assert report["counts"]["fetched"] == 1


def run_in_process(
    tmp_path: Path,
    *arguments: str,
    adapter: StubAdapter,
    sleeper=lambda _seconds: None,
):
    output = tmp_path / "report.json"
    exit_code = smoke_acquisition_sources.run(
        [*arguments, "--output", str(output)],
        adapter_factory=lambda _key: adapter,
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "a" * 40,
        sleeper=sleeper,
    )
    return exit_code, output, json.loads(output.read_text())


def test_injected_live_run_records_injected_execution_provenance(tmp_path: Path):
    adapter = StubAdapter(search_results=(reference(1),))

    exit_code, _output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--security-code",
        "600000",
        "--days",
        "2",
        adapter=adapter,
    )

    assert exit_code == 0
    assert report.get("schema_version") == "acquisition-source-smoke/v3"
    execution = report.get("execution")
    assert execution is not None
    assert set(execution) == {
        "generator",
        "mode",
        "network",
        "worktree_clean_at_start",
    }
    assert execution["generator"] == GENERATOR
    assert execution["mode"] == "in_process_injected"
    assert execution["network"] == "injected"
    assert isinstance(execution["worktree_clean_at_start"], bool) or execution[
        "worktree_clean_at_start"
    ] is None


def test_non_adapter_injection_cannot_claim_cli_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "injected-provenance.json"
    monkeypatch.delenv("GILDATA_TOKEN", raising=False)

    exit_code = smoke_acquisition_sources.run(
        [
            "--source",
            "gildata",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--output",
            str(output),
        ],
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "a" * 40,
        worktree_state_resolver=lambda: True,
        sleeper=lambda _seconds: None,
    )

    assert exit_code != 0
    report = json.loads(output.read_text())
    assert report["execution"] == {
        "generator": GENERATOR,
        "mode": "in_process_injected",
        "network": "injected",
        "worktree_clean_at_start": True,
    }


def test_git_attestation_is_anchored_to_source_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        stdout = "a" * 40 + "\n" if command[-2:] == ["rev-parse", "HEAD"] else ""
        return SimpleNamespace(stdout=stdout)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(smoke_acquisition_sources.subprocess, "run", fake_run)

    assert smoke_acquisition_sources._git_commit() == "a" * 40
    assert smoke_acquisition_sources._git_worktree_clean() is True
    repository_root = str(BACKEND_ROOT.parent.resolve())
    assert calls == [
        ["git", "-C", repository_root, "rev-parse", "HEAD"],
        ["git", "-C", repository_root, "status", "--porcelain"],
    ]


@pytest.mark.parametrize("resolver_outcome", ["raises", "raw_status"])
def test_worktree_state_resolution_fails_safe_without_path_or_status_leakage(
    tmp_path: Path,
    resolver_outcome: str,
):
    output = tmp_path / "provenance.json"
    adapter = StubAdapter(search_results=(reference(1),))

    def resolver():
        if resolver_outcome == "raises":
            raise OSError("git failed for /secret/worktree/path")
        return " M backend/secret-production-file.py"

    try:
        exit_code = smoke_acquisition_sources.run(
            [
                "--source",
                "sse",
                "--security-code",
                "600000",
                "--days",
                "2",
                "--output",
                str(output),
            ],
            adapter_factory=lambda _key: adapter,
            clock=lambda: FIXED_NOW,
            commit_resolver=lambda: "a" * 40,
            worktree_state_resolver=resolver,
            sleeper=lambda _seconds: None,
        )
    except OSError as exc:
        pytest.fail(f"worktree resolver exception escaped: {type(exc).__name__}")

    report = json.loads(output.read_text())
    assert exit_code == 0
    assert report["execution"]["worktree_clean_at_start"] is None
    serialized = output.read_text().casefold()
    assert "/secret/worktree/path" not in serialized
    assert "secret-production-file.py" not in serialized


def test_gildata_without_token_exits_nonzero_and_writes_safe_failure_report(
    tmp_path: Path,
):
    output = tmp_path / "gildata.json"
    env = dict(os.environ)
    env.pop("GILDATA_TOKEN", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.scripts.smoke_acquisition_sources",
            "--source",
            "gildata",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--output",
            str(output),
        ],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    report = json.loads(output.read_text())
    assert report["status"] == "failed"
    assert report["live_success"] is False
    assert report.get("schema_version") == "acquisition-source-smoke/v3"
    execution = report.get("execution")
    assert execution is not None
    assert set(execution) == {
        "generator",
        "mode",
        "network",
        "worktree_clean_at_start",
    }
    assert execution["generator"] == GENERATOR
    assert execution["mode"] == "cli_live"
    assert execution["network"] == "live"
    assert isinstance(execution["worktree_clean_at_start"], bool) or execution[
        "worktree_clean_at_start"
    ] is None
    assert report["errors"] == [
        {
            "category": "configuration",
            "retryable": False,
            "stage": "configuration",
        }
    ]
    serialized = output.read_text().casefold()
    assert "gildata_token" not in serialized
    assert "traceback" not in serialized
    assert completed.stderr == ""
    assert str(output) in completed.stdout
    assert str(BACKEND_ROOT).casefold() not in serialized


def test_default_cli_dry_run_records_non_network_execution_provenance(
    tmp_path: Path,
):
    output = tmp_path / "sse-dry-run.json"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            GENERATOR,
            "--source",
            "sse",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--dry-run",
            "--output",
            str(output),
        ],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    report = json.loads(output.read_text())
    assert report.get("schema_version") == "acquisition-source-smoke/v3"
    execution = report.get("execution")
    assert execution is not None
    assert set(execution) == {
        "generator",
        "mode",
        "network",
        "worktree_clean_at_start",
    }
    assert execution["generator"] == GENERATOR
    assert execution["mode"] == "cli_dry_run"
    assert execution["network"] == "none"
    assert isinstance(execution["worktree_clean_at_start"], bool) or execution[
        "worktree_clean_at_start"
    ] is None


def test_dry_run_validates_descriptor_without_search_fetch_or_live_success(
    tmp_path: Path, capsys
):
    adapter = StubAdapter()

    exit_code, output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--security-code",
        "600000",
        "--name",
        "Highly Sensitive Issuer",
        "--days",
        "2",
        "--dry-run",
        adapter=adapter,
    )

    assert exit_code == 0
    assert adapter.search_calls == 0
    assert adapter.fetch_calls == []
    assert adapter.close_calls == 1
    assert report["status"] == "dry_run"
    assert report["live_success"] is False
    assert report["query"]["window_start"] == "2026-08-12"
    assert report["query"]["window_end"] == "2026-08-13"
    assert report["source"] == {
        "adapter_key": "sse",
        "adapter_version": "sse-announcement-source/v1",
        "descriptor": {
            "allowed_hosts": ["example.test"],
            "allowed_schemes": ["https"],
            "allowed_source_roles": ["company_disclosure"],
            "provider_identity": "Test Official Source",
        },
    }
    combined = output.read_text() + capsys.readouterr().out
    assert "Highly Sensitive Issuer" not in combined
    assert "live_success=true" not in combined


def test_sse_live_uses_real_adapter_search_and_bounded_fetch_via_injected_transport(
    tmp_path: Path, capsys
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "query.sse.com.cn":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={
                    "pageHelp": {
                        "pageNo": 1,
                        "pageSize": 20,
                        "pageCount": 1,
                        "total": 1,
                    },
                    "result": [
                        {
                            "BULLETIN_ID": "smoke-1",
                            "SECURITY_CODE": "600000",
                            "TITLE": "Secret announcement body title",
                            "SSEDATE": "2026-08-12",
                            "URL": "/disclosure/listedinfo/announcement/c/new/2026-08-12/smoke.pdf",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "X-Request-ID": "safe-request-1",
            },
            content=b"%PDF-1.4\nsensitive raw bytes",
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = ExchangeHttpTransport(
        client,
        allowed_hosts=frozenset(
            {"query.sse.com.cn", "www.sse.com.cn", "static.sse.com.cn"}
        ),
    )
    adapter = SSEAnnouncementSource(transport=transport)

    output = tmp_path / "sse.json"
    exit_code = smoke_acquisition_sources.run(
        [
            "--source",
            "sse",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--output",
            str(output),
        ],
        adapter_factory=lambda _key: adapter,
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "b" * 40,
        sleeper=lambda _seconds: None,
    )

    report = json.loads(output.read_text())
    assert exit_code == 0
    assert len(requests) == 2
    assert requests[0].url.params["beginDate"] == "2026-08-12"
    assert requests[0].url.params["endDate"] == "2026-08-13"
    assert report["status"] == "succeeded"
    assert report["live_success"] is True
    assert report["counts"] == {
        "accepted": 1,
        "fetched": 1,
        "rejected": 0,
        "returned": 1,
    }
    assert report["references"] == [
        {
            "canonical_url_sha256": hashlib.sha256(
                "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-08-12/smoke.pdf".encode()
            ).hexdigest(),
            "external_version": "published:2026-08-12",
            "stable_id": "sse:smoke-1",
        }
    ]
    assert report["fetches"] == [
        {
            "byte_sha256": hashlib.sha256(
                b"%PDF-1.4\nsensitive raw bytes"
            ).hexdigest(),
            "byte_size": 28,
            "final_url_sha256": hashlib.sha256(
                "https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2026-08-12/smoke.pdf".encode()
            ).hexdigest(),
            "mime_type": "application/pdf",
            "provider_request_id": "safe-request-1",
            "stable_id": "sse:smoke-1",
        }
    ]
    serialized = output.read_text()
    stdout = capsys.readouterr().out
    assert "sensitive raw bytes" not in serialized.casefold()
    assert "Secret announcement body title" not in serialized
    assert "https://www.sse.com.cn" not in serialized
    assert "sse:smoke-1" in stdout
    assert "Secret announcement body title" not in stdout
    assert adapter._transport._client.is_closed


def test_szse_zero_results_is_a_successful_live_smoke_without_fixture_fallback(
    tmp_path: Path,
):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"announceCount": 0, "data": []},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = SZSEAnnouncementSource(
        transport=ExchangeHttpTransport(
            client,
            allowed_hosts=frozenset({"www.szse.cn", "disc.static.szse.cn"}),
        )
    )
    output = tmp_path / "szse.json"

    exit_code = smoke_acquisition_sources.run(
        [
            "--source",
            "szse",
            "--security-code",
            "000001",
            "--days",
            "2",
            "--output",
            str(output),
        ],
        adapter_factory=lambda _key: adapter,
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "c" * 40,
        sleeper=lambda _seconds: None,
    )

    report = json.loads(output.read_text())
    assert exit_code == 0
    assert len(requests) == 1
    assert json.loads(requests[0].content)["seDate"] == [
        "2026-08-12",
        "2026-08-13",
    ]
    assert report["status"] == "succeeded"
    assert report["live_success"] is True
    assert report["counts"]["returned"] == 0
    assert report["fetches"] == []
    assert client.is_closed


def test_provider_search_failure_is_sanitized_nonzero_and_closes_adapter(
    tmp_path: Path,
):
    adapter = StubAdapter(
        search_error=SourceUnavailable(
            "request failed at https://example.test?token=top-secret",
            retryable=True,
            diagnostics={"error_type": "timeout"},
        )
    )

    exit_code, output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--security-code",
        "600000",
        "--days",
        "2",
        adapter=adapter,
    )

    assert exit_code != 0
    assert adapter.close_calls == 1
    assert report["status"] == "failed"
    assert report["errors"] == [
        {"category": "provider_unavailable", "retryable": True, "stage": "search"}
    ]
    serialized = output.read_text().casefold()
    assert "top-secret" not in serialized
    assert "traceback" not in serialized


def test_nonretryable_fetch_failure_continues_and_is_partial_and_rate_limited(
    tmp_path: Path,
):
    adapter = StubAdapter(
        search_results=tuple(reference(number) for number in range(1, 5)),
        fetch_errors={
            "sse:record-2": SourceUnavailable(
                "nonretryable provider item failure",
                retryable=False,
                diagnostics={"error_type": "response_type"},
            )
        },
    )
    sleeps: list[float] = []

    exit_code, _output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--security-code",
        "600000",
        "--days",
        "2",
        adapter=adapter,
        sleeper=sleeps.append,
    )

    assert exit_code != 0
    assert adapter.fetch_calls == ["sse:record-1", "sse:record-2", "sse:record-3"]
    assert sleeps == [0.5, 0.5]
    assert adapter.close_calls == 1
    assert report["status"] == "partial"
    assert report["counts"] == {
        "accepted": 4,
        "fetched": 2,
        "rejected": 0,
        "returned": 4,
    }
    assert report["fetch_limit"] == 3
    assert report["not_fetched_count"] == 1
    assert report["errors"] == [
        {"category": "provider_unavailable", "retryable": False, "stage": "fetch"}
    ]
    assert report["fetches"][0]["provider_request_id"].startswith("sha256:")


@pytest.mark.parametrize("error_type", ["http_429", "timeout", "network"])
def test_retryable_fetch_unavailability_stops_remaining_fetches_without_sleep(
    tmp_path: Path,
    error_type: str,
):
    adapter = StubAdapter(
        search_results=tuple(reference(number) for number in range(1, 4)),
        fetch_errors={
            "sse:record-1": SourceUnavailable(
                "provider failed at /secret/worktree/path with token=must-not-leak",
                retryable=True,
                diagnostics={"error_type": error_type},
            )
        },
    )
    sleeps: list[float] = []

    exit_code, output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--security-code",
        "600000",
        "--days",
        "2",
        adapter=adapter,
        sleeper=sleeps.append,
    )

    assert exit_code != 0
    assert adapter.fetch_calls == ["sse:record-1"]
    assert sleeps == []
    assert adapter.close_calls == 1
    assert report["status"] == "failed"
    assert report["counts"] == {
        "accepted": 3,
        "fetched": 0,
        "rejected": 0,
        "returned": 3,
    }
    assert report["not_fetched_count"] == 2
    assert report["errors"] == [
        {"category": "provider_unavailable", "retryable": True, "stage": "fetch"}
    ]
    serialized = output.read_text().casefold()
    assert "must-not-leak" not in serialized
    assert "/secret/worktree/path" not in serialized


def test_persist_case_id_fails_closed_before_adapter_or_network(tmp_path: Path):
    output = tmp_path / "persist.json"
    factory_calls = 0

    def factory(_key: str):
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("adapter must not be constructed")

    exit_code = smoke_acquisition_sources.run(
        [
            "--source",
            "sse",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--persist-case-id",
            "case-123",
            "--output",
            str(output),
        ],
        adapter_factory=factory,
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "d" * 40,
        sleeper=lambda _seconds: None,
    )

    report = json.loads(output.read_text())
    assert exit_code != 0
    assert factory_calls == 0
    assert report["status"] == "failed"
    assert report["errors"] == [
        {
            "category": "persistence_unsupported",
            "retryable": False,
            "stage": "persistence",
        }
    ]


def test_missing_code_and_name_fails_before_adapter_and_writes_report(tmp_path: Path):
    adapter = StubAdapter()

    exit_code, _output, report = run_in_process(
        tmp_path,
        "--source",
        "sse",
        "--days",
        "2",
        adapter=adapter,
    )

    assert exit_code != 0
    assert adapter.search_calls == 0
    assert adapter.close_calls == 0
    assert report["errors"] == [
        {"category": "invalid_arguments", "retryable": False, "stage": "validation"}
    ]


def test_existing_report_is_atomically_replaced_but_symlink_is_rejected(
    tmp_path: Path,
):
    adapter = StubAdapter()
    output = tmp_path / "replace.json"
    output.write_text("old")
    target = tmp_path / "target.json"
    target.write_text("do not replace")
    symlink = tmp_path / "link.json"
    symlink.symlink_to(target)

    replaced = smoke_acquisition_sources.run(
        [
            "--source",
            "sse",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--dry-run",
            "--output",
            str(output),
        ],
        adapter_factory=lambda _key: adapter,
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "e" * 40,
        sleeper=lambda _seconds: None,
    )
    rejected = smoke_acquisition_sources.run(
        [
            "--source",
            "sse",
            "--security-code",
            "600000",
            "--days",
            "2",
            "--dry-run",
            "--output",
            str(symlink),
        ],
        adapter_factory=lambda _key: StubAdapter(),
        clock=lambda: FIXED_NOW,
        commit_resolver=lambda: "f" * 40,
        sleeper=lambda _seconds: None,
    )

    assert replaced == 0
    assert json.loads(output.read_text())["status"] == "dry_run"
    assert not tuple(tmp_path.glob(".replace.json.*"))
    assert rejected != 0
    assert symlink.is_symlink()
    assert target.read_text() == "do not replace"
