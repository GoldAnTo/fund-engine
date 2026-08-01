import assert from "node:assert/strict";
import { access, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";
import { deflateSync } from "node:zlib";
import { reexecWithCompatibleNode } from "./capture.mjs";

const UI_DIR = path.dirname(fileURLToPath(import.meta.url));
const REQUIRED_SCREENS = [
  "overview",
  "new-research",
  "plan",
  "case",
  "graph",
  "review",
  "library",
  "data",
  "versions",
];
const FORBIDDEN_ASSESSMENT_PATTERNS = [
  /置信度/u,
  /成熟度/u,
  /ready_for_review/iu,
  /(?:证据(?:评分|得分|覆盖率|相关性|相关度|可靠性|质量|支持度)?|相关(?:性|度)?|可靠(?:性|度)?|质量(?:评分|得分)?|评分)\s*(?:为|[:：=])?\s*\d+(?:\.\d+)?\s*%/u,
  /\b\d+(?:\.\d+)?\s*%\s*(?:的)?\s*(?:证据(?:评分|得分|覆盖率|相关性|相关度|可靠性|质量|支持度)?|相关(?:性|度)?|可靠(?:性|度)?|质量(?:评分|得分)?|评分)/u,
];

function pngCrc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function makePngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const chunk = Buffer.alloc(12 + data.length);
  chunk.writeUInt32BE(data.length, 0);
  typeBuffer.copy(chunk, 4);
  data.copy(chunk, 8);
  chunk.writeUInt32BE(pngCrc32(Buffer.concat([typeBuffer, data])), 8 + data.length);
  return chunk;
}

function rawPngChunks(png) {
  const chunks = [];
  let offset = 8;
  while (offset + 12 <= png.length) {
    const length = png.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > png.length) break;
    chunks.push({ offset, end, length, type: png.toString("ascii", offset + 4, offset + 8) });
    offset = end;
  }
  return chunks;
}

function replaceFirstIdatWithInvalidZlib(png) {
  const idat = rawPngChunks(png).find((chunk) => chunk.type === "IDAT");
  assert.ok(idat, "valid PNG test fixture must contain IDAT");
  const invalidIdat = makePngChunk("IDAT", Buffer.from([0x78, 0x9c, 0x00]));
  return Buffer.concat([png.subarray(0, idat.offset), invalidIdat, png.subarray(idat.end)]);
}

function replaceIhdrProfile(png, overrides) {
  const ihdr = rawPngChunks(png).find((chunk) => chunk.type === "IHDR");
  assert.ok(ihdr, "valid PNG test fixture must contain IHDR");
  const data = Buffer.from(png.subarray(ihdr.offset + 8, ihdr.end - 4));
  if (Object.hasOwn(overrides, "bitDepth")) data[8] = overrides.bitDepth;
  if (Object.hasOwn(overrides, "colorType")) data[9] = overrides.colorType;
  if (Object.hasOwn(overrides, "interlace")) data[12] = overrides.interlace;
  return Buffer.concat([png.subarray(0, ihdr.offset), makePngChunk("IHDR", data), png.subarray(ihdr.end)]);
}

function replaceAllIdatData(png, decodedData) {
  const idats = rawPngChunks(png).filter((chunk) => chunk.type === "IDAT");
  assert.ok(idats.length > 0, "valid PNG test fixture must contain IDAT");
  return Buffer.concat([
    png.subarray(0, idats[0].offset),
    makePngChunk("IDAT", deflateSync(decodedData)),
    png.subarray(idats.at(-1).end),
  ]);
}

export function assessmentScoringViolations(text) {
  return FORBIDDEN_ASSESSMENT_PATTERNS.filter((pattern) => pattern.test(text));
}

export function indexUniqueById(records, collectionName) {
  const index = new Map();
  for (const record of records) {
    assert.ok(record?.id, `${collectionName} records must include an id`);
    assert.ok(!index.has(record.id), `duplicate id ${record.id} in ${collectionName}`);
    index.set(record.id, record);
  }
  return index;
}

function isStrictISODate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString().slice(0, 10) === value;
}

export function isSkippableWindowsSymlinkError(error, platform = process.platform) {
  const permissionOrCapabilityCodes = new Set(["EPERM", "EACCES", "ENOSYS", "ENOTSUP", "EOPNOTSUPP"]);
  return platform === "win32" && permissionOrCapabilityCodes.has(error?.code);
}

export function assertFixtureContract(data) {
  const expectedKeys = [
    "case",
    "theses",
    "factors",
    "documents",
    "statements",
    "evidenceLinks",
    "metrics",
    "companies",
    "funds",
    "reviewQueue",
    "snapshots",
    "providerRuns",
  ].sort();
  assert.equal(Object.keys(data).sort().join(","), expectedKeys.join(","), "fixture must expose exactly the 12 contracted top-level keys");
  assert.equal(data.case.cutoff, "2025-06-30", "fixture cutoff must remain frozen at 2025-06-30");
  assert.equal(data.case.snapshotId, "RS-2025-06-30-v3", "fixture current snapshot must remain RS-2025-06-30-v3");
  assert.equal(
    data.case.researchObject,
    "从云厂商资本开支，经芯片、互连与系统交付，到分部收入的 AI 算力产业链",
    "fixture must expose the approved AI-compute research object",
  );
  assert.equal(
    data.case.phenomenon,
    "AI 资本开支持续扩张，但订单、交付与收入确认的节奏出现分化",
    "fixture must expose the approved phenomenon to explain",
  );
  assert.equal(data.case.researchPeriod?.start, "2025-01-01", "fixture must expose the approved research period start");
  assert.equal(data.case.researchPeriod?.end, "2027-12-31", "fixture must expose the approved research period end");
  assert.ok(data.case.researchPeriod.start <= data.case.researchPeriod.end, "research period start must not exceed its end");
  assert.notEqual(data.case.researchPeriod.start, data.snapshots.at(-1).cutoff, "research period start must not be synthesized from a prior snapshot cutoff");
  assert.notEqual(data.case.researchPeriod.end, data.case.cutoff, "research period end must remain independent from the evidence cutoff");
  const workbench = data.case.workbench;
  assert.ok(workbench, "case must expose an explicit workbench dossier");
  assert.equal(
    Object.keys(workbench).sort().join(","),
    ["factorAnalyses", "formalJudgment", "largestGapFactorId", "mainContradictionEvidenceLinkId", "mainContradictionFactorId", "nextValidationThesisId", "selectedFactorId", "sourceEvidenceLinkIds"].sort().join(","),
    "workbench must remain a nested case concern without adding fixture top-level keys",
  );
  assert.equal(workbench.formalJudgment.reviewState, "reviewed", "formal judgment must be human reviewed");
  assert.equal(workbench.formalJudgment.snapshotId, data.case.snapshotId, "formal judgment must bind to the current immutable snapshot");
  assert.equal(workbench.factorAnalyses.length, 4, "workbench must compare four competing factors");
  const researchPlan = data.case.researchPlan;
  assert.ok(researchPlan, "case must expose an explicit researchPlan");
  assert.equal(
    Object.keys(researchPlan).sort().join(","),
    ["candidateReuseAssetIds", "collectionTasks", "currentGapFactorIds", "negativeEvidenceSearches", "plannedProviderQueries", "positiveEvidenceSearches", "resultMetricIds", "reusableAssets", "revision"].sort().join(","),
    "researchPlan must expose the explicit planning concerns without adding top-level fixture keys",
  );
  assert.equal(researchPlan.revision, "RP-AIC-2025-01-v1", "researchPlan must expose an explicit immutable revision");
  assert.equal(
    Object.keys(researchPlan.reusableAssets).sort().join(","),
    ["documentIds", "evidenceLinkIds", "metricIds", "relatedCaseIds", "statementIds"].sort().join(","),
    "researchPlan reusableAssets must retain typed fixture references",
  );
  assert.ok(researchPlan.positiveEvidenceSearches.length > 0, "researchPlan must plan positive evidence searches");
  assert.ok(researchPlan.negativeEvidenceSearches.length > 0, "researchPlan must plan negative evidence searches");
  assert.equal(
    researchPlan.plannedProviderQueries.map((query) => query.capability).sort().join(","),
    ["announcement_filing_fulltext", "fund_holding_detail", "industry_analysis_view"].join(","),
    "researchPlan must explicitly cover the first-case Juyuan capabilities",
  );
  for (const query of researchPlan.plannedProviderQueries) {
    assert.equal(query.provider, "juyuan", `${query.id} must remain Juyuan-centered`);
    assert.equal(query.mode, "capability_probe", `${query.id} must remain a capability probe`);
    assert.equal(query.status, "planned", `${query.id} must remain planned rather than historical success`);
    assert.equal(query.exposureStatus, "probe_required", `${query.id} must not claim the catalog capability is exposed or authorized`);
    for (const field of ["purpose", "intendedArtifact"]) assert.ok(query[field], `${query.id} must include ${field}`);
    assert.deepEqual(Object.keys(query.dateScope).sort(), ["end", "start"], `${query.id} must include an independent dateScope`);
    assert.ok(isStrictISODate(query.dateScope.start), `${query.id} dateScope start must be a real ISO date`);
    assert.ok(isStrictISODate(query.dateScope.end), `${query.id} dateScope end must be a real ISO date`);
    assert.ok(query.dateScope.start <= query.dateScope.end, `${query.id} dateScope must not be reversed`);
    assert.equal(query.cutoff, data.case.cutoff, `${query.id} must bind its evidence cutoff explicitly`);
  }
  assert.equal(
    researchPlan.collectionTasks.map((task) => task.state).sort().join(","),
    ["awaiting_capability_probe", "blocked_permission", "reused_frozen"].join(","),
    "collection tasks must distinguish reused, awaiting probe, and blocked work",
  );
  assert.equal(researchPlan.collectionTasks.some((task) => task.state === "running"), false, "fixture must not claim a running collection task");
  for (const task of researchPlan.collectionTasks) {
    assert.ok(task.id && task.label, "collection tasks must be explicit inspectable items");
    assert.equal(task.cutoff, data.case.cutoff, `${task.id} must bind to the case cutoff`);
  }
  for (const search of [...researchPlan.positiveEvidenceSearches, ...researchPlan.negativeEvidenceSearches]) {
    assert.ok(search.id && search.label && search.scope && search.status, "evidence searches must include id, label, scope, and status");
  }

  const idCollections = [
    "theses",
    "factors",
    "documents",
    "statements",
    "evidenceLinks",
    "metrics",
    "companies",
    "funds",
    "reviewQueue",
    "snapshots",
    "providerRuns",
  ];
  const indexes = Object.fromEntries(
    idCollections.map((collectionName) => [collectionName, indexUniqueById(data[collectionName], collectionName)]),
  );
  const reusableReferences = [
    ["documentIds", "documents"],
    ["statementIds", "statements"],
    ["metricIds", "metrics"],
    ["evidenceLinkIds", "evidenceLinks"],
  ];
  for (const [planField, collectionName] of reusableReferences) {
    for (const id of researchPlan.reusableAssets[planField]) {
      assert.ok(indexes[collectionName].has(id), `researchPlan ${planField} references unknown ${collectionName} record ${id}`);
    }
  }
  for (const caseId of researchPlan.reusableAssets.relatedCaseIds) {
    assert.equal(caseId, data.case.id, `researchPlan relatedCaseIds references unknown case ${caseId}`);
  }
  const allReusableAssetIds = new Set(reusableReferences.flatMap(([, collectionName]) => [...indexes[collectionName].keys()]));
  const selectedReusableAssetIds = new Set(reusableReferences.flatMap(([planField]) => researchPlan.reusableAssets[planField]));
  assert.ok(researchPlan.candidateReuseAssetIds.length > 0, "researchPlan must expose at least one explicit reuse candidate");
  for (const assetId of researchPlan.candidateReuseAssetIds) {
    assert.ok(allReusableAssetIds.has(assetId), `researchPlan candidateReuseAssetIds references unknown asset ${assetId}`);
    assert.equal(selectedReusableAssetIds.has(assetId), false, `researchPlan candidate reuse asset ${assetId} must not already be selected`);
  }
  const providerQueryIds = new Set(researchPlan.plannedProviderQueries.map((query) => query.id));
  for (const task of researchPlan.collectionTasks) {
    for (const assetId of task.assetIds ?? []) {
      assert.ok(allReusableAssetIds.has(assetId), `researchPlan collection task ${task.id} references unknown asset ${assetId}`);
    }
    for (const queryId of [...(task.providerQueryIds ?? []), ...(task.providerQueryId ? [task.providerQueryId] : [])]) {
      assert.ok(providerQueryIds.has(queryId), `researchPlan collection task ${task.id} references unknown provider query ${queryId}`);
    }
  }
  const reusedTask = researchPlan.collectionTasks.find((task) => task.state === "reused_frozen");
  assert.equal(
    [...reusedTask.assetIds].sort().join(","),
    [...selectedReusableAssetIds].sort().join(","),
    "reused_frozen task assets must exactly match selected reusable assets",
  );
  for (const metricId of researchPlan.resultMetricIds) {
    assert.ok(indexes.metrics.has(metricId), `researchPlan resultMetricIds references unknown metric ${metricId}`);
  }
  for (const factorId of researchPlan.currentGapFactorIds) {
    assert.ok(indexes.factors.has(factorId), `researchPlan currentGapFactorIds references unknown factor ${factorId}`);
  }
  for (const factorId of [workbench.mainContradictionFactorId, workbench.largestGapFactorId, workbench.selectedFactorId]) {
    assert.ok(indexes.factors.has(factorId), `workbench references unknown factor ${factorId}`);
  }
  assert.ok(indexes.theses.has(workbench.nextValidationThesisId), `workbench references unknown Thesis ${workbench.nextValidationThesisId}`);
  for (const evidenceLinkId of workbench.sourceEvidenceLinkIds) {
    assert.ok(indexes.evidenceLinks.has(evidenceLinkId), `workbench references unknown evidence link ${evidenceLinkId}`);
  }
  assert.ok(indexes.evidenceLinks.has(workbench.mainContradictionEvidenceLinkId), `workbench references unknown contradiction evidence link ${workbench.mainContradictionEvidenceLinkId}`);
  assert.equal(indexes.evidenceLinks.get(workbench.mainContradictionEvidenceLinkId).role, "contradict", "main contradiction evidence must use the contradict role");
  const factorAnalysisIds = new Set();
  for (const analysis of workbench.factorAnalyses) {
    assert.ok(indexes.factors.has(analysis.factorId), `workbench factor analysis references unknown factor ${analysis.factorId}`);
    assert.ok(!factorAnalysisIds.has(analysis.factorId), `workbench factor analysis duplicates ${analysis.factorId}`);
    factorAnalysisIds.add(analysis.factorId);
    for (const field of ["proposedRole", "timeOrder", "mechanism", "directEvidence", "alternatives", "differenceExplanation", "scope", "falsifier"]) {
      assert.ok(analysis[field], `workbench factor analysis ${analysis.factorId} must include ${field}`);
    }
  }
  for (const collectionName of idCollections) {
    for (const record of data[collectionName]) {
      if (!Object.hasOwn(record, "snapshotMembership")) continue;
      assert.ok(
        Array.isArray(record.snapshotMembership) && record.snapshotMembership.length > 0,
        `${collectionName}/${record.id} snapshotMembership must be a non-empty array`,
      );
      for (const snapshotId of record.snapshotMembership) {
        assert.ok(
          indexes.snapshots.has(snapshotId),
          `${collectionName}/${record.id} references unknown snapshot ${snapshotId}`,
        );
      }
    }
  }

  const requiredRecordIds = {
    companies: ["CO-NVDA", "CO-TSM"],
    documents: ["DOC-MSFT-FY25Q3-CALL"],
    funds: ["FUND-ETF-AI-INFRA", "FUND-SEMI-INDEX"],
    reviewQueue: ["RQ-001", "RQ-002"],
    statements: ["ST-001", "ST-002", "ST-003", "ST-004"],
    evidenceLinks: ["EL-001", "EL-002", "EL-003", "EL-004"],
  };
  for (const [group, requiredIds] of Object.entries(requiredRecordIds)) {
    for (const id of requiredIds) {
      assert.ok(indexes[group].has(id), `fixture ${group} must include ${id}`);
    }
  }

  assert.equal(data.theses.length, 3, "fixture must contain exactly three theses");
  const validThesisEvidenceStates = new Set(["reviewed_links_present", "pending_relationship_review", "no_evidence_links"]);
  for (const thesis of data.theses) {
    assert.equal(thesis.origin, "ai", `${thesis.id} fixture draft must explicitly declare AI origin`);
    assert.equal(Object.hasOwn(thesis, "reviewState"), false, `${thesis.id} must not conflate Thesis draft state with evidence-link review state`);
    assert.ok(validThesisEvidenceStates.has(thesis.evidenceReviewState), `${thesis.id} must expose an explicit evidence relationship review state`);
    assert.equal(thesis.observationStart, "2025-01-01", `${thesis.id} must expose a structured observation start`);
    assert.equal(thesis.observationEnd, "2027-12-31", `${thesis.id} must expose a structured observation end`);
    for (const field of ["supportCondition", "falsifier", "nextValidationEvent"]) {
      assert.ok(thesis[field], `${thesis.id} must include ${field}`);
    }
  }

  const requiredFactorGroups = ["demand", "supply", "transmission", "constraints", "alternatives", "contradiction"];
  const factorGroups = new Set(data.factors.map((factor) => factor.group));
  for (const group of requiredFactorGroups) {
    assert.ok(factorGroups.has(group), `fixture factors must cover ${group}`);
  }

  const provenanceFields = ["sourceVersion", "sourceSpan", "publishedAt", "availableAt", "reviewState", "snapshotMembership"];
  for (const group of ["documents", "statements", "evidenceLinks", "metrics"]) {
    assert.ok(data[group].length > 0, `fixture must include at least one ${group} record`);
    for (const record of data[group]) {
      for (const field of provenanceFields) {
        assert.ok(record[field], `${group}/${record.id} must include ${field}`);
      }
      assert.ok(record.publishedAt.slice(0, 10) <= data.case.cutoff, `${group}/${record.id} must be published by the cutoff`);
      assert.ok(record.availableAt.slice(0, 10) <= data.case.cutoff, `${group}/${record.id} must be available by the cutoff`);
    }
  }

  const documentsById = indexes.documents;
  const statementsById = indexes.statements;
  const thesesById = indexes.theses;
  const factorsById = indexes.factors;
  const companiesById = indexes.companies;
  const fundsById = indexes.funds;
  const reviewQueueById = indexes.reviewQueue;

  for (const statement of data.statements) {
    assert.ok(documentsById.has(statement.documentId), `${statement.id} must reference an existing document ${statement.documentId}`);
  }
  for (const link of data.evidenceLinks) {
    assert.ok(statementsById.has(link.statementId), `${link.id} must reference an existing statement ${link.statementId}`);
    const hasThesisTarget = Object.hasOwn(link, "thesisId");
    const hasFactorTarget = Object.hasOwn(link, "factorId");
    if (hasThesisTarget) {
      assert.ok(thesesById.has(link.thesisId), `${link.id} references unknown thesis ${link.thesisId}`);
    }
    if (hasFactorTarget) {
      assert.ok(factorsById.has(link.factorId), `${link.id} references unknown factor ${link.factorId}`);
    }
    assert.equal(
      Number(hasThesisTarget) + Number(hasFactorTarget),
      1,
      `${link.id} must include exactly one target reference`,
    );
  }
  const evidenceStates = new Set(data.evidenceLinks.map((link) => link.reviewState));
  assert.ok(evidenceStates.has("reviewed"), "fixture evidence links must include reviewed evidence");
  assert.ok(evidenceStates.has("pending_review"), "fixture evidence links must include pending evidence");
  const evidenceRoles = new Set(data.evidenceLinks.map((link) => link.role));
  assert.ok(evidenceRoles.has("support"), "fixture evidence links must include support evidence");
  assert.ok(evidenceRoles.has("contradict"), "fixture evidence links must include contradictory evidence");
  for (const thesis of data.theses) {
    const links = data.evidenceLinks.filter((link) => link.thesisId === thesis.id);
    const expectedState = links.some((link) => link.reviewState === "pending_review")
      ? "pending_relationship_review"
      : (links.some((link) => link.reviewState === "reviewed") ? "reviewed_links_present" : "no_evidence_links");
    assert.equal(thesis.evidenceReviewState, expectedState, `${thesis.id} evidenceReviewState must match its evidence-link review facts`);
  }

  assert.ok(data.funds.length > 0, "fixture must include at least one fund mapping");
  for (const fund of data.funds) {
    assert.match(fund.disclosureDate, /^\d{4}-\d{2}-\d{2}$/u, `${fund.id} must include a disclosure date`);
    assert.equal(fund.mappingRole, "holding-disclosure-only", `${fund.id} must remain an explicit holding disclosure mapping`);
    assert.ok(companiesById.has(fund.companyId), `${fund.id} must reference an existing company ${fund.companyId}`);
  }

  const reviewTargets = new Map([
    ...data.documents,
    ...data.statements,
    ...data.evidenceLinks,
    ...data.metrics,
    ...data.companies,
    ...data.funds,
    ...data.theses,
    ...data.factors,
  ].map((record) => [record.id, record]));
  for (const item of data.reviewQueue) {
    const target = reviewTargets.get(item.targetId);
    assert.ok(target, `${item.id} must reference an existing review target ${item.targetId}`);
    assert.equal(item.sourceVersion, target.sourceVersion, `${item.id} must retain the target source version`);
  }
  assert.equal(data.snapshots.length, 3, "fixture must contain exactly three snapshots");
  assert.equal(data.snapshots.filter((snapshot) => snapshot.id === data.case.snapshotId).length, 1, "fixture must contain exactly one current snapshot");
  const priorSnapshots = data.snapshots.filter((snapshot) => snapshot.id !== data.case.snapshotId);
  assert.equal(priorSnapshots.length, 2, "fixture must preserve exactly two prior frozen snapshots");
  for (const snapshot of priorSnapshots) {
    assert.ok(snapshot.frozenAt, `${snapshot.id} must retain its freeze timestamp`);
    assert.ok(snapshot.cutoff < data.case.cutoff, `${snapshot.id} must predate the current cutoff`);
  }

  const providerOutcomes = new Set(data.providerRuns.map((run) => run.outcome));
  for (const outcome of ["success", "quota_failure", "permission_gap", "manual_upload"]) {
    assert.ok(providerOutcomes.has(outcome), `fixture provider runs must include ${outcome}`);
  }
  for (const run of data.providerRuns.filter((item) => item.outcome === "manual_upload")) {
    const queueItem = reviewQueueById.get(run.reviewQueueId);
    assert.ok(queueItem, `${run.id} manual upload must link to an existing review queue item`);
    assert.equal(run.sourceVersion, queueItem.sourceVersion, `${run.id} manual upload source must match its review queue item`);
    assert.ok(fundsById.has(queueItem.targetId), `${run.id} manual upload review target must be a known fund disclosure`);
  }
}

