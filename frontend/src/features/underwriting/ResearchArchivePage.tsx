import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  UnderwritingResearchRequestError,
  underwritingResearchApi,
  type ResearchArchiveItem,
  type ResearchArchiveList,
  type CandidateEvidence,
  type ResearchRevision,
  type ResearchRevisionBoundary,
  type ResearchRevisionDiff,
  type ResearchRevisionHistory,
} from "../../data/underwritingResearchApi";

type LoadState<T> =
  | { state: "loading"; value?: T }
  | { state: "ready"; value: T }
  | { state: "error"; error: unknown };

type ArchiveKind = "" | ResearchArchiveItem["object_kind"];
type Artifact = ResearchRevisionDiff["entries"][number]["after"] extends infer Value
  ? Exclude<Value, null>
  : never;
type DetailLoad = {
  revision: ResearchRevision;
  diff: ResearchRevisionDiff | null;
  boundary: ResearchRevisionBoundary | null;
  candidate: CandidateEvidence | null;
};
type DiffEntry = ResearchRevisionDiff["entries"][number];

const GROUPS = [
  ["evidence", "证据"],
  ["mechanism", "机制"],
  ["industry_model", "行业模型"],
  ["answerability", "可回答性"],
] as const;

const INTEGRITY_ERROR = "冻结档案的身份或版本链无法校验，未展示任何资料。";
const CANDIDATE_INTEGRITY_ERROR = "冻结研究记录不完整或不匹配，未展示候选证据。";

class ArchiveIntegrityError extends Error {
  constructor() {
    super(INTEGRITY_ERROR);
    this.name = "ArchiveIntegrityError";
  }
}

class CandidateEvidenceIntegrityError extends Error {
  constructor() {
    super(CANDIDATE_INTEGRITY_ERROR);
    this.name = "CandidateEvidenceIntegrityError";
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function nullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

function isArtifact(value: unknown): value is Artifact {
  const artifact = record(value);
  return artifact?.schema_version === "underwriting.v1"
    && nonEmptyString(artifact.reference)
    && nonEmptyString(artifact.artifact_type)
    && nonEmptyString(artifact.identity)
    && nonEmptyString(artifact.content_hash)
    && Array.isArray(artifact.source_locators)
    && artifact.source_locators.every((locator) => typeof locator === "string")
    && nullableString(artifact.unit)
    && nullableString(artifact.period_start)
    && nullableString(artifact.period_end)
    && nullableString(artifact.available_at)
    && nullableString(artifact.status);
}

function sameArtifact(left: Artifact, right: Artifact): boolean {
  return left.schema_version === right.schema_version
    && left.reference === right.reference
    && left.artifact_type === right.artifact_type
    && left.identity === right.identity
    && left.content_hash === right.content_hash
    && left.source_locators.length === right.source_locators.length
    && left.source_locators.every((locator, index) => locator === right.source_locators[index])
    && left.unit === right.unit
    && left.period_start === right.period_start
    && left.period_end === right.period_end
    && left.available_at === right.available_at
    && left.status === right.status;
}

function artifactIdentity(artifact: Artifact): string {
  return `${artifact.artifact_type}\u0000${artifact.identity}`;
}

function artifactOrder(artifact: Artifact): string {
  return `${artifactIdentity(artifact)}\u0000${artifact.reference}`;
}

function canonicalArtifacts(value: unknown): value is Artifact[] {
  if (!Array.isArray(value)) return false;
  let previousOrder: string | null = null;
  const identities = new Set<string>();
  return value.every((artifact) => {
    if (!isArtifact(artifact)) return false;
    const order = artifactOrder(artifact);
    const identity = artifactIdentity(artifact);
    if ((previousOrder !== null && previousOrder >= order) || identities.has(identity)) return false;
    previousOrder = order;
    identities.add(identity);
    return true;
  });
}

function sameArtifactList(expected: Artifact[], received: Artifact[]): boolean {
  return expected.length === received.length
    && expected.every((artifact, index) => sameArtifact(artifact, received[index]));
}

function isRevision(value: unknown): value is ResearchRevision {
  const revision = record(value);
  return revision?.schema_version === "underwriting.v1"
    && nonEmptyString(revision.id)
    && nonEmptyString(revision.object_id)
    && nonEmptyString(revision.basis_id)
    && nonEmptyString(revision.version_kind)
    && Number.isSafeInteger(revision.sequence)
    && (revision.sequence as number) > 0
    && nonEmptyString(revision.content_hash)
    && nonEmptyString(revision.cutoff)
    && nonEmptyString(revision.source_manifest_hash)
    && canonicalArtifacts(revision.parent_refs);
}

function checkedHistory(value: unknown, objectId: string, versionKind: string): ResearchRevisionHistory {
  const history = record(value);
  if (
    history?.schema_version !== "underwriting.v1"
    || history.object_id !== objectId
    || history.version_kind !== versionKind
    || !["company", "industry", "security"].includes(history.object_kind as string)
    || !nonEmptyString(history.canonical_name)
    || !nonEmptyString(history.external_key)
    || !Array.isArray(history.revisions)
  ) throw new ArchiveIntegrityError();

  const ids = new Set<string>();
  const sequences = new Set<number>();
  for (const revision of history.revisions) {
    if (
      !isRevision(revision)
      || revision.object_id !== objectId
      || revision.version_kind !== versionKind
      || ids.has(revision.id)
      || sequences.has(revision.sequence)
    ) throw new ArchiveIntegrityError();
    ids.add(revision.id);
    sequences.add(revision.sequence);
  }
  const ordered = [...history.revisions].sort((left, right) => left.sequence - right.sequence);
  if (ordered.some((revision, index) => revision.sequence !== index + 1)) throw new ArchiveIntegrityError();
  return { ...history, revisions: ordered } as ResearchRevisionHistory;
}

function labelForObjectKind(kind: ResearchRevisionHistory["object_kind"]): string {
  const labels: Record<ResearchRevisionHistory["object_kind"], string> = {
    company: "公司",
    industry: "行业",
    security: "证券",
  };
  return labels[kind];
}

function sameRevisionSummary(expected: ResearchRevision, received: ResearchRevision): boolean {
  return expected.id === received.id
    && expected.object_id === received.object_id
    && expected.basis_id === received.basis_id
    && expected.version_kind === received.version_kind
    && expected.sequence === received.sequence
    && expected.content_hash === received.content_hash
    && expected.cutoff === received.cutoff
    && expected.source_manifest_hash === received.source_manifest_hash;
}

function checkedRevision(value: unknown, expected: ResearchRevision): ResearchRevision {
  if (
    !isRevision(value)
    || !sameRevisionSummary(expected, value)
    || !sameArtifactList(expected.parent_refs, value.parent_refs)
  ) throw new ArchiveIntegrityError();
  return value;
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const allowed = new Set(keys);
  return Object.keys(value).every((key) => allowed.has(key))
    && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(nonEmptyString);
}

const CANONICAL_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const CONTENT_HASH = /^[0-9a-f]{64}$/;
const UTC_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/;

function isCanonicalUuid(value: unknown): value is string {
  return typeof value === "string" && CANONICAL_UUID.test(value);
}

function isContentHash(value: unknown): value is string {
  return typeof value === "string" && CONTENT_HASH.test(value);
}

function isUtcTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = UTC_TIMESTAMP.exec(value);
  if (match === null) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, fraction = ""] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  const millisecond = Number(fraction.padEnd(3, "0").slice(0, 3));
  const timestamp = new Date(0);
  timestamp.setUTCFullYear(year, month - 1, day);
  timestamp.setUTCHours(hour, minute, second, millisecond);
  return timestamp.getUTCFullYear() === year
    && timestamp.getUTCMonth() === month - 1
    && timestamp.getUTCDate() === day
    && timestamp.getUTCHours() === hour
    && timestamp.getUTCMinutes() === minute
    && timestamp.getUTCSeconds() === second
    && timestamp.getUTCMilliseconds() === millisecond;
}

