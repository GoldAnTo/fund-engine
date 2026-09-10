# Gateway conclusion provenance and quality review

Status: User approved the four-part proposal and explicitly requested implementation with “开始”. This document records that scope; no new layout or research algorithm is proposed.

## Scope and authority

Add a read-only review alongside the existing immutable report: actual assessment → frozen evidence snapshot → currently authorized evidence → original quote. Display a concise answer inventory, original AI verdicts and gaps, and deterministic quality cautions. Do not regenerate, persist, overwrite or silently correct old reports. No model/provider calls, migrations, professional-role scheduling, speed changes or new source permissions.

Current scope is a local private research workspace. Reuse the existing Gateway content endpoint and its owner/tenant/conversation/run/cutoff/source-contract authorization. The report must already be authorized before exposing an assessment. Follow the exact completed run's final-round result tasks, assessment IDs and snapshots; never join arbitrary assessments by thesis or infer citations from “Link N” prose. Validate task/case/run/round/thesis, assessment and snapshot identity, evidence membership and complete union against the authorized report. Unresolvable review lineage produces an explicit unavailable review, not fabricated matches or partial sensitive content. Existing report authorization still controls whether that report may be shown at all.

## Additive wire contract

`GET .../runs/{run_spec_id}/research` adds `assessment_review`, null when no authorized report is available. Old servers may omit it; the frontend then shows the original report with an unavailable-review notice. Malformed present data is rejected rather than treated as an old server.

```ts
type QualityFlag = 'mixed_data_periods' | 'unknown_data_period' |
  'user_material_only' | 'no_primary_disclosure' |
  'source_independence_unverified' | 'retrieval_direction_unverified';
type AssessmentReview = {
  method_version: 'gateway-assessment-review/v1';
  state: 'available' | 'unavailable';
  reason_code: null | 'lineage_unavailable' | 'evidence_list_truncated';
  items: {
    assessment_id: string; snapshot_id: string; task_id: string;
    thesis_id: string; thesis_statement: string;
    conclusion: 'supported' | 'contradicted' | 'insufficient_evidence';
    rationale: string; gaps: string[]; evidence_link_ids: string[];
    quality_flags: QualityFlag[];
  }[];
  quality_flags: QualityFlag[];
};
```

The bounded existing evidence list is the clickable source inventory. If truncated, do not imply complete conclusion coverage: return unavailable review with `evidence_list_truncated`. Available items have unique assessment/task/snapshot/thesis identities, unique nonempty evidence IDs, an exact authorized union and the correct thesis per evidence. Arrays are bounded to 200 and known flags to six. Unavailable reviews contain no items/flags. Null/omitted review is not a successful check.

## Meaning of quality checks

Checks use source metadata of the snapshot's actual inputs, not model-generated guesses. Per-item flags describe only that item's inputs; report-level flags describe the complete union, so an item can be user-material-only even when the full report contains official sources. Item flags therefore need not be a subset of report-level flags. Different observed data periods are a comparison caution, not proof that a source is false or outside the intended target period. Missing observed periods remain unknown. All-user-material inputs are not independently verified. No official-disclosure source is explicitly highlighted. Multiple documents do not prove independent sources, so independence remains unverified. Retrieval labels are search directions, not confirmed semantic evidence verdicts. No reliability score or green “passed” badge is generated. No inferred target-year parser is added because existing scope lacks a normalized target period.

## Reading experience

Within the current result tab, show “结果摘要” first: number of original supported/contradicted/insufficient judgments, with AI and review limitations explicit. Render each thesis as a readable vertical section, its original AI verdict, rationale/gaps and separate “新增质量检查”. “查看本项依据” shows only the snapshot's evidence IDs and opens the existing original reader. Original report remains byte-for-byte unchanged under “查看原始报告”; if structured review is unavailable, keep it directly readable with a clear limitation notice. No sentence-level mapping claim.

Preserve current stale-response, authorization-change and detail-404 protections. Crossing to original evidence retains the evidence-to-task relationship and current source authorization. No token UI or new browser credential storage.

## Acceptance

TDD covers exact task/snapshot associations, final round, cross-run/thesis rejection, revoked sources, unavailable/truncated lineage, all quality flag combinations and immutable history. Frontend tests cover strict parsing, direct original navigation, summary/verdict honesty, missing/malformed review, historical report expansion, authorization withdrawal and responsive readability. Dedicated test databases only. Verify the original local 7-evidence case without restarting research, and record actual results rather than claiming independent financial verification.