function assertDeepFrozen(value, location = "PROTOTYPE_DATA") {
  if (!value || typeof value !== "object") return;
  assert.ok(Object.isFrozen(value), `${location} must be frozen`);
  for (const [key, child] of Object.entries(value)) {
    assertDeepFrozen(child, `${location}.${key}`);
  }
}

async function assertFixtureDataContract() {
  const sandbox = { window: {} };
  vm.runInNewContext(await readFile(path.join(UI_DIR, "data.js"), "utf8"), sandbox);
  const data = sandbox.window.PROTOTYPE_DATA;
  assertFixtureContract(data);
  assertDeepFrozen(data);

  const originalCutoff = data.case.cutoff;
  try {
    data.case.cutoff = "2099-12-31";
  } catch {
    // Strict-mode assignment may throw; classic consumers may fail silently.
  }
  assert.equal(data.case.cutoff, originalCutoff, "nested cutoff mutation must not change the fixture");

  const originalThesisCount = data.theses.length;
  try {
    data.theses.push({ id: "TH-MUTATION" });
  } catch {
    // Frozen arrays throw in strict consumers; final-value assertion is authoritative.
  }
  assert.equal(data.theses.length, originalThesisCount, "nested thesis push must not change the fixture");

  const missingTopLevel = structuredClone(data);
  delete missingTopLevel.providerRuns;
  assert.throws(() => assertFixtureContract(missingTopLevel), /exactly the 12/u);

  const missingProvenance = structuredClone(data);
  delete missingProvenance.metrics[0].sourceSpan;
  assert.throws(() => assertFixtureContract(missingProvenance), /metrics\/M-NVDA-DC-REV.*sourceSpan/u);

  const missingCurrentSnapshot = structuredClone(data);
  missingCurrentSnapshot.snapshots = missingCurrentSnapshot.snapshots.filter((snapshot) => snapshot.id !== data.case.snapshotId);
  assert.throws(() => assertFixtureContract(missingCurrentSnapshot), /unknown snapshot RS-2025-06-30-v3/u);

  const missingFunds = structuredClone(data);
  missingFunds.funds = [];
  assert.throws(() => assertFixtureContract(missingFunds), /fixture funds must include/u);

  const brokenStatement = structuredClone(data);
  brokenStatement.statements[0].documentId = "DOC-MISSING";
  assert.throws(() => assertFixtureContract(brokenStatement), /existing document DOC-MISSING/u);

  const brokenEvidenceTarget = structuredClone(data);
  brokenEvidenceTarget.evidenceLinks[0].thesisId = "TH-MISSING";
  assert.throws(() => assertFixtureContract(brokenEvidenceTarget), /unknown thesis TH-MISSING/u);

  const brokenHolding = structuredClone(data);
  brokenHolding.funds[0].companyId = "CO-MISSING";
  assert.throws(() => assertFixtureContract(brokenHolding), /existing company CO-MISSING/u);

  const noPendingEvidence = structuredClone(data);
  for (const link of noPendingEvidence.evidenceLinks) link.reviewState = "reviewed";
  assert.throws(() => assertFixtureContract(noPendingEvidence), /pending evidence/u);

  const unlinkedUpload = structuredClone(data);
  delete unlinkedUpload.providerRuns.find((run) => run.outcome === "manual_upload").reviewQueueId;
  assert.throws(() => assertFixtureContract(unlinkedUpload), /manual upload must link/u);

  const duplicateStatement = structuredClone(data);
  duplicateStatement.statements.push(structuredClone(duplicateStatement.statements[0]));
  assert.throws(() => assertFixtureContract(duplicateStatement), /duplicate id ST-001 in statements/u);

  const missingSnapshotMembership = structuredClone(data);
  missingSnapshotMembership.documents[0].snapshotMembership = ["RS-MISSING"];
  assert.throws(() => assertFixtureContract(missingSnapshotMembership), /unknown snapshot RS-MISSING/u);

  const missingFundSnapshot = structuredClone(data);
  missingFundSnapshot.funds[0].snapshotMembership = ["RS-MISSING"];
  assert.throws(() => assertFixtureContract(missingFundSnapshot), /funds\/FUND-ETF-AI-INFRA references unknown snapshot RS-MISSING/u);

  const emptyReviewSnapshotMembership = structuredClone(data);
  emptyReviewSnapshotMembership.reviewQueue[0].snapshotMembership = [];
  assert.throws(() => assertFixtureContract(emptyReviewSnapshotMembership), /reviewQueue\/RQ-001 snapshotMembership must be a non-empty array/u);

  const maskedInvalidFactor = structuredClone(data);
  maskedInvalidFactor.evidenceLinks[0].factorId = "F-MISSING";
  assert.throws(() => assertFixtureContract(maskedInvalidFactor), /unknown factor F-MISSING/u);

  const multipleValidTargets = structuredClone(data);
  multipleValidTargets.evidenceLinks[0].factorId = "F-D-01";
  assert.throws(() => assertFixtureContract(multipleValidTargets), /exactly one target reference/u);

  const unknownPlanMetric = structuredClone(data);
  unknownPlanMetric.case.researchPlan.resultMetricIds = ["M-MISSING"];
  assert.throws(() => assertFixtureContract(unknownPlanMetric), /researchPlan resultMetricIds references unknown metric M-MISSING/u);

  const historicalProviderSuccess = structuredClone(data);
  historicalProviderSuccess.case.researchPlan.plannedProviderQueries[0].status = "success";
  assert.throws(() => assertFixtureContract(historicalProviderSuccess), /must remain planned rather than historical success/u);

  const exposedWithoutProbe = structuredClone(data);
  exposedWithoutProbe.case.researchPlan.plannedProviderQueries[0].exposureStatus = "authorized";
  assert.throws(() => assertFixtureContract(exposedWithoutProbe), /must not claim the catalog capability/u);

  const reversedPlanScope = structuredClone(data);
  reversedPlanScope.case.researchPlan.plannedProviderQueries[0].dateScope = { start: "2025-06-30", end: "2025-01-01" };
  assert.throws(() => assertFixtureContract(reversedPlanScope), /dateScope must not be reversed/u);

  const impossiblePlanDate = structuredClone(data);
  impossiblePlanDate.case.researchPlan.plannedProviderQueries[0].dateScope.start = "2025-02-30";
  assert.throws(() => assertFixtureContract(impossiblePlanDate), /dateScope start must be a real ISO date/u);

  const unknownCollectionAsset = structuredClone(data);
  unknownCollectionAsset.case.researchPlan.collectionTasks[0].assetIds = ["DOC-MISSING"];
  assert.throws(() => assertFixtureContract(unknownCollectionAsset), /references unknown asset DOC-MISSING/u);

  const unknownCollectionQuery = structuredClone(data);
  unknownCollectionQuery.case.researchPlan.collectionTasks[1].providerQueryIds = ["PQ-MISSING"];
  assert.throws(() => assertFixtureContract(unknownCollectionQuery), /references unknown provider query PQ-MISSING/u);

  const candidateAlreadySelected = structuredClone(data);
  candidateAlreadySelected.case.researchPlan.candidateReuseAssetIds = [candidateAlreadySelected.case.researchPlan.reusableAssets.documentIds[0]];
  assert.throws(() => assertFixtureContract(candidateAlreadySelected), /must not already be selected/u);

  const unknownWorkbenchFactor = structuredClone(data);
  unknownWorkbenchFactor.case.workbench.selectedFactorId = "F-MISSING";
  assert.throws(() => assertFixtureContract(unknownWorkbenchFactor), /workbench references unknown factor F-MISSING/u);

  const unknownWorkbenchEvidence = structuredClone(data);
  unknownWorkbenchEvidence.case.workbench.sourceEvidenceLinkIds = ["EL-MISSING"];
  assert.throws(() => assertFixtureContract(unknownWorkbenchEvidence), /workbench references unknown evidence link EL-MISSING/u);

  const unknownContradictionEvidence = structuredClone(data);
  unknownContradictionEvidence.case.workbench.mainContradictionEvidenceLinkId = "EL-MISSING";
  assert.throws(() => assertFixtureContract(unknownContradictionEvidence), /workbench references unknown contradiction evidence link EL-MISSING/u);
}

async function assertResearchPlanStateContract() {
  const sandbox = { window: {} };
  vm.runInNewContext(await readFile(path.join(UI_DIR, "data.js"), "utf8"), sandbox);
  vm.runInNewContext(await readFile(path.join(UI_DIR, "research-plan-state.js"), "utf8"), sandbox);
  const state = sandbox.window.RESEARCH_PLAN_STATE;
  assert.ok(Object.isFrozen(state), "research-plan state API must be a narrow frozen global");
  const view = state.buildResearchPlanViewModel(sandbox.window.PROTOTYPE_DATA);
  const withoutHistory = state.buildResearchPlanViewModel({ ...sandbox.window.PROTOTYPE_DATA, providerRuns: [] });
  assert.deepEqual(view.providerQueries, withoutHistory.providerQueries, "provider query plan must not derive intent from historical runs");
  assert.deepEqual(view.collection, withoutHistory.collection, "collection state must not infer running work from provider history");
  assert.equal(view.collection.running.length, 0, "plan fixture must have no running tasks");
  assert.ok(view.existingAssets.length > 0, "plan must resolve reusable frozen assets");
  const candidates = new Set(sandbox.window.PROTOTYPE_DATA.case.researchPlan.candidateReuseAssetIds);
  assert.equal(view.existingAssets.filter((item) => !item.selected).every((item) => candidates.has(item.id)), true, "only explicit candidate assets may begin unselected");
  assert.ok(view.pendingResults.every((item) => item.sourceVersion && item.targetLabel && item.reviewLabel), "pending results must retain source, target, and review state");
  assert.ok(view.failures.every((item) => ["quota_failure", "permission_gap"].includes(item.outcome)), "retryable failures must include only actual historical failures");
  assert.ok(view.manualUploads.every((item) => item.outcome === "manual_upload"), "manual uploads must remain separate historical outcomes");
  assert.equal(view.failures.some((item) => item.outcome === "success"), false, "successful runs must not be repurposed as future plan");
}

async function assertNewResearchStateDateContract() {
  const sandbox = { window: {} };
  vm.runInNewContext(await readFile(path.join(UI_DIR, "data.js"), "utf8"), sandbox);
  vm.runInNewContext(await readFile(path.join(UI_DIR, "new-research-state.js"), "utf8"), sandbox);
  const state = sandbox.window.NEW_RESEARCH_STATE;
  assert.ok(Object.isFrozen(state), "new-research state API must be a narrow frozen global");
  assert.equal(state.isStrictISODate("2025-01-01"), true);
  for (const invalid of ["2025-1-01", "2025-02-30", "not-a-date", ""] ) {
    assert.equal(state.isStrictISODate(invalid), false, `${invalid || "empty date"} must fail strict ISO date validation`);
  }

  const period = sandbox.window.PROTOTYPE_DATA.case.researchPeriod;
  assert.deepEqual({ ...state.validateObservationRange("2025-01-01", "2027-12-31", period) }, {}, "research period boundaries must be valid");
  assert.deepEqual({ ...state.validateObservationRange("2025-04-01", "2026-06-30", period) }, {}, "an in-range subperiod must be valid");
  assert.equal(state.validateObservationRange("2025-02-30", "2025-03-01", period).observationStart, "invalid_date");
  assert.equal(state.validateObservationRange("2026-01-02", "2026-01-01", period).observationStart, "reversed_range");
  assert.equal(state.validateObservationRange("2024-12-31", "2025-02-01", period).observationStart, "before_research_period");
  assert.equal(state.validateObservationRange("2026-01-01", "2028-01-01", period).observationEnd, "after_research_period");
}

async function loadNewResearchStateFixture() {
  const sandbox = { window: {} };
  vm.runInNewContext(await readFile(path.join(UI_DIR, "data.js"), "utf8"), sandbox);
  vm.runInNewContext(await readFile(path.join(UI_DIR, "new-research-state.js"), "utf8"), sandbox);
  return { data: sandbox.window.PROTOTYPE_DATA, state: sandbox.window.NEW_RESEARCH_STATE };
}

async function assertNewResearchStateSessionContract() {
  const { data, state } = await loadNewResearchStateFixture();
  assert.deepEqual({ ...state.FIELD_LIMITS }, { title: 120, statement: 2000, nextValidationEvent: 2000, supportCondition: 2000, falsifier: 2000 }, "state API must expose honest field limits");
  assert.deepEqual([...state.EVIDENCE_REVIEW_STATES], ["reviewed_links_present", "pending_relationship_review", "no_evidence_links"], "state API must keep evidence-review vocabulary separate from confirmation state");
  assert.equal(state.evidenceReviewStateForDraft("TH-AIC-01", data), "reviewed_links_present");
  assert.equal(state.evidenceReviewStateForDraft("TH-DRAFT-1", data), "no_evidence_links", "human drafts must not inherit fixture evidence review");
  const rawFixtureDraft = (thesis) => ({
    id: thesis.id,
    origin: thesis.origin,
    title: thesis.title,
    statement: thesis.statement,
    observationStart: thesis.observationStart,
    observationEnd: thesis.observationEnd,
    supportCondition: thesis.supportCondition,
    falsifier: thesis.falsifier,
    nextValidationEvent: thesis.nextValidationEvent,
  });
  const unchanged = rawFixtureDraft(data.theses[0]);
  const modified = { ...rawFixtureDraft(data.theses[1]), statement: `${data.theses[1].statement} 人工补充` };
  const human = {
    id: "TH-DRAFT-1",
    origin: "human",
    title: "人工新增命题",
    statement: "人工新增命题表述",
    observationStart: "2025-03-01",
    observationEnd: "2026-09-30",
    supportCondition: "人工支持条件",
    falsifier: "人工反证条件",
    nextValidationEvent: "人工下一验证事件",
  };
  const created = state.createConfirmationRecord([unchanged, modified, human], data);
  assert.deepEqual({ ...created.errors }, {}, "three complete drafts must create a confirmation record");
  assert.equal(created.record.confirmationState, "confirmed", "confirmation state must belong to the record");
  assert.deepEqual([...created.record.theses].map((draft) => draft.origin), ["ai", "ai", "human"]);
  assert.deepEqual([...created.record.theses].map((draft) => draft.lastEditedBy), ["ai", "human", "human"]);
  assert.equal(created.record.theses[2].title, "人工新增命题");
  assert.equal(created.record.theses[2].observationStart, "2025-03-01");
  assert.equal(created.record.theses[2].observationEnd, "2026-09-30");
  assert.equal(Object.hasOwn(created.record.theses[2], "observationPeriod"), false, "session record must not persist a formatted period string");
  assert.ok(state.normalizeConfirmationRecord(created.record, data), "a created record must round-trip through stored-record validation");
  assert.equal(created.record.schemaVersion, 2, "changed record shape must use confirmation schema v2");
  assert.match(state.confirmationStorageKey(data.case.id), /^new-research-confirmation:v2:/u);

  for (const [field, limit] of Object.entries(state.FIELD_LIMITS)) {
    const boundaryDraft = { ...human, id: `TH-BOUNDARY-${field.toUpperCase()}`, [field]: "字".repeat(limit) };
    assert.ok(state.createConfirmationRecord([boundaryDraft], data).record, `${field} must accept its exact character boundary`);
    const overlengthDraft = { ...boundaryDraft, [field]: "字".repeat(limit + 1) };
    assert.equal(state.createConfirmationRecord([overlengthDraft], data).errors[0][field], "too_long", `${field} boundary +1 must produce too_long`);
  }
  const storedOverlength = structuredClone(created.record);
  storedOverlength.theses[2].statement = "字".repeat(state.FIELD_LIMITS.statement + 1);
  assert.equal(state.normalizeConfirmationRecord(storedOverlength, data), undefined, "stored overlength drafts must be rejected");
  assert.equal(state.normalizeConfirmationRecord({ ...created.record, schemaVersion: 1 }, data), undefined, "old v1 records must not validate as current confirmations");

  const tamperedEdit = structuredClone(created.record);
  tamperedEdit.theses[0].lastEditedBy = "human";
  assert.equal(state.normalizeConfirmationRecord(tamperedEdit, data), undefined, "tampered edit attribution must invalidate stored confirmation");
  const forgedAi = structuredClone(created.record);
  forgedAi.theses[2].origin = "ai";
  forgedAi.theses[2].lastEditedBy = "ai";
  assert.equal(state.normalizeConfirmationRecord(forgedAi, data), undefined, "a non-fixture draft must never claim AI origin");
  const tamperedConfirmation = { ...created.record, confirmationState: "pending" };
  assert.equal(state.normalizeConfirmationRecord(tamperedConfirmation, data), undefined, "tampered confirmation state must invalidate stored confirmation");
}