function isFrozenAnswerability(value: unknown): boolean {
  const answerability = record(value);
  return answerability !== null
    && hasOnlyKeys(answerability, [
      "schema_version", "reference", "content_hash", "state", "blockers",
      "research_debt_keys", "resolvable_within_mandate", "resolution_requirements",
    ])
    && answerability.schema_version === "underwriting.v1"
    && isCanonicalUuid(answerability.reference)
    && isContentHash(answerability.content_hash)
    && ["answerable", "partially_answerable", "not_answerable"].includes(answerability.state as string)
    && Array.isArray(answerability.blockers)
    && answerability.blockers.every((blocker) => [
      "missing_key_baseline", "unresolved_source_conflict", "mechanism_unidentified",
      "financial_model_not_closed", "expectation_surface_unidentifiable", "source_unavailable",
      "future_information_leakage",
    ].includes(blocker as string))
    && isStringList(answerability.research_debt_keys)
    && typeof answerability.resolvable_within_mandate === "boolean"
    && isStringList(answerability.resolution_requirements);
}

function isFrozenUnknownGap(value: unknown): boolean {
  const gap = record(value);
  const dimensions = record(gap?.dimensions);
  return gap !== null
    && hasOnlyKeys(gap, [
      "schema_version", "reference", "content_hash", "metric_key", "unit", "source_id",
      "source_locator", "observed_start", "observed_end", "effective_at", "available_at",
      "source_role", "observation_status", "dimensions",
    ])
    && gap.schema_version === "underwriting.v1"
    && isCanonicalUuid(gap.reference)
    && isContentHash(gap.content_hash)
    && ["metric_key", "unit", "source_id", "source_locator", "source_role"].every((key) => nonEmptyString(gap[key]))
    && ["observed_start", "observed_end", "effective_at", "available_at"].every((key) => isUtcTimestamp(gap[key]))
    && gap.observation_status === "unknown"
    && dimensions !== null
    && Object.entries(dimensions).every(([key, dimension]) => nonEmptyString(key) && nonEmptyString(dimension));
}

function checkedBoundary(value: unknown, selected: ResearchRevision): ResearchRevisionBoundary {
  const boundary = record(value);
  if (
    boundary === null
    || !hasOnlyKeys(boundary, [
      "schema_version", "revision_id", "object_id", "basis_id", "version_kind", "content_hash",
      "cutoff", "source_manifest_hash", "answerability", "unknown_evidence_gaps",
    ])
    || boundary.schema_version !== "underwriting.v1"
    || boundary.revision_id !== selected.id
    || boundary.object_id !== selected.object_id
    || boundary.basis_id !== selected.basis_id
    || boundary.version_kind !== selected.version_kind
    || boundary.content_hash !== selected.content_hash
    || boundary.cutoff !== selected.cutoff
    || boundary.source_manifest_hash !== selected.source_manifest_hash
    || (boundary.answerability !== null && !isFrozenAnswerability(boundary.answerability))
    || !Array.isArray(boundary.unknown_evidence_gaps)
    || !boundary.unknown_evidence_gaps.every(isFrozenUnknownGap)
  ) throw new ArchiveIntegrityError();
  return boundary as ResearchRevisionBoundary;
}

function isCandidateItem(value: unknown): boolean {
  const item = record(value);
  if (
    item === null
    || !hasOnlyKeys(item, [
      "schema_version", "metric_key", "status", "value", "unit", "observed_start", "observed_end",
      "available_at", "source_id", "source_locator", "scope_statement", "exclusions", "methodology",
      "prohibited_splicing_declaration", "transcription_method", "error_bound", "scenario_use",
      "not_observed_declared", "unknown_reason",
    ])
    || item.schema_version !== "underwriting.v1"
    || !nonEmptyString(item.metric_key)
    || !["source_reported", "official_aggregate", "chart_approximation", "assumption_bound", "unknown"].includes(item.status as string)
    || !(item.value === null || nonEmptyString(item.value))
    || !(item.unit === null || nonEmptyString(item.unit))
    || !isUtcTimestamp(item.observed_start)
    || !isUtcTimestamp(item.observed_end)
    || !isUtcTimestamp(item.available_at)
    || !["source_id", "source_locator", "scope_statement", "methodology", "prohibited_splicing_declaration"].every((key) => nonEmptyString(item[key]))
    || !isStringList(item.exclusions)
    || !(item.transcription_method === null || nonEmptyString(item.transcription_method))
    || !(item.error_bound === null || nonEmptyString(item.error_bound))
    || !(item.scenario_use === null || nonEmptyString(item.scenario_use))
    || typeof item.not_observed_declared !== "boolean"
    || !(item.unknown_reason === null || nonEmptyString(item.unknown_reason))
  ) return false;

  const numeric = item.status !== "unknown" && item.value !== null && item.unit !== null;
  if (!numeric) return item.status === "unknown"
    && item.transcription_method === null && item.error_bound === null && item.scenario_use === null
    && item.not_observed_declared === false && item.unknown_reason !== null;
  if (item.status === "chart_approximation") {
    return item.transcription_method !== null && item.error_bound !== null
      && item.scenario_use === null && item.not_observed_declared === false && item.unknown_reason === null;
  }
  if (item.status === "assumption_bound") {
    return item.transcription_method === null && item.error_bound === null
      && item.scenario_use !== null && item.not_observed_declared === true && item.unknown_reason === null;
  }
  return item.transcription_method === null && item.error_bound === null
    && item.scenario_use === null && item.not_observed_declared === false && item.unknown_reason === null;
}

function isCandidateReview(value: unknown): boolean {
  const review = record(value);
  return review !== null
    && hasOnlyKeys(review, ["schema_version", "reference", "content_hash", "reviewer_identity", "reviewer_role", "decision", "rationale", "reviewed_at"])
    && review.schema_version === "underwriting.v1"
    && isCanonicalUuid(review.reference)
    && isContentHash(review.content_hash)
    && nonEmptyString(review.reviewer_identity)
    && ["provenance", "methodology"].includes(review.reviewer_role as string)
    && review.decision === "approve"
    && nonEmptyString(review.rationale)
    && isUtcTimestamp(review.reviewed_at);
}

