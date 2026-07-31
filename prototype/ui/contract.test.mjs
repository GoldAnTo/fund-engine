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
    funds: ["FUND-ETF-AI-INFRA", "FUND-SEMI-INDEX"],
    reviewQueue: ["RQ-001", "RQ-002"],
    statements: ["ST-001", "ST-002", "ST-003"],
    evidenceLinks: ["EL-001", "EL-002", "EL-003"],
  };
  for (const [group, requiredIds] of Object.entries(requiredRecordIds)) {
    for (const id of requiredIds) {
      assert.ok(indexes[group].has(id), `fixture ${group} must include ${id}`);
    }
  }

  assert.equal(data.theses.length, 3, "fixture must contain exactly three theses");
  for (const thesis of data.theses) {
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
  const requiredFiles = ["index.html", "styles.css", "data.js", "app.js", "capture.mjs"];
  for (const filename of requiredFiles) {
    await assert.doesNotReject(
      access(path.join(UI_DIR, filename)),
      `Missing prototype harness file: ${filename}`,
    );
  }

  const [html, app, styles, readme] = await Promise.all([
    readFile(path.join(UI_DIR, "index.html"), "utf8"),
    readFile(path.join(UI_DIR, "app.js"), "utf8"),
    readFile(path.join(UI_DIR, "styles.css"), "utf8"),
    readFile(path.join(UI_DIR, "README.md"), "utf8"),
  ]);

  assert.match(html, /<main\s+id=["']app["']/u, "index.html must expose <main id=\"app\">");
  assert.match(html, /<link[^>]+href=["']\.\/styles\.css["']/u, "index.html must load ./styles.css");
  assert.match(html, /<script[^>]+src=["']\.\/data\.js["'][^>]*><\/script>/u, "index.html must load classic ./data.js");
  assert.match(html, /<script[^>]+src=["']\.\/app\.js["'][^>]*><\/script>/u, "index.html must load classic ./app.js");

  for (const screen of REQUIRED_SCREENS) {
    assert.match(app, new RegExp(`["']${screen}["']\\s*:`, "u"), `SCREEN_RENDERERS must expose ${screen}`);
  }

  assert.equal((styles.match(/\.case-question\s*\{/gu) ?? []).length, 1, "overview must define .case-question only once");
  assert.doesNotMatch(styles, /border-left:\s*4px/u, "selected ResearchCase must not use a generic colored side stripe");
  assert.doesNotMatch(styles, /\.assessment\s*\{[^}]*border-left:/su, "AI assessment must not use a colored side stripe");
  for (const token of ["--action-surface", "--decision-surface", "--status-surface", "--gap-accent", "--provider-accent", "--frozen-accent"]) {
    assert.match(styles, new RegExp(`${token}:`, "u"), `styles must define semantic token ${token}`);
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
  for (const placeholder of REQUIRED_SCREENS.filter((screen) => !["overview", "new-research"].includes(screen))) {
    assert.throws(
      () => captureTargetForScreen(placeholder),
      /Capture renderer not implemented/u,
      `${placeholder} placeholder must not map to a final PNG`,
    );
  }
}

async function assertNewResearchProductContract(page, marker, baseURL) {
  const creationContext = await page.evaluate(() => ({
    view: window.PROTOTYPE_NEW_RESEARCH?.buildNewResearchViewModel(window.PROTOTYPE_DATA),
    fixtureCase: window.PROTOTYPE_DATA.case,
    snapshotCutoffs: window.PROTOTYPE_DATA.snapshots.map((snapshot) => snapshot.cutoff),
  }));
  assert.equal(creationContext.view.researchObject, creationContext.fixtureCase.researchObject, "creation screen must render researchObject directly from the case fixture");
  assert.equal(creationContext.view.phenomenon, creationContext.fixtureCase.phenomenon, "creation screen must render phenomenon directly from the case fixture");
  assert.deepEqual(creationContext.view.researchPeriod, creationContext.fixtureCase.researchPeriod, "creation screen must preserve the explicit researchPeriod fields");
  assert.equal(creationContext.view.studyRange, "2025-01-01 至 2027-12-31", "creation screen must format the approved independent research period");
  assert.ok(!creationContext.snapshotCutoffs.includes(creationContext.fixtureCase.researchPeriod.start), "creation period start must not come from snapshot cutoffs");
  assert.ok(!creationContext.snapshotCutoffs.includes(creationContext.fixtureCase.researchPeriod.end), "creation period end must not come from snapshot cutoffs");
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
  const fixtureTheses = await page.evaluate(() => window.PROTOTYPE_DATA.theses.map((thesis) => ({
    title: thesis.title,
    statement: thesis.statement,
    supportCondition: thesis.supportCondition,
    falsifier: thesis.falsifier,
    nextValidationEvent: thesis.nextValidationEvent,
  })));
  for (let index = 0; index < fixtureTheses.length; index += 1) {
    const editor = editors.nth(index);
    const editorText = await editor.textContent();
    for (const label of ["观察期间", "支持条件", "反证条件", "下一验证事件"]) {
      assert.ok(editorText.includes(label), `thesis editor ${index + 1} must include ${label}`);
    }
    for (const value of Object.values(fixtureTheses[index])) {
      assert.ok(editorText.includes(value) || await editor.locator(`[value="${value.replaceAll('"', '\\"')}"]`).count(), `thesis editor ${index + 1} must use fixture value ${value}`);
    }
    assert.equal(await editor.locator("label").count() >= 5, true, `thesis editor ${index + 1} fields must use labels`);
    assert.equal((await editor.locator("[data-ai-suggestion-label]").textContent()).trim(), "AI 草案 · 未经人工复核");
  }
  assert.equal(await form.getByRole("button", { name: "AI 协助拆分", exact: true }).count(), 1, "AI help must be a single secondary action");
  const addThesis = form.getByRole("button", { name: "新增命题", exact: true });
  assert.equal(await addThesis.count(), 1, "thesis actions must expose a real add control");
  assert.ok(await addThesis.isDisabled(), "add Thesis must be disabled when all three fixture theses are present");
  const addDescriptionId = await addThesis.getAttribute("aria-describedby");
  assert.ok(addDescriptionId, "disabled add Thesis control must reference an accessible explanation");
  assert.equal(
    (await form.locator(`#${addDescriptionId}`).textContent()).trim(),
    "已达 3 条上限；删除或合并后可新增",
    "disabled add Thesis explanation must state how the limit can be cleared",
  );
  const primaryActions = form.locator("[data-primary-action]");
  assert.equal(await primaryActions.count(), 1, "step 2 must expose one primary form action");
  assert.equal((await primaryActions.textContent()).trim(), "确认命题并继续");
  assert.equal(await primaryActions.getAttribute("type"), "submit");

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
  for (const forbidden of [
    "awaiting_validation", "pending_review", "reviewed", "quota_failure", "permission_gap",
    "Daily call limit exceeded", "Current credential lacks historical holdings permission",
  ]) {
    assert.ok(!pageText.includes(forbidden), `new-research must not expose internal value: ${forbidden}`);
  }

  await page.setViewportSize({ width: 375, height: 812 });
  const narrowLayout = await page.evaluate(() => ({
    bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
    documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    formBottom: document.querySelector('[data-primary-action]').getBoundingClientRect().bottom,
    documentHeight: document.documentElement.scrollHeight,
  }));
  assert.ok(narrowLayout.bodyOverflow <= 0 && narrowLayout.documentOverflow <= 0, `new-research must fit 375px without horizontal overflow: ${JSON.stringify(narrowLayout)}`);
  assert.ok(narrowLayout.formBottom > 0 && narrowLayout.formBottom <= narrowLayout.documentHeight, "new-research form action must remain reachable at 375px");
  await page.setViewportSize({ width: 1600, height: 1000 });

  async function assertStepThreeState(trigger) {
    await trigger();
    const url = new URL(page.url());
    assert.equal(url.searchParams.get("screen"), "new-research", "form progression must preserve the new-research route");
    assert.equal(url.searchParams.get("step"), "3", "form progression must set step=3");
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
  }

  await assertStepThreeState(async () => {
    await Promise.all([
      page.waitForURL((url) => url.searchParams.get("screen") === "new-research" && url.searchParams.get("step") === "3"),
      primaryActions.click(),
    ]);
  });

  await page.goto(`${baseURL}/?screen=new-research`, { waitUntil: "networkidle" });
  await assertStepThreeState(async () => {
    const observationPeriod = page.locator('[data-screen="new-research"] input[id$="-period"]').first();
    await observationPeriod.focus();
    await Promise.all([
      page.waitForURL((url) => url.searchParams.get("screen") === "new-research" && url.searchParams.get("step") === "3"),
      page.keyboard.press("Enter"),
    ]);
  });

  await page.goto(`${baseURL}/?screen=new-research&step=3`, { waitUntil: "networkidle" });
  await assertStepThreeState(async () => {});

  await page.goto(`${baseURL}/?screen=new-research&step=not-a-step`, { waitUntil: "networkidle" });
  const fallbackSteps = page.locator('[data-screen="new-research"] ol[data-research-steps] > li');
  assert.equal(await fallbackSteps.nth(1).getAttribute("aria-current"), "step", "invalid step must safely fall back to step 2");
  assert.equal(await fallbackSteps.nth(1).getAttribute("data-step-state"), "current");
  assert.equal(await fallbackSteps.nth(2).getAttribute("data-step-state"), "upcoming");
  assert.equal(
    (await page.locator('[data-screen="new-research"] [data-stage-status]').textContent()).trim(),
    "当前阶段 · 命题待人工确认",
    "invalid step header must align with the normalized step 2 fallback",
  );
  assert.equal(await page.locator('[data-screen="new-research"] form[aria-label="初始命题"]').count(), 1, "invalid step fallback must render the step 2 section");
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