function assertAssessmentScoringSemantics() {
  const factualPercentages = [
    "数据中心收入同比增长 34.8%。",
    "基金披露持仓权重为 8.4%。",
    "三年收入 CAGR 为 31%。",
  ];
  const prohibitedScores = [
    "证据评分：82%",
    "相关性 76%",
    "可靠度为 90%",
    "质量得分 88%",
    "ready_for_review",
    "当前命题成熟度较高",
  ];

  for (const fact of factualPercentages) {
    assert.deepEqual(
      assessmentScoringViolations(fact),
      [],
      `Legitimate financial percentage must remain allowed in an assessment: ${fact}`,
    );
  }
  for (const score of prohibitedScores) {
    assert.ok(
      assessmentScoringViolations(score).length > 0,
      `Evidence/relevance scoring language must remain forbidden: ${score}`,
    );
  }
}

function selectedRoutes(argv) {
  const option = argv.find((value) => value.startsWith("--screens="));
  const positionalIndex = argv.indexOf("--screens");
  const value = option?.split("=", 2)[1]
    ?? (positionalIndex >= 0 ? argv[positionalIndex + 1] : "all");

  if (!value || value === "all") return REQUIRED_SCREENS;
  if (value === "shell") return ["overview"];

  const requested = value.split(",").filter(Boolean);
  for (const screen of requested) {
    assert.ok(REQUIRED_SCREENS.includes(screen), `Unknown screen requested: ${screen}`);
  }
  return requested;
}

