"""Shared, fail-closed setup for auditable walkthrough commands."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final

from app.api.v1.tenant_context import _configured_tokens


class WalkthroughConfigurationError(RuntimeError):
    """A required host-owned walkthrough setting is unavailable."""


class WalkthroughResponseError(RuntimeError):
    """A walkthrough API response no longer matches its required contract."""


_OMIT: Final = object()
_AUDIT_FORBIDDEN_KEYS: Final = frozenset(
    {
        "api_key",
        "authorization",
        "body",
        "candidates",
        "content",
        "detail",
        "headers",
        "json",
        "message",
        "normalized_text",
        "params",
        "provider_url",
        "query",
        "quote",
        "raw",
        "request",
        "response_body",
        "rows",
        "text",
        "token",
        "url",
        "verbatim_text",
    }
)
_AUDIT_SAFE_STRING_KEYS: Final = frozenset(
    {
        "assessment_id",
        "case_id",
        "category",
        "claim_id",
        "claim_type",
        "code",
        "doc_kind",
        "document_id",
        "error_code",
        "fund_code",
        "id",
        "method",
        "mode",
        "outcome",
        "path",
        "period",
        "proposal_id",
        "report_date",
        "run_id",
        "stage",
        "state",
        "status",
        "step",
        "thesis_id",
        "tool",
        "version_id",
    }
)
_AUDIT_SAFE_LIST_KEYS: Final = frozenset(
    {
        "assessment_ids",
        "claim_ids",
        "error_codes",
        "ids",
        "stages",
        "tools",
        "version_ids",
    }
)
_URL_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_URL_IGNORED_CONTROLS = str.maketrans("", "", "\t\n\r")
_BEARER_VALUE = re.compile(r"\bbearer\s+(?P<value>[^\s,:;{}\[\]\"']+)", re.IGNORECASE)
_SAFE_BEARER_NOUNS: Final = frozenset(
    {
        "bond",
        "bonds",
        "instrument",
        "instruments",
        "note",
        "notes",
        "security",
        "securities",
    }
)
_HIGH_CONFIDENCE_SECRET_VALUE = re.compile(
    r"(?:"
    r"\bsk[-_](?:proj[-_])?[A-Za-z0-9_-]{6,}"
    r"|\bgh[pousr]_[A-Za-z0-9_=-]{8,}"
    r"|\bgithub_pat_[A-Za-z0-9_=-]{16,}"
    r"|\bglpat-[A-Za-z0-9_-]{16,}"
    r"|\bhf_[A-Za-z0-9]{16,}"
    r"|\bxox[baprs]-[A-Za-z0-9-]{8,}"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\bAIza[A-Za-z0-9_-]{20,}"
    r"|\bnpm_[A-Za-z0-9]{16,}"
    r"|\bpypi-[A-Za-z0-9_-]{16,}"
    r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"
    r"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
    r")"
)
_ASSIGNMENT_LABEL = re.compile(r"(?P<label>[^:=]{1,256})[=:]")
_ASSIGNMENT_LABEL_UNIT = re.compile(r"[A-Za-z0-9_.-]+")
_ASSIGNMENT_LABEL_TOKEN = re.compile(r"[A-Za-z0-9]+")
_AUDIT_SAFE_ATOM = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")
_AUDIT_SAFE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")
_PERSISTED_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_CREDENTIAL_KEY_FRAGMENTS: Final = (
    "accesskey",
    "apikey",
    "authentication",
    "authorization",
    "bearer",
    "credential",
    "jwt",
    "passphrase",
    "passwd",
    "password",
    "privatekey",
    "secret",
    "signature",
    "token",
)
_CREDENTIAL_KEY_ALIASES: Final = frozenset({"auth", "authheader", "authkey", "pwd"})
_CREDENTIAL_LABEL_QUALIFIERS: Final = frozenset(
    {
        "digest",
        "hash",
        "header",
        "headers",
        "id",
        "identifier",
        "identifiers",
        "ids",
        "key",
        "keys",
        "material",
        "materials",
        "value",
        "values",
    }
)
_CREDENTIAL_KEY_PREFIXES: Final = frozenset({"access", "api", "client", "private"})
_FUND_CODE = re.compile(r"^(\d{6})(?:\.OF)?$")
_ISO_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:$|T)")
_WALKTHROUGH_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_ARTIFACT_MODE: Final = 0o600
_KNOWN_EXTRACT_REASONS: Final = {
    "该版本没有附加来源片段，无法抽取陈述": "no_source_spans",
    "LLM 抽取调用完成但未返回任何陈述（可能为纯结构化或合规受限）": (
        "llm_returned_no_candidates"
    ),
    "提取未产生陈述": "zero_candidates_reported",
}
_SAFE_EXTRACT_REASON_CATEGORIES: Final = frozenset(
    {
        "llm_returned_no_candidates",
        "no_source_spans",
        "reason_reported_with_candidates",
        "zero_candidates_reported",
        "zero_candidates_unspecified",
    }
)


@dataclass(frozen=True)
class WalkthroughPaths:
    state: Path
    jsonl: Path
    summary: Path


def configured_research_headers() -> dict[str, str]:
    """Return one configured bearer credential without exposing its value."""
    configured = _configured_tokens()
    if not configured:
        raise WalkthroughConfigurationError(
            "RESEARCH_TENANT_TOKENS must configure a bearer token for the walkthrough"
        )
    token, _actor = configured[0]
    return {"Authorization": f"Bearer {token}"}


def _walkthrough_int_setting(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise WalkthroughConfigurationError(f"{name} is invalid") from None


def _walkthrough_float_setting(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        raise WalkthroughConfigurationError(f"{name} is invalid") from None


def validate_live_walkthrough_environment(*, require_live_llm: bool) -> None:
    """Fail closed on host-owned rights and live extraction bounds."""
    configured_research_headers()

    from app.datasources.gildata.governance import GildataEvidenceRights

    rights = GildataEvidenceRights.from_env()
    if not rights.formal_evidence_allowed:
        raise WalkthroughConfigurationError(
            "GILDATA_ALLOW_AI_PROCESSING and GILDATA_ALLOW_DISPLAY "
            "must both be literal true"
        )
    if not require_live_llm:
        return

    from app.ai.client import (
        DEFAULT_MAX_ATTEMPTS,
        DEFAULT_MAX_OUTPUT_TOKENS,
        DEFAULT_TIMEOUT_SECONDS,
        LLMClient,
    )

    if not (os.getenv("LLM_API_KEY") or "").strip():
        raise WalkthroughConfigurationError(
            "LLM_API_KEY must configure live LLM access"
        )
    max_attempts = _walkthrough_int_setting("LLM_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
    if max_attempts < 2:
        raise WalkthroughConfigurationError("LLM_MAX_ATTEMPTS must be at least 2")
    timeout_seconds = _walkthrough_float_setting(
        "LLM_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
    )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise WalkthroughConfigurationError(
            "LLM_TIMEOUT_SECONDS must be finite and positive"
        )
    output_tokens = _walkthrough_int_setting(
        "LLM_MAX_OUTPUT_TOKENS", DEFAULT_MAX_OUTPUT_TOKENS
    )
    if output_tokens < DEFAULT_MAX_OUTPUT_TOKENS:
        raise WalkthroughConfigurationError(
            "LLM_MAX_OUTPUT_TOKENS must meet the tested extraction floor"
        )

    try:
        llm_client = LLMClient.from_env()
    except (TypeError, ValueError, RuntimeError):
        raise WalkthroughConfigurationError(
            "LLM runtime configuration is invalid"
        ) from None
    if llm_client._mock:
        raise WalkthroughConfigurationError(
            "LLM_API_KEY must configure live LLM access"
        )


def summarize_extract_response(response: object) -> dict[str, object]:
    """Validate and safely summarize the current extraction API contract."""
    if not isinstance(response, dict):
        raise WalkthroughResponseError("extract response must be an object")
    count = response.get("candidate_count")
    candidates = response.get("candidates")
    reason = response.get("reason")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise WalkthroughResponseError(
            "extract candidate_count must be a non-negative integer"
        )
    if not isinstance(candidates, list) or count != len(candidates):
        raise WalkthroughResponseError(
            "extract candidate_count does not match candidates"
        )
    if reason is not None and not isinstance(reason, str):
        raise WalkthroughResponseError("extract reason must be a string or null")

    claim_types: dict[str, int] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise WalkthroughResponseError("extract candidate must be an object")
        claim_type = candidate.get("claim_type")
        if not isinstance(claim_type, str) or not claim_type.strip():
            raise WalkthroughResponseError(
                "extract candidate claim_type must be a non-empty string"
            )
        claim_types[claim_type] = claim_types.get(claim_type, 0) + 1
    return {
        "candidate_count": count,
        "claim_types": claim_types,
        "reason": classify_extract_reason(count, reason),
    }


def classify_extract_reason(candidate_count: int, reason: str | None) -> str | None:
    """Map provider-controlled extraction prose onto a closed audit category."""
    if candidate_count > 0:
        return "reason_reported_with_candidates" if reason else None
    if reason in _SAFE_EXTRACT_REASON_CATEGORIES - {"reason_reported_with_candidates"}:
        return reason
    if not reason:
        return "zero_candidates_unspecified"
    return _KNOWN_EXTRACT_REASONS.get(reason, "zero_candidates_reported")


def _is_credential_key(key: str) -> bool:
    normalized = "".join(char for char in key.casefold() if char.isalnum())
    return (
        normalized in _CREDENTIAL_KEY_ALIASES
        or normalized.endswith("pwd")
        or any(fragment in normalized for fragment in _CREDENTIAL_KEY_FRAGMENTS)
    )


def _is_credential_assignment_label(label: str) -> bool:
    """Recognize credential-shaped assignment labels without scanning prose."""
    units = _ASSIGNMENT_LABEL_UNIT.findall(label)
    if not units:
        return False
    # Identifier-shaped aliases retain separators, so ``token_count`` remains
    # fail-closed even though the prose ``Token count: 0`` below is harmless.
    if _is_credential_key(units[-1]):
        return True

    tokens = [token.casefold() for token in _ASSIGNMENT_LABEL_TOKEN.findall(label)]
    index = len(tokens) - 1
    qualifiers: set[str] = set()
    while index >= 0 and tokens[index] in _CREDENTIAL_LABEL_QUALIFIERS:
        qualifiers.add(tokens[index])
        index -= 1
    if not qualifiers or index < 0:
        return False
    root = tokens[index]
    if _is_credential_key(root):
        return True
    return root in _CREDENTIAL_KEY_PREFIXES and bool(qualifiers & {"key", "keys"})


def _contains_unsafe_string(value: str) -> bool:
    if _HIGH_CONFIDENCE_SECRET_VALUE.search(value):
        return True
    if _URL_SCHEME.search(value.translate(_URL_IGNORED_CONTROLS)):
        return True
    for match in _BEARER_VALUE.finditer(value):
        if match.group("value").casefold() not in _SAFE_BEARER_NOUNS:
            return True
    return any(
        _is_credential_assignment_label(match.group("label"))
        for match in _ASSIGNMENT_LABEL.finditer(value)
    )


def validate_persisted_json(
    value: object,
    *,
    forbidden_keys: frozenset[str] = frozenset(),
    forbidden_list_keys: frozenset[str] = frozenset(),
) -> None:
    """Validate the exact JSON-only, credential-free persistence boundary."""
    normalized_forbidden = frozenset(key.casefold() for key in forbidden_keys)
    normalized_list_keys = frozenset(key.casefold() for key in forbidden_list_keys)

    def validate(item: object) -> None:
        item_type = type(item)
        if item is None or item_type is bool or item_type is int:
            return
        if item_type is float:
            if not math.isfinite(item):
                raise WalkthroughResponseError("unsafe persisted numeric value")
            return
        if item_type is str:
            if _contains_unsafe_string(item):
                raise WalkthroughResponseError("unsafe persisted string")
            return
        if item_type is list:
            for child in item:
                validate(child)
            return
        if item_type is not dict:
            raise WalkthroughResponseError("unsafe persisted value type")

        for key, child in item.items():
            if type(key) is not str or _PERSISTED_SAFE_KEY.fullmatch(key) is None:
                raise WalkthroughResponseError("unsafe persisted key")
            normalized_key = key.casefold()
            if _is_credential_key(key):
                raise WalkthroughResponseError("unsafe persisted key")
            if normalized_key in normalized_forbidden or (
                normalized_key in normalized_list_keys and type(child) is list
            ):
                raise WalkthroughResponseError("unsafe persisted raw field")
            validate(child)

    validate(value)


def _safe_audit_value(value: object, *, key: str | None) -> object:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if not isinstance(value, float) or math.isfinite(value) else _OMIT
    if isinstance(value, str):
        if (
            key not in _AUDIT_SAFE_STRING_KEYS
            or _contains_unsafe_string(value)
            or _AUDIT_SAFE_ATOM.fullmatch(value) is None
        ):
            return _OMIT
        return value
    if isinstance(value, dict):
        projected: dict[str, object] = {}
        for raw_key, item in value.items():
            if (
                not isinstance(raw_key, str)
                or _AUDIT_SAFE_KEY.fullmatch(raw_key) is None
            ):
                continue
            normalized_key = raw_key.casefold()
            if normalized_key in _AUDIT_FORBIDDEN_KEYS or _is_credential_key(raw_key):
                continue
            safe_item = _safe_audit_value(item, key=normalized_key)
            if safe_item is not _OMIT:
                projected[raw_key] = safe_item
        return projected
    if isinstance(value, (list, tuple)):
        if key not in _AUDIT_SAFE_LIST_KEYS:
            return _OMIT
        projected_items: list[object] = []
        for item in value:
            safe_item = _safe_audit_value(item, key=key.removesuffix("s"))
            if safe_item is not _OMIT:
                projected_items.append(safe_item)
        return projected_items
    return _OMIT


def safe_audit_data(value: object) -> dict[str, object]:
    """Project an observation onto the non-content audit schema.

    The projector deliberately defaults to omission for strings and collections.
    This keeps request payloads, licensed provider rows and generated prose out of
    JSONL even when a caller accidentally passes a full response object.
    """
    projected = _safe_audit_value(value, key=None)
    return projected if isinstance(projected, dict) else {}


def _safe_decimal(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return text if parsed.is_finite() else None


def _safe_fund_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > 128 or any(ord(char) < 32 for char in name):
        return None
    if _contains_unsafe_string(name):
        return None
    return name


def project_gildata_probes(
    *,
    quote_rows: object,
    annual_rows: object,
    fund_rows: object,
    smart_response_chars: object,
    smart_failed: bool,
) -> dict[str, object]:
    """Retain only numeric/identifier probes needed by later walkthrough phases."""
    quote_metrics: dict[str, str] = {}
    if isinstance(quote_rows, list) and quote_rows and isinstance(quote_rows[0], dict):
        for name in ("latest_price", "total_mv", "pe_ttm", "pe_lyr", "pb"):
            number = _safe_decimal(quote_rows[0].get(name))
            if number is not None:
                quote_metrics[name] = number

    annual_2025: dict[str, str] = {}
    if isinstance(annual_rows, list):
        revenue = next(
            (
                row
                for row in annual_rows
                if isinstance(row, dict) and row.get("财务科目名称") == "营业收入"
            ),
            None,
        )
        if revenue is not None:
            amount = _safe_decimal(revenue.get("财务科目数额"))
            yoy = _safe_decimal(revenue.get("同比(%)"))
            if amount is not None:
                annual_2025["revenue_amount"] = amount
            if yoy is not None:
                annual_2025["revenue_yoy_percent"] = yoy

    fund_holders: list[dict[str, str]] = []
    if isinstance(fund_rows, list):
        for row in fund_rows:
            if not isinstance(row, dict) or row.get("机构类型") != "基金":
                continue
            name = _safe_fund_name(row.get("机构股东名称"))
            raw_code = row.get("交易代码")
            code_match = (
                _FUND_CODE.fullmatch(raw_code.strip())
                if isinstance(raw_code, str)
                else None
            )
            weight = _safe_decimal(row.get("持股数量占流通A股比例(%)"))
            raw_date = row.get("报告日期")
            date_match = (
                _ISO_DATE_PREFIX.match(raw_date.strip())
                if isinstance(raw_date, str)
                else None
            )
            if (
                name is None
                or code_match is None
                or weight is None
                or date_match is None
            ):
                continue
            fund_holders.append(
                {
                    "fund_name": name,
                    "fund_code": code_match.group(1),
                    "weight_percent": weight,
                    "report_date": date_match.group(1),
                }
            )

    response_chars = (
        smart_response_chars
        if isinstance(smart_response_chars, int)
        and not isinstance(smart_response_chars, bool)
        and smart_response_chars >= 0
        else 0
    )
    return {
        "quote_metrics": quote_metrics,
        "annual_2025": annual_2025,
        "fund_holders": fund_holders,
        "smart_fund_selection": {
            "status": "failed" if smart_failed else "succeeded",
            "response_chars": response_chars,
        },
    }


def validate_walkthrough_run_id(run_id: object) -> str:
    """Return a filesystem-safe run slug or fail without echoing its value."""
    if not isinstance(run_id, str) or _WALKTHROUGH_RUN_ID.fullmatch(run_id) is None:
        raise WalkthroughConfigurationError(
            "WALKTHROUGH_RUN_ID must be a 1-64 character ASCII slug"
        )
    return run_id


def _absolute_path(path: Path) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError):
        raise WalkthroughConfigurationError("walkthrough path is invalid") from None


def _validate_directory_path(
    directory: Path,
    *,
    must_exist: bool,
    error_type: type[RuntimeError] = WalkthroughConfigurationError,
) -> Path:
    """Reject symlinked/non-directory components in an artifact directory."""
    absolute = _absolute_path(directory)
    current = Path(absolute.anchor)
    try:
        root_mode = os.lstat(current).st_mode
    except OSError:
        raise error_type("walkthrough directory is invalid") from None
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        raise error_type("walkthrough directory is invalid")

    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            if must_exist:
                raise error_type("walkthrough directory is unavailable") from None
            return absolute
        except OSError:
            raise error_type("walkthrough directory is invalid") from None
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise error_type("walkthrough directory is invalid")
    return absolute


def ensure_walkthrough_directory(directory: Path) -> None:
    """Create the output directory without accepting symlinked path components."""
    absolute = _validate_directory_path(directory, must_exist=False)
    try:
        absolute.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        raise WalkthroughConfigurationError(
            "walkthrough directory could not be created"
        ) from None
    _validate_directory_path(absolute, must_exist=True)


def _artifact_target_stat(
    path: Path,
    *,
    error_type: type[RuntimeError],
) -> os.stat_result | None:
    _validate_directory_path(path.parent, must_exist=True, error_type=error_type)
    try:
        target_stat = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError:
        raise error_type("walkthrough artifact target is invalid") from None
    if (
        stat.S_ISLNK(target_stat.st_mode)
        or not stat.S_ISREG(target_stat.st_mode)
        or target_stat.st_nlink != 1
    ):
        raise error_type("walkthrough artifact target is not a regular file")
    return target_stat


def _secure_open_regular_file(
    path: Path,
    flags: int,
    *,
    create: bool,
    error_type: type[RuntimeError],
) -> int:
    """Open one artifact without following links and verify the opened inode."""
    target_stat = _artifact_target_stat(path, error_type=error_type)
    open_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if create:
        open_flags |= os.O_CREAT
    elif target_stat is None:
        raise error_type("walkthrough artifact target is unavailable")

    try:
        descriptor = os.open(path, open_flags, _ARTIFACT_MODE)
    except OSError:
        raise error_type("walkthrough artifact target could not be opened") from None

    try:
        opened_stat = os.fstat(descriptor)
        current_stat = os.lstat(path)
        if (
            stat.S_ISLNK(current_stat.st_mode)
            or not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_nlink != 1
            or (opened_stat.st_dev, opened_stat.st_ino)
            != (current_stat.st_dev, current_stat.st_ino)
        ):
            raise error_type("walkthrough artifact target changed during access")
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(descriptor, _ARTIFACT_MODE)
        else:  # pragma: no cover - Windows fallback
            os.chmod(path, _ARTIFACT_MODE)
    except (WalkthroughConfigurationError, WalkthroughResponseError):
        os.close(descriptor)
        raise
    except OSError:
        os.close(descriptor)
        raise error_type("walkthrough artifact target could not be secured") from None
    return descriptor


def prepare_walkthrough_database(path: Path) -> None:
    """Create or validate the run database before SQLAlchemy can connect."""
    descriptor = _secure_open_regular_file(
        path,
        os.O_RDWR,
        create=True,
        error_type=WalkthroughConfigurationError,
    )
    os.close(descriptor)


def prepare_walkthrough_artifact_targets(paths: tuple[Path, ...]) -> None:
    """Validate and tighten existing state, JSONL and summary artifacts."""
    for path in paths:
        if (
            _artifact_target_stat(path, error_type=WalkthroughConfigurationError)
            is None
        ):
            continue
        descriptor = _secure_open_regular_file(
            path,
            os.O_RDWR,
            create=False,
            error_type=WalkthroughConfigurationError,
        )
        os.close(descriptor)


def secure_append_text(path: Path, text: str) -> None:
    """Append and fsync one JSONL record through an O_NOFOLLOW descriptor."""
    descriptor: int | None = None
    try:
        descriptor = _secure_open_regular_file(
            path,
            os.O_WRONLY | os.O_APPEND,
            create=True,
            error_type=WalkthroughResponseError,
        )
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            descriptor = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except (WalkthroughConfigurationError, WalkthroughResponseError):
        raise
    except (OSError, TypeError, ValueError):
        raise WalkthroughResponseError("walkthrough audit append failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def secure_read_json(path: Path) -> object | None:
    """Read an optional checkpoint through a verified regular-file descriptor."""
    if _artifact_target_stat(path, error_type=WalkthroughResponseError) is None:
        return None
    descriptor: int | None = None
    try:
        descriptor = _secure_open_regular_file(
            path,
            os.O_RDONLY,
            create=False,
            error_type=WalkthroughResponseError,
        )
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = None
            return json.load(handle)
    except (WalkthroughConfigurationError, WalkthroughResponseError):
        raise
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        raise WalkthroughResponseError(
            "walkthrough checkpoint could not be read"
        ) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _fsync_directory(directory: Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    if not directory_flag:  # pragma: no cover - Windows has no directory fsync
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | directory_flag
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
    except OSError:
        # The file itself is already durable and atomically installed. Some
        # filesystems reject fsync on a directory, so this is best effort.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


def atomic_write_json(path: Path, value: object) -> None:
    """Durably replace a JSON artifact via a same-directory 0600 temp file."""
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        validate_persisted_json(value)
        payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        _artifact_target_stat(path, error_type=WalkthroughResponseError)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        fchmod = getattr(os, "fchmod", None)
        if fchmod is not None:
            fchmod(descriptor, _ARTIFACT_MODE)
        else:  # pragma: no cover - Windows fallback
            os.chmod(temporary_path, _ARTIFACT_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        # Refuse a target that was swapped to a link or special file while the
        # replacement payload was being serialized.
        _artifact_target_stat(path, error_type=WalkthroughResponseError)
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    except (WalkthroughConfigurationError, WalkthroughResponseError):
        raise
    except (OSError, TypeError, ValueError):
        raise WalkthroughResponseError("walkthrough artifact write failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def walkthrough_paths(output_dir: Path, run_id: str) -> WalkthroughPaths:
    """Keep each invocation's state and audit files separate by run id."""
    run_id = validate_walkthrough_run_id(run_id)
    _validate_directory_path(output_dir, must_exist=False)
    stem = f"cambricon_walkthrough_{run_id}"
    return WalkthroughPaths(
        state=output_dir / f"{stem}_state.json",
        jsonl=output_dir / f"{stem}.jsonl",
        summary=output_dir / f"{stem}_summary.json",
    )


