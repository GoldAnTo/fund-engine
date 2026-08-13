"""Run an explicit, secret-safe smoke check against one acquisition source."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedEnvelope,
    RetrievedSearchResult,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.datasources.exchanges.http import SourceProtocolError
from app.datasources.exchanges.sse import SSEAnnouncementSource
from app.datasources.exchanges.szse import SZSEAnnouncementSource
from app.datasources.gildata.client import GildataMCPClient
from app.datasources.gildata.research_source import GildataResearchSource


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SCHEMA_VERSION = "acquisition-source-smoke/v1"
_FETCH_LIMIT = 3
_FETCH_INTERVAL_SECONDS = 0.5
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ADAPTER_VERSIONS = {
    "gildata": "gildata-research-source/v1",
    "sse": "sse-announcement-source/v1",
    "szse": "szse-announcement-source/v1",
}


AdapterFactory = Callable[[str], SourceAdapter]
Clock = Callable[[], datetime]
CommitResolver = Callable[[], str]
Sleeper = Callable[[float], None]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify one governed acquisition source without persistence"
    )
    parser.add_argument("--source", required=True, choices=("gildata", "sse", "szse"))
    parser.add_argument("--security-code")
    parser.add_argument("--name")
    parser.add_argument("--days", type=int)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--persist-case-id")
    return parser


def _window(args: argparse.Namespace, *, today: date) -> tuple[date, date]:
    explicit = args.start is not None or args.end is not None
    if args.days is not None and explicit:
        raise ValueError("days cannot be combined with start or end")
    if args.days is not None:
        if args.days <= 0 or args.days > 366:
            raise ValueError("days must be between 1 and 366")
        end = today
        return end - timedelta(days=args.days - 1), end
    if args.start is None or args.end is None:
        raise ValueError("provide days or both start and end")
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        raise ValueError("start and end must be ISO dates") from None
    if start > end:
        raise ValueError("start must not be after end")
    return start, end


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    value = completed.stdout.strip()
    if len(value) == 40 and all(
        character in "0123456789abcdef" for character in value
    ):
        return value
    return "unknown"


def _base_report(
    args: argparse.Namespace,
    *,
    now: datetime,
    commit: str,
    window: tuple[date, date] | None,
) -> dict[str, Any]:
    start, end = window if window is not None else (None, None)
    query = " ".join(
        value
        for value in (
            args.name,
            args.security_code,
            start.isoformat() if start else args.start,
            end.isoformat() if end else args.end,
            str(args.days) if args.days is not None else None,
        )
        if value
    )
    return {
        "schema_version": _SCHEMA_VERSION,
        "timestamp_utc": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "git_commit": commit,
        "status": "failed",
        "live_success": False,
        "source": {"adapter_key": args.source},
        "query": {
            "sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "window_start": start.isoformat() if start else None,
            "window_end": end.isoformat() if end else None,
        },
        "counts": {"returned": 0, "accepted": 0, "rejected": 0, "fetched": 0},
        "references": [],
        "fetches": [],
        "errors": [],
    }


def _atomic_write(path: Path, report: dict[str, Any]) -> None:
    if (
        not path.parent.is_dir()
        or _has_symlink_component(path.parent)
        or path.is_symlink()
        or (path.exists() and not path.is_file())
    ):
        raise ValueError("unsafe output path")
    payload = (
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary_name = handle.name
            os.chmod(temporary_name, 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _has_symlink_component(path: Path) -> bool:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _build_adapter(key: str) -> SourceAdapter:
    if key == "gildata":
        return GildataResearchSource(GildataMCPClient.from_env())
    if key == "sse":
        return SSEAnnouncementSource()
    if key == "szse":
        return SZSEAnnouncementSource()
    raise ValueError("unsupported acquisition source")


def _descriptor_value(descriptor: SourceDescriptor) -> dict[str, Any]:
    return {
        "provider_identity": descriptor.provider_identity,
        "allowed_schemes": sorted(descriptor.allowed_schemes),
        "allowed_hosts": sorted(descriptor.allowed_hosts),
        "allowed_source_roles": sorted(descriptor.allowed_source_roles),
    }


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    if _SAFE_IDENTIFIER.fullmatch(value) is not None:
        return value
    return f"sha256:{_digest_text(value)}"


def _safe_error(exc: Exception, *, stage: str) -> dict[str, Any]:
    if isinstance(exc, SourceUnavailable):
        category = "provider_unavailable"
        retryable = exc.retryable
    elif isinstance(exc, SourceProtocolError):
        category = "provider_protocol"
        retryable = False
    elif isinstance(exc, ValueError):
        category = "adapter_validation"
        retryable = False
    else:
        category = "internal"
        retryable = False
    return {"category": category, "retryable": retryable, "stage": stage}


def _reference_value(reference: SourceReferenceValue) -> dict[str, Any]:
    return {
        "stable_id": _safe_identifier(reference.external_record_id),
        "external_version": _safe_identifier(reference.external_version),
        "canonical_url_sha256": _digest_text(reference.canonical_url),
    }


def _fetch_value(
    reference: SourceReferenceValue, envelope: RetrievedEnvelope
) -> dict[str, Any]:
    return {
        "stable_id": _safe_identifier(reference.external_record_id),
        "mime_type": envelope.mime_type,
        "byte_sha256": hashlib.sha256(envelope.content).hexdigest(),
        "byte_size": len(envelope.content),
        "final_url_sha256": _digest_text(envelope.final_url),
        "provider_request_id": _safe_identifier(envelope.provider_request_id),
    }


def _print_summary(report: dict[str, Any], output: Path) -> None:
    counts = report["counts"]
    for reference in report["references"]:
        print(f"stable_id={reference['stable_id']}")
    print(
        f"status={report['status']} returned={counts['returned']} "
        f"fetched={counts['fetched']} errors={len(report['errors'])} report={output}"
    )


def _validate_args(args: argparse.Namespace, window: tuple[date, date] | None) -> bool:
    if window is None or (not args.security_code and not args.name):
        return False
    for value in (args.security_code, args.name):
        if value is not None and (
            not value.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return False
    return True


def _run_live(
    *,
    adapter: SourceAdapter,
    args: argparse.Namespace,
    report: dict[str, Any],
    window: tuple[date, date],
    sleeper: Sleeper,
) -> int:
    start, end = window
    query = " ".join(
        part
        for part in (
            args.name.strip() if args.name else None,
            args.security_code.strip() if args.security_code else None,
            start.isoformat(),
            end.isoformat(),
        )
        if part
    )
    cutoff = datetime.combine(end, datetime_time.max, tzinfo=_SHANGHAI)
    try:
        results = adapter.search(query, cutoff)
    except Exception as exc:
        report["errors"].append(_safe_error(exc, stage="search"))
        return 2

    report["counts"]["returned"] = len(results)
    accepted: list[tuple[SourceReferenceValue, RetrievedEnvelope | None]] = []
    for item in results:
        if isinstance(item, RejectedSearchItem):
            report["counts"]["rejected"] += 1
            report.setdefault("rejections", []).append(
                {
                    "stable_id": _safe_identifier(item.external_record_id),
                    "reason": _safe_identifier(item.reason),
                }
            )
            report["errors"].append(
                {
                    "category": "provider_item_rejected",
                    "retryable": item.reason == "source_unavailable",
                    "stage": "search",
                }
            )
            continue
        inline_envelope = None
        if isinstance(item, RetrievedSearchResult):
            inline_envelope = item.envelope
            item = item.reference
        if not isinstance(item, SourceReferenceValue):
            report["errors"].append(
                {
                    "category": "provider_protocol",
                    "retryable": False,
                    "stage": "search",
                }
            )
            continue
        try:
            adapter.descriptor.validate_reference(item)
        except Exception as exc:
            report["errors"].append(_safe_error(exc, stage="search"))
            continue
        accepted.append((item, inline_envelope))
        report["references"].append(_reference_value(item))
    report["counts"]["accepted"] = len(accepted)
    report["fetch_limit"] = _FETCH_LIMIT
    report["not_fetched_count"] = max(0, len(accepted) - _FETCH_LIMIT)

    for index, (reference, inline_envelope) in enumerate(accepted[:_FETCH_LIMIT]):
        if index:
            sleeper(_FETCH_INTERVAL_SECONDS)
        try:
            envelope = inline_envelope or adapter.fetch(reference)
        except Exception as exc:
            report["errors"].append(_safe_error(exc, stage="fetch"))
            continue
        report["fetches"].append(_fetch_value(reference, envelope))
        report["counts"]["fetched"] += 1

    if report["errors"]:
        report["status"] = "partial" if report["counts"]["fetched"] else "failed"
        return 2
    report["status"] = "succeeded"
    report["live_success"] = True
    return 0


def run(
    argv: Sequence[str] | None = None,
    *,
    adapter_factory: AdapterFactory | None = None,
    clock: Clock = lambda: datetime.now(UTC),
    commit_resolver: CommitResolver = _git_commit,
    sleeper: Sleeper = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    try:
        _atomic_write_path_check(args.output)
    except ValueError:
        print(f"status=failed returned=0 fetched=0 errors=1 report={args.output}")
        return 2
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    try:
        window = _window(args, today=now.astimezone(_SHANGHAI).date())
    except ValueError:
        window = None
    report = _base_report(args, now=now, commit=commit_resolver(), window=window)
    if not _validate_args(args, window):
        report["errors"] = [
            {"category": "invalid_arguments", "retryable": False, "stage": "validation"}
        ]
        _atomic_write(args.output, report)
        _print_summary(report, args.output)
        return 2
    if args.persist_case_id:
        report["errors"] = [
            {
                "category": "persistence_unsupported",
                "retryable": False,
                "stage": "persistence",
            }
        ]
        _atomic_write(args.output, report)
        _print_summary(report, args.output)
        return 2
    if (
        args.source == "gildata"
        and adapter_factory is None
        and not os.getenv("GILDATA_TOKEN")
    ):
        report["errors"] = [
            {
                "category": "configuration",
                "retryable": False,
                "stage": "configuration",
            }
        ]
        _atomic_write(args.output, report)
        _print_summary(report, args.output)
        return 2
    adapter: SourceAdapter | None = None
    exit_code = 2
    try:
        adapter = (adapter_factory or _build_adapter)(args.source)
        descriptor = adapter.descriptor
        if descriptor.adapter_key != args.source:
            raise ValueError("adapter key does not match requested source")
        report["source"] = {
            "adapter_key": descriptor.adapter_key,
            "adapter_version": _ADAPTER_VERSIONS[args.source],
            "descriptor": _descriptor_value(descriptor),
        }
        if args.dry_run:
            report["status"] = "dry_run"
            exit_code = 0
        else:
            assert window is not None
            exit_code = _run_live(
                adapter=adapter,
                args=args,
                report=report,
                window=window,
                sleeper=sleeper,
            )
    except Exception as exc:
        report["errors"].append(_safe_error(exc, stage="configuration"))
    finally:
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                report["errors"].append(_safe_error(exc, stage="close"))
                report["status"] = "failed" if not report["fetches"] else "partial"
                report["live_success"] = False
                exit_code = 2
    _atomic_write(args.output, report)
    _print_summary(report, args.output)
    return exit_code


def _atomic_write_path_check(path: Path) -> None:
    if (
        not path.parent.is_dir()
        or _has_symlink_component(path.parent)
        or path.is_symlink()
        or (path.exists() and not path.is_file())
    ):
        raise ValueError("unsafe output path")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