async function assertSourceContract() {
  const requiredFiles = ["index.html", "styles.css", "data.js", "new-research-state.js", "research-plan-state.js", "case-workbench-state.js", "app.js", "capture.mjs"];
  for (const filename of requiredFiles) {
    await assert.doesNotReject(
      access(path.join(UI_DIR, filename)),
      `Missing prototype harness file: ${filename}`,
    );
  }

  const [html, stateSource, planStateSource, caseStateSource, app, styles, readme] = await Promise.all([
    readFile(path.join(UI_DIR, "index.html"), "utf8"),
    readFile(path.join(UI_DIR, "new-research-state.js"), "utf8"),
    readFile(path.join(UI_DIR, "research-plan-state.js"), "utf8"),
    readFile(path.join(UI_DIR, "case-workbench-state.js"), "utf8"),
    readFile(path.join(UI_DIR, "app.js"), "utf8"),
    readFile(path.join(UI_DIR, "styles.css"), "utf8"),
    readFile(path.join(UI_DIR, "README.md"), "utf8"),
  ]);

  assert.match(html, /<main\s+id=["']app["']/u, "index.html must expose <main id=\"app\">");
  assert.match(html, /<link[^>]+href=["']\.\/styles\.css["']/u, "index.html must load ./styles.css");
  assert.match(html, /<script[^>]+src=["']\.\/data\.js["'][^>]*><\/script>/u, "index.html must load classic ./data.js");
  assert.match(html, /<script[^>]+src=["']\.\/new-research-state\.js["'][^>]*><\/script>/u, "index.html must load classic ./new-research-state.js");
  assert.match(html, /<script[^>]+src=["']\.\/research-plan-state\.js["'][^>]*><\/script>/u, "index.html must load classic ./research-plan-state.js");
  assert.match(html, /<script[^>]+src=["']\.\/case-workbench-state\.js["'][^>]*><\/script>/u, "index.html must load classic ./case-workbench-state.js");
  assert.match(html, /<script[^>]+src=["']\.\/app\.js["'][^>]*><\/script>/u, "index.html must load classic ./app.js");
  assert.ok(html.indexOf("./data.js") < html.indexOf("./new-research-state.js") && html.indexOf("./new-research-state.js") < html.indexOf("./app.js"), "new-research state must load between fixture data and rendering");
  assert.ok(html.indexOf("./new-research-state.js") < html.indexOf("./research-plan-state.js") && html.indexOf("./research-plan-state.js") < html.indexOf("./app.js"), "research-plan state must load after shared state and before rendering");
  assert.ok(html.indexOf("./research-plan-state.js") < html.indexOf("./case-workbench-state.js") && html.indexOf("./case-workbench-state.js") < html.indexOf("./app.js"), "case-workbench state must load after shared state and before rendering");
  assert.match(planStateSource, /function buildResearchPlanViewModel\(/u, "research-plan state must own its view-model builder");
  assert.doesNotMatch(app, /function buildResearchPlanViewModel\(/u, "app.js must not duplicate the plan view-model builder");
  assert.match(caseStateSource, /function buildCaseWorkbenchViewModel\(/u, "case-workbench state must own its view-model builder");
  assert.doesNotMatch(app, /function buildCaseWorkbenchViewModel\(/u, "app.js must not duplicate the case view-model builder");
  for (const stateFunction of ["isStrictISODate", "validateObservationRange", "createConfirmationRecord", "normalizeConfirmationRecord", "readConfirmationRecord"]) {
    assert.match(stateSource, new RegExp(`function ${stateFunction}\\(`, "u"), `state module must own ${stateFunction}`);
    assert.doesNotMatch(app, new RegExp(`function ${stateFunction}\\(`, "u"), `app.js must not duplicate ${stateFunction}`);
  }

  for (const screen of REQUIRED_SCREENS) {
    assert.match(app, new RegExp(`["']${screen}["']\\s*:`, "u"), `SCREEN_RENDERERS must expose ${screen}`);
  }

  assert.equal((styles.match(/\.case-question\s*\{/gu) ?? []).length, 1, "overview must define .case-question only once");
  assert.doesNotMatch(styles, /border-left:\s*4px/u, "selected ResearchCase must not use a generic colored side stripe");
  assert.doesNotMatch(styles, /\.assessment\s*\{[^}]*border-left:/su, "AI assessment must not use a colored side stripe");
  for (const token of ["--action-surface", "--decision-surface", "--status-surface", "--gap-accent", "--provider-accent", "--frozen-accent"]) {
    assert.match(styles, new RegExp(`${token}:`, "u"), `styles must define semantic token ${token}`);
  }
  assert.match(styles, /@scope\s*\(\.new-research-screen\)/u, "new-research styles must be isolated beneath the screen root");
  assert.doesNotMatch(styles, /(^|\n)\.secondary-action/u, "new-research must not leak a generic .secondary-action selector");
  assert.doesNotMatch(styles, /(^|\n)\.form-actions/u, "new-research must not leak a generic .form-actions selector");
  for (const token of ["--workflow-border", "--workflow-muted-border", "--workflow-text", "--workflow-surface", "--workflow-hover-border"]) {
    assert.match(styles, new RegExp(`${token}:`, "u"), `new-research must promote repeated color literals to ${token}`);
  }
  for (const selector of [
    ".primary-action:hover",
    ".primary-action:active",
    ".primary-action:focus-visible",
    ".next-action:hover",
    ".next-action:active",
    ".next-action:focus-visible",
  ]) {
    assert.match(styles, new RegExp(selector.replaceAll(".", "\\."), "u"), `${selector} must have an explicit interaction state`);
  }
  for (const [selector, baseSelector] of [
    [".mobile-nav summary:hover", ".mobile-nav summary {"],
    [".mobile-nav summary:active", ".mobile-nav summary {"],
    ['.mobile-nav .nav-link:not([aria-current="page"]):hover', ".mobile-nav .nav-link {"],
    ['.mobile-nav .nav-link:not([aria-current="page"]):active', ".mobile-nav .nav-link {"],
  ]) {
    const stateIndex = styles.indexOf(`${selector} {`);
    assert.ok(stateIndex > styles.indexOf(baseSelector), `${selector} must appear after its mobile base rule`);
  }

  assert.match(readme, /Node 20/u, "README must document the compatible Node runtime");
  assert.match(readme, /bundled Chromium/iu, "README must document the bundled Chromium path");
  assert.match(readme, /system (?:Google )?Chrome/iu, "README must document the system Chrome fallback");
  assert.match(readme, /npx playwright install chromium/u, "README must document browser-runtime remediation");
  assert.match(readme, /--screens shell[^\n]+without writing/iu, "README must document shell verification as non-writing");
  assert.match(readme, /--screens overview[^\n]+prototype\/设计原型1\.png/iu, "README must document the overview final capture target");
  assert.match(readme, /unimplemented screens?[^\n]+rejected/iu, "README must say unimplemented screens are rejected");
  assert.match(readme, /atomic/iu, "README must document atomic final-image replacement");
  assert.match(readme, /CRC32/u, "README must document per-chunk CRC32 validation");
  assert.match(readme, /zlib/iu, "README must document IDAT zlib validation");
  assert.match(readme, /scanline/iu, "README must document decoded scanline validation");
  assert.match(readme, /RGB8[^\n]+non-interlaced/iu, "README must document the strict Playwright PNG profile");
  assert.match(readme, /bounded[^\n]+expected scanline length/iu, "README must document bounded PNG decompression");
  assert.doesNotMatch(readme, /Task 1 has no final screenshot mapping/u, "README must not retain the obsolete Task 1 capture boundary");
  assert.match(readme, /scrollWidth.*1600/u, "README must document the horizontal fit gate");
  assert.match(readme, /scrollHeight.*1000/u, "README must document the vertical fit gate");
  assert.match(readme, /IHDR.*1600.*1000/u, "README must document PNG dimension verification");
}

async function assertCaptureRemediationContract() {
  const { captureRemediation } = await import("./capture.mjs");
  assert.equal(typeof captureRemediation, "function", "capture.mjs must export captureRemediation");
  assert.equal(captureRemediation("dependency"), "cd frontend && npm ci");
  assert.match(captureRemediation("browser"), /cd frontend && npx playwright install chromium/u);
  assert.doesNotMatch(captureRemediation("browser"), /^cd frontend && npm ci$/u);
}

async function assertMalformedURLContract() {
  const { startPrototypeServer } = await import("./capture.mjs");
  const server = await startPrototypeServer();
  try {
    const response = await fetch(`${server.baseURL}/%E0%A4%A`);
    assert.equal(response.status, 400, "malformed URL encoding must return HTTP 400");
  } finally {
    await server.close();
  }
}

async function assertServerFilesystemBoundary() {
  assert.equal(isSkippableWindowsSymlinkError({ code: "EPERM" }, "win32"), true);
  assert.equal(isSkippableWindowsSymlinkError({ code: "EACCES" }, "win32"), true);
  assert.equal(isSkippableWindowsSymlinkError({ code: "EINVAL" }, "win32"), false);
  assert.equal(isSkippableWindowsSymlinkError({ code: "UNKNOWN" }, "win32"), false);
  assert.equal(isSkippableWindowsSymlinkError({ code: "EPERM" }, "darwin"), false);

  const { startPrototypeServer } = await import("./capture.mjs");
  const fixtureRoot = await mkdtemp(path.join(os.tmpdir(), "prototype-server-root-"));
  const outsideRoot = await mkdtemp(path.join(os.tmpdir(), "prototype-server-outside-"));
  let server;
  let symlinkSkipReason;
  try {
    await writeFile(path.join(fixtureRoot, "index.html"), "fixture home");
    await writeFile(path.join(outsideRoot, "secret.txt"), "must not be served");
    try {
      await symlink(path.join(outsideRoot, "secret.txt"), path.join(fixtureRoot, "escape.txt"));
    } catch (error) {
      if (!isSkippableWindowsSymlinkError(error)) throw error;
      symlinkSkipReason = `${error.code}: Windows symlink creation is unavailable without the required permission/support`;
    }
    server = await startPrototypeServer({ rootDir: fixtureRoot });

    const traversal = await fetch(`${server.baseURL}/%2e%2e%2fcapture.mjs`);
    assert.equal(traversal.status, 403, "encoded lexical traversal must be rejected");
    if (symlinkSkipReason) {
      console.warn(`SKIP symlink escape assertion: ${symlinkSkipReason}`);
    } else {
      const escaped = await fetch(`${server.baseURL}/escape.txt`);
      assert.equal(escaped.status, 403, "symlink targets outside the served root must be rejected");
    }
    const missing = await fetch(`${server.baseURL}/missing.txt`);
    assert.equal(missing.status, 404, "missing files must remain a controlled 404");
  } finally {
    if (server) await server.close();
    await Promise.all([
      rm(fixtureRoot, { recursive: true, force: true }),
      rm(outsideRoot, { recursive: true, force: true }),
    ]);
  }
}

async function assertCaptureDimensionAndOutputContract() {
  const {
    assertPngDimensions,
    assertViewportFit,
  } = await import("./capture.mjs");
  assert.equal(typeof assertViewportFit, "function", "capture.mjs must export assertViewportFit");
  assert.equal(typeof assertPngDimensions, "function", "capture.mjs must export assertPngDimensions");

  assert.doesNotThrow(() => assertViewportFit({ scrollWidth: 1600, scrollHeight: 1000 }));
  assert.throws(
    () => assertViewportFit({ scrollWidth: 1601, scrollHeight: 1000 }),
    /1601.*1600/u,
  );
  assert.throws(
    () => assertViewportFit({ scrollWidth: 1600, scrollHeight: 1001 }),
    /1001.*1000/u,
  );

  const validPng = await readFile(path.resolve(UI_DIR, "../设计原型1.png"));
  assert.doesNotThrow(() => assertPngDimensions(validPng));
  assert.throws(() => assertPngDimensions(validPng, { width: 1600, height: 1001 }), /1600x1000.*1600x1001/u);
}

async function assertAtomicFinalCaptureContract() {
  const { writeFinalCaptureAtomically } = await import("./capture.mjs");
  assert.equal(typeof writeFinalCaptureAtomically, "function", "capture.mjs must export writeFinalCaptureAtomically");

  const fixtureDir = await mkdtemp(path.join(os.tmpdir(), "prototype-final-capture-"));
  const target = path.join(fixtureDir, "final.png");
  const original = Buffer.from("existing final image");
  const png = await readFile(path.resolve(UI_DIR, "../设计原型1.png"));
  const chunks = rawPngChunks(png);
  const iend = chunks.find((chunk) => chunk.type === "IEND");
  const firstIdat = chunks.find((chunk) => chunk.type === "IDAT");
  assert.ok(iend && firstIdat, "valid PNG test fixture must include IDAT and IEND");

  const badLength = Buffer.from(png);
  badLength.writeUInt32BE(Math.min(0xffffffff, firstIdat.length + png.length), firstIdat.offset);
  const badCrc = Buffer.from(png);
  badCrc[iend.end - 1] ^= 0xff;
  const expectedDecodedLength = 1000 * (1 + (1600 * 3));
  const oversizedInflate = replaceAllIdatData(png, Buffer.alloc(expectedDecodedLength + 2));
  const invalidPngs = new Map([
    ["truncated", png.subarray(0, png.length - 1)],
    ["missing IEND", png.subarray(0, iend.offset)],
    ["trailing data", Buffer.concat([png, Buffer.from("trailing")])],
    ["bad chunk length", badLength],
    ["bad CRC", badCrc],
    ["invalid zlib with valid CRC", replaceFirstIdatWithInvalidZlib(png)],
    ["interlaced profile with non-Adam7 data", replaceIhdrProfile(png, { interlace: 1 })],
    ["palette profile with illegal bit depth and no PLTE", replaceIhdrProfile(png, { colorType: 3, bitDepth: 16 })],
    ["unsupported RGBA profile", replaceIhdrProfile(png, { colorType: 6, bitDepth: 8 })],
    ["zlib output beyond the RGB8 scanline bound", oversizedInflate],
  ]);

  try {
    for (const [label, invalidPng] of invalidPngs) {
      await writeFile(target, original);
      let rejection;
      try {
        await writeFinalCaptureAtomically(target, invalidPng);
      } catch (error) {
        rejection = error;
      }
      assert.ok(rejection, `${label} PNG must be rejected`);
      if (label === "zlib output beyond the RGB8 scanline bound") {
        assert.equal(rejection.message, "PNG IDAT zlib stream is invalid", "oversized inflate must fail inside the bounded zlib operation");
        assert.equal(rejection.cause?.code, "ERR_BUFFER_TOO_LARGE", "oversized inflate must use maxOutputLength rather than allocate the full output");
      }
      assert.deepEqual(await readFile(target), original, `${label} validation failure must leave existing final bytes unchanged`);
      assert.deepEqual(await readdir(fixtureDir), ["final.png"], `${label} validation failure must create no temporary sibling`);
    }

    await writeFile(target, original);
    let attemptedRename;
    await assert.rejects(
      writeFinalCaptureAtomically(target, png, {
        renameFile: async (temporaryPath, finalPath) => {
          attemptedRename = { temporaryPath, finalPath };
          throw new Error("simulated atomic rename failure");
        },
      }),
      /simulated atomic rename failure/u,
    );
    assert.equal(attemptedRename.finalPath, target);
    assert.equal(path.dirname(attemptedRename.temporaryPath), fixtureDir, "validated temp image must be a sibling of the final target");
    assert.deepEqual(await readFile(target), original, "failed atomic replacement must leave the existing final unchanged");
    assert.deepEqual(await readdir(fixtureDir), ["final.png"], "failed atomic replacement must clean its temporary sibling");

    await writeFinalCaptureAtomically(target, png);
    assert.deepEqual(await readFile(target), png, "successful atomic replacement must preserve the exact validated buffer");
    assert.deepEqual(await readdir(fixtureDir), ["final.png"], "successful atomic replacement must leave no temporary sibling");
  } finally {
    await rm(fixtureDir, { recursive: true, force: true });
  }
}

async function assertFinalCaptureRegistryContract() {
  const { captureTargetForScreen } = await import("./capture.mjs");
  assert.equal(typeof captureTargetForScreen, "function", "capture.mjs must export captureTargetForScreen");
  assert.equal(
    captureTargetForScreen("overview"),
    path.resolve(UI_DIR, "../设计原型1.png"),
    "overview must map exactly to prototype/设计原型1.png",
  );
  assert.equal(
    captureTargetForScreen("new-research"),
    path.resolve(UI_DIR, "../设计原型3-新建研究.png"),
    "new-research must map exactly to prototype/设计原型3-新建研究.png",
  );
  assert.equal(
    captureTargetForScreen("plan"),
    path.resolve(UI_DIR, "../设计原型4-研究计划.png"),
    "plan must map exactly to prototype/设计原型4-研究计划.png",
  );
  assert.equal(
    captureTargetForScreen("case"),
    path.resolve(UI_DIR, "../设计原型2.png"),
    "case must map exactly to prototype/设计原型2.png",
  );
  assert.equal(
    captureTargetForScreen("graph"),
    path.resolve(UI_DIR, "../设计原型.png"),
    "graph must map exactly to prototype/设计原型.png",
  );
  assert.equal(
    captureTargetForScreen("review"),
    path.resolve(UI_DIR, "../设计原型5-审核工作区.png"),
    "review must map exactly to prototype/设计原型5-审核工作区.png",
  );
  assert.equal(
    captureTargetForScreen("library"),
    path.resolve(UI_DIR, "../设计原型6-资料与知识.png"),
    "library must map exactly to prototype/设计原型6-资料与知识.png",
  );
  for (const placeholder of REQUIRED_SCREENS.filter((screen) => !["overview", "new-research", "plan", "case", "graph", "review", "library"].includes(screen))) {
    assert.throws(
      () => captureTargetForScreen(placeholder),
      /Capture renderer not implemented/u,
      `${placeholder} placeholder must not map to a final PNG`,
    );
  }
}

async function assertLibraryProductContract(page, marker) {
  assert.equal((await marker.locator("h1").textContent()).trim(), "资料与知识工作台");

  const filters = marker.locator("[data-library-filters]");
  for (const label of ["文档类型", "来源", "发布日期", "版本", "审核状态", "关联案例", "实体"]) {
    assert.equal(await filters.getByLabel(label, { exact: true }).count(), 1, `library must expose the ${label} filter`);
  }

  const sourceLayer = marker.locator("[data-source-layer]");
  const knowledgeLayer = marker.locator("[data-knowledge-layer]");
  for (const label of ["不可变来源层", "DocumentVersion", "SourceSpan", "复用 2 次"]) {
    assert.ok((await sourceLayer.innerText()).includes(label), `source layer must expose ${label}`);
  }
  for (const label of ["已复核知识层", "SourceStatement", "EvidenceLink", "反驳", "目标 Thesis", "目标因素", "复核人", "复核时间"]) {
    assert.ok((await knowledgeLayer.innerText()).includes(label), `knowledge layer must expose ${label}`);
  }

  const selected = marker.locator("[data-selected-source]");
  for (const label of ["Microsoft FY2025 Q3 earnings call transcript", "issuer-call-2025-04-30-v1", "版本沿革", "发布时间", "首次可用", "采集时间", "截止日可用", "精确原文区段", "关联 ResearchCase", "复用记录"]) {
    assert.ok((await selected.innerText()).includes(label), `selected source inspector must expose ${label}`);
  }
  assert.ok((await selected.locator("blockquote").textContent()).trim().length > 30, "selected source must expose a non-vacuous exact excerpt");

  const proposal = marker.locator("[data-ai-proposal]");
  assert.ok((await proposal.innerText()).includes("未经人工复核"), "pending AI proposal must remain visibly isolated and unreviewed");
  assert.ok((await proposal.innerText()).includes("不会进入已复核知识"), "pending AI proposal must not imply reviewed status");

  const reuse = marker.getByRole("button", { name: "引用到研究案例", exact: true });
  assert.ok(await reuse.isVisible(), "library must expose a real visible reuse action");
  assert.ok((await marker.locator("[data-reuse-note]").innerText()).includes("复用现有冻结来源与已复核知识，不复制来源文档"), "reuse wording must forbid source duplication");
  await reuse.click();
  assert.ok((await marker.locator("[data-library-status]").innerText()).includes("已选择复用"), "reuse action must provide visible interaction feedback");

  const desktop = await page.evaluate(() => ({
    scrollWidth: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
    scrollHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
    coreSizes: [...document.querySelectorAll("[data-library-core]")].map((element) => parseFloat(getComputedStyle(element).fontSize)),
    internalScroll: [...document.querySelectorAll('[data-screen="library"] *')].filter((element) => {
      const style = getComputedStyle(element);
      return ["auto", "scroll"].includes(style.overflowY) && element.scrollHeight > element.clientHeight;
    }).length,
  }));
  assert.ok(desktop.scrollWidth <= 1600 && desktop.scrollHeight <= 1000, `library must fit the 1600x1000 capture: ${JSON.stringify(desktop)}`);
  assert.ok(desktop.coreSizes.length > 0 && desktop.coreSizes.every((size) => size >= 13), "library core text must remain at least 13px");
  assert.equal(desktop.internalScroll, 0, "library must not hide content in internal scrolling regions");

  await page.setViewportSize({ width: 375, height: 812 });
  const mobile = await page.evaluate(() => ({
    body: document.body.scrollWidth - document.body.clientWidth,
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    internalScroll: [...document.querySelectorAll('[data-screen="library"] *')].filter((element) => {
      const style = getComputedStyle(element);
      return ["auto", "scroll"].includes(style.overflowY) && element.scrollHeight > element.clientHeight;
    }).length,
  }));
  assert.ok(mobile.body <= 0 && mobile.document <= 0, `library must fit 375px without horizontal overflow: ${JSON.stringify(mobile)}`);
  assert.equal(mobile.internalScroll, 0, "library mobile layout must use normal vertical flow without internal scrolling");
  await page.setViewportSize({ width: 1600, height: 1000 });
}

async function assertReviewProductContract(page, marker) {
  assert.equal((await marker.locator("h1").textContent()).trim(), "证据审核工作区");
  assert.equal(await marker.locator('[data-review-queue-item]').count(), 2, "review queue must expose the two honest fixture items");
  const selected = marker.locator('[data-review-queue-item][data-selected="true"]');
  assert.equal(await selected.count(), 1, "review queue must expose one unmistakable selected row");
  for (const label of ["类型", "目标", "来源", "优先级", "审核状态", "剩余进度"]) {
    assert.ok((await selected.innerText()).includes(label), `selected review item must expose ${label}`);
  }

  const comparison = marker.locator('[data-review-comparison]');
  for (const label of ["冻结 SourceSpan", "DocumentVersion", "发布日期", "精确位置", "证据截止", "冻结快照", "AI 规范化陈述", "未经人工复核", "目标 Thesis", "目标因素", "拟建关系"]) {
    assert.ok((await comparison.innerText()).includes(label), `review comparison must expose ${label}`);
  }
  assert.ok((await comparison.locator("blockquote").textContent()).trim().length > 20, "review comparison must show a non-vacuous frozen source excerpt");

  const decision = marker.locator('[data-human-decision]');
  assert.equal(await decision.locator('input[name="relation"]').count(), 4, "human decision must require an explicit relation selection");
  assert.equal(await decision.getByLabel("因素角色").count(), 1, "human decision must expose factor-role selection");
  assert.equal(await decision.getByLabel("适用边界").count(), 1, "human decision must require an applicability boundary");
  assert.equal(await decision.getByLabel("审核理由").count(), 1, "human decision must require review rationale");
  for (const action of ["确认并写入审核知识", "驳回", "要求补充证据"]) {
    assert.ok(await decision.getByRole("button", { name: action, exact: true }).isVisible(), `review must expose exact action ${action}`);
  }
  const accept = decision.getByRole("button", { name: "确认并写入审核知识", exact: true });
  assert.ok(await accept.isDisabled(), "acceptance must be gated until all human fields are explicit");
  await decision.locator('input[name="relation"][value="gap"]').check();
  await decision.getByLabel("因素角色").selectOption("transmission");
  await decision.getByLabel("适用边界").fill("仅适用于 2025-06-30 截止的当前冻结快照。");
  await decision.getByLabel("审核理由").fill("展望不能代替实际交付与分部收入确认。");
  assert.ok(await accept.isEnabled(), "acceptance must unlock only after all four human decisions are explicit");
  const immutable = decision.locator('[data-immutable-record]');
  assert.ok((await immutable.innerText()).includes("追加新记录"), "decision must preview the immutable write");
  assert.ok((await immutable.innerText()).includes("更正不覆盖"), "decision must state append-only correction semantics");
  assert.doesNotMatch(await marker.innerText(), /置信度|成熟度|confidence|maturity/iu, "review must not expose confidence or maturity scores");
  assert.ok(await page.evaluate(() => document.documentElement.scrollHeight <= 1000), "review desktop document must fit the 1000px capture height");

  await page.setViewportSize({ width: 375, height: 812 });
  const mobileActions = marker.locator('[data-review-actions]');
  await mobileActions.scrollIntoViewIfNeeded();
  assert.ok(await accept.isVisible(), "review decisions must remain reachable in normal mobile flow");
  await page.setViewportSize({ width: 1600, height: 1000 });
}

async function assertGraphProductContract(page, marker) {
  assert.equal((await marker.locator("h1").textContent()).trim(), "因素关系路径");
  for (const layer of [
    "DocumentVersion",
    "SourceStatement",
    "支持证据",
    "反面证据",
    "ReviewedFactor",
    "CausalStep",
    "Company",
    "Stock",
    "HoldingDisclosure",
    "Fund",
  ]) {
    assert.equal(await marker.getByText(layer, { exact: true }).first().count(), 1, `graph must visibly include ${layer}`);
  }

  for (const semantic of ["来源事实", "AI 提议关系 · 未经人工复核", "已人工复核关系", "投影节点"]) {
    assert.ok(await marker.getByText(semantic, { exact: true }).first().isVisible(), `graph must distinguish ${semantic}`);
  }

  const edgeKinds = new Set(await marker.locator("[data-edge-kind]").evaluateAll((edges) => edges.map((edge) => edge.dataset.edgeKind)));
  assert.ok(edgeKinds.has("source"), "graph must label source extraction edges");
  assert.ok(edgeKinds.has("support"), "graph must label support edges");
  assert.ok(edgeKinds.has("contradict"), "graph must label contradiction edges");
  assert.ok(edgeKinds.has("reviewed"), "graph must label reviewed transmission edges");
  assert.ok(edgeKinds.has("projection"), "graph must label projection edges");

  const pathList = marker.getByRole("list", { name: "结构化关系路径" });
  assert.equal(await pathList.count(), 1, "graph must expose one accessible structured path list");
  assert.ok(await pathList.getByRole("button").count() >= 9, "structured path must expose every main-path node as a real button");

  const inspector = marker.locator("[data-graph-inspector]");
  for (const field of ["原文区段", "关系语义", "审核状态", "适用范围", "as-of", "披露日期"]) {
    assert.ok((await inspector.innerText()).includes(field), `graph inspector must show ${field}`);
  }
  assert.ok(await inspector.getByRole("button", { name: "提交审核", exact: true }).isVisible(), "proposed relation must expose 提交审核");
  assert.ok(await inspector.getByRole("button", { name: "撤回提议", exact: true }).isVisible(), "proposed relation must expose 撤回提议");

  const fundNode = marker.locator('.graph-node[data-graph-node-id="FUND-ETF-AI-INFRA"]');
  const fundText = await fundNode.innerText();
  for (const copy of ["披露持仓", "as-of 2025-03-31", "不构成投资建议"]) {
    assert.ok(fundText.includes(copy), `fund projection must visibly include ${copy}`);
  }

  const sourcePathButton = pathList.getByRole("button", { name: /NVIDIA FY2026 Q1 Form 10-Q/u });
  await sourcePathButton.click();
  assert.ok((await inspector.innerText()).includes("pp. 22-27, Data Center revenue and supply commitments"), "path selection must update inspector source span");
  assert.equal(await inspector.getByRole("button", { name: "提交审核", exact: true }).count(), 0, "source facts must not expose proposal actions");

  await marker.locator('.graph-node[data-graph-node-id="EL-PROPOSED-CAUSAL"]').click();
  assert.ok((await inspector.innerText()).includes("未经人工复核"), "canvas selection must update inspector review state");
  assert.ok(await inspector.getByRole("button", { name: "提交审核", exact: true }).isVisible(), "canvas-selected proposal must restore review action");

  const desktop = await marker.evaluate((element) => {
    const graph = element.querySelector("[data-graph-canvas]");
    const nodeSizes = [...element.querySelectorAll("[data-graph-node-id]")].map((node) => ({
      fontSize: Number.parseFloat(getComputedStyle(node).fontSize),
      clipped: node.scrollWidth > node.clientWidth || node.scrollHeight > node.clientHeight,
    }));
    return {
      graphVisible: getComputedStyle(graph).display !== "none",
      graphWidth: graph.getBoundingClientRect().width,
      nodeSizes,
    };
  });
  assert.ok(desktop.graphVisible && desktop.graphWidth >= 780, `desktop graph must remain the readable primary view: ${JSON.stringify(desktop)}`);
  assert.ok(desktop.nodeSizes.every((node) => node.fontSize >= 13 && !node.clipped), `graph nodes must be readable and unclipped: ${JSON.stringify(desktop.nodeSizes)}`);

  await page.setViewportSize({ width: 375, height: 812 });
  const mobile = await marker.evaluate((element) => ({
    graphDisplay: getComputedStyle(element.querySelector("[data-graph-canvas]")).display,
    listVisible: getComputedStyle(element.querySelector('[aria-label="结构化关系路径"]')).display !== "none",
    bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
    documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  assert.equal(mobile.graphDisplay, "none", "mobile must replace the positioned graph with normal-flow path content");
  assert.ok(mobile.listVisible, "mobile structured path must remain visible");
  assert.ok(mobile.bodyOverflow <= 0 && mobile.documentOverflow <= 0, `graph must fit 375px without horizontal overflow: ${JSON.stringify(mobile)}`);
  await page.setViewportSize({ width: 1600, height: 1000 });
}

async function assertResearchPlanProductContract(page, marker) {
  assert.equal((await marker.locator("h1").textContent()).trim(), "研究计划与证据获取");
  for (const region of ["已有资料与数据", "Provider 查询计划", "获取与冻结状态", "待审核结果", "证据缺口", "失败、额度与权限"]) {
    assert.equal(await marker.getByRole("heading", { name: region, exact: true }).count(), 1, `plan must expose exactly one ${region} region`);
  }
  for (const action of ["复用", "移除", "重试", "调整范围", "上传材料", "暂时无法获得"]) {
    assert.ok(await marker.getByText(action, { exact: true }).first().isVisible(), `${action} must be visible`);
  }
  const headerText = await marker.locator("[data-plan-case-header]").innerText();
  for (const copy of ["RC-AIC-2025-01", "2025-01-01 至 2027-12-31", "截止 2025-06-30", "RP-AIC-2025-01-v1", "计划草案", "需人工确认"]) {
    assert.ok(headerText.includes(copy), `plan header must show ${copy}`);
  }
  assert.equal(await marker.locator('[data-collection-state="running"]').count(), 0, "plan must not render a false running task");
  assert.ok((await marker.locator("[data-empty-running]").textContent()).includes("当前没有运行中的获取任务"));
  const providerText = await marker.locator('[data-plan-region="providers"]').innerText();
  for (const copy of ["查询目的", "日期范围", "拟冻结产物", "计划状态", "能力目录", "尚待探测是否实际暴露并获授权"]) {
    assert.ok(providerText.includes(copy), `provider rows must explain ${copy}`);
  }
  assert.doesNotMatch(providerText, /industry_analysis_view|announcement_filing_fulltext|fund_holding_detail|juyuan|capability_probe|probe_required/u, "plan must not leak raw provider enums");
  assert.ok(await marker.getByText("复用", { exact: true }).first().isVisible(), "explicit candidate reuse must be a real visible action");
  const firstPageAssetButtons = marker.locator("[data-asset-id] [data-toggle-asset]");
  const assetAccessibleNames = await firstPageAssetButtons.evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-label")));
  assert.ok(assetAccessibleNames.every((name) => name && /^(?:复用|移除)：/u.test(name)), `asset actions must have item-specific accessible names: ${JSON.stringify(assetAccessibleNames)}`);
  assert.equal(new Set(assetAccessibleNames).size, assetAccessibleNames.length, "visible asset action names must be unique");
  const gapButtons = marker.locator("[data-gap-id] [data-toggle-gap]");
  const gapAccessibleNames = await gapButtons.evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-label")));
  assert.ok(gapAccessibleNames.every((name) => name?.startsWith("暂时无法获得：")), `gap actions must have item-specific accessible names: ${JSON.stringify(gapAccessibleNames)}`);
  assert.equal(new Set(gapAccessibleNames).size, gapAccessibleNames.length, "gap action names must be unique");
  const metricAssetText = await marker.locator('[data-asset-id="M-NVDA-DC-REV"]').innerText();
  for (const copy of ["NVIDIA 数据中心业务收入", "391 亿美元", "2026 财年第一季度"]) assert.ok(metricAssetText.includes(copy), `metric asset must localize ${copy}`);
  assert.doesNotMatch(metricAssetText, /Data Center revenue|\$39\.1bn|FY2026 Q1/u, "metric asset must not leak raw English values");

  for (const selector of [".asset-list", ".provider-plan-list", ".collection-list", ".compact-result-list", ".gap-list", ".failure-list"]) {
    const measurement = await marker.locator(selector).evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: getComputedStyle(element).overflowY,
    }));
    assert.ok(measurement.scrollHeight <= measurement.clientHeight + 1, `${selector} must show all desktop content without internal scrolling: ${JSON.stringify(measurement)}`);
    assert.ok(!["auto", "scroll", "hidden", "clip"].includes(measurement.overflowY), `${selector} must not hide desktop overflow: ${JSON.stringify(measurement)}`);
  }
  for (const region of ["assets", "providers", "collection", "pending", "gaps", "failures"]) {
    const coreText = marker.locator(`[data-plan-region="${region}"] [data-core-text]`);
    assert.ok(await coreText.count() > 0, `${region} must mark its core readable text`);
    const undersized = await coreText.evaluateAll((elements) => elements
      .map((element) => ({ text: element.textContent.trim(), size: Number.parseFloat(getComputedStyle(element).fontSize) }))
      .filter((item) => item.size < 13));
    assert.deepEqual(undersized, [], `${region} core text must be at least 13px: ${JSON.stringify(undersized)}`);
  }
  assert.ok(await marker.locator('.provider-plan-row dt').evaluateAll((elements) => elements.every((element) => Number.parseFloat(getComputedStyle(element).fontSize) >= 13)), "provider field labels must be core 13px text");
  assert.ok(await marker.locator('.plan-case-facts dt, .type-label').evaluateAll((elements) => elements.every((element) => Number.parseFloat(getComputedStyle(element).fontSize) >= 13)), "case and structural type labels must be at least 13px");
  assert.ok(await page.evaluate(() => document.documentElement.scrollHeight <= 1000), "plan desktop document must fit the 1000px capture height");
  const secondaryText = marker.locator("[data-secondary-text]");
  assert.ok(await secondaryText.count() > 0, "secondary IDs and timestamps must be explicitly marked");
  assert.ok(await secondaryText.evaluateAll((elements) => elements.every((element) => Number.parseFloat(getComputedStyle(element).fontSize) >= 11)), "marked secondary text must remain at least 11px");
  assert.equal(await secondaryText.evaluateAll((elements) => elements.some((element) => /待人工审核|关系待人工审核|已纳入复用|未纳入复用|候选/u.test(element.textContent))), false, "review and reuse status must not be folded into secondary provenance text");
  const reviewStatuses = marker.locator("[data-review-status]");
  assert.ok(await reviewStatuses.count() > 0, "review statuses must be explicit core labels");
  assert.ok(await reviewStatuses.evaluateAll((elements) => elements.every((element) => Number.parseFloat(getComputedStyle(element).fontSize) >= 13)), "review statuses must remain at least 13px");
  for (const status of ["待人工审核", "关系待人工审核"]) {
    assert.ok(await marker.getByText(status, { exact: true }).first().isVisible(), `${status} must be separately visible`);
  }
  const requiredVisibleCopy = [
    "复用",
    "移除",
    "NVIDIA 数据中心业务收入",
    "391 亿美元",
    "2026 财年第一季度",
    "台积电月度营收同比增幅",
    "34.8%",
    "2025 年 5 月",
    "核对 AI 收入口径、对应交付期与分部归属",
    "确认基金份额类别与报告期持仓口径",
    "审核证据与目标之间的关系",
    "云厂商 AI 资本开支、订单积压与分部收入同向披露",
    "资本开支下调、交付延迟、订单取消或收入口径不匹配",
    "数据中心电力与并网周期",
    "高投入但相关收入确认滞后",
    "市场数据接口 · 配额受限",
    "持仓数据接口 · 权限缺口",
    "研究资料补录 · 人工补录",
    "材料已进入待审核结果，不需要重试。",
    "当前没有运行中的获取任务",
  ];
  for (const copy of requiredVisibleCopy) {
    const item = marker.getByText(copy, { exact: true }).first();
    assert.ok(await item.isVisible(), `${copy} must be visible at 1600px`);
    const rect = await item.evaluate((element) => element.getBoundingClientRect().toJSON());
    assert.ok(rect.top >= 0 && rect.bottom <= 1000, `${copy} must be inside the 1600x1000 viewport: ${JSON.stringify(rect)}`);
  }

  const assetList = marker.locator(".asset-list");
  assert.doesNotMatch(await assetList.innerText(), /完整清单/u, "asset region must not promise an inaccessible complete list");
  const previousPage = marker.getByRole("button", { name: "上一页资产" });
  const nextPage = marker.getByRole("button", { name: "下一页资产" });
  const pageStatus = marker.locator("[data-asset-page-status]");
  const reuseCount = marker.locator("[data-reuse-count]");
  const candidateCount = marker.locator("[data-candidate-count]");
  assert.equal(await pageStatus.getAttribute("aria-live"), "polite", "asset page changes must be announced");
  assert.equal(Number(await reuseCount.textContent()), 12);
  assert.equal(Number(await candidateCount.textContent()), 2);
  assert.ok(await previousPage.isDisabled(), "asset previous page must be disabled at the first page");
  assert.ok(!await nextPage.isDisabled(), "asset next page must be enabled before the last page");
  assert.equal((await pageStatus.textContent()).trim(), "第 1 / 4 页");
  const visitedAssetIds = new Set(await assetList.locator("[data-asset-id]").evaluateAll((elements) => elements.map((element) => element.dataset.assetId)));

  await nextPage.press("Enter");
  assert.equal((await pageStatus.textContent()).trim(), "第 2 / 4 页", "keyboard pagination must reach page 2");
  for (const id of await assetList.locator("[data-asset-id]").evaluateAll((elements) => elements.map((element) => element.dataset.assetId))) visitedAssetIds.add(id);
  const secondPageSelected = marker.locator('[data-asset-id="DOC-MSFT-FY25Q3"] [data-toggle-asset]');
  assert.equal(await secondPageSelected.getAttribute("aria-pressed"), "true", "a non-first-page selected asset must retain fixture selection");
  await secondPageSelected.press("Enter");
  assert.equal(await secondPageSelected.getAttribute("aria-pressed"), "false", "a non-first-page selected asset must be removable");
  assert.equal(Number(await reuseCount.textContent()), 11, "removing a page-2 asset must update the global selected count");
  assert.equal(Number(await candidateCount.textContent()), 3, "removing a page-2 asset must update the global candidate count");

  await nextPage.press("Enter");
  assert.equal((await pageStatus.textContent()).trim(), "第 3 / 4 页", "keyboard pagination must reach page 3");
  for (const id of await assetList.locator("[data-asset-id]").evaluateAll((elements) => elements.map((element) => element.dataset.assetId))) visitedAssetIds.add(id);
  const laterCandidate = marker.locator('[data-asset-id="ST-003"] [data-toggle-asset]');
  assert.equal(await laterCandidate.getAttribute("aria-pressed"), "false", "ST-003 must remain an explicit later-page candidate");
  await laterCandidate.press("Enter");
  assert.equal(await laterCandidate.getAttribute("aria-pressed"), "true", "a later-page candidate must be reusable");
  assert.equal(Number(await reuseCount.textContent()), 12, "reusing a page-3 candidate must update the global selected count");
  assert.equal(Number(await candidateCount.textContent()), 2, "reusing a page-3 candidate must update the global candidate count");
  await nextPage.press("Enter");
  assert.equal((await pageStatus.textContent()).trim(), "第 4 / 4 页", "keyboard pagination must reach page 4");
  for (const id of await assetList.locator("[data-asset-id]").evaluateAll((elements) => elements.map((element) => element.dataset.assetId))) visitedAssetIds.add(id);
  assert.ok(await nextPage.isDisabled(), "asset next page must be disabled at the last page");
  assert.deepEqual([...visitedAssetIds].sort(), ["DOC-BRCM-FY25Q2", "DOC-MSFT-FY25Q3", "DOC-NVDA-FY26Q1", "DOC-MSFT-FY25Q3-CALL", "DOC-TSMC-2025M05", "EL-001", "EL-002", "EL-004", "M-NVDA-DC-REV", "M-TSMC-M05-YOY", "ST-001", "ST-002", "ST-003", "ST-004"].sort(), "asset pagination must expose exactly all 14 unique assets");

  await previousPage.press("Enter");
  await previousPage.press("Enter");
  assert.equal(await marker.locator('[data-asset-id="DOC-MSFT-FY25Q3"] [data-toggle-asset]').getAttribute("aria-pressed"), "false", "removed selection must persist when page 2 is revisited");
  await nextPage.press("Enter");
  assert.equal(await marker.locator('[data-asset-id="ST-003"] [data-toggle-asset]').getAttribute("aria-pressed"), "true", "candidate reuse must persist when page 3 is revisited");
  await previousPage.press("Enter");
  await previousPage.press("Enter");
  assert.equal((await pageStatus.textContent()).trim(), "第 1 / 4 页");

  const initialCount = Number(await reuseCount.textContent());
  const candidate = marker.locator('[data-asset-id="DOC-BRCM-FY25Q2"] [data-toggle-asset]');
  await candidate.press("Enter");
  assert.equal(Number(await reuseCount.textContent()), initialCount + 1, "keyboard reuse must add an explicit candidate locally");
  assert.equal((await candidate.textContent()).trim(), "移除");
  assert.ok((await candidate.getAttribute("aria-label")).startsWith("移除："), "asset accessible name must track its current action");
  await candidate.press("Enter");
  assert.equal(Number(await reuseCount.textContent()), initialCount, "candidate reuse must remain reversible without fixture mutation");
  const remove = marker.getByRole("button", { name: "移除" }).first();
  assert.equal(await remove.getAttribute("aria-pressed"), "true");
  await remove.press("Enter");
  assert.equal(Number(await reuseCount.textContent()), initialCount - 1, "keyboard removal must update the local reuse count");
  assert.equal((await page.locator(":focus").textContent()).trim(), "复用", "asset action must retain focus and expose the inverse action");
  assert.equal(await page.locator(":focus").getAttribute("aria-pressed"), "false");

  const retry = marker.getByRole("button", { name: "重试" }).first();
  await retry.click();
  assert.ok((await retry.locator("xpath=ancestor::*[@data-provider-run]").innerText()).includes("已加入重试队列（原型）"));

  const gap = marker.locator("[data-toggle-gap]").first();
  await gap.press("Enter");
  assert.equal((await gap.textContent()).trim(), "恢复获取");
  assert.equal(await gap.getAttribute("aria-pressed"), "true");
  assert.ok((await gap.getAttribute("aria-label")).startsWith("恢复获取："), "gap accessible name must track its current action");

  const input = marker.locator('input[type="file"]');
  assert.equal(await input.count(), 1, "upload material must use a real file input");
  await input.setInputFiles({ name: "补充披露.pdf", mimeType: "application/pdf", buffer: Buffer.from("prototype") });
  assert.ok((await marker.locator("[data-upload-status]").textContent()).includes("已选择：补充披露.pdf"));
  assert.doesNotMatch(await marker.locator("[data-upload-status]").textContent(), /上传成功/u);
  await input.focus();
  assert.notEqual(await marker.locator(".plan-upload span").evaluate((element) => getComputedStyle(element).outlineStyle), "none", "file input keyboard focus must be visible on its label");
  const manualUpload = marker.locator('[data-provider-outcome="manual_upload"]');
  assert.equal(await manualUpload.count(), 1, "manual upload must remain an inspectable historical outcome");
  assert.equal(await manualUpload.getByRole("button", { name: "重试" }).count(), 0, "successful manual upload must not expose retry");
  assert.equal(await marker.getByRole("link", { name: "调整范围" }).getAttribute("href"), "?screen=new-research");
  assert.ok(await marker.locator('[aria-live="polite"]').count() > 0, "plan interactions must expose an aria-live status");

  await page.setViewportSize({ width: 375, height: 812 });
  const narrow = await page.evaluate(() => ({
    body: document.body.scrollWidth - document.body.clientWidth,
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    height: document.documentElement.scrollHeight,
  }));
  assert.ok(narrow.body <= 0 && narrow.document <= 0, `plan must fit 375px without horizontal overflow: ${JSON.stringify(narrow)}`);
  assert.ok(narrow.height > 812, "plan mobile layout must use normal vertical scrolling");
  const undersizedMobileTargets = await marker.locator('[data-toggle-asset], [data-toggle-gap], [data-retry-run], [data-asset-page], .plan-text-link, .plan-upload span').evaluateAll((elements) => elements
    .map((element) => ({ label: element.getAttribute("aria-label") ?? element.textContent.trim(), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height }))
    .filter((item) => item.width < 40 || item.height < 40));
  assert.deepEqual(undersizedMobileTargets, [], `mobile plan controls must expose at least 40px hit targets: ${JSON.stringify(undersizedMobileTargets)}`);
  const mobileRetry = marker.getByRole("button", { name: "重试" }).last();
  await mobileRetry.scrollIntoViewIfNeeded();
  assert.ok(await mobileRetry.isVisible(), "last plan control must remain reachable on mobile");
  await mobileRetry.press("Enter");
  assert.ok((await mobileRetry.locator("xpath=ancestor::*[@data-provider-run]").innerText()).includes("已加入重试队列（原型）"), "mobile keyboard activation must preserve honest retry behavior");
  await page.setViewportSize({ width: 1600, height: 1000 });
}

async function assertCaseWorkbenchProductContract(page, marker) {
  const context = marker.locator("[data-case-context]");
  assert.equal(await context.count(), 1, "case must expose one fixed context header");
  const contextText = await context.innerText();
  for (const fact of [
    "核心问题",
    "截至 2025-06-30，AI 算力资本开支能否通过已披露订单、交付与收入，形成可审计且仍需持续验证的产业链判断？",
    "研究对象",
    "从云厂商资本开支，经芯片、互连与系统交付，到分部收入的 AI 算力产业链",
    "时间范围",
    "2025-01-01 至 2027-12-31",
    "证据截止",
    "2025-06-30",
    "当前快照",
    "RS-2025-06-30-v3",
    "AI 草案 · 未经人工复核",
    "人工复核 · 正式判断已冻结",
  ]) {
    assert.ok(contextText.includes(fact), `fixed case context must visibly include ${fact}`);
  }
  assert.equal(await context.evaluate((element) => getComputedStyle(element).position), "sticky", "case context must remain sticky while reading");
  const clippedContextFacts = await context.locator(".case-context-facts [data-core-text]").evaluateAll((elements) => elements
    .map((element) => ({ text: element.textContent.trim(), clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }))
    .filter((item) => item.scrollWidth > item.clientWidth));
  assert.deepEqual(clippedContextFacts, [], `fixed case facts must not be clipped on desktop: ${JSON.stringify(clippedContextFacts)}`);

  const tabs = marker.getByRole("tablist", { name: "ResearchCase 内部导航" }).getByRole("tab");
  assert.deepEqual(
    (await tabs.allTextContents()).map((text) => text.trim()),
    ["研究档案", "命题与证据", "因素分析", "关系路径", "公司与基金", "历史版本"],
    "case tabs and order are contractual",
  );
  assert.equal(await tabs.nth(0).getAttribute("aria-selected"), "true", "研究档案 must be the selected tab");

  const viewSwitch = marker.getByRole("group", { name: "研究视图" });
  const explore = viewSwitch.getByRole("button", { name: "探索模式", exact: true });
  const frozen = viewSwitch.getByRole("button", { name: "已冻结版本", exact: true });
  assert.equal(await explore.getAttribute("aria-pressed"), "true");
  assert.equal(await frozen.getAttribute("aria-pressed"), "false");

  const compass = marker.locator("[data-review-compass]");
  for (const [kind, heading] of [
    ["formal", "当前正式判断"],
    ["ai", "AI 判断草案"],
    ["contradiction", "主要反证"],
    ["gap", "最大缺口"],
    ["next", "下一验证事件"],
  ]) {
    const item = compass.locator(`[data-decision-kind="${kind}"]`);
    assert.equal(await item.count(), 1, `reviewer compass must expose one ${kind} item`);
    assert.ok(await item.getByRole("heading", { name: heading, exact: true }).isVisible(), `${heading} must be visible without leaving the page`);
  }
  const formal = compass.locator('[data-decision-kind="formal"]');
  const proposal = compass.locator('[data-decision-kind="ai"]');
  assert.equal(await formal.getAttribute("data-review-state"), "reviewed", "official judgment must be semantically reviewed");
  assert.equal(await proposal.getAttribute("data-review-state"), "pending_review", "AI proposal must remain semantically pending review");
  assert.equal(await proposal.getByText("AI 草案 · 未经人工复核", { exact: true }).count(), 1, "AI proposal must carry the exact provisional label");
  const decisionStyles = await compass.locator('[data-decision-kind="formal"], [data-decision-kind="ai"]').evaluateAll((elements) => elements.map((element) => ({
    background: getComputedStyle(element).backgroundColor,
    border: getComputedStyle(element).borderStyle,
  })));
  assert.notDeepEqual(decisionStyles[0], decisionStyles[1], "official judgment and AI proposal must be visually distinct");

  for (const heading of ["Thesis 与证据", "因素比较", "所选因素解释", "原文引用"]) {
    assert.equal(await marker.getByRole("heading", { name: heading, exact: true }).count(), 1, `case must expose one ${heading} section`);
  }
  const thesisRegion = marker.locator("[data-thesis-evidence]");
  for (const label of ["支持条件", "证据关系", "适用范围", "证伪条件"]) {
    assert.ok((await thesisRegion.innerText()).includes(label), `Thesis evidence must explain ${label}`);
  }
  const unreviewedTheses = thesisRegion.locator('[data-thesis-review-state="unreviewed"]');
  assert.equal(await unreviewedTheses.count(), 3, "exploration mode must expose all three explicitly unreviewed Thesis drafts");
  const pendingRelationshipThesis = thesisRegion.locator('[data-evidence-review-state="pending_relationship_review"]');
  assert.equal(await pendingRelationshipThesis.count(), 1, "exploration mode must expose the pending relationship Thesis explicitly");
  const rebuttal = thesisRegion.locator('[data-thesis-rebuttal][data-evidence-role="contradict"]');
  assert.equal(await rebuttal.count(), 1, "Thesis evidence must expose one explicit contradictory SourceSpan");
  const rebuttalText = await rebuttal.innerText();
  for (const copy of [
    "反驳证据",
    "Microsoft FY2025 Q3 earnings call transcript",
    "issuer-call-2025-04-30-v1",
    "2025-04-30",
    "prepared remarks, pp. 4-5, capacity constraints and revenue timing",
    "已人工复核",
  ]) {
    assert.ok(rebuttalText.includes(copy), `Thesis rebuttal must visibly include ${copy}`);
  }
  assert.match(await rebuttal.getByRole("link", { name: "查看原文定位", exact: true }).getAttribute("href"), /^\?screen=library&document=DOC-MSFT-FY25Q3-CALL&span=/u, "Thesis rebuttal must preserve its document and SourceSpan locator");

  const factorRegion = marker.locator("[data-factor-comparison]");
  assert.equal(await factorRegion.locator("table").count(), 1, "factor comparison must use a semantic table");
  for (const label of ["因素", "角色", "状态", "时间顺序", "传导机制", "直接证据", "替代解释", "差异解释", "适用边界", "反例和证伪条件"]) {
    assert.ok((await factorRegion.innerText()).includes(label), `factor comparison must explain ${label}`);
  }
  for (const label of ["候选因素", "传导因素", "限制因素", "替代解释", "矛盾观察"]) {
    assert.ok((await factorRegion.innerText()).includes(label), `factor comparison must use approved factor label ${label}`);
  }
  assert.doesNotMatch(await factorRegion.innerText(), /candidate|under_review|key_factor|confidence|置信度|成熟度|评分|得分/iu, "factor comparison must not expose raw enums or synthetic scoring");

  const mechanism = marker.locator("[data-factor-detail]");
  for (const label of ["机制", "直接证据", "反例", "替代解释", "影响对象", "适用范围", "证伪条件"]) {
    assert.ok((await mechanism.innerText()).includes(label), `selected factor explanation must include ${label}`);
  }

  const citations = marker.locator("[data-source-citation]");
  assert.equal(await marker.locator('[data-source-citation][data-evidence-role="support"]').count(), 2, "source list must expose two explicit support relations");
  assert.equal(await marker.locator('[data-source-citation][data-evidence-role="contradict"]').count(), 1, "source list must expose one explicit contradictory relation");
  assert.equal(await marker.locator('[data-source-citation][data-evidence-role="gap"]').count(), 1, "source list must preserve the pending gap relation");
  const contradictoryCitation = marker.locator('[data-source-citation][data-evidence-role="contradict"]');
  assert.ok((await contradictoryCitation.innerText()).includes("prepared remarks, pp. 4-5, capacity constraints and revenue timing"), "contradictory citation must expose its exact SourceSpan");
  assert.equal(await contradictoryCitation.getAttribute("data-review-state"), "reviewed", "contradictory citation must be reviewed");
  for (let index = 0; index < await citations.count(); index += 1) {
    const citation = citations.nth(index);
    const text = await citation.innerText();
    for (const label of ["文档版本", "发布日期", "原文区段", "复核状态", "快照归属"]) {
      assert.ok(text.includes(label), `source citation ${index + 1} must show ${label}`);
    }
    const locate = citation.getByRole("link", { name: "查看原文定位", exact: true });
    assert.equal(await locate.count(), 1, `source citation ${index + 1} must expose one real source locator control`);
    assert.match(await locate.getAttribute("href"), /^\?screen=library&document=[^&]+&span=[^&]+$/u, `source citation ${index + 1} locator must preserve document and span identity`);
  }

  await frozen.focus();
  await page.keyboard.press("Space");
  assert.equal(await frozen.getAttribute("aria-pressed"), "true", "frozen switch must be a real control");
  assert.equal(await explore.getAttribute("aria-pressed"), "false");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.caseMode), "frozen", "keyboard switching must retain focus on the frozen-mode control");
  assert.ok((await marker.locator("[data-current-basis]").innerText()).includes("只显示当前快照中的资料、数据和已审核关系"), "frozen view must explain its point-in-time boundary");
  assert.ok(await proposal.isHidden(), "frozen view must exclude the provisional AI proposal");
  assert.ok(await thesisRegion.isHidden(), "frozen view must exclude unreviewed Thesis drafts rather than leaking their relationship state");
  assert.ok(await pendingRelationshipThesis.isHidden(), "frozen view must expose no pending_relationship_review Thesis");
  assert.ok(await compass.locator('[data-decision-kind="contradiction"]').isHidden(), "frozen view must exclude the candidate-factor main contradiction");
  assert.ok(await compass.locator('[data-decision-kind="gap"]').isHidden(), "frozen view must exclude the candidate-factor largest gap");
  assert.ok(await compass.locator('[data-decision-kind="next"]').isHidden(), "frozen view must exclude a next event sourced from an unreviewed Thesis");
  assert.ok(await factorRegion.isHidden(), "frozen view must exclude candidate factor comparison");
  assert.ok(await mechanism.isHidden(), "frozen view must exclude the candidate factor detail");
  assert.equal(await citations.filter({ hasText: "待人工审核" }).count(), 1, "exploration view must contain one pending source citation");
  assert.ok(await citations.filter({ hasText: "待人工审核" }).isHidden(), "frozen view must exclude pending source citations");
  assert.equal(await citations.filter({ hasText: "已人工复核" }).count(), 3, "frozen view must retain all reviewed source citations");
  assert.ok(await citations.filter({ hasText: "已人工复核" }).first().isVisible(), "reviewed source citations must remain visible in frozen mode");
  const visibleExcluded = await marker.locator('[data-frozen-eligibility="excluded"]').evaluateAll((elements) => elements.filter((element) => element.getClientRects().length > 0).length);
  assert.equal(visibleExcluded, 0, "frozen mode must have no semantically excluded content visible");
  const visibleCitationStates = await citations.evaluateAll((elements) => elements
    .filter((element) => element.getClientRects().length > 0)
    .map((element) => element.dataset.reviewState));
  assert.deepEqual(visibleCitationStates, ["reviewed", "reviewed", "reviewed"], "frozen source list must contain reviewed relations only");
  await explore.focus();
  await page.keyboard.press("Enter");
  assert.ok(await proposal.isVisible(), "exploration view must restore the provisional AI proposal");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.caseMode), "exploration", "keyboard switching must retain focus on the exploration-mode control");
  assert.ok(await thesisRegion.isVisible(), "exploration view must restore unreviewed Thesis drafts");
  assert.ok(await compass.locator('[data-decision-kind="contradiction"]').isVisible(), "exploration view must restore the candidate-factor main contradiction");
  assert.ok(await compass.locator('[data-decision-kind="gap"]').isVisible(), "exploration view must restore the candidate-factor largest gap");
  assert.ok(await factorRegion.isVisible(), "exploration view must restore candidate factor comparison");

  for (const selector of ["[data-case-reading]", "[data-thesis-evidence]", "[data-factor-comparison]", "[data-factor-detail]", "[data-source-list]"]) {
    const measurement = await marker.locator(selector).evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
      overflowY: getComputedStyle(element).overflowY,
    }));
    assert.ok(measurement.scrollHeight <= measurement.clientHeight + 1, `${selector} must not internally clip desktop content: ${JSON.stringify(measurement)}`);
    assert.ok(!["auto", "scroll", "hidden", "clip"].includes(measurement.overflowY), `${selector} must use normal page flow: ${JSON.stringify(measurement)}`);
  }
  assert.ok(await page.evaluate(() => document.documentElement.scrollHeight <= 1000), "case desktop document must fit the 1000px capture height");

  await page.setViewportSize({ width: 375, height: 812 });
  const mobile = await page.evaluate(() => ({
    body: document.body.scrollWidth - document.body.clientWidth,
    document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    height: document.documentElement.scrollHeight,
  }));
  assert.ok(mobile.body <= 0 && mobile.document <= 0, `case must fit 375px without horizontal overflow: ${JSON.stringify(mobile)}`);
  assert.ok(mobile.height > 812, "case mobile layout must use normal vertical scrolling");
  const clippedMobile = await marker.locator("[data-core-text]").evaluateAll((elements) => elements
    .filter((element) => element.getClientRects().length > 0)
    .map((element) => ({ text: element.textContent.trim(), size: Number.parseFloat(getComputedStyle(element).fontSize), clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }))
    .filter((item) => item.size < 13 || item.scrollWidth > item.clientWidth + 1));
  assert.deepEqual(clippedMobile, [], `case mobile core text must remain readable and unclipped: ${JSON.stringify(clippedMobile)}`);
  await page.setViewportSize({ width: 1600, height: 1000 });
}

async function assertVisibleThesisTextareasFit(root, context) {
  const measurements = await root.locator("[data-thesis-editor] textarea").evaluateAll((items) => items
    .filter((item) => item.getClientRects().length > 0)
    .map((item) => ({
      id: item.id,
      field: item.dataset.field,
      clientHeight: item.clientHeight,
      scrollHeight: item.scrollHeight,
    })));
  const clipped = measurements.filter((item) => item.scrollHeight > item.clientHeight + 1);
  assert.deepEqual(clipped, [], `${context} must not internally clip Thesis textareas: ${JSON.stringify(clipped)}`);
  return measurements;
}

async function waitForResponsiveTextareaLayout(page) {
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function assertNewResearchProductContract(page, marker, baseURL) {
  await page.evaluate(() => sessionStorage.clear());
  await page.reload({ waitUntil: "networkidle" });
  const creationContext = await page.evaluate(() => ({
    view: window.PROTOTYPE_NEW_RESEARCH?.buildNewResearchViewModel(window.PROTOTYPE_DATA),
    viewWithoutProviderRuns: window.PROTOTYPE_NEW_RESEARCH?.buildNewResearchViewModel({
      ...window.PROTOTYPE_DATA,
      providerRuns: [],
    }),
    fixtureCase: window.PROTOTYPE_DATA.case,
    snapshotCutoffs: window.PROTOTYPE_DATA.snapshots.map((snapshot) => snapshot.cutoff),
  }));
  assert.equal(creationContext.view.researchObject, creationContext.fixtureCase.researchObject, "creation screen must render researchObject directly from the case fixture");
  assert.equal(creationContext.view.phenomenon, creationContext.fixtureCase.phenomenon, "creation screen must render phenomenon directly from the case fixture");
  assert.deepEqual(creationContext.view.researchPeriod, creationContext.fixtureCase.researchPeriod, "creation screen must preserve the explicit researchPeriod fields");
  assert.equal(creationContext.view.studyRange, "2025-01-01 至 2027-12-31", "creation screen must format the approved independent research period");
  assert.ok(!creationContext.snapshotCutoffs.includes(creationContext.fixtureCase.researchPeriod.start), "creation period start must not come from snapshot cutoffs");
  assert.ok(!creationContext.snapshotCutoffs.includes(creationContext.fixtureCase.researchPeriod.end), "creation period end must not come from snapshot cutoffs");
  assert.deepEqual(creationContext.view.plan, creationContext.viewWithoutProviderRuns.plan, "new-research plan must not derive from providerRuns history");
  assert.equal(creationContext.view.activeStep, 2, "missing step must normalize to active step 2");
  const stepRail = marker.locator("ol[data-research-steps]");
  assert.equal(await stepRail.count(), 1, "new-research must expose one ordered four-step rail");
  const steps = stepRail.locator(":scope > li");
  assert.equal(await steps.count(), 4, "new-research must expose exactly four ordered steps");
  assert.deepEqual(
    (await steps.allTextContents()).map((text) => text.trim()),
    ["研究问题", "初始命题", "已有资产", "研究计划"],
    "new-research step labels and order are contractual",
  );
  assert.equal(await steps.filter({ has: page.locator('[aria-current="step"]') }).count(), 0, "aria-current belongs on the active step item itself");
  assert.equal(await steps.locator('[aria-current="step"]').count(), 0, "step descendants must not own aria-current");
  assert.equal(await steps.filter({ hasNot: page.locator("*") }).count(), 4, "step labels must remain direct readable text");
  assert.equal(await steps.nth(1).getAttribute("aria-current"), "step", "step 2 must be the current step");
  assert.equal(await steps.nth(0).getAttribute("data-step-state"), "completed", "step 1 must be explicitly completed");
  assert.equal(await steps.nth(1).getAttribute("data-step-state"), "current", "step 2 must be explicitly current");
  assert.equal(await steps.nth(2).getAttribute("data-step-state"), "upcoming", "step 3 must remain upcoming");
  assert.equal(await steps.nth(3).getAttribute("data-step-state"), "upcoming", "step 4 must remain upcoming");
  const defaultStageStatus = marker.locator("[data-stage-status]");
  assert.equal(await defaultStageStatus.count(), 1, "new-research must expose one stage status derived from its active step");
  assert.equal((await defaultStageStatus.textContent()).trim(), "当前阶段 · 命题待人工确认");
  assert.equal((await steps.nth(1).textContent()).trim(), "初始命题", "default header status must align with the current rail item");
  assert.equal((await marker.locator(".eyebrow").textContent()).trim(), "新建产业命题", "new-research eyebrow must be localized in Chinese");

  const summary = marker.locator("[data-question-summary]");
  assert.equal(await summary.count(), 1, "completed research question must be summarized once");
  const summaryText = await summary.textContent();
  for (const fact of [
    "研究名称",
    "AI 算力需求能否穿透至可验证的收入与持仓表达",
    "核心问题",
    "研究对象",
    "从云厂商资本开支，经芯片、互连与系统交付，到分部收入的 AI 算力产业链",
    "待解释现象",
    "AI 资本开支持续扩张，但订单、交付与收入确认的节奏出现分化",
    "研究时间范围",
    "2025-01-01 至 2027-12-31",
    "证据截止日",
    "2025-06-30",
  ]) {
    assert.ok(summaryText.includes(fact), `question summary must visibly include ${fact}`);
  }
  assert.notEqual(
    (await summary.locator('[data-summary-field="research-range"]').textContent()).trim(),
    (await summary.locator('[data-summary-field="evidence-cutoff"]').textContent()).trim(),
    "research time range and evidence cutoff must remain distinct fields",
  );

  const form = marker.getByRole("form", { name: "初始命题" });
  assert.equal(await form.count(), 1, "step 2 must be one semantic named form");
  assert.equal(await form.getByText("初始命题支持 1–3 条", { exact: true }).count(), 1, "new-research must visibly explain the 1–3 initial Thesis range");
  const editors = form.locator("fieldset[data-thesis-editor]");
  assert.equal(await editors.count(), 3, "new-research must use all three fixture theses");
  assert.equal(await form.getByText("AI 草案 · 未经人工复核", { exact: true }).count(), 3, "default fixture editors must expose exactly three AI-origin labels");
  const fixtureTheses = await page.evaluate(() => window.PROTOTYPE_DATA.theses.map((thesis) => ({
    title: thesis.title,
    statement: thesis.statement,
    observationStart: thesis.observationStart,
    observationEnd: thesis.observationEnd,
    supportCondition: thesis.supportCondition,
    falsifier: thesis.falsifier,
    nextValidationEvent: thesis.nextValidationEvent,
  })));
  for (let index = 0; index < fixtureTheses.length; index += 1) {
    const editor = editors.nth(index);
    const editorText = await editor.textContent();
    for (const label of ["命题标题", "观察开始", "观察结束", "支持条件", "反证条件", "下一验证事件"]) {
      assert.ok(editorText.includes(label), `thesis editor ${index + 1} must include ${label}`);
    }
    for (const value of Object.values(fixtureTheses[index])) {
      assert.ok(editorText.includes(value) || await editor.locator(`[value="${value.replaceAll('"', '\\"')}"]`).count(), `thesis editor ${index + 1} must use fixture value ${value}`);
    }
    assert.equal(await editor.locator("label").count() >= 5, true, `thesis editor ${index + 1} fields must use labels`);
    assert.equal(await editor.getAttribute("data-origin"), "ai", `fixture thesis ${index + 1} must preserve explicit AI origin`);
    assert.equal((await editor.locator("[data-ai-suggestion-label]").textContent()).trim(), "AI 草案 · 未经人工复核");
    assert.equal(await editor.locator('[data-field="title"]').inputValue(), fixtureTheses[index].title, "fixture title must be a real editable value");
    assert.equal(await editor.locator('[data-field="title"]').getAttribute("maxlength"), "120");
    assert.ok(await editor.locator("textarea").evaluateAll((items) => items.every((item) => item.maxLength === 2000)), "all Thesis textareas must expose the 2000-character limit");
    assert.equal(await editor.locator('input[type="date"]').count(), 2, "each editor must expose two native date controls");
    assert.ok(await editor.locator('input[type="date"]').evaluateAll((items) => items.every((item) => item.getBoundingClientRect().width >= 150)), "native date controls must be wide enough to display a complete YYYY/MM/DD date");
  }
  const draftControls = form.locator('input:not([type="hidden"]), textarea');
  assert.equal(await draftControls.count(), 21, "three Thesis editors must expose exactly seven draft controls each");
  const defaultTextareaMeasurements = await assertVisibleThesisTextareasFit(form, "default 1600px fixture");
  assert.equal(defaultTextareaMeasurements.length, 12, "default 1600px fixture must expose all twelve visible Thesis textareas");
  const longTextarea = editors.first().locator('[data-field="falsifier"]');
  const originalLongTextareaValue = await longTextarea.inputValue();
  await longTextarea.fill("若主要云厂商连续两个披露期下调资本开支，并明确说明相关投入未形成部署、订单或交付，同时供应链公司披露产能利用率和相关收入同步回落，则该命题需要被推翻并重新建立验证路径。");
  await assertVisibleThesisTextareasFit(form, "representative longer allowed input");
  await longTextarea.fill(originalLongTextareaValue);
  await assertVisibleThesisTextareasFit(form, "textarea shrink after restoring fixture text");
  assert.ok(await draftControls.evaluateAll((controls) => controls.every((control) => !control.hasAttribute("name"))), "visible draft controls must not submit names into the GET URL");
  assert.ok(await draftControls.evaluateAll((controls) => controls.every((control) => control.dataset.field)), "visible draft controls must identify fields through data-field");
  assert.deepEqual(await editors.evaluateAll((items) => items.map((item) => item.dataset.thesisId)), ["TH-AIC-01", "TH-AIC-02", "TH-AIC-03"]);
  const aiAssist = form.getByRole("button", { name: "AI 协助拆分", exact: true });
  assert.equal(await aiAssist.count(), 1, "AI help must be a single secondary action");
  assert.ok(await aiAssist.isDisabled(), "AI assist must not remain an enabled no-op");
  const aiAssistDescription = await aiAssist.getAttribute("aria-describedby");
  assert.equal((await form.locator(`#${aiAssistDescription}`).textContent()).trim(), "当前原型展示既有 AI 拆分结果，不执行重新生成");
  assert.ok(await form.locator(`#${aiAssistDescription}`).isVisible(), "disabled AI assist explanation must remain visible");
  const addThesis = form.getByRole("button", { name: "新增命题", exact: true });
  assert.equal(await addThesis.count(), 1, "thesis actions must expose a real add control");
  assert.ok(await addThesis.isDisabled(), "add Thesis must be disabled when all three fixture theses are present");
  const addDescriptionId = await addThesis.getAttribute("aria-describedby");
  assert.ok(addDescriptionId, "disabled add Thesis control must reference an accessible explanation");
  assert.equal(
    (await form.locator(`#${addDescriptionId}`).textContent()).trim(),
    "已达 3 条上限；删除后可新增",
    "disabled add Thesis explanation must state how the limit can be cleared",
  );
  assert.ok(await form.locator(`#${addDescriptionId}`).isVisible(), "disabled add Thesis description must remain visibly available");
  const defaultRemoveButtons = form.locator("[data-remove-thesis]");
  assert.equal(await defaultRemoveButtons.count(), 3, "each default Thesis editor must expose an honest remove control");
  assert.ok(await defaultRemoveButtons.evaluateAll((buttons) => buttons.every((button) => !button.disabled)), "all remove controls must be enabled while more than one Thesis remains");
  const primaryActions = form.locator("[data-primary-action]");
  assert.equal(await primaryActions.count(), 1, "step 2 must expose one primary form action");
  assert.equal((await primaryActions.textContent()).trim(), "确认命题并继续");
  assert.equal(await primaryActions.getAttribute("type"), "submit");
  const overlengthTitle = editors.first().locator('[data-field="title"]');
  await overlengthTitle.evaluate((element) => {
    element.value = "字".repeat(121);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await primaryActions.click();
  assert.equal((await editors.first().locator('[data-field-error="title"]').textContent()).trim(), "内容超过允许长度（最多 120 个字符）", "overlength title must not be mislabeled as required");
  await overlengthTitle.fill(fixtureTheses[0].title);

  for (const preview of ["assets", "plan"]) {
    const region = marker.locator(`[data-step-preview="${preview}"]`);
    assert.equal(await region.count(), 1, `new-research must expose the ${preview} compact preview`);
    assert.equal((await region.locator("[data-preview-state]").textContent()).trim(), "尚未完成 · 下一步预览");
  }
  const pageText = await marker.textContent();
  for (const concept of [
    "可复用文档", "可复用陈述", "可复用数据", "已复核关系", "相关案例资产",
    "计划内部复用", "提供方查询", "正面与反面证据搜索", "结果数据", "当前缺口",
  ]) {
    assert.ok(pageText.includes(concept), `new-research preview must visibly include ${concept}`);
  }
  for (const plannedCopy of [
    "聚源 · 行业分析观点 · 能力探测（计划）",
    "聚源 · 公告财报原文 · 能力探测（计划）",
    "聚源 · 基金持股明细 · 能力探测（计划）",
    "NVIDIA 数据中心业务收入 · 391 亿美元 · 2026 财年第一季度",
    "台积电月度营收同比增幅 · 34.8% · 2025 年 5 月",
  ]) {
    assert.ok(pageText.includes(plannedCopy), `new-research must render explicit localized plan copy: ${plannedCopy}`);
  }
  for (const forbidden of [
    "awaiting_validation", "pending_review", "reviewed", "quota_failure", "permission_gap",
    "Daily call limit exceeded", "Current credential lacks historical holdings permission",
    "industry_analysis_view", "announcement_filing_fulltext", "fund_holding_detail", "capability_probe",
    "$39.1bn", "FY2026 Q1",
  ]) {
    assert.ok(!pageText.includes(forbidden), `new-research must not expose internal value: ${forbidden}`);
  }

  const bodySizeSelectors = [
    ".form-guidance",
    ".thesis-fields label > span",
    ".thesis-fields input",
    ".thesis-fields textarea",
    ".step-previews li strong",
    ".step-previews li span",
  ];
  for (const selector of bodySizeSelectors) {
    const sizes = await marker.locator(selector).evaluateAll((elements) => elements.map((element) => Number.parseFloat(getComputedStyle(element).fontSize)));
    assert.ok(sizes.length > 0 && sizes.every((size) => size >= 13), `${selector} core copy must render at 13px or larger`);
  }

  const firstTitle = editors.nth(0).locator('[data-field="title"]');
  await firstTitle.focus();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.field), "statement", "Tab must move from title to statement");
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.field), "observationStart", "Tab must continue to observation start");
  assert.deepEqual(
    await editors.first().locator("[data-field]").evaluateAll((items) => items.map((item) => item.dataset.field)),
    ["title", "statement", "observationStart", "observationEnd", "nextValidationEvent", "supportCondition", "falsifier"],
    "draft controls must retain a logical DOM order even though native date internals have browser-specific Tab stops",
  );
  assert.ok(await editors.first().locator("[data-field]").evaluateAll((items) => items.every((item) => item.tabIndex >= 0)), "all draft controls must be keyboard focusable");
  await editors.last().locator('[data-field="falsifier"]').focus();
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement?.textContent.trim()), "确认命题并继续", "Tab must skip disabled AI/add controls and reach the primary action");

  const responsiveLongValue = "当研究编辑器从桌面三栏切换到移动端单栏，再返回桌面布局时，这段较长但合法的反证文本必须保持完整可读，不能因为断点变化而恢复为旧高度或出现内部滚动。";
  await page.setViewportSize({ width: 375, height: 812 });
  await waitForResponsiveTextareaLayout(page);
  await longTextarea.fill(responsiveLongValue);
  await assertVisibleThesisTextareasFit(form, "1600 to 375 responsive transition with long input");
  const narrowLayout = await page.evaluate(() => ({
    bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
    documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  assert.ok(narrowLayout.bodyOverflow <= 0 && narrowLayout.documentOverflow <= 0, `new-research must fit 375px without horizontal overflow: ${JSON.stringify(narrowLayout)}`);
  for (const target of [
    primaryActions,
    marker.locator('[data-step-preview="assets"] header'),
    marker.locator('[data-step-preview="plan"] header'),
    marker.locator('[data-step-preview="plan"] li').last(),
  ]) {
    await target.scrollIntoViewIfNeeded();
    const box = await target.boundingBox();
    assert.ok(box && box.y >= 0 && box.y + box.height <= 812, "each lower new-research control or preview fact must be bringable inside the 375px viewport");
  }
  await page.setViewportSize({ width: 1600, height: 1000 });
  await waitForResponsiveTextareaLayout(page);
  await assertVisibleThesisTextareasFit(form, "375 to 1600 responsive transition with long input");
  const responsiveDesktopHeight = await longTextarea.evaluate((element) => element.clientHeight);
  await page.setViewportSize({ width: 375, height: 812 });
  await waitForResponsiveTextareaLayout(page);
  await assertVisibleThesisTextareasFit(form, "1600 back to 375 responsive transition with long input");
  assert.ok(await longTextarea.evaluate((element) => element.clientHeight) < responsiveDesktopHeight, "responsive autosize must shrink a long textarea when its column becomes wider");
  await page.setViewportSize({ width: 1600, height: 1000 });
  await waitForResponsiveTextareaLayout(page);
  await assertVisibleThesisTextareasFit(form, "second 375 to 1600 responsive transition with long input");
  await longTextarea.fill(originalLongTextareaValue);
  await assertVisibleThesisTextareasFit(form, "responsive long input restored before confirmation");

  async function assertStepThreeState(expectedDraftText) {
    const url = new URL(page.url());
    assert.equal(url.search, "?screen=new-research&step=3", "confirmed progression URL must contain exactly the canonical screen and step keys");
    assert.deepEqual([...url.searchParams.keys()], ["screen", "step"], "confirmed progression URL must expose no draft field keys");
    assert.ok(!page.url().includes(encodeURIComponent(expectedDraftText)), "confirmed progression URL must not leak draft text");
    const progressedMarker = page.locator('[data-screen="new-research"]');
    const progressedSteps = progressedMarker.locator("ol[data-research-steps] > li");
    assert.equal(await progressedSteps.locator('[aria-current="step"]').count(), 0, "aria-current must remain on the step item after progression");
    assert.equal(await progressedMarker.locator('ol[data-research-steps] > li[aria-current="step"]').count(), 1, "step 3 must be the sole current step");
    assert.equal(await progressedSteps.nth(1).getAttribute("data-step-state"), "completed", "step 2 must become completed after progression");
    assert.equal(await progressedSteps.nth(2).getAttribute("aria-current"), "step", "step 3 must become current after progression");
    assert.equal(await progressedSteps.nth(2).getAttribute("data-step-state"), "current");
    assert.equal((await progressedSteps.nth(2).textContent()).trim(), "已有资产");
    const progressedStatus = progressedMarker.locator("[data-stage-status]");
    assert.equal((await progressedStatus.textContent()).trim(), "当前阶段 · 复用资产待确认", "step 3 header must align with the current assets stage");
    assert.ok(!(await progressedMarker.textContent()).includes("当前阶段 · 命题待人工确认"), "step 3 must not retain the contradictory step 2 header status");
    const assetStage = progressedMarker.locator('[data-step-stage="assets"]');
    assert.equal(await assetStage.count(), 1, "step 3 must render the existing-assets section as a stage");
    assert.equal(await assetStage.getAttribute("data-step-preview"), null, "current assets stage must not remain marked as a preview");
    assert.equal(await assetStage.locator("[data-preview-state]").count(), 0, "current assets stage must not carry a not-complete preview badge");
    assert.equal(await assetStage.locator("[data-current-stage]").count(), 1, "current assets stage must visibly explain its current state");
    assert.ok((await progressedMarker.locator("[data-confirmed-theses]").textContent()).includes(expectedDraftText), "step 3 must render the validated confirmation record");
    assert.ok((await progressedMarker.textContent()).includes("命题确认不等于证据关系已审核"), "Thesis confirmation and evidence review must remain visibly separate");
    assert.equal(await progressedMarker.locator('[data-confirmed-theses] [data-draft-origin-label]').filter({ hasText: "待确认" }).count(), 0, "confirmed Thesis labels must not retain pending wording");
    assert.equal(await progressedMarker.locator('[data-confirmed-theses] [data-draft-origin-label]').filter({ hasText: "未经人工复核" }).count(), 0, "confirmed Thesis labels must not retain unreviewed wording");
  }

  await defaultRemoveButtons.first().focus();
  await page.keyboard.press("Enter");
  assert.equal(await form.locator("[data-thesis-editor]").count(), 2, "keyboard removal must reduce the draft to two theses");
  assert.equal(await form.locator('[data-thesis-editor]').first().locator('[data-field="title"]').inputValue(), fixtureTheses[1].title, "removal must preserve the next stable draft title rather than synthesizing one from position");
  assert.equal((await page.evaluate(() => document.activeElement?.textContent)).trim(), "新增命题", "keyboard removal must move focus to the now-enabled add control");
  assert.ok(!await addThesis.isDisabled(), "add Thesis must enable below the three-Thesis maximum");
  assert.equal((await form.locator(`#${addDescriptionId}`).textContent()).trim(), "当前 2 条；可新增至 3 条");

  const clickEdit = "点击确认后的唯一命题文本";
  await form.locator('[data-field="statement"]').first().fill(`  ${clickEdit}  `);
  await Promise.all([
    page.waitForURL((url) => url.search === "?screen=new-research&step=3"),
    primaryActions.click(),
  ]);
  await assertStepThreeState(clickEdit);
  const storageKey = await page.evaluate(() => window.PROTOTYPE_NEW_RESEARCH.confirmationStorageKey(window.PROTOTYPE_DATA.case.id));
  const savedTwoRecord = await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)), storageKey);
  assert.equal(savedTwoRecord.schemaVersion, 2, "changed confirmation record must expose schemaVersion 2");
  assert.equal(savedTwoRecord.caseId, "RC-AIC-2025-01", "confirmation record must be keyed to the active case");
  assert.equal(savedTwoRecord.snapshotId, "RS-2025-06-30-v3", "confirmation record must bind to the immutable snapshot");
  assert.equal(savedTwoRecord.cutoff, "2025-06-30", "confirmation record must bind to the evidence cutoff");
  assert.equal(savedTwoRecord.researchPlanRevision, "RP-AIC-2025-01-v1", "confirmation record must bind to the research plan revision");
  assert.equal(savedTwoRecord.confirmationState, "confirmed", "confirmationState must be record-level state");
  assert.equal(savedTwoRecord.theses.length, 2, "confirmation validator must accept two complete Thesis drafts");
  assert.deepEqual(savedTwoRecord.theses.map((thesis) => thesis.origin), ["ai", "ai"], "confirmation must preserve AI origin after removal");
  assert.deepEqual(savedTwoRecord.theses.map((thesis) => thesis.lastEditedBy), ["human", "ai"], "editing fixture text must derive human edit attribution without changing origin");
  assert.deepEqual(savedTwoRecord.theses.map((thesis) => [thesis.observationStart, thesis.observationEnd]), [["2025-01-01", "2027-12-31"], ["2025-01-01", "2027-12-31"]]);
  const firstConfirmationLabels = await page.locator('[data-confirmed-theses] [data-draft-origin-label]').allTextContents();
  assert.ok(firstConfirmationLabels.includes("AI 起草 · 人工修改并确认"));
  assert.ok(firstConfirmationLabels.includes("AI 起草 · 人工已确认"));
  const firstEvidenceStates = await page.locator('[data-confirmed-theses] [data-evidence-review-state]').allTextContents();
  assert.ok(firstEvidenceStates.includes("证据关系：尚无证据关系"));
  assert.ok(firstEvidenceStates.includes("证据关系：已有已审核关系 · 另有待审核关系"), "reviewed and pending evidence relations must remain distinct from Thesis confirmation");
  assert.equal(savedTwoRecord.theses[0].statement, clickEdit, "confirmation record must normalize surrounding whitespace");

  await page.reload({ waitUntil: "networkidle" });
  await assertStepThreeState(clickEdit);
  await page.goBack({ waitUntil: "networkidle" });
  assert.equal(new URL(page.url()).search, "?screen=new-research", "Back from confirmed step 3 must return to canonical step 2");
  assert.equal(await page.locator('[data-screen="new-research"] [data-field="statement"]').first().inputValue(), clickEdit, "Back to step 2 must repopulate the validated confirmation record");
  assert.equal(await page.locator('[data-screen="new-research"] [data-thesis-editor]').count(), 2, "Back must preserve a two-Thesis confirmation");
  await assertVisibleThesisTextareasFit(page.locator('[data-screen="new-research"] form[aria-label="初始命题"]'), "back-restored confirmation");
  await page.setViewportSize({ width: 375, height: 812 });
  await waitForResponsiveTextareaLayout(page);
  await assertVisibleThesisTextareasFit(page.locator('[data-screen="new-research"] form[aria-label="初始命题"]'), "back-restored confirmation at 375");
  await page.setViewportSize({ width: 1600, height: 1000 });
  await waitForResponsiveTextareaLayout(page);
  await assertVisibleThesisTextareasFit(page.locator('[data-screen="new-research"] form[aria-label="初始命题"]'), "back-restored confirmation returned to 1600");
  await page.setViewportSize({ width: 375, height: 812 });
  await waitForResponsiveTextareaLayout(page);
  await assertVisibleThesisTextareasFit(page.locator('[data-screen="new-research"] form[aria-label="初始命题"]'), "back-restored confirmation returned to 375");
  assert.deepEqual(
    await page.locator('[data-screen="new-research"] [data-draft-origin-label]').allTextContents(),
    ["AI 起草 · 人工已修改 · 待重新确认", "AI 起草 · 已确认过 · 待重新确认"],
    "Back must preserve human edit provenance while returning both AI-origin drafts to pending confirmation",
  );
  const restoredUnchangedAi = page.locator('[data-screen="new-research"] [data-thesis-editor]').nth(1);
  await restoredUnchangedAi.locator('[data-field="title"]').fill(`${fixtureTheses[2].title}（再次编辑）`);
  assert.equal((await restoredUnchangedAi.locator('[data-draft-origin-label]').textContent()).trim(), "AI 起草 · 人工已修改 · 待重新确认", "post-back input must immediately mark a previously confirmed AI draft dirty");

  const stepTwoForm = page.locator('[data-screen="new-research"] form[aria-label="初始命题"]');
  await stepTwoForm.locator("[data-remove-thesis]").last().click();
  assert.equal(await stepTwoForm.locator("[data-thesis-editor]").count(), 1, "remove must support a genuine one-Thesis draft");
  const soleRemove = stepTwoForm.locator("[data-remove-thesis]");
  assert.ok(await soleRemove.isDisabled(), "the final Thesis must not be removable");
  const minimumDescriptionId = await soleRemove.getAttribute("aria-describedby");
  assert.equal((await stepTwoForm.locator(`#${minimumDescriptionId}`).textContent()).trim(), "至少保留 1 条初始命题");
  assert.ok(await stepTwoForm.locator(`#${minimumDescriptionId}`).isVisible(), "minimum Thesis explanation must be visible");
  const oneEdit = "仅保留一条后的确认命题";
  await stepTwoForm.locator('[data-field="statement"]').fill(oneEdit);
  await Promise.all([
    page.waitForURL((url) => url.search === "?screen=new-research&step=3"),
    stepTwoForm.locator("[data-primary-action]").click(),
  ]);
  await assertStepThreeState(oneEdit);
  const savedOneRecord = await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)), storageKey);
  assert.equal(savedOneRecord.theses.length, 1, "confirmation validator must accept one complete Thesis draft");
  assert.equal(savedOneRecord.theses[0].origin, "ai", "one-Thesis confirmation must preserve AI origin");
  assert.equal(savedOneRecord.theses[0].lastEditedBy, "human");
  await page.reload({ waitUntil: "networkidle" });
  await assertStepThreeState(oneEdit);
  await page.goBack({ waitUntil: "networkidle" });
  const oneThesisForm = page.locator('[data-screen="new-research"] form[aria-label="初始命题"]');
  assert.equal(await oneThesisForm.locator("[data-thesis-editor]").count(), 1, "Back must preserve a one-Thesis confirmation");

  async function fillBlankEditor(editor, suffix) {
    const values = {
      title: `人工新增标题 ${suffix}`,
      statement: `新增命题表述 ${suffix}`,
      observationStart: "2025-03-01",
      observationEnd: "2026-09-30",
      nextValidationEvent: `新增下一验证事件 ${suffix}`,
      supportCondition: `新增支持条件 ${suffix}`,
      falsifier: `新增反证条件 ${suffix}`,
    };
    for (const [field, value] of Object.entries(values)) await editor.locator(`[data-field="${field}"]`).fill(value);
  }

  const addFromOne = oneThesisForm.getByRole("button", { name: "新增命题", exact: true });
  await addFromOne.focus();
  await page.keyboard.press("Enter");
  assert.equal(await oneThesisForm.locator("[data-thesis-editor]").count(), 2, "keyboard add must restore a second blank Thesis editor");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.field), "title", "adding a Thesis must focus its first draft field");
  const firstHumanEditor = oneThesisForm.locator("[data-thesis-editor]").last();
  assert.equal(await firstHumanEditor.getAttribute("data-origin"), "human", "manual add must create an explicit human-origin draft");
  assert.equal((await firstHumanEditor.locator("[data-draft-origin-label]").textContent()).trim(), "人工草稿 · 待确认");
  assert.equal(await firstHumanEditor.locator("[data-ai-suggestion-label]").count(), 0, "a human-origin fieldset must contain no AI-authorship label");
  await oneThesisForm.locator("[data-primary-action]").click();
  assert.equal(new URL(page.url()).search, "?screen=new-research", "an incomplete added Thesis must not advance the workflow");
  const emptyHumanTitle = firstHumanEditor.locator('[data-field="title"]');
  assert.equal(await emptyHumanTitle.getAttribute("aria-invalid"), "true", "empty human title must receive field-level invalid state");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.field), "title", "first invalid field must receive focus");
  const emptyTitleErrorId = await emptyHumanTitle.getAttribute("aria-describedby");
  assert.ok(await firstHumanEditor.locator(`#${emptyTitleErrorId}`).isVisible(), "empty title must reference a visible field error");
  assert.equal(
    (await oneThesisForm.locator("[data-form-error]").textContent()).trim(),
    "请修正已标记的命题字段后再确认。",
    "the invalid-draft summary must direct the user to field-level errors",
  );
  await fillBlankEditor(oneThesisForm.locator("[data-thesis-editor]").last(), "A");
  await firstHumanEditor.locator('[data-field="observationStart"]').fill("2026-01-02");
  await firstHumanEditor.locator('[data-field="observationEnd"]').fill("2026-01-01");
  await oneThesisForm.locator("[data-primary-action]").click();
  assert.equal(await firstHumanEditor.locator('[data-field="observationStart"]').getAttribute("aria-invalid"), "true", "reversed dates must receive field-level invalid state");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.field), "observationStart", "reversed date validation must focus the first invalid date");
  const reversedDateErrorId = await firstHumanEditor.locator('[data-field="observationStart"]').getAttribute("aria-describedby");
  assert.ok(await firstHumanEditor.locator(`#${reversedDateErrorId}`).isVisible(), "reversed date must reference a visible field error");
  await firstHumanEditor.locator('[data-field="observationStart"]').fill("2025-03-01");
  await firstHumanEditor.locator('[data-field="observationEnd"]').fill("2026-09-30");
  await firstHumanEditor.locator('[data-field="falsifier"]').fill("");
  await oneThesisForm.locator("[data-primary-action]").click();
  assert.equal(await firstHumanEditor.locator('[data-field="falsifier"]').getAttribute("aria-invalid"), "true", "empty falsifier must receive field-level invalid state");
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.field), "falsifier", "empty falsifier must receive focus");
  const falsifierErrorId = await firstHumanEditor.locator('[data-field="falsifier"]').getAttribute("aria-describedby");
  assert.ok(await firstHumanEditor.locator(`#${falsifierErrorId}`).isVisible(), "empty falsifier must reference a visible field error");
  await firstHumanEditor.locator('[data-field="falsifier"]').fill("新增反证条件 A");
  await addFromOne.click();
  assert.equal(await oneThesisForm.locator("[data-thesis-editor]").count(), 3, "pointer add must restore the three-Thesis maximum");
  const secondHumanEditor = oneThesisForm.locator("[data-thesis-editor]").last();
  assert.equal(await secondHumanEditor.getAttribute("data-origin"), "human");
  assert.equal((await secondHumanEditor.locator("[data-draft-origin-label]").textContent()).trim(), "人工草稿 · 待确认");
  assert.equal(await secondHumanEditor.locator("[data-ai-suggestion-label]").count(), 0);
  await fillBlankEditor(oneThesisForm.locator("[data-thesis-editor]").last(), "B");
  const restoredIds = await oneThesisForm.locator("[data-thesis-editor]").evaluateAll((items) => items.map((item) => item.dataset.thesisId));
  assert.equal(new Set(restoredIds).size, 3, "restored Thesis editors must use unique draft IDs");
  assert.ok(await addFromOne.isDisabled(), "add Thesis must disable again at the maximum");
  assert.equal((await oneThesisForm.locator(`#${addDescriptionId}`).textContent()).trim(), "已达 3 条上限；删除后可新增");

  const enterEdit = "Enter 确认后的唯一支持条件";
  await oneThesisForm.locator('[data-field="supportCondition"]').nth(1).fill(enterEdit);
  await oneThesisForm.locator('[data-field="observationStart"]').first().focus();
  await Promise.all([
    page.waitForURL((url) => url.search === "?screen=new-research&step=3"),
    page.keyboard.press("Enter"),
  ]);
  await assertStepThreeState(enterEdit);
  const savedRecord = await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)), storageKey);
  assert.equal(savedRecord.theses.length, 3, "confirmation validator must accept three complete unique Thesis drafts");
  assert.deepEqual(savedRecord.theses.map((thesis) => thesis.origin), ["ai", "human", "human"], "confirmation must preserve mixed trusted origins");
  assert.deepEqual(savedRecord.theses.map((thesis) => thesis.lastEditedBy), ["human", "human", "human"]);
  assert.deepEqual(savedRecord.theses.map((thesis) => thesis.title), [fixtureTheses[1].title, "人工新增标题 A", "人工新增标题 B"], "editable titles must persist without synthesis from ID or position");
  assert.deepEqual(await page.locator('[data-confirmed-theses] [data-draft-origin-label]').allTextContents(), ["AI 起草 · 人工修改并确认", "人工起草 · 已确认", "人工起草 · 已确认"]);
  assert.equal(await page.locator('[data-confirmed-theses] [data-evidence-review-state]').filter({ hasText: "尚无证据关系" }).count(), 3, "fixture without a linked relation and human drafts must not inherit Thesis confirmation as evidence review");

  await page.reload({ waitUntil: "networkidle" });
  await assertStepThreeState(enterEdit);
  await page.goBack({ waitUntil: "networkidle" });
  const restoredOriginEditors = page.locator('[data-screen="new-research"] [data-thesis-editor]');
  await assertVisibleThesisTextareasFit(page.locator('[data-screen="new-research"] form[aria-label="初始命题"]'), "mixed-origin back-restored confirmation");
  assert.deepEqual(await restoredOriginEditors.evaluateAll((items) => items.map((item) => item.dataset.origin)), ["ai", "human", "human"], "Back after refresh must preserve each draft origin");
  assert.deepEqual(await restoredOriginEditors.locator('[data-field="title"]').evaluateAll((items) => items.map((item) => item.value)), [fixtureTheses[1].title, "人工新增标题 A", "人工新增标题 B"], "Back after refresh must preserve editable titles");
  assert.equal(await restoredOriginEditors.nth(0).locator("[data-ai-suggestion-label]").count(), 1);
  assert.equal(await restoredOriginEditors.nth(1).locator("[data-ai-suggestion-label]").count(), 0);
  assert.equal((await restoredOriginEditors.nth(1).locator("[data-draft-origin-label]").textContent()).trim(), "人工起草 · 已确认过 · 待重新确认");
  assert.deepEqual(
    await restoredOriginEditors.locator("[data-draft-origin-label]").allTextContents(),
    ["AI 起草 · 人工已修改 · 待重新确认", "人工起草 · 已确认过 · 待重新确认", "人工起草 · 已确认过 · 待重新确认"],
    "Back after mixed confirmation must preserve prior-confirmation edit-mode labels",
  );
  await restoredOriginEditors.nth(1).locator('[data-field="title"]').fill("人工新增标题 A（再次编辑）");
  assert.equal((await restoredOriginEditors.nth(1).locator("[data-draft-origin-label]").textContent()).trim(), "人工起草 · 已修改 · 待重新确认", "post-back human input must immediately mark the draft dirty");

  await page.goto(`${baseURL}/?screen=new-research&step=3`, { waitUntil: "networkidle" });
  await assertStepThreeState(enterEdit);

  const escapedRecord = structuredClone(savedRecord);
  escapedRecord.theses[0].statement = '<img src=x onerror="window.__draftXss = true">';
  await page.evaluate(({ key, record }) => sessionStorage.setItem(key, JSON.stringify(record)), { key: storageKey, record: escapedRecord });
  await page.goto(`${baseURL}/?screen=new-research&step=3`, { waitUntil: "networkidle" });
  assert.equal(await page.locator('[data-confirmed-theses] img').count(), 0, "stored draft markup must render only as escaped text");
  assert.equal(await page.evaluate(() => window.__draftXss), undefined, "stored draft markup must never execute");

  async function assertRecoveredStepTwo(expectedMessage = false) {
    assert.equal(new URL(page.url()).search, "?screen=new-research", "unconfirmed or invalid state must canonicalize to step 2");
    const recoveredMarker = page.locator('[data-screen="new-research"]');
    const recoveredSteps = recoveredMarker.locator("ol[data-research-steps] > li");
    assert.equal(await recoveredSteps.nth(1).getAttribute("aria-current"), "step");
    assert.equal(await recoveredSteps.nth(1).getAttribute("data-step-state"), "current");
    assert.notEqual(await recoveredSteps.nth(1).getAttribute("data-step-state"), "completed", "step 2 must not fabricate completion");
    assert.equal(await recoveredSteps.nth(2).getAttribute("data-step-state"), "upcoming");
    assert.equal(await recoveredMarker.locator('[data-step-current]').count(), 0, "assets must not become current without confirmation");
    assert.equal(await recoveredMarker.locator('[data-recovery-message]').count(), expectedMessage ? 1 : 0);
    if (expectedMessage) {
      assert.equal((await recoveredMarker.locator('[data-recovery-message]').textContent()).trim(), "未找到已确认草稿，请重新确认初始命题");
    }
  }

  await page.evaluate((key) => sessionStorage.removeItem(key), storageKey);
  await page.goto(`${baseURL}/?screen=new-research&step=3`, { waitUntil: "networkidle" });
  await assertRecoveredStepTwo(true);

  const legacyStorageKey = storageKey.replace(":v2:", ":v1:");
  await page.evaluate(({ legacyKey, record }) => {
    sessionStorage.setItem(legacyKey, JSON.stringify({ ...record, schemaVersion: 1 }));
  }, { legacyKey: legacyStorageKey, record: savedRecord });
  await page.goto(`${baseURL}/?screen=new-research&step=3`, { waitUntil: "networkidle" });
  await assertRecoveredStepTwo(true);
  await page.evaluate((legacyKey) => sessionStorage.removeItem(legacyKey), legacyStorageKey);

  for (const rawRecord of [
    "{not-json",
    JSON.stringify({ ...savedRecord, caseId: "RC-WRONG-CASE" }),
    JSON.stringify({ ...savedRecord, schemaVersion: 0 }),
    JSON.stringify({ ...savedRecord, snapshotId: "RS-STALE" }),
    JSON.stringify({ ...savedRecord, cutoff: "2025-03-31" }),
    JSON.stringify({ ...savedRecord, researchPlanRevision: "RP-STALE" }),
    JSON.stringify({ ...savedRecord, confirmationState: "pending" }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, origin: "robot" } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, origin: undefined } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, lastEditedBy: "system" } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, observationStart: "2025-02-30" } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, observationStart: "2026-01-02", observationEnd: "2026-01-01" } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, observationStart: "2024-12-31" } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, observationEnd: "2028-01-01" } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: [] }),
    JSON.stringify({ ...savedRecord, theses: [...savedRecord.theses, { ...savedRecord.theses[0], id: "TH-DRAFT-OVERFLOW" }] }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 1 ? { ...thesis, id: savedRecord.theses[0].id } : thesis) }),
    JSON.stringify({ ...savedRecord, theses: savedRecord.theses.map((thesis, index) => index === 0 ? { ...thesis, falsifier: "" } : thesis) }),
  ]) {
    await page.evaluate(({ key, raw }) => sessionStorage.setItem(key, raw), { key: storageKey, raw: rawRecord });
    await page.goto(`${baseURL}/?screen=new-research&step=3`, { waitUntil: "networkidle" });
    await assertRecoveredStepTwo(true);
  }

  await page.evaluate((key) => sessionStorage.removeItem(key), storageKey);
  for (const search of [
    "?screen=new-research",
    "?screen=new-research&step=2",
    "?screen=new-research&step=1",
    "?screen=new-research&step=4",
    "?screen=new-research&step=-1",
    "?screen=new-research&step=bad",
    "?screen=new-research&step=3&step=3",
  ]) {
    await page.goto(`${baseURL}/${search}`, { waitUntil: "networkidle" });
    await assertRecoveredStepTwo(false);
  }
}

