"""One-input entry point for automatic event research."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from app.errors import ValidationFailedError
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.event_extraction import EventExtraction, EventExtractionService
from app.services.event_research import EventResearchService, InitialUploadedOriginal


_UPLOAD_MIME_TYPES_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
}
_SUPPORTED_UPLOAD_MIME_TYPES = frozenset(_UPLOAD_MIME_TYPES_BY_EXTENSION.values())
_MAX_UPLOADED_FILE_NAME_LENGTH = 512


class _EventExtractor(Protocol):
    def extract(
        self, *, raw_input: str, source_url: str | None
    ) -> EventExtraction: ...


@dataclass(frozen=True, slots=True)
class AutomaticResearchStart:
    case_id: str
    run_id: str
    preparation_id: None = None


class AutomaticResearchIntakeService:
    def __init__(
        self, session: Session, extractor: _EventExtractor | None = None
    ) -> None:
        self._session = session
        self._extractor = extractor or EventExtractionService()

    def start(self, raw_input: str, *, tenant_id: str) -> AutomaticResearchStart:
        return self._start(
            raw_input,
            tenant_id=tenant_id,
            source_type="pasted_snapshot",
            source_metadata={
                "authority_level": "user_supplied",
                "intake_role": "research_prompt",
                "input_kind": "topic",
                "permissions": {
                    "ai_processing": True,
                    "display": True,
                    "export": False,
                    "api": False,
                },
            },
        )

    def start_uploaded(
        self,
        *,
        raw_input: str | None,
        raw: bytes,
        file_name: str,
        mime_type: str | None,
        tenant_id: str,
    ) -> AutomaticResearchStart:
        safe_name = file_name.strip()
        if not safe_name:
            raise ValidationFailedError("uploaded file must have a file name")
        if len(safe_name) > _MAX_UPLOADED_FILE_NAME_LENGTH:
            raise ValidationFailedError(
                "uploaded file name must not exceed 512 characters"
            )
        normalized_tenant_id = self._normalized_tenant_id(tenant_id)
        normalized_mime_type = self._uploaded_mime_type(
            file_name=safe_name, mime_type=mime_type
        )
        self._validate_uploaded_original_bytes(
            raw=raw, mime_type=normalized_mime_type
        )
        prompt = raw_input.strip() if isinstance(raw_input, str) else ""
        source_metadata = {
            "authority_level": "user_supplied",
            "tenant": normalized_tenant_id,
            "intake_role": "provided_material",
            "input_kind": "material",
            "permissions": {
                "ai_processing": True,
                "display": True,
                "export": False,
                "api": False,
            },
        }
        return self._start(
            prompt or safe_name,
            tenant_id=normalized_tenant_id,
            source_type="uploaded_file",
            source_metadata=source_metadata,
            forced_input_kind="material",
            forced_event_title=safe_name if not prompt else None,
            initial_uploaded_original=InitialUploadedOriginal(
                raw=raw,
                file_name=safe_name,
                mime_type=normalized_mime_type,
                source_metadata=source_metadata,
            ),
        )

    @staticmethod
    def _normalized_tenant_id(tenant_id: str) -> str:
        normalized_tenant_id = tenant_id.strip()
        if not normalized_tenant_id:
            raise ValueError("automatic research tenant_id must not be blank")
        if len(normalized_tenant_id) > 121:
            raise ValueError(
                "automatic research tenant_id must not exceed 121 characters"
            )
        return normalized_tenant_id

    @staticmethod
    def _uploaded_mime_type(*, file_name: str, mime_type: str | None) -> str:
        extension = Path(file_name).suffix.casefold()
        if extension:
            try:
                return _UPLOAD_MIME_TYPES_BY_EXTENSION[extension]
            except KeyError as exc:
                raise ValidationFailedError(
                    "unsupported original file type; upload PDF, TXT, Markdown, or CSV"
                ) from exc
        if mime_type in _SUPPORTED_UPLOAD_MIME_TYPES:
            return mime_type
        raise ValidationFailedError(
            "unsupported original file type; upload PDF, TXT, Markdown, or CSV"
        )

    @staticmethod
    def _validate_uploaded_original_bytes(*, raw: bytes, mime_type: str) -> None:
        if mime_type == "application/pdf":
            if not raw.startswith(b"%PDF-"):
                raise ValidationFailedError("uploaded PDF must have a valid PDF signature")
            return
        if b"\x00" in raw:
            raise ValidationFailedError("uploaded text file must not contain NUL bytes")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailedError(
                "uploaded text file must be valid UTF-8"
            ) from exc

    def _start(
        self,
        raw_input: str,
        *,
        tenant_id: str,
        source_type: str,
        source_metadata: dict[str, object],
        forced_input_kind: str | None = None,
        forced_event_title: str | None = None,
        initial_uploaded_original: InitialUploadedOriginal | None = None,
    ) -> AutomaticResearchStart:
        text = raw_input.strip()
        if not text:
            raise ValueError("automatic research input must not be blank")
        normalized_tenant_id = self._normalized_tenant_id(tenant_id)

        extracted = self._extractor.extract(raw_input=text, source_url=None)
        input_kind = forced_input_kind or (
            extracted.input_kind
            if extracted.input_kind in {"topic", "material"}
            else "topic"
        )
        event_title = forced_event_title or (
            extracted.event_title.strip()
            if extracted.event_title and extracted.event_title.strip()
            else text[:80]
        )
        created = EventResearchService(self._session).create(
            CreateEventResearchRequest(
                raw_input=text,
                source_url=None,
                source_type=source_type,
                source_metadata={
                    **source_metadata,
                    "intake_role": (
                        "provided_material"
                        if input_kind == "material"
                        else "research_prompt"
                    ),
                    "input_kind": input_kind,
                },
                event_title=event_title,
                company_name=extracted.company_name,
                ticker=extracted.ticker,
                event_at=extracted.event_at,
                market_reaction=extracted.market_reaction,
                research_question=extracted.research_question,
                candidate_factors=list(extracted.candidate_factors),
                research_protocol_required=False,
                created_by=f"tenant:{normalized_tenant_id}",
            ),
            tenant_id=normalized_tenant_id,
            workflow_mode="automatic",
            initial_uploaded_original=initial_uploaded_original,
        )
        if created.run_id is None:
            raise RuntimeError("automatic research intake did not create a run")
        return AutomaticResearchStart(
            case_id=created.case_id,
            run_id=created.run_id,
        )