def walkthrough_database_path(backend_dir: Path, run_id: str) -> Path:
    """Return the run-scoped SQLite database path used by a walkthrough."""
    run_id = validate_walkthrough_run_id(run_id)
    _validate_directory_path(backend_dir, must_exist=False)
    return backend_dir / f"evidence_walkthrough_{run_id}.db"


def classify_historical_case_read(status: int, body: object) -> str | None:
    """Classify the expected absence of a Case before it was created.

    Point-in-time reads must not invent a dossier before its ledger creation;
    their 404 is an observation, not an operational failure.
    """
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if status == 404 and isinstance(error, dict) and error.get("code") == "not_found":
        return "case_not_created_at_cutoff"
    return None


def atomic_claim_review_payload(claim_id: str) -> dict[str, str]:
    """Build the explicit human decision that releases one atomic claim."""
    return {
        "outcome": "confirmed",
        "reviewer": "walkthrough-reviewer",
        "reason": "人工核对原文连续引文后确认发布",
        "idempotency_key": f"walkthrough-atomic-claim-{claim_id}",
    }


def proposal_review_payload(version: int, *, can_accept: bool) -> dict[str, str | int]:
    """Build the decision that respects the source-admission gate."""
    outcome = "confirmed" if can_accept else "rejected"
    reason = (
        "人工核对来源与命题关联后确认发布"
        if can_accept
        else "人工复核：该来源不满足正式证据准入条件，保留提案审计记录但不发布"
    )
    return {
        "outcome": outcome,
        "reason": reason,
        "reviewer_id": "walkthrough-reviewer",
        "expected_version": version,
    }


def assessment_review_payload(
    ai_conclusion: str, *, evidence_count: int
) -> dict[str, str | None]:
    """Confirm, but never fabricate, an assessment conclusion in the walkthrough."""
    if evidence_count == 0 and ai_conclusion == "insufficient_evidence":
        reason = (
            "人工复核：当前冻结快照没有已发布证据，确认证据不足结论；"
            "历史交叉验证不改写该快照。"
        )
    else:
        reason = "人工复核：已按当前冻结证据快照核对，确认 AI 结论。"
    return {
        "outcome": "confirmed",
        "conclusion": None,
        "reason": reason,
        "reviewer": "walkthrough-reviewer",
    }