async function assertTeardownContract() {
  const { withPrototypeBrowser } = await import("./capture.mjs");
  const events = [];
  const primaryError = new Error("primary callback failure");
  const browserCloseError = new Error("browser close failure");
  const serverCloseError = new Error("server close failure");
  const dependencies = {
    startServer: async () => ({
      baseURL: "http://127.0.0.1:1",
      close: async () => {
        events.push("server-close");
        throw serverCloseError;
      },
    }),
    launchBrowser: async () => ({
      newContext: async () => ({ newPage: async () => ({}) }),
      close: async () => {
        events.push("browser-close");
        throw browserCloseError;
      },
    }),
  };

  let observedError;
  try {
    await withPrototypeBrowser(async () => {
      events.push("callback");
      throw primaryError;
    }, dependencies);
  } catch (error) {
    observedError = error;
  }

  assert.equal(observedError, primaryError, "teardown must preserve the primary callback error");
  assert.deepEqual(events, ["callback", "browser-close", "server-close"], "browser and server teardown must both run in order");
  assert.deepEqual(primaryError.teardownErrors, [browserCloseError, serverCloseError], "teardown failures must remain inspectable on the primary error");

  for (const thrownValue of [
    Object.freeze(new Error("frozen primary")),
    Object.preventExtensions(new Error("non-extensible primary")),
    0,
  ]) {
    const teardownEvents = [];
    const isolatedDependencies = {
      startServer: async () => ({
        baseURL: "http://127.0.0.1:1",
        close: async () => {
          teardownEvents.push("server-close");
          throw new Error("isolated server close failure");
        },
      }),
      launchBrowser: async () => ({
        newContext: async () => ({ newPage: async () => ({}) }),
        close: async () => {
          teardownEvents.push("browser-close");
          throw new Error("isolated browser close failure");
        },
      }),
    };
    let isolatedObserved = Symbol("not thrown");
    try {
      await withPrototypeBrowser(async () => { throw thrownValue; }, isolatedDependencies);
    } catch (error) {
      isolatedObserved = error;
    }
    assert.ok(Object.is(isolatedObserved, thrownValue), "teardown must rethrow the exact frozen or primitive primary value");
    assert.deepEqual(teardownEvents, ["browser-close", "server-close"], "both teardown paths must run for frozen or primitive primary values");
  }
}

