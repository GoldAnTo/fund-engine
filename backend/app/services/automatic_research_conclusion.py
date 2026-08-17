"""Pure deterministic automatic-result conclusion construction."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence


_CONCLUSION_LABELS = {
    "supported": "得到当前证据支持",
    "contradicted": "受到当前证据反驳",
    "insufficient_evidence": "证据不足",
}


@dataclass(frozen=True, slots=True)
class AutomaticAssessmentInput:
    assessment_id: uuid.UUID
    task_thesis_id: uuid.UUID
    snapshot_thesis_id: uuid.UUID
    thesis_statement: str
    conclusion: object
    rationale: object
    gaps: object
    displayed_as_provisional: object
    creator_type: object
    evidence_link_ids: object


@dataclass(frozen=True, slots=True)
class AutomaticSourceJobInput:
    status: object
    admitted_count: object
    exception_count: object


@dataclass(frozen=True, slots=True)
class AutomaticConclusionProjection:
    text: str
    primary_factor: str | None
    evidence_link_ids: tuple[uuid.UUID, ...]
    key_findings: tuple[str, ...]
    limitations: tuple[str, ...]


def build_automatic_research_conclusion(
    *,
    factor_scope: Sequence[tuple[uuid.UUID, str]],
    assessments: Sequence[AutomaticAssessmentInput],
    source_jobs: Sequence[AutomaticSourceJobInput],
) -> AutomaticConclusionProjection:
    """Validate immutable inputs and reproduce the writer's exact result."""
    expected_ids = [thesis_id for thesis_id, _statement in factor_scope]
    if (
        len(assessments) != len(factor_scope)
        or [row.task_thesis_id for row in assessments] != expected_ids
        or len({row.assessment_id for row in assessments}) != len(assessments)
    ):
        raise ValueError("automatic assessment bindings are invalid")

    text_lines: list[str] = []
    findings: list[str] = []
    gaps: list[str] = []
    evidence_ids: list[uuid.UUID] = []
    primary_factor: str | None = None
    for (thesis_id, statement), row in zip(factor_scope, assessments):
        if (
            row.snapshot_thesis_id != thesis_id
            or row.thesis_statement != statement
            or row.displayed_as_provisional is not True
            or row.creator_type != "ai"
            or row.conclusion not in _CONCLUSION_LABELS
            or not isinstance(row.rationale, str)
            or not isinstance(row.gaps, list)
            or any(not isinstance(gap, str) for gap in row.gaps)
            or not isinstance(row.evidence_link_ids, list)
        ):
            raise ValueError("automatic assessment content is invalid")
        try:
            snapshot_ids = [
                uuid.UUID(str(value)) for value in row.evidence_link_ids
            ]
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("automatic assessment evidence is invalid") from exc
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("automatic assessment evidence is duplicated")
        evidence_ids.extend(snapshot_ids)
        label = _CONCLUSION_LABELS[row.conclusion]
        text_lines.append(f"{statement}：{label}。{row.rationale}")
        findings.append(row.rationale)
        if primary_factor is None and row.conclusion == "supported":
            primary_factor = statement
        gaps.extend(gap.strip() for gap in row.gaps if gap.strip())
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("automatic assessment evidence is duplicated")

    unique_gaps = sorted(set(gaps))
    if unique_gaps:
        text_lines.append("证据缺口：" + "；".join(unique_gaps))

    failed_count = 0
    cancelled_count = 0
    partial_count = 0
    partial_without_admitted_count = 0
    exception_count = 0
    for job in source_jobs:
        if (
            isinstance(job.exception_count, bool)
            or not isinstance(job.exception_count, int)
            or job.exception_count < 0
        ):
            raise ValueError("automatic source exception count is invalid")
        exception_count += job.exception_count
        if job.status == "failed":
            failed_count += 1
        elif job.status == "cancelled":
            cancelled_count += 1
        elif job.status == "partial":
            partial_count += 1
            if (
                isinstance(job.admitted_count, bool)
                or not isinstance(job.admitted_count, int)
                or job.admitted_count < 0
            ):
                raise ValueError("automatic partial admitted count is invalid")
            if job.admitted_count == 0:
                partial_without_admitted_count += 1
        elif not isinstance(job.status, str):
            raise ValueError("automatic source status is invalid")
    text_lines.append(
        "局限：结论仅基于本次冻结范围内自动准入且映射到当前范围的证据。"
        f"采集任务：失败 {failed_count}，取消 {cancelled_count}，"
        f"部分完成 {partial_count}，"
        f"部分完成但无准入证据 {partial_without_admitted_count}，"
        "未执行 0，"
        f"跳过/异常条目 {exception_count}。"
    )
    return AutomaticConclusionProjection(
        text="\n".join(text_lines),
        primary_factor=primary_factor,
        evidence_link_ids=tuple(evidence_ids),
        key_findings=tuple(findings),
        limitations=tuple(unique_gaps),
    )