const FORBIDDEN_CANDIDATE_CALCULATION = new RegExp([
  ...[
    [97, 99, 116, 105, 111, 110], [98, 117, 121], [115, 101, 108, 108], [115, 116, 111, 112],
    [112, 111, 115, 105, 116, 105, 111, 110], [112, 114, 105, 99, 101], [116, 97, 114, 103, 101, 116],
    [118, 97, 108, 117, 97, 116, 105, 111, 110], [114, 101, 99, 111, 109, 109, 101, 110, 100],
    [114, 101, 116, 117, 114, 110], [101, 110, 116, 114, 121], [112, 101], [112, 98], [100, 99, 102],
  ].map((codepoints) => String.fromCharCode(...codepoints)),
  "\\u5e02\\u76c8\\u7387", "\\u5e02\\u51c0\\u7387", "\\u76ee\\u6807\\u4ef7", "\\u4e70\\u5165", "\\u5356\\u51fa",
  "\\u6b62\\u635f", "\\u4ed3\\u4f4d", "\\u6536\\u76ca\\u7387", "\\u4f30\\u503c", "\\u63a8\\u8350", "\\u64cd\\u4f5c",
  "\\u5efa\\u4ed3", "\\u52a0\\u4ed3", "\\u51cf\\u4ed3", "\\u6295\\u8d44\\u5efa\\u8bae", "\\u6295\\u8d44\\u7ed3\\u8bba",
].join("|"), "i");

function hasControlledCandidateCalculations(value: unknown): value is string[] {
  return isStringList(value)
    && value.length > 0
    && value.every((calculation) => !FORBIDDEN_CANDIDATE_CALCULATION.test(calculation));
}

function isStrictCandidateParentRef(value: unknown): boolean {
  const parent = record(value);
  return parent !== null
    && hasOnlyKeys(parent, [
      "schema_version", "reference", "artifact_type", "identity", "content_hash", "source_locators",
      "unit", "period_start", "period_end", "available_at", "status",
    ])
    && parent.schema_version === "underwriting.v1"
    && isCanonicalUuid(parent.reference)
    && nonEmptyString(parent.artifact_type)
    && nonEmptyString(parent.identity)
    && isContentHash(parent.content_hash)
    && Array.isArray(parent.source_locators) && parent.source_locators.every((locator) => typeof locator === "string")
    && nullableString(parent.unit) && nullableString(parent.period_start) && nullableString(parent.period_end)
    && nullableString(parent.available_at) && nullableString(parent.status)
    && (parent.period_start === null || isUtcTimestamp(parent.period_start))
    && (parent.period_end === null || isUtcTimestamp(parent.period_end))
    && (parent.available_at === null || isUtcTimestamp(parent.available_at));
}

function isCandidateEvidenceParentRef(value: unknown): boolean {
  const parent = record(value);
  return parent !== null
    && hasOnlyKeys(parent, ["schema_version", "reference", "artifact_type", "identity", "content_hash", "descriptor_preimage"])
    && parent.schema_version === "underwriting.v1"
    && isCanonicalUuid(parent.reference)
    && ["candidate_dossier", "candidate_review", "source_manifest"].includes(parent.artifact_type as string)
    && nonEmptyString(parent.identity)
    && isContentHash(parent.content_hash)
    && ((parent.artifact_type === "candidate_dossier" && (() => {
      const preimage = record(parent.descriptor_preimage);
      return preimage !== null
        && hasOnlyKeys(preimage, ["schema_version", "raw_content_hash", "created_at"])
        && preimage.schema_version === "underwriting.v1"
        && isContentHash(preimage.raw_content_hash) && isUtcTimestamp(preimage.created_at);
    })()) || (parent.artifact_type === "candidate_review" && (() => {
      const preimage = record(parent.descriptor_preimage);
      return preimage !== null
        && hasOnlyKeys(preimage, ["schema_version", "raw_content_hash", "created_at", "reviewed_at"])
        && preimage.schema_version === "underwriting.v1"
        && isContentHash(preimage.raw_content_hash)
        && isUtcTimestamp(preimage.created_at) && isUtcTimestamp(preimage.reviewed_at);
    })()) || (parent.artifact_type === "source_manifest" && (() => {
      const preimage = record(parent.descriptor_preimage);
      return preimage !== null
        && hasOnlyKeys(preimage, ["schema_version", "row_content_hash", "manifest_hash"])
        && preimage.schema_version === "underwriting.v1"
        && isContentHash(preimage.row_content_hash) && isContentHash(preimage.manifest_hash);
    })()));
}

function exactCandidateParentDescriptors(candidate: Record<string, unknown>, selected: ResearchRevision): boolean {
  if (!Array.isArray(candidate.parent_refs) || !candidate.parent_refs.every(isCandidateEvidenceParentRef)) return false;
  // Descriptor hashes may include server-side sealing timestamps, so they are
  // authenticated against the selected revision rather than a raw response hash.
  const selectedDescriptors = selected.parent_refs.filter((parent) => [
    "candidate_dossier", "candidate_review", "source_manifest",
  ].includes(parent.artifact_type));
  if (candidate.parent_refs.length !== 4 || selectedDescriptors.length !== 4) return false;
  return candidate.parent_refs.every((parent, index) => {
    const responseParent = record(parent);
    const selectedParent = selectedDescriptors[index];
    return responseParent !== null && selectedParent !== undefined
      && responseParent.reference === selectedParent.reference
      && responseParent.artifact_type === selectedParent.artifact_type
      && responseParent.identity === selectedParent.identity
      && responseParent.content_hash === selectedParent.content_hash;
  });
}

function candidateParentBindings(candidate: Record<string, unknown>, selected: ResearchRevision): boolean {
  const dossier = record(candidate.dossier);
  const answerability = record(candidate.answerability);
  if (
    dossier === null || answerability === null || !selected.parent_refs.every(isStrictCandidateParentRef)
    || !exactCandidateParentDescriptors(candidate, selected)
  ) return false;
  const byType = new Map<string, Record<string, unknown>[]>();
  for (const parent of selected.parent_refs) {
    const parentRecord = record(parent);
    if (parentRecord === null) return false;
    const existing = byType.get(parentRecord.artifact_type as string) ?? [];
    existing.push(parentRecord);
    byType.set(parentRecord.artifact_type as string, existing);
  }
  if (
    selected.parent_refs.length !== 5
    || byType.size !== 4
    || byType.get("candidate_dossier")?.length !== 1
    || byType.get("candidate_review")?.length !== 2
    || byType.get("source_manifest")?.length !== 1
    || byType.get("answerability")?.length !== 1
  ) return false;

  const dossierParent = byType.get("candidate_dossier")?.[0];
  const manifestParent = byType.get("source_manifest")?.[0];
  const answerabilityParent = byType.get("answerability")?.[0];
  if (
    dossierParent === undefined || manifestParent === undefined || answerabilityParent === undefined
    || dossierParent.reference !== dossier.reference
    || dossierParent.identity !== `${dossier.dossier_key}|${dossier.version}`
    || dossierParent.status !== dossier.status
    || manifestParent.status !== "frozen"
    || answerabilityParent.reference !== answerability.reference
    || answerabilityParent.identity !== "answerability"
    || answerabilityParent.content_hash !== answerability.content_hash
    || answerabilityParent.status !== answerability.state
  ) return false;

  const reviews = candidate.reviews as unknown[];
  const valid = reviews.every((review) => {
    const reviewRecord = record(review);
    if (reviewRecord === null) return false;
    const parent = byType.get("candidate_review")?.find((item) => item.reference === reviewRecord.reference);
    return parent !== undefined
      && parent.identity === `${dossier.reference}|${reviewRecord.reviewer_role}|${reviewRecord.reviewer_identity}`
      && parent.status === reviewRecord.decision;
  });
  return valid;
}

function normalizedUtc(value: string): string {
  return value.endsWith("Z") ? `${value.slice(0, -1)}+00:00` : value;
}