async function assertOverviewViewModelContract(page) {
  const result = await page.evaluate(() => {
    const fixture = window.PROTOTYPE_DATA;
    const selectors = window.PROTOTYPE_OVERVIEW;
    const baseline = selectors?.buildOverviewViewModel(fixture);

    const reorderedFixture = structuredClone(fixture);
    reorderedFixture.reviewQueue.reverse();
    const reordered = selectors?.buildOverviewViewModel(reorderedFixture);

    const missingChainFixture = structuredClone(fixture);
    missingChainFixture.evidenceLinks = missingChainFixture.evidenceLinks.filter((link) => link.statementId !== "ST-003");
    const missingChain = selectors?.buildOverviewViewModel(missingChainFixture);

    const expectedReview = fixture.reviewQueue.find((item) => item.id === "RQ-001");
    const expectedStatement = fixture.statements.find((item) => item.id === expectedReview.targetId);
    const expectedEvidence = fixture.evidenceLinks.find((item) => item.statementId === expectedStatement.id);
    const expectedThesis = fixture.theses.find((item) => item.id === expectedEvidence.thesisId);
    return {
      baseline,
      reordered,
      missingChain,
      expected: {
        workItemId: expectedReview.id,
        task: expectedReview.task,
        sourceId: expectedEvidence.id,
        sourceVersion: expectedEvidence.sourceVersion,
        blockerTitle: expectedThesis.title,
      },
    };
  });

  assert.ok(result.baseline, "overview must expose a pure buildOverviewViewModel selector");
  assert.deepEqual(result.reordered.workItem, result.baseline.workItem, "reordering reviewQueue must not mix blocker facts");
  for (const [field, expected] of Object.entries(result.expected)) {
    assert.equal(result.baseline.workItem[field], expected, `overview work item must derive ${field} through explicit fixture IDs`);
  }
  assert.equal(result.baseline.workItem.reviewStatusLabel, "待人工审核");
  assert.equal(result.baseline.workItem.actionLabel, `审核：${result.expected.task}`);
  assert.equal(result.baseline.workItem.actionRoute, `?screen=review&item=${result.expected.workItemId}`);
  assert.equal(result.baseline.workItem.isFallback, false);

  assert.equal(result.missingChain.workItem.workItemId, result.expected.workItemId);
  assert.equal(result.missingChain.workItem.isFallback, true, "missing optional relationship chain must be explicit");
  assert.equal(result.missingChain.workItem.blockerTitle, `待审核事项 ${result.expected.workItemId}`);
  assert.equal(result.missingChain.workItem.sourceId, "ST-003", "fallback must retain the selected item's own target source");
  assert.equal(result.missingChain.workItem.sourceVersion, result.expected.sourceVersion);
}

