(function () {
  "use strict";

  const CONFIRMATION_SCHEMA_VERSION = 1;
  const DRAFT_FIELDS = Object.freeze([
    "title",
    "statement",
    "observationStart",
    "observationEnd",
    "nextValidationEvent",
    "supportCondition",
    "falsifier",
  ]);
  const TEXT_FIELDS = Object.freeze(DRAFT_FIELDS.filter((field) => !field.startsWith("observation")));
  const ORIGINS = Object.freeze(["ai", "human"]);
  const LAST_EDITORS = Object.freeze(["ai", "human", "system"]);
  const EVIDENCE_REVIEW_STATES = Object.freeze(["reviewed_links_present", "pending_relationship_review", "no_evidence_links"]);

  function isStrictISODate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/u.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00.000Z`);
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  function validateObservationRange(observationStart, observationEnd, researchPeriod) {
    const errors = {};
    if (!isStrictISODate(observationStart)) errors.observationStart = "invalid_date";
    if (!isStrictISODate(observationEnd)) errors.observationEnd = "invalid_date";
    if (Object.keys(errors).length) return errors;
    if (observationStart > observationEnd) errors.observationStart = "reversed_range";
    if (observationStart < researchPeriod.start) errors.observationStart = "before_research_period";
    if (observationEnd > researchPeriod.end) errors.observationEnd = "after_research_period";
    return errors;
  }

  function confirmationStorageKey(caseId) {
    return `new-research-confirmation:v${CONFIRMATION_SCHEMA_VERSION}:${caseId}`;
  }

  function normalizeText(value) {
    if (typeof value !== "string") return undefined;
    const normalized = value.trim();
    return normalized && normalized.length <= 2000 ? normalized : undefined;
  }

  function indexFixtureTheses(fixture) {
    return new Map(fixture.theses.map((thesis) => [thesis.id, thesis]));
  }

  function evidenceReviewStateForDraft(id, fixture) {
    const state = indexFixtureTheses(fixture).get(id)?.evidenceReviewState ?? "no_evidence_links";
    return EVIDENCE_REVIEW_STATES.includes(state) ? state : undefined;
  }

  function deriveLastEditedBy(draft, trustedFixture) {
    if (draft.origin === "human" || !trustedFixture) return "human";
    return DRAFT_FIELDS.some((field) => draft[field] !== trustedFixture[field]) ? "human" : "ai";
  }

  function validateDraft(candidate, fixture) {
    const errors = {};
    const id = normalizeText(candidate?.id);
    if (!id || !/^[A-Z0-9][A-Z0-9-]{2,63}$/u.test(id)) errors.id = "invalid_id";

    const trustedFixture = id ? indexFixtureTheses(fixture).get(id) : undefined;
    const origin = candidate?.origin;
    if (!ORIGINS.includes(origin) || (trustedFixture ? origin !== trustedFixture.origin : origin !== "human")) {
      errors.origin = "invalid_origin";
    }

    const normalized = { id, origin };
    for (const field of TEXT_FIELDS) {
      normalized[field] = normalizeText(candidate?.[field]);
      if (!normalized[field]) errors[field] = "required";
    }
    normalized.observationStart = candidate?.observationStart;
    normalized.observationEnd = candidate?.observationEnd;
    Object.assign(errors, validateObservationRange(
      normalized.observationStart,
      normalized.observationEnd,
      fixture.case.researchPeriod,
    ));

    if (Object.keys(errors).length) return { errors };
    normalized.lastEditedBy = deriveLastEditedBy(normalized, trustedFixture);
    return { draft: normalized, errors };
  }

  function validateDraftCollection(candidates, fixture) {
    const errors = {};
    if (!Array.isArray(candidates) || candidates.length < 1 || candidates.length > 3) {
      errors._record = "invalid_draft_count";
      return { errors };
    }
    const drafts = [];
    const observedIds = new Set();
    candidates.forEach((candidate, index) => {
      const result = validateDraft(candidate, fixture);
      if (result.draft && observedIds.has(result.draft.id)) result.errors.id = "duplicate_id";
      if (result.draft) observedIds.add(result.draft.id);
      if (Object.keys(result.errors).length) errors[index] = result.errors;
      else drafts.push(result.draft);
    });
    return Object.keys(errors).length ? { errors } : { drafts, errors };
  }

  function confirmationContext(fixture) {
    return {
      schemaVersion: CONFIRMATION_SCHEMA_VERSION,
      caseId: fixture.case.id,
      snapshotId: fixture.case.snapshotId,
      cutoff: fixture.case.cutoff,
      researchPlanRevision: fixture.case.researchPlan.revision,
    };
  }

  function createConfirmationRecord(candidates, fixture) {
    const validated = validateDraftCollection(candidates, fixture);
    if (!validated.drafts) return { errors: validated.errors };
    return {
      record: {
        ...confirmationContext(fixture),
        confirmationState: "confirmed",
        theses: validated.drafts,
      },
      errors: {},
    };
  }

  function normalizeConfirmationRecord(candidate, fixture) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return undefined;
    const context = confirmationContext(fixture);
    if (Object.entries(context).some(([field, value]) => candidate[field] !== value)) return undefined;
    if (candidate.confirmationState !== "confirmed") return undefined;
    const validated = validateDraftCollection(candidate.theses, fixture);
    if (!validated.drafts) return undefined;
    for (let index = 0; index < validated.drafts.length; index += 1) {
      const suppliedEditor = candidate.theses[index]?.lastEditedBy;
      if (!LAST_EDITORS.includes(suppliedEditor) || suppliedEditor !== validated.drafts[index].lastEditedBy) return undefined;
    }
    return { ...context, confirmationState: "confirmed", theses: validated.drafts };
  }

  function readConfirmationRecord(storage, fixture) {
    const key = confirmationStorageKey(fixture.case.id);
    try {
      const raw = storage.getItem(key);
      if (!raw) return undefined;
      const normalized = normalizeConfirmationRecord(JSON.parse(raw), fixture);
      if (!normalized) storage.removeItem(key);
      return normalized;
    } catch {
      try { storage.removeItem(key); } catch { /* Storage may be unavailable. */ }
      return undefined;
    }
  }

  window.NEW_RESEARCH_STATE = Object.freeze({
    CONFIRMATION_SCHEMA_VERSION,
    DRAFT_FIELDS,
    EVIDENCE_REVIEW_STATES,
    confirmationStorageKey,
    createConfirmationRecord,
    evidenceReviewStateForDraft,
    isStrictISODate,
    normalizeConfirmationRecord,
    readConfirmationRecord,
    validateDraft,
    validateObservationRange,
  });
}());