export function compareCodePointTuple(left: readonly string[], right: readonly string[]): number {
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
    const leftPoints = Array.from(left[index]);
    const rightPoints = Array.from(right[index]);
    for (let pointIndex = 0; pointIndex < Math.min(leftPoints.length, rightPoints.length); pointIndex += 1) {
      const leftPoint = leftPoints[pointIndex].codePointAt(0) as number;
      const rightPoint = rightPoints[pointIndex].codePointAt(0) as number;
      if (leftPoint < rightPoint) return -1;
      if (leftPoint > rightPoint) return 1;
    }
    if (leftPoints.length < rightPoints.length) return -1;
    if (leftPoints.length > rightPoints.length) return 1;
  }
  return left.length - right.length;
}

async function canonicalCandidateHash(value: object, ensureAscii: boolean): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const canonicalPayload = JSON.stringify(value);
  const serialized = ensureAscii
    ? canonicalPayload.replace(/[\u0080-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`)
    : canonicalPayload;
  const bytes = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(serialized));
  return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function canonicalCandidateItem(value: unknown): Record<string, unknown> | null {
  const item = record(value);
  if (item === null || !isCandidateItem({ ...item, schema_version: "underwriting.v1" })) return null;
  const { schema_version: _schemaVersion, ...canonical } = item;
  return {
    available_at: normalizedUtc(canonical.available_at as string), error_bound: canonical.error_bound,
    exclusions: canonical.exclusions, methodology: canonical.methodology, metric_key: canonical.metric_key,
    not_observed_declared: canonical.not_observed_declared, observed_end: normalizedUtc(canonical.observed_end as string),
    observed_start: normalizedUtc(canonical.observed_start as string),
    prohibited_splicing_declaration: canonical.prohibited_splicing_declaration, scenario_use: canonical.scenario_use,
    scope_statement: canonical.scope_statement, source_id: canonical.source_id, source_locator: canonical.source_locator,
    status: canonical.status, transcription_method: canonical.transcription_method, unit: canonical.unit,
    unknown_reason: canonical.unknown_reason, value: canonical.value,
  };
}

function canonicalDossierPayload(value: unknown): Record<string, unknown> | null {
  const payload = record(value);
  if (
    payload === null
    || !hasOnlyKeys(payload, [
      "object_id", "basis_id", "source_manifest_id", "dossier_key", "version", "scope_statement", "status",
      "purpose", "items", "rejected_calculations", "source_manifest_hash", "created_at", "supersedes_id",
    ])
    || !isCanonicalUuid(payload.object_id) || !isCanonicalUuid(payload.basis_id) || !isCanonicalUuid(payload.source_manifest_id)
    || !nonEmptyString(payload.dossier_key) || !Number.isSafeInteger(payload.version) || (payload.version as number) < 1
    || !nonEmptyString(payload.scope_statement) || payload.status !== "candidate" || payload.purpose !== "evidence_candidate"
    || !Array.isArray(payload.items) || payload.items.length === 0 || !payload.items.every((item) => canonicalCandidateItem(item) !== null)
    || !isStringList(payload.rejected_calculations) || payload.rejected_calculations.length === 0
    || !isContentHash(payload.source_manifest_hash) || !isUtcTimestamp(payload.created_at)
    || !(payload.supersedes_id === null || isCanonicalUuid(payload.supersedes_id))
  ) return null;
  return {
    basis_id: payload.basis_id, created_at: payload.created_at, dossier_key: payload.dossier_key,
    items: payload.items.map((item) => canonicalCandidateItem(item)), object_id: payload.object_id,
    purpose: payload.purpose, rejected_calculations: payload.rejected_calculations, scope_statement: payload.scope_statement,
    source_manifest_hash: payload.source_manifest_hash, source_manifest_id: payload.source_manifest_id,
    status: payload.status, supersedes_id: payload.supersedes_id, version: payload.version,
  };
}

async function verifiedCandidatePayloadHashes(candidate: Record<string, unknown>): Promise<boolean> {
  const dossier = record(candidate.dossier);
  const payload = dossier === null ? null : canonicalDossierPayload(dossier.canonical_payload);
  const parents = candidate.parent_refs as unknown[];
  const dossierParent = parents.map(record).find((parent) => parent?.artifact_type === "candidate_dossier");
  const manifestParent = parents.map(record).find((parent) => parent?.artifact_type === "source_manifest");
  if (dossier === null || payload === null || dossierParent == null || manifestParent == null) return false;
  const dossierPreimage = record(dossierParent.descriptor_preimage);
  const manifestPreimage = record(manifestParent.descriptor_preimage);
  if (
    dossierPreimage === null || manifestPreimage === null
    || dossier.content_hash !== dossierPreimage.raw_content_hash
    || payload.object_id !== candidate.object_id || payload.basis_id !== candidate.basis_id
    || payload.dossier_key !== dossier.dossier_key || payload.version !== dossier.version || payload.status !== dossier.status
    || payload.scope_statement !== dossier.scope_statement || payload.supersedes_id !== dossier.supersedes_id
    || JSON.stringify(payload.rejected_calculations) !== JSON.stringify(dossier.rejected_calculations)
    || payload.created_at !== dossierPreimage.created_at
    || payload.source_manifest_id !== manifestParent.reference
    || payload.source_manifest_hash !== candidate.source_manifest_hash
    || manifestPreimage.manifest_hash !== candidate.source_manifest_hash
    || manifestPreimage.row_content_hash !== manifestParent.content_hash
  ) return false;
  const canonicalItems = payload.items as Record<string, unknown>[];
  const itemHashes = await Promise.all(canonicalItems.map((item) => canonicalCandidateHash(item, true)));
  if (itemHashes.some((hash) => hash === null)) return false;
  const sortedItems = canonicalItems.map((item, index) => ({ item, hash: itemHashes[index] as string })).sort((left, right) => {
    const leftKey = `${left.item.metric_key}\u0000${left.item.source_id}\u0000${left.item.source_locator}\u0000${left.item.observed_start}\u0000${left.item.observed_end}\u0000${left.hash}`;
    const rightKey = `${right.item.metric_key}\u0000${right.item.source_id}\u0000${right.item.source_locator}\u0000${right.item.observed_start}\u0000${right.item.observed_end}\u0000${right.hash}`;
    return compareCodePointTuple([leftKey], [rightKey]);
  }).map(({ item }) => item);
  const visibleItems = (candidate.items as unknown[]).map(canonicalCandidateItem);
  if (visibleItems.some((item) => item === null) || JSON.stringify(visibleItems) !== JSON.stringify(sortedItems)) return false;
  const dossierHash = await canonicalCandidateHash(payload, true);
  const dossierDescriptorHash = await canonicalCandidateHash({
    candidate_dossier_content_hash: dossier.content_hash, created_at: dossierPreimage.created_at,
  }, false);
  if (dossierHash !== dossier.content_hash || dossierHash !== dossierPreimage.raw_content_hash || dossierDescriptorHash !== dossierParent.content_hash) return false;

  const reviews = candidate.reviews as unknown[];
  for (const reviewValue of reviews) {
    const review = record(reviewValue);
    const parent = review === null ? undefined : parents.map(record).find((item) => item?.reference === review.reference);
    const preimage = parent == null ? null : record(parent.descriptor_preimage);
    if (review === null || parent == null || preimage === null || preimage.raw_content_hash !== review.content_hash) return false;
    const reviewedAt = normalizedUtc(review.reviewed_at as string);
    if (reviewedAt !== preimage.reviewed_at) return false;
    const reviewHash = await canonicalCandidateHash({
      decision: review.decision, dossier_content_hash: dossier.content_hash, dossier_id: dossier.reference,
      rationale: review.rationale, reviewed_at: preimage.reviewed_at,
      reviewer_identity: review.reviewer_identity, reviewer_role: review.reviewer_role,
    }, true);
    const descriptorHash = await canonicalCandidateHash({
      candidate_review_content_hash: review.content_hash, created_at: preimage.created_at, reviewed_at: preimage.reviewed_at,
    }, false);
    if (reviewHash !== review.content_hash || descriptorHash !== parent.content_hash) return false;
  }
  return true;
}

async function checkedCandidateEvidence(value: unknown, selected: ResearchRevision): Promise<CandidateEvidence> {
  const candidate = record(value);
  const dossier = record(candidate?.dossier);
  const answerability = record(candidate?.answerability);
  if (
    candidate === null
    || !hasOnlyKeys(candidate, ["schema_version", "revision_id", "object_id", "basis_id", "version_kind", "content_hash", "cutoff", "source_manifest_hash", "parent_refs", "dossier", "items", "reviews", "answerability"])
    || candidate.schema_version !== "underwriting.v1"
    || !isCanonicalUuid(candidate.revision_id) || !isCanonicalUuid(candidate.object_id) || !isCanonicalUuid(candidate.basis_id)
    || candidate.revision_id !== selected.id || candidate.object_id !== selected.object_id || candidate.basis_id !== selected.basis_id
    || candidate.version_kind !== "industry_evidence_candidate" || selected.version_kind !== "industry_evidence_candidate"
    || !isContentHash(candidate.content_hash) || candidate.content_hash !== selected.content_hash
    || !isUtcTimestamp(candidate.cutoff) || candidate.cutoff !== selected.cutoff
    || !isContentHash(candidate.source_manifest_hash) || candidate.source_manifest_hash !== selected.source_manifest_hash
    || dossier === null
    || !hasOnlyKeys(dossier, ["schema_version", "reference", "content_hash", "dossier_key", "version", "status", "scope_statement", "rejected_calculations", "supersedes_id", "canonical_payload"])
    || dossier.schema_version !== "underwriting.v1" || !isCanonicalUuid(dossier.reference) || !isContentHash(dossier.content_hash)
    || !nonEmptyString(dossier.dossier_key) || !Number.isSafeInteger(dossier.version) || (dossier.version as number) < 1
    || dossier.status !== "candidate" || !nonEmptyString(dossier.scope_statement)
    || !(dossier.supersedes_id === null || isCanonicalUuid(dossier.supersedes_id))
    || !hasControlledCandidateCalculations(dossier.rejected_calculations)
    || !Array.isArray(candidate.items) || candidate.items.length === 0 || !candidate.items.every(isCandidateItem)
    || !Array.isArray(candidate.reviews) || candidate.reviews.length !== 2 || !candidate.reviews.every(isCandidateReview)
    || new Set(candidate.reviews.map((review) => record(review)?.reviewer_role)).size !== 2
    || new Set(candidate.reviews.map((review) => record(review)?.reviewer_identity)).size !== 2
    || answerability === null
    || !hasOnlyKeys(answerability, ["schema_version", "reference", "content_hash", "state", "research_debt_keys", "resolution_requirements"])
    || answerability.schema_version !== "underwriting.v1" || !isCanonicalUuid(answerability.reference)
    || !isContentHash(answerability.content_hash) || answerability.state !== "not_answerable"
    || !isStringList(answerability.research_debt_keys) || !isStringList(answerability.resolution_requirements)
    || !candidateParentBindings(candidate, selected)
  ) throw new CandidateEvidenceIntegrityError();
  if (!await verifiedCandidatePayloadHashes(candidate)) {
    throw new CandidateEvidenceIntegrityError();
  }
  return candidate as CandidateEvidence;
}

function isDiff(value: unknown): value is ResearchRevisionDiff {
  const diff = record(value);
  return diff?.schema_version === "underwriting.v1"
    && nonEmptyString(diff.from_revision_id)
    && nonEmptyString(diff.to_revision_id)
    && nonEmptyString(diff.from_content_hash)
    && nonEmptyString(diff.to_content_hash)
    && nonEmptyString(diff.diff_hash)
    && Array.isArray(diff.entries)
    && diff.entries.every((entry) => {
      const change = record(entry);
      return change?.schema_version === "underwriting.v1"
        && GROUPS.some(([group]) => change.group === group)
        && ["added", "removed", "replaced"].includes(change.change_type as string)
        && nonEmptyString(change.artifact_type)
        && nonEmptyString(change.identity)
        && (change.before === null || isArtifact(change.before))
        && (change.after === null || isArtifact(change.after));
    });
}

function checkedDiff(value: unknown, previous: ResearchRevision, selected: ResearchRevision): ResearchRevisionDiff {
  if (
    !isDiff(value)
    || value.from_revision_id !== previous.id
    || value.to_revision_id !== selected.id
    || value.from_content_hash !== previous.content_hash
    || value.to_content_hash !== selected.content_hash
  ) throw new ArchiveIntegrityError();

  const before = new Map(previous.parent_refs.map((artifact) => [artifactIdentity(artifact), artifact]));
  const after = new Map(selected.parent_refs.map((artifact) => [artifactIdentity(artifact), artifact]));
  const expected = [...new Set([...before.keys(), ...after.keys()])]
    .sort()
    .flatMap((identity): DiffEntry[] => {
      const earlier = before.get(identity) ?? null;
      const later = after.get(identity) ?? null;
      if (earlier !== null && later !== null && sameArtifact(earlier, later)) return [];
      const artifact = later ?? earlier;
      if (artifact === null) return [];
      return [{
        schema_version: "underwriting.v1",
        group: groupForArtifact(artifact.artifact_type),
        change_type: earlier === null ? "added" : later === null ? "removed" : "replaced",
        artifact_type: artifact.artifact_type,
        identity: artifact.identity,
        before: earlier,
        after: later,
      } as DiffEntry];
    })
    .sort(compareDiffEntries);

  if (
    value.entries.length !== expected.length
    || value.entries.some((entry, index) => !sameDiffEntry(entry, expected[index]))
  ) throw new ArchiveIntegrityError();
  return value;
}

function groupForArtifact(artifactType: string): DiffEntry["group"] {
  if (["source_manifest", "metric_definition", "metric_observation", "ledger", "semantic_snapshot", "source_fact"].includes(artifactType)) return "evidence";
  if (artifactType === "mechanism") return "mechanism";
  if (["industry_state", "industry_scenario", "company_exposure", "earnings_engine", "forecast_input", "falsifier"].includes(artifactType)) return "industry_model";
  if (["answerability", "research_boundary"].includes(artifactType)) return "answerability";
  throw new ArchiveIntegrityError();
}

function compareDiffEntries(left: DiffEntry, right: DiffEntry): number {
  const rank: Record<DiffEntry["group"], number> = {
    evidence: 0, mechanism: 1, industry_model: 2, answerability: 3,
  };
  const leftKey = [rank[left.group], left.artifact_type, left.identity, left.change_type, left.before?.reference ?? "", left.after?.reference ?? ""];
  const rightKey = [rank[right.group], right.artifact_type, right.identity, right.change_type, right.before?.reference ?? "", right.after?.reference ?? ""];
  return leftKey.join("\u0000").localeCompare(rightKey.join("\u0000"));
}

function sameDiffEntry(left: DiffEntry, right: DiffEntry | undefined): boolean {
  return right !== undefined
    && left.schema_version === right.schema_version
    && left.group === right.group
    && left.change_type === right.change_type
    && left.artifact_type === right.artifact_type
    && left.identity === right.identity
    && (left.before === null ? right.before === null : right.before !== null && sameArtifact(left.before, right.before))
    && (left.after === null ? right.after === null : right.after !== null && sameArtifact(left.after, right.after));
}

function safeEnvelopeText(value: string | undefined): string | null {
  if (!value) return null;
  const clean = value.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 240);
  return clean || null;
}

function errorCopy(error: unknown, subject: "directory" | "detail"): string {
  if (error instanceof CandidateEvidenceIntegrityError) return CANDIDATE_INTEGRITY_ERROR;
  if (error instanceof ArchiveIntegrityError) return INTEGRITY_ERROR;
  if (error instanceof UnderwritingResearchRequestError) {
    if (error.status === 404 && subject === "detail") return "找不到该冻结版本档案。";
    if (error.status === 422) {
      const message = safeEnvelopeText(error.message);
      const requestId = safeEnvelopeText(error.requestId);
      const prefix = subject === "directory"
        ? "查询条件无法读取，请修改后重试。"
        : "所选冻结版本无法安全读取或完成校验，未展示该版本、当前或更新资料。";
      return [prefix, message && `服务说明：${message}`, requestId && `请求编号：${requestId}`]
        .filter((part): part is string => Boolean(part))
        .join(" ");
    }
  }
  return subject === "directory"
    ? "冻结档案目录暂时无法读取，未展示替代资料。"
    : "这个冻结版本暂时无法读取，未展示替代资料。";
}

function labelForStatus(status: string | null): string | null {
  if (status === "not_answerable") return "研究尚需验证";
  if (status === "wait_for_validation") return "等待验证资料";
  if (status === "unknown_evidence_gap") return "Unknown evidence gap";
  return status;
}

function formatPeriod(start: string | null, end: string | null): string {
  if (!start && !end) return "未提供";
  if (!start) return `截至 ${end}`;
  if (!end) return `自 ${start}`;
  return `${start} 至 ${end}`;
}

function ArtifactDetails({ artifact }: { artifact: Artifact }) {
  const status = labelForStatus(artifact.status);
  return (
    <dl className="ura-artifact-details">
      <div><dt>参考</dt><dd>{artifact.reference}</dd></div>
      <div><dt>标识</dt><dd>{artifact.identity}</dd></div>
      <div><dt>定位</dt><dd>{artifact.source_locators.length ? artifact.source_locators.join("；") : "未提供"}</dd></div>
      <div><dt>单位</dt><dd>{artifact.unit ?? "未提供"}</dd></div>
      <div><dt>期间</dt><dd>{formatPeriod(artifact.period_start, artifact.period_end)}</dd></div>
      <div><dt>可用时间</dt><dd>{artifact.available_at ?? "未提供"}</dd></div>
      <div><dt>状态</dt><dd>{status ?? "未提供"}</dd></div>
    </dl>
  );
}

function EvidenceTable({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null;
  return (
    <section className="ura-evidence" aria-label="冻结证据记录">
      <h2>冻结证据记录</h2>
      <div className="ura-evidence-scroll">
        <table>
          <thead><tr><th>参考</th><th>定位</th><th>单位</th><th>期间</th><th>可用时间</th><th>状态</th></tr></thead>
          <tbody>{artifacts.map((artifact) => (
            <tr key={`${artifact.artifact_type}:${artifact.identity}:${artifact.content_hash}`}>
              <td>{artifact.reference}</td>
              <td>{artifact.source_locators.length ? artifact.source_locators.join("；") : "未提供"}</td>
              <td>{artifact.unit ?? "未提供"}</td>
              <td>{formatPeriod(artifact.period_start, artifact.period_end)}</td>
              <td>{artifact.available_at ?? "未提供"}</td>
              <td>{labelForStatus(artifact.status) ?? "未提供"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

function answerabilityLabel(state: "answerable" | "partially_answerable" | "not_answerable"): string {
  return { answerable: "可回答", partially_answerable: "部分可回答", not_answerable: "研究尚需验证" }[state];
}

function blockerLabel(blocker: string): string {
  const labels: Record<string, string> = {
    missing_key_baseline: "缺少关键基线",
    unresolved_source_conflict: "来源冲突尚未解决",
    mechanism_unidentified: "传导机制尚未识别",
    financial_model_not_closed: "财务模型尚未闭合",
    expectation_surface_unidentifiable: "预期面无法识别",
    source_unavailable: "来源不可用",
    future_information_leakage: "存在未来信息泄漏",
  };
  return labels[blocker] ?? blocker;
}

function ResearchBoundaries({ boundary }: { boundary: ResearchRevisionBoundary }) {
  const answerability = boundary.answerability;
  const gaps = boundary.unknown_evidence_gaps;
  return (
    <aside className="ura-boundaries" aria-label="研究边界">
      <p className="ros-eyebrow">已校验的冻结父图</p>
      <h2>研究边界</h2>
      <p className="ura-boundaries__intro">只呈现该版本已验证的可回答性与明确记录的 Unknown gap，不从父引用状态推导边界。</p>
      {answerability === null ? <p className="ura-boundaries__empty">此版本的冻结父图未记录可展示的可回答性资料。</p> : <>
        <dl className="ura-boundary-details">
          <div><dt>可回答性</dt><dd>{answerabilityLabel(answerability.state)}</dd></div>
          <div><dt>研究债务</dt><dd>{answerability.research_debt_keys.length ? answerability.research_debt_keys.join("；") : "未记录"}</dd></div>
          <div><dt>当前范围内可解决</dt><dd>{answerability.resolvable_within_mandate ? "是" : "否"}</dd></div>
        </dl>
        <section className="ura-boundary-section" aria-label="记录的阻塞项"><h3>记录的阻塞项</h3>{answerability.blockers.length ? <ul className="ura-boundary-list">{answerability.blockers.map((blocker) => <li key={blocker}>{blockerLabel(blocker)}</li>)}</ul> : <p>未记录</p>}</section>
        <section className="ura-boundary-section" aria-label="验证要求"><h3>验证要求</h3>{answerability.resolution_requirements.length ? <ul className="ura-boundary-list">{answerability.resolution_requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}</ul> : <p>未记录</p>}</section>
      </>}
      <section className="ura-boundary-section" aria-label="明确记录的 Unknown gap">
        <h3>明确记录的 Unknown gap</h3>
        {gaps.length === 0 ? <p className="ura-boundaries__empty">此版本的冻结父图未记录可展示的 Unknown gap；这不表示行业缺口已解决。</p> : <ul className="ura-boundary-list">{gaps.map((gap) => <li key={`${gap.reference}:${gap.content_hash}`}><b>{gap.metric_key}</b><dl className="ura-gap-details"><div><dt>来源标识</dt><dd>{gap.source_id}</dd></div><div><dt>来源角色</dt><dd>{gap.source_role}</dd></div><div><dt>来源定位</dt><dd>{gap.source_locator}</dd></div><div><dt>单位</dt><dd>{gap.unit}</dd></div><div><dt>观测期间</dt><dd>{formatPeriod(gap.observed_start, gap.observed_end)}</dd></div><div><dt>生效时间</dt><dd>{gap.effective_at}</dd></div><div><dt>可用时间</dt><dd>{gap.available_at}</dd></div>{Object.entries(gap.dimensions).sort(([left], [right]) => left.localeCompare(right)).map(([key, dimension]) => <div key={key}><dt>维度 · {key}</dt><dd>{dimension}</dd></div>)}</dl></li>)}</ul>}
      </section>
    </aside>
  );
}

function candidateStatusLabel(status: CandidateEvidence["items"][number]["status"]): string {
  return {
    source_reported: "来源报告",
    official_aggregate: "官方汇总",
    chart_approximation: "图表近似",
    assumption_bound: "假设边界",
    unknown: "Unknown",
  }[status];
}

function reviewerRoleLabel(role: CandidateEvidence["reviews"][number]["reviewer_role"]): string {
  return role === "provenance" ? "来源审阅" : "方法审阅";
}

function CandidateEvidencePanel({ candidate }: { candidate: CandidateEvidence }) {
  return (
    <section className="ura-candidate" aria-label="候选证据（已审阅，未正式化）">
      <p className="ros-eyebrow">候选证据（已审阅，未正式化）</p>
      <h2>候选证据</h2>
      <p>范围：{candidate.dossier.scope_statement}</p>
      <p>边界声明：不得作为正式 IndustryState 输入或模型完成依据；不会从当前记录补全、拼接或推导。</p>
      <section aria-label="冻结拒绝的计算"><h3>冻结拒绝的计算</h3><ol>{candidate.dossier.rejected_calculations.map((calculation) => <li key={calculation}>{calculation}</li>)}</ol></section>
      <section aria-label="候选条目">
        <h3>冻结候选条目</h3>
        {candidate.items.map((item) => <article key={`${item.metric_key}:${item.status}`} className="ura-candidate-item">
          <h4>{item.metric_key}</h4>
          <dl className="ura-artifact-details">
            <div><dt>状态</dt><dd>{candidateStatusLabel(item.status)}</dd></div>
            <div><dt>来源标识</dt><dd>{item.source_id}</dd></div>
            <div><dt>来源定位</dt><dd>{item.source_locator}</dd></div>
            <div><dt>期间</dt><dd>{formatPeriod(item.observed_start, item.observed_end)}</dd></div>
            <div><dt>可用时间</dt><dd>{item.available_at}</dd></div>
            <div><dt>范围</dt><dd>{item.scope_statement}</dd></div>
            <div><dt>排除项</dt><dd>{item.exclusions.length ? item.exclusions.join("；") : "未记录"}</dd></div>
            <div><dt>方法</dt><dd>{item.methodology}</dd></div>
            <div><dt>禁止拼接声明</dt><dd>{item.prohibited_splicing_declaration}</dd></div>
            {item.status === "chart_approximation" && <><div><dt>图表转录</dt><dd>{item.transcription_method}</dd></div><div><dt>误差范围</dt><dd>{item.error_bound}</dd></div></>}
            {item.status === "assumption_bound" && <><div><dt>假设约束</dt><dd>{item.scenario_use}</dd></div><div><dt>非观测声明</dt><dd>是</dd></div></>}
            {item.status === "unknown" && <div><dt>Unknown 原因</dt><dd>{item.unknown_reason}</dd></div>}
          </dl>
        </article>)}
      </section>
      <section aria-label="候选审阅">
        <h3>冻结审阅</h3>
        {candidate.reviews.map((review) => <dl className="ura-artifact-details" key={review.reference}>
          <div><dt>角色</dt><dd>{reviewerRoleLabel(review.reviewer_role)}</dd></div>
          <div><dt>审阅身份</dt><dd>{review.reviewer_identity}</dd></div>
          <div><dt>决定</dt><dd>批准</dd></div>
          <div><dt>理由</dt><dd>{review.rationale}</dd></div>
          <div><dt>审阅时间</dt><dd>{review.reviewed_at}</dd></div>
        </dl>)}
      </section>
    </section>
  );
}

function DirectoryPage() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<ArchiveKind>("");
  const [archiveState, setArchiveState] = useState<LoadState<ResearchArchiveList>>({ state: "loading" });
  const filterGeneration = useRef(0);

  useEffect(() => {
    let live = true;
    const generation = ++filterGeneration.current;
    setArchiveState({ state: "loading" });
    void underwritingResearchApi.listArchives({ query: query.trim() || undefined, kind: kind === "" ? undefined : kind })
      .then((value) => { if (live && filterGeneration.current === generation) setArchiveState({ state: "ready", value }); })
      .catch((error: unknown) => { if (live && filterGeneration.current === generation) setArchiveState({ state: "error", error }); });
    return () => { live = false; };
  }, [query, kind]);

  async function loadMore() {
    if (archiveState.state !== "ready") return;
    const previous = archiveState.value;
    const cursor = previous.next_cursor;
    if (!cursor) return;
    const generation = filterGeneration.current;
    setArchiveState({ state: "loading", value: previous });
    try {
      const next = await underwritingResearchApi.listArchives({
        query: query.trim() || undefined,
        kind: kind === "" ? undefined : kind,
        cursor,
      });
      if (filterGeneration.current === generation) {
        setArchiveState({ state: "ready", value: { ...next, items: [...previous.items, ...next.items] } });
      }
    } catch (error) {
      if (filterGeneration.current === generation) setArchiveState({ state: "error", error });
    }
  }

  const archives: ResearchArchiveItem[] = archiveState.state === "error"
    ? []
    : archiveState.value?.items ?? [];
  return (
    <main className="ros-page ura-page">
      <header className="ros-page-head ura-page-head">
        <div>
          <p className="ros-eyebrow">研究资产 · Immutable archive</p>
          <h1>公司／行业档案</h1>
          <p>从冻结版本、原始定位与证据缺口进入研究，不以新的资料替代历史记录。</p>
        </div>
      </header>
      <section className="ura-directory" aria-label="档案目录">
        <div className="ura-filters">
          <label><span>检索</span><input type="search" aria-label="搜索公司或行业档案" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称或代码" /></label>
          <label><span>对象</span><select aria-label="研究对象类型" value={kind} onChange={(event) => setKind(event.target.value as ArchiveKind)}><option value="">全部</option><option value="company">公司</option><option value="industry">行业</option><option value="security">证券</option></select></label>
        </div>
        {archiveState.state === "loading" && !archiveState.value && <p className="ura-loading" role="status">正在读取冻结档案目录</p>}
        {archiveState.state === "error" && <p className="ura-alert" role="alert">{errorCopy(archiveState.error, "directory")}</p>}
        {archiveState.state === "ready" && archives.length === 0 && <p className="ura-empty" role="status">没有符合条件的冻结档案。</p>}
        {archives.length > 0 && <div className="ura-directory-list">{archives.map((item) => item.lineage_state === "readable" ? (
          <Link className="ura-archive-row" key={`${item.object_id}:${item.version_kind}`} to={`/underwriting/research/${encodeURIComponent(item.object_id)}/${encodeURIComponent(item.version_kind)}`} aria-label={`打开${item.canonical_name}档案`}>
            <span className="ura-archive-row__title"><b>{item.canonical_name}</b><small>{item.external_key}</small></span><span>{item.object_kind}</span><span>版本 {item.version_count}</span><span>序列 {item.latest_sequence ?? "未提供"}</span><span>{item.cutoff ?? "未提供"}</span>
          </Link>
        ) : (
          <article className="ura-archive-row ura-archive-row--unreadable" key={`${item.object_id}:${item.version_kind}`}>
            <span className="ura-archive-row__title"><b>{item.canonical_name}</b><small>{item.external_key}</small></span><span>{item.object_kind}</span><span>版本 {item.version_count}</span><span className="ura-unreadable-copy">该档案无法校验，未展示版本资料。</span>
          </article>
        ))}</div>}
        {archiveState.state === "ready" && archiveState.value.next_cursor && <button className="ura-load-more" type="button" onClick={() => void loadMore()}>载入后续档案</button>}
      </section>
    </main>
  );
}

function DetailPage({ objectId, versionKind }: { objectId: string; versionKind: string }) {
  const [historyState, setHistoryState] = useState<LoadState<ResearchRevisionHistory>>({ state: "loading" });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detailState, setDetailState] = useState<LoadState<DetailLoad> | null>(null);

  useEffect(() => {
    let live = true;
    setHistoryState({ state: "loading" });
    setSelectedId(null); setDetailState(null);
    void underwritingResearchApi.history(objectId, versionKind).then((value) => {
      if (!live) return;
      const history = checkedHistory(value, objectId, versionKind);
      setHistoryState({ state: "ready", value: history });
      const latest = history.revisions[history.revisions.length - 1];
      setSelectedId(latest?.id ?? null);
    }).catch((error: unknown) => { if (live) setHistoryState({ state: "error", error }); });
    return () => { live = false; };
  }, [objectId, versionKind]);

  const revisions = useMemo(() => historyState.state === "ready" ? historyState.value.revisions : [], [historyState]);
  const selectedIndex = revisions.findIndex((revision) => revision.id === selectedId);
  const selected = selectedIndex >= 0 ? revisions[selectedIndex] : null;
  const previous = selectedIndex > 0 ? revisions[selectedIndex - 1] : null;

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setDetailState({ state: "loading" });
    const candidateSelected = selected.version_kind === "industry_evidence_candidate";
    void Promise.all([
      underwritingResearchApi.revision(selected.id),
      previous ? underwritingResearchApi.diff(previous.id, selected.id) : Promise.resolve(null),
      candidateSelected ? Promise.resolve(null) : underwritingResearchApi.boundary(selected.id),
      candidateSelected ? underwritingResearchApi.candidateEvidence(selected.id) : Promise.resolve(null),
    ]).then(async ([revision, diff, boundary, candidate]) => {
      if (!live) return;
      const checked = checkedRevision(revision, selected);
      const checkedCandidate = candidateSelected ? await checkedCandidateEvidence(candidate, checked) : null;
      if (!live) return;
      setDetailState({ state: "ready", value: {
        revision: checked,
        diff: previous ? checkedDiff(diff, previous, checked) : null,
        boundary: candidateSelected ? null : checkedBoundary(boundary, checked),
        candidate: checkedCandidate,
      } });
    }).catch((error: unknown) => { if (live) setDetailState({ state: "error", error }); });
    return () => { live = false; };
  }, [selected?.id, previous?.id]);

  if (historyState.state === "loading") return <main className="ros-page ura-page"><p className="ura-loading" role="status">正在读取冻结版本档案</p></main>;
  if (historyState.state === "error") return <main className="ros-page ura-page"><p className="ura-alert" role="alert">{errorCopy(historyState.error, "detail")}</p></main>;
  if (!revisions.length || !selected) return <main className="ros-page ura-page"><p className="ura-empty" role="status">该档案没有可读取的冻结版本。</p></main>;
  if (!detailState || detailState.state === "loading") return <main className="ros-page ura-page"><p className="ura-loading" role="status">正在校验已选冻结版本与相邻差异</p></main>;
  if (detailState.state === "error") return <main className="ros-page ura-page"><p className="ura-alert" role="alert">{errorCopy(detailState.error, "detail")}</p></main>;

  const revision = detailState.value.revision;
  const changed = detailState.value.diff?.entries ?? [];
  const artifacts = revision.parent_refs;
  const candidate = detailState.value.candidate;

  return (
    <main className="ros-page ura-page">
      <header className="ros-page-head ura-page-head"><div><p className="ros-eyebrow">冻结研究档案</p><h1>{historyState.value.canonical_name}</h1><p>{labelForObjectKind(historyState.value.object_kind)} · {historyState.value.external_key}</p></div><Link className="ura-back-link" to="/underwriting/research">返回档案目录</Link></header>
      <div className="ura-layout">
        <aside className="ura-timeline" aria-label="研究版本时间线"><p className="ros-eyebrow">不可变版本</p>{revisions.map((revision) => <button key={revision.id} type="button" aria-pressed={revision.id === selected.id} className={revision.id === selected.id ? "ura-version is-selected" : "ura-version"} onClick={() => setSelectedId(revision.id)}><b>版本 {revision.sequence}</b><span>{revision.cutoff}</span></button>)}</aside>
        <section className="ura-detail" aria-label="已选冻结版本">
          <section className="ura-version-summary"><h2>版本 {revision.sequence}</h2><dl><div><dt>截点</dt><dd>{revision.cutoff}</dd></div><div><dt>来源摘要</dt><dd>{revision.source_manifest_hash}</dd></div><div><dt>内容摘要</dt><dd>{revision.content_hash}</dd></div></dl></section>
          {candidate ? <CandidateEvidencePanel candidate={candidate} /> : <>
          {previous ? <p className="ura-predecessor">仅与紧邻的版本 {previous.sequence} 对照。</p> : <p className="ura-predecessor">这是该档案最早的冻结版本，没有前序版本可对照。</p>}
          {previous && detailState.value.diff && <div className="ura-diff-groups">{GROUPS.map(([group, heading]) => {
            const entries = changed.filter((entry) => entry.group === group);
            return <section className="ura-diff-group" key={group}><h2>{heading}</h2>{entries.length === 0 ? <p>这一版本没有该类冻结变化</p> : entries.map((entry) => <article className="ura-diff-entry" key={`${entry.change_type}:${entry.artifact_type}:${entry.identity}`}><header><b>{entry.change_type}</b><span>{entry.artifact_type}</span></header>{entry.before && <section className="ura-diff-card" aria-label="前一版本证据"><h3>前一版本</h3><ArtifactDetails artifact={entry.before} /></section>}{entry.after && <section className="ura-diff-card" aria-label="当前版本证据"><h3>当前版本</h3><ArtifactDetails artifact={entry.after} /></section>}</article>)}</section>;
          })}</div>}
          <EvidenceTable artifacts={artifacts} />
          </>}
        </section>
        {detailState.value.boundary && <ResearchBoundaries boundary={detailState.value.boundary} />}
      </div>
    </main>
  );
}

export default function ResearchArchivePage() {
  const { objectId, versionKind } = useParams();
  return objectId && versionKind
    ? <DetailPage key={`${objectId}:${versionKind}`} objectId={objectId} versionKind={versionKind} />
    : <DirectoryPage />;
}