async function assertOverviewProductContract(page, marker) {
  await assertOverviewViewModelContract(page);
  const overviewText = await marker.textContent();
  for (const concept of [
    "新建研究",
    "ResearchCase 队列",
    "待审核关系",
    "新反面证据",
    "数据修订与缺口",
    "Provider 状态",
    "最近冻结版本",
  ]) {
    assert.ok(overviewText.includes(concept), `overview must visibly include ${concept}`);
  }

  const primaryActions = marker.locator("[data-primary-action]");
  assert.equal(await primaryActions.count(), 1, "overview must expose exactly one primary action");
  assert.equal((await primaryActions.first().textContent()).trim(), "新建研究", "overview primary action must be 新建研究");

  const caseRows = marker.locator("[data-research-case-row]");
  assert.ok(await caseRows.count() > 0, "overview must render at least one ResearchCase queue row");
  for (let index = 0; index < await caseRows.count(); index += 1) {
    const nextActions = caseRows.nth(index).locator("[data-next-action]");
    assert.equal(await nextActions.count(), 1, `ResearchCase row ${index + 1} must expose exactly one next action`);
    assert.ok((await nextActions.first().textContent()).trim(), `ResearchCase row ${index + 1} next action must be visible`);
  }

  const queueList = marker.getByRole("list", { name: "ResearchCase 队列" });
  assert.equal(await queueList.count(), 1, "ResearchCase queue must use list semantics");
  const selectedCase = queueList.getByRole("listitem");
  assert.equal(await selectedCase.count(), 1, "overview must have exactly one selected ResearchCase row");
  assert.equal(await selectedCase.locator('[aria-selected]').count(), 0, "plain ResearchCase rows must not use aria-selected");
  assert.equal(await selectedCase.getByText("当前研究案例", { exact: true }).count(), 1, "current case must have explicit screen-reader text");
  const selectedText = await selectedCase.textContent();
  for (const fixtureFact of [
    "AI 算力需求能否穿透至可验证的收入与持仓表达",
    "截至 2025-06-30，AI 算力资本开支能否通过已披露订单、交付与收入，形成可审计且仍需持续验证的产业链判断？",
    "截止日",
    "2025-06-30",
    "RS-2025-06-30-v3",
    "案例状态",
    "关系审核状态",
    "主要阻塞",
  ]) {
    assert.ok(selectedText.includes(fixtureFact), `selected ResearchCase must visibly include ${fixtureFact}`);
  }

  const supportLaneSourceIds = await marker.locator("[data-support-lane][data-source-id]").evaluateAll((elements) => (
    elements.map((element) => element.dataset.sourceId)
  ));
  for (const sourceId of ["RQ-001", "F-X-01", "M-NVDA-DC-REV", "PR-003,PR-004", "RS-2025-06-30-v3"]) {
    assert.ok(supportLaneSourceIds.includes(sourceId), `overview support lane must remain tied to fixture source ${sourceId}`);
  }

  const explicitStates = await marker.locator("[data-state-label]").allTextContents();
  assert.ok(explicitStates.length >= 5, "overview must label operational states in text, not color alone");
  assert.ok(explicitStates.every((label) => label.trim().length > 0), "overview state labels must be non-empty");

  const viewModel = await page.evaluate(() => window.PROTOTYPE_OVERVIEW.buildOverviewViewModel(window.PROTOTYPE_DATA));
  for (const localizedValue of [
    viewModel.caseStateLabel,
    viewModel.workItem.reviewStatusLabel,
    viewModel.contradiction.stateLabel,
    viewModel.metric.displayName,
    viewModel.metric.gapLabel,
    ...viewModel.providers.flatMap((provider) => [provider.displayName, provider.outcomeLabel, provider.detailLabel]),
  ]) {
    assert.ok(overviewText.includes(localizedValue), `overview must render localized view-model value: ${localizedValue}`);
  }
  for (const internalValue of [
    "awaiting_validation",
    "candidate",
    "quota_failure",
    "permission_gap",
    "Market data quota",
    "Licensed holdings feed",
    "Data Center revenue",
    "Daily call limit exceeded; no inferred replacement values",
    "Current credential lacks historical holdings permission",
  ]) {
    assert.ok(!overviewText.includes(internalValue), `overview must not expose internal value: ${internalValue}`);
  }

  for (const selector of [".case-facts dd", ".decision-source", ".lane-state", ".lane-detail"]) {
    const sizes = await marker.locator(selector).evaluateAll((elements) => elements.map((element) => parseFloat(getComputedStyle(element).fontSize)));
    assert.ok(sizes.every((size) => size >= 11), `${selector} operational metadata must be at least 11px`);
  }

  await page.setViewportSize({ width: 375, height: 812 });
  const narrowLayout = await page.evaluate(() => {
    const action = document.querySelector("[data-primary-action]").getBoundingClientRect();
    return {
      bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
      documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      actionLeft: action.left,
      actionRight: action.right,
      actionWidth: action.width,
      viewportWidth: window.innerWidth,
    };
  });
  assert.ok(narrowLayout.bodyOverflow <= 0 && narrowLayout.documentOverflow <= 0, `overview must fit 375px without horizontal overflow: ${JSON.stringify(narrowLayout)}`);
  assert.ok(narrowLayout.actionLeft >= 0 && narrowLayout.actionRight <= narrowLayout.viewportWidth && narrowLayout.actionWidth > 0, "primary action must remain visible at 375px");
  await page.setViewportSize({ width: 1600, height: 1000 });
}

async function assertBrowserContract(routes) {
  const { captureViewportPng, withPrototypeBrowser } = await import("./capture.mjs");
  const directNavLabels = new Map([
    ["overview", "工作台"],
    ["case", "研究案例"],
    ["library", "资料与知识"],
    ["data", "数据中心"],
    ["review", "审核中心"],
    ["versions", "监测与更新"],
  ]);

  await withPrototypeBrowser(async ({ baseURL, page }) => {
    await page.setViewportSize({ width: 1600, height: 1000 });

    for (const screen of routes) {
      await page.goto(`${baseURL}/?screen=${screen}`, { waitUntil: "networkidle" });
      const marker = page.locator(`[data-screen="${screen}"]`);
      await marker.waitFor({ state: "visible" });
      assert.equal(await marker.count(), 1, `${screen} must render exactly one [data-screen] marker`);

      const currentPageLinks = page.locator('.nav-rail a[aria-current="page"]');
      const expectedNavLabel = directNavLabels.get(screen);
      assert.equal(
        await currentPageLinks.count(),
        expectedNavLabel ? 1 : 0,
        `${screen} must mark a nav page current only when its route exactly matches a nav destination`,
      );
      if (expectedNavLabel) {
        assert.equal((await currentPageLinks.locator("span:last-child").textContent()).trim(), expectedNavLabel);
        assert.equal(await currentPageLinks.getAttribute("href"), `?screen=${screen}`);
      }

      const overflow = await page.evaluate(() => ({
        body: document.body.scrollWidth - document.body.clientWidth,
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }));
      assert.ok(overflow.body <= 0 && overflow.document <= 0, `${screen} has horizontal overflow: ${JSON.stringify(overflow)}`);

      const assessments = await page.locator("[data-evidence-assessment]").allTextContents();
      if (screen === "overview") {
        await assertOverviewProductContract(page, marker);
        assert.ok(assessments.length > 0, "overview must expose a non-vacuous [data-evidence-assessment] example");
        await captureViewportPng(page);
        await page.evaluate(() => { document.body.style.minHeight = "1001px"; });
        await assert.rejects(
          captureViewportPng(page),
          /Document height 1001 exceeds viewport height 1000/u,
        );
        await page.evaluate(() => { document.body.style.minHeight = ""; });
      }
      if (screen === "new-research") {
        await assertNewResearchProductContract(page, marker, baseURL);
      }
      if (screen === "plan") {
        await assertResearchPlanProductContract(page, marker);
      }
      if (screen === "case") {
        await assertCaseWorkbenchProductContract(page, marker);
      }
      if (screen === "graph") {
        await assertGraphProductContract(page, marker);
      }
      if (screen === "review") {
        await assertReviewProductContract(page, marker);
      }
      if (screen === "library") {
        await assertLibraryProductContract(page, marker);
      }
      for (const assessment of assessments) {
        for (const forbidden of assessmentScoringViolations(assessment)) {
          assert.doesNotMatch(assessment, forbidden, `${screen} evidence assessment contains forbidden scoring: ${forbidden}`);
        }
      }

      await page.setViewportSize({ width: 375, height: 812 });
      const mobileMenu = page.locator("details.mobile-nav");
      const mobileSummary = mobileMenu.locator("summary");
      assert.equal(await mobileMenu.count(), 1, `${screen} must expose one compact mobile navigation`);
      assert.ok(await mobileSummary.isVisible(), `${screen} mobile navigation control must be visible at 375px`);
      assert.equal((await mobileSummary.textContent()).trim(), "导航");
      assert.equal(await mobileSummary.evaluate((element) => element.tagName), "SUMMARY", "mobile navigation control must use native summary semantics");
      assert.ok(await mobileSummary.evaluate((element) => element.tabIndex >= 0), "mobile navigation control must be keyboard focusable");

      const closedOverflow = await page.evaluate(() => ({
        body: document.body.scrollWidth - document.body.clientWidth,
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }));
      assert.ok(closedOverflow.body <= 0 && closedOverflow.document <= 0, `${screen} closed mobile navigation must not overflow: ${JSON.stringify(closedOverflow)}`);

      await mobileSummary.focus();
      await page.keyboard.press("Enter");
      assert.ok(await mobileMenu.evaluate((element) => element.open), `${screen} mobile navigation must open from the keyboard`);
      const mobileNavigation = page.getByRole("navigation", { name: "移动端主导航" });
      assert.ok(await mobileNavigation.isVisible(), `${screen} opened mobile navigation must expose its named navigation region`);
      const mobileLinks = mobileNavigation.locator("a");
      assert.equal(await mobileLinks.count(), 6, `${screen} mobile navigation must contain all six exact destinations`);
      for (let index = 0; index < await mobileLinks.count(); index += 1) {
        assert.ok(await mobileLinks.nth(index).isVisible(), `${screen} mobile navigation link ${index + 1} must be visible when open`);
        assert.ok(await mobileLinks.nth(index).evaluate((element) => element.tabIndex >= 0), `${screen} mobile navigation link ${index + 1} must be keyboard reachable`);
      }
      const mobileCurrentPages = mobileNavigation.locator('a[aria-current="page"]');
      assert.equal(await mobileCurrentPages.count(), expectedNavLabel ? 1 : 0, `${screen} mobile current-page state must use exact route matching`);
      if (expectedNavLabel) {
        assert.equal((await mobileCurrentPages.locator("span:last-child").textContent()).trim(), expectedNavLabel);
        assert.equal(await mobileCurrentPages.getAttribute("href"), `?screen=${screen}`);
      }
      const openOverflow = await page.evaluate(() => ({
        body: document.body.scrollWidth - document.body.clientWidth,
        document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }));
      assert.ok(openOverflow.body <= 0 && openOverflow.document <= 0, `${screen} open mobile navigation must not overflow: ${JSON.stringify(openOverflow)}`);
      await mobileSummary.click();
      assert.ok(!await mobileMenu.evaluate((element) => element.open), `${screen} mobile navigation must close from pointer activation`);
      await page.setViewportSize({ width: 1600, height: 1000 });
    }
  });
}

async function main() {
  if (reexecWithCompatibleNode()) return;
  const routes = selectedRoutes(process.argv.slice(2));
  assertAssessmentScoringSemantics();
  await assertFixtureDataContract();
  await assertResearchPlanStateContract();
  await assertNewResearchStateDateContract();
  await assertNewResearchStateSessionContract();
  await assertSourceContract();
  await assertCaptureRemediationContract();
  await assertMalformedURLContract();
  await assertServerFilesystemBoundary();
  await assertCaptureDimensionAndOutputContract();
  await assertAtomicFinalCaptureContract();
  await assertFinalCaptureRegistryContract();
  await assertTeardownContract();
  await assertBrowserContract(routes);
  console.log(`PASS prototype contract: ${routes.join(", ")}`);
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    console.error(`FAIL prototype contract: ${error.message}`);
    process.exitCode = 1;
  });
}
