import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { chmod, lstat, mkdir, mkdtemp, readdir, readFile, rename, rm, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import test from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as supportModule from "./live-company-research-support.mjs";
import {
  assertLoopbackUrl,
  assertProcessesRunning,
  assertModelWorkspace,
  assertWorkspace,
  buildVerifierEnvironment,
  chooseRunError,
  createPrivateRuntime,
  createTrafficAudit,
  assertExactReviewSuccessor,
  parseVerifierArgs,
  removePrivateRuntime,
  startOwnedProcess,
  stopOwnedProcess,
  waitUntil,
} from "./live-company-research-support.mjs";

const MODEL_ARTIFACT_KINDS = [
  "evidence_index",
  "research_gaps",
  "business_map",
  "driver_map",
  "financial_bridge",
  "scenario_set",
  "judgment_context",
  "memo",
];

const MODEL_PROJECT_ID = "00000000-0000-4000-8000-000000000001";
const modelHash = (digit) => String(digit).repeat(64);
const canonicalPayloadHash = (value) => {
  const canonical = (item) => Array.isArray(item)
    ? item.map(canonical)
    : item !== null && typeof item === "object"
      ? Object.fromEntries(Object.keys(item).sort().map((key) => [key, canonical(item[key])]))
      : item;
  return createHash("sha256").update(JSON.stringify(canonical(value))).digest("hex");
};
const canonicalArtifactPayloadHash = (payload) => {
  const { _lineage: _lineage, ...domainPayload } = payload;
  return canonicalPayloadHash(domainPayload);
};
const modelUuid = (index) => `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
const modelSourceRef = () => ({
  raw_hash: modelHash("a"),
  source_locator: "Alphabet 2025 10-K, p. 1",
  source_role: "filing",
  source_url: "https://www.sec.gov/example",
});
const lineageSourceRef = () => ({ ...modelSourceRef(), fact_key: "alphabet.revenue.2025" });
const marketSourceRef = () => ({
  raw_hash: modelHash("c"),
  source_locator: "NASDAQ close 2026-08-29",
  source_role: "market_data",
  source_url: "https://example.test/market/alphabet",
});
const marketLineageSourceRef = () => ({ ...marketSourceRef(), fact_key: "market.price.googl" });
const reportedObservation = (key = "company.revenue", { unit = "USD", currency = "USD" } = {}) => ({
  key,
  value: "1",
  unit,
  currency,
  period: "2025",
  state: "reported",
  source_ref: { kind: "external", ...lineageSourceRef() },
  gap_key: null,
  assumption_key: null,
});

function modelWorkspace() {
  const artifacts = [];
  const marketBinding = {
    snapshot_id: modelUuid(70),
    snapshot_kind: "price",
    snapshot_content_hash: modelHash("d"),
    security_external_key: "NASDAQ:GOOGL",
    source_ref: marketLineageSourceRef(),
    capture_envelope_id: modelUuid(71),
    capture_content_hash: modelHash("e"),
    provenance_role: "primary",
    provider_policy_version: "fixture-market.v1",
    raw_components: [{
      raw_file: "alphabet-price.json",
      raw_hash: modelHash("c"),
      source_url: "https://example.test/market/alphabet",
      source_locator: "NASDAQ close 2026-08-29",
    }],
  };
  const artifact = (kind, payload) => {
    const index = MODEL_ARTIFACT_KINDS.indexOf(kind) + 10;
    const next = {
      schema_version: "underwriting.v1",
      id: modelUuid(index),
      project_id: MODEL_PROJECT_ID,
      kind,
      version: kind === "evidence_index" ? 8 : 1,
      input_hash: modelHash(String((index + 1) % 10)),
      content_hash: modelHash(String((index + 2) % 10)),
      payload,
      source_refs: [
        modelSourceRef(),
        ...(!["evidence_index", "research_gaps"].includes(kind) ? [marketSourceRef()] : []),
      ],
    };
    artifacts.push(next);
    return next;
  };
  const parent = (kind) => {
    const head = artifacts.find((item) => item.kind === kind);
    return { artifact_id: head.id, artifact_kind: kind, content_hash: head.content_hash };
  };
  const lineage = (kinds) => ({
    artifact_refs: kinds.map(parent),
    market_snapshot_ids: [marketBinding.snapshot_id],
    market_snapshot_bindings: [structuredClone(marketBinding)],
  });
  const evidence = artifact("evidence_index", {
    fixture_content_hash: modelHash("b"),
    cutoff: "2026-08-30T00:00:00Z",
    company_external_key: "US:ALPHABET:COMPANY",
    security_external_keys: ["US:GOOG", "US:GOOGL"],
    facts: [{
      fact_key: "alphabet.revenue.2025",
      company_external_key: "US:ALPHABET:COMPANY",
      business_module: "search",
      metric_key: "company.revenue",
      observation: reportedObservation(),
      period_start: "2025-01-01",
      period_end: "2025-12-31",
      published_at: "2026-02-01T00:00:00Z",
      available_at: "2026-02-01T00:00:00Z",
      source_role: "filing",
      source_url: "https://www.sec.gov/example",
      source_locator: "Alphabet 2025 10-K, p. 1",
      raw_hash: modelHash("a"),
      review_decision: "confirmed",
    }],
  });
  artifact("research_gaps", {
    fixture_content_hash: modelHash("b"),
    company_external_key: "US:ALPHABET:COMPANY",
    gaps: [{ gap_key: "missing-segment-margin", business_module: "cloud", reason: "Not disclosed" }],
  });
  const business = artifact("business_map", {
    modules: [{
      module_key: "search",
      revenue_sources: ["advertising"],
      cost_structure: ["traffic acquisition"],
      capital_needs: ["data centers"],
      fact_refs: [lineageSourceRef()],
      gap_refs: ["missing-segment-margin"],
      classified_evidence: [{
        fact_ref: lineageSourceRef(), metric_key: "company.revenue", category: "revenue",
        observation: reportedObservation(), period_start: "2025-01-01", period_end: "2025-12-31",
      }],
    }],
    _lineage: lineage(["evidence_index"]),
  });
  const driver = artifact("driver_map", {
    drivers: [{
      driver_key: "search-demand",
      module_key: "search",
      fact_refs: [lineageSourceRef()],
      assumption_refs: [],
      equation: "revenue = demand",
      output_metric: "company.revenue",
      equation_id: "search-revenue-v1",
      values: [reportedObservation()],
      assumption_rationale: null,
      assumption_equation: null,
    }],
    _lineage: lineage(["business_map"]),
  });
  const bridgeObservationKeys = [
    "revenue", "operating_income", "cash_tax_rate", "depreciation", "capex",
    "working_capital_change", "fcff",
  ];
  artifact("financial_bridge", {
    rows: Array.from({ length: 5 }, (_, index) => Object.fromEntries([
      ["period", String(2025 + index)],
      ...bridgeObservationKeys.map((key) => [key, reportedObservation(`company.${key}`)]),
      ["fact_refs", [lineageSourceRef()]],
      ["assumption_refs", []],
    ])),
    _lineage: lineage(["driver_map"]),
  });
  artifact("scenario_set", {
    scenarios: ["base", "bull", "bear"].map((scenarioId) => ({
      scenario_id: scenarioId,
      mechanism_id: `${scenarioId}-mechanism`,
      driver_overrides: [{
        driver_key: "search-demand",
        observation: reportedObservation("search-demand", { unit: "multiplier", currency: "N/A" }),
        rationale: null, equation: null,
      }],
    })),
    _lineage: lineage(["driver_map"]),
  });
  const judgment = artifact("judgment_context", {
    operating_baseline_available: true,
    financial_bridge_closed: false,
    market_security_bridge_available: true,
    strongest_counterevidence: [lineageSourceRef()],
    next_verification_events: ["Next 10-Q"],
    _lineage: lineage([
      "evidence_index", "business_map", "driver_map", "financial_bridge", "scenario_set", "research_gaps",
    ]),
  });
  artifact("memo", {
    assessment_status: "not_answerable",
    business_map_ref: { artifact_kind: "business_map", content_hash: canonicalArtifactPayloadHash(business.payload) },
    driver_map_ref: { artifact_kind: "driver_map", content_hash: canonicalArtifactPayloadHash(driver.payload) },
    financial_bridge_ref: { artifact_kind: "financial_bridge", content_hash: canonicalArtifactPayloadHash(artifacts.find((item) => item.kind === "financial_bridge").payload) },
    scenario_set_ref: { artifact_kind: "scenario_set", content_hash: canonicalArtifactPayloadHash(artifacts.find((item) => item.kind === "scenario_set").payload) },
    valuation_set_ref: null,
    gap_keys: ["missing-segment-margin"],
    strongest_counterevidence: [lineageSourceRef()],
    next_verification_events: ["Next 10-Q"],
    candidate_status: "machine_draft",
    _lineage: {
      artifact_refs: [{ artifact_id: judgment.id, artifact_kind: "judgment_context", content_hash: judgment.content_hash }],
      market_snapshot_ids: [marketBinding.snapshot_id],
      market_snapshot_bindings: [structuredClone(marketBinding)],
    },
  });
  return {
    schema_version: "underwriting.v1",
    project_id: MODEL_PROJECT_ID,
    preparation: { status: "awaiting_judgment_review", progress: 85 },
    artifacts,
    expectedEvidence: structuredClone(evidence),
  };
}

test("model workspace requires the exact unique not-answerable artifact set", () => {
  const { expectedEvidence, ...workspace } = modelWorkspace();
  assert.strictEqual(assertModelWorkspace(workspace, { evidenceArtifact: expectedEvidence }), workspace);

  for (const artifacts of [
    workspace.artifacts.slice(1),
    [...workspace.artifacts, { ...workspace.artifacts[0], id: "duplicate-kind" }],
    [...workspace.artifacts, {
      ...workspace.artifacts[0], id: "valuation-1", kind: "valuation_set",
    }],
  ]) {
    assert.throws(
      () => assertModelWorkspace({ ...workspace, artifacts }, { evidenceArtifact: expectedEvidence }),
      /model artifact set/u,
    );
  }
});

test("model workspace requires exact state and valid artifact heads", () => {
  const { expectedEvidence, ...workspace } = modelWorkspace();
  for (const [label, mutate, pattern] of [
    ["status", (next) => { next.preparation.status = "building_model"; }, /model workspace state/u],
    ["progress", (next) => { next.preparation.progress = 84; }, /model workspace state/u],
    ["schema", (next) => { next.artifacts[0].schema_version = "underwriting.v2"; }, /artifact identity/u],
    ["invalid id", (next) => { next.artifacts[0].id = "not-a-uuid"; }, /artifact identity/u],
    ["duplicate id", (next) => { next.artifacts[1].id = next.artifacts[0].id; }, /artifact identity/u],
    ["foreign project", (next) => { next.artifacts[0].project_id = "project-2"; }, /artifact identity/u],
    ["zero version", (next) => { next.artifacts[0].version = 0; }, /artifact version/u],
    ["fractional version", (next) => { next.artifacts[0].version = 1.5; }, /artifact version/u],
    ["missing payload", (next) => { delete next.artifacts[0].payload; }, /artifact (?:payload|envelope)/u],
    ["array payload", (next) => { next.artifacts[0].payload = []; }, /artifact payload/u],
  ]) {
    const next = structuredClone(workspace);
    mutate(next);
    assert.throws(() => assertModelWorkspace(next, { evidenceArtifact: expectedEvidence }), pattern, label);
  }
});

test("model workspace keeps every not-answerable memo investment field closed", () => {
  const { expectedEvidence, ...workspace } = modelWorkspace();
  for (const [field, value] of [
    ["candidate_status", "human_confirmed"],
    ["assessment_status", "answerable"],
    ["valuation_set_ref", { artifact_kind: "valuation_set", content_hash: "hash" }],
    ["direction", "provisional_bullish"],
    ["confidence", "high"],
    ["target_value", 200],
    ["expected_return", 0.2],
  ]) {
    const next = structuredClone(workspace);
    next.artifacts.find((artifact) => artifact.kind === "memo").payload[field] = value;
    assert.throws(() => assertModelWorkspace(next, { evidenceArtifact: expectedEvidence }), /not-answerable memo contract/u, field);
  }
  assert.strictEqual(assertModelWorkspace(workspace, { evidenceArtifact: expectedEvidence }), workspace);
  for (const field of ["direction", "confidence", "target_value", "expected_return"]) {
    const next = structuredClone(workspace);
    next.artifacts.find((artifact) => artifact.kind === "memo").payload[field] = null;
    assert.throws(
      () => assertModelWorkspace(next, { evidenceArtifact: expectedEvidence }),
      /memo payload/u,
      field,
    );
  }
});

test("model workspace validates closed artifact envelopes, hashes, sources, and payloads", () => {
  const { expectedEvidence, ...workspace } = modelWorkspace();
  for (const [label, mutate, pattern] of [
    ["extra top-level key", (next) => { next.artifacts[0].fabricated = true; }, /artifact envelope/u],
    ["missing input hash", (next) => { delete next.artifacts[0].input_hash; }, /artifact envelope/u],
    ["missing content hash", (next) => { delete next.artifacts[0].content_hash; }, /artifact envelope/u],
    ["bad input hash", (next) => { next.artifacts[0].input_hash = "not-a-hash"; }, /artifact hash/u],
    ["bad content hash", (next) => { next.artifacts[0].content_hash = "not-a-hash"; }, /artifact hash/u],
    ["empty source refs", (next) => { next.artifacts[0].source_refs = []; }, /artifact source refs/u],
    ["source ref extra key", (next) => { next.artifacts[0].source_refs[0].secret = "junk"; }, /artifact source refs/u],
    ["source ref bad hash", (next) => { next.artifacts[0].source_refs[0].raw_hash = "junk"; }, /artifact source refs/u],
    ["duplicate source ref", (next) => { next.artifacts[0].source_refs.push(structuredClone(next.artifacts[0].source_refs[0])); }, /artifact source refs/u],
    ["drift downstream source refs", (next) => { next.artifacts.find((item) => item.kind === "driver_map").source_refs[0].source_locator = "different but valid"; }, /model artifact source refs/u],
    ["fabricated evidence payload", (next) => { next.artifacts.find((item) => item.kind === "evidence_index").payload = {}; }, /reviewed evidence|evidence_index payload/u],
    ["fabricated gaps payload", (next) => { next.artifacts.find((item) => item.kind === "research_gaps").payload = {}; }, /research_gaps payload/u],
    ["fabricated business payload", (next) => { next.artifacts.find((item) => item.kind === "business_map").payload = {}; }, /business_map payload/u],
    ["junk driver payload", (next) => { next.artifacts.find((item) => item.kind === "driver_map").payload.drivers = [{}]; }, /driver_map payload/u],
    ["junk financial payload", (next) => { next.artifacts.find((item) => item.kind === "financial_bridge").payload.rows = "junk"; }, /financial_bridge payload/u],
    ["junk scenario payload", (next) => { next.artifacts.find((item) => item.kind === "scenario_set").payload.scenarios = []; }, /scenario_set payload/u],
    ["invalid scenario observation", (next) => { next.artifacts.find((item) => item.kind === "scenario_set").payload.scenarios[0].driver_overrides[0].observation.unit = "USD"; }, /scenario_set payload/u],
    ["junk judgment payload", (next) => { next.artifacts.find((item) => item.kind === "judgment_context").payload.operating_baseline_available = "yes"; }, /judgment_context payload/u],
  ]) {
    const next = structuredClone(workspace);
    mutate(next);
    assert.throws(() => assertModelWorkspace(next, { evidenceArtifact: expectedEvidence }), pattern, label);
  }
});

test("model workspace requires exact typed memo reference envelopes", () => {
  for (const mutate of [
    (ref) => { delete ref.content_hash; },
    (ref) => { ref.content_hash = "junk"; },
    (ref) => { ref.artifact_kind = "driver_map"; },
    (ref) => { ref.fabricated = true; },
  ]) {
    const { expectedEvidence, ...workspace } = modelWorkspace();
    const ref = workspace.artifacts.find((artifact) => artifact.kind === "memo")
      .payload.business_map_ref;
    mutate(ref);
    assert.throws(
      () => assertModelWorkspace(workspace, { evidenceArtifact: expectedEvidence }),
      /not-answerable memo contract/u,
    );
  }
});

test("model workspace requires exact lineage-bound derived observation provenance", () => {
  const { expectedEvidence, ...workspace } = modelWorkspace();
  const bridge = workspace.artifacts.find((artifact) => artifact.kind === "financial_bridge");
  const memo = workspace.artifacts.find((artifact) => artifact.kind === "memo");
  bridge.payload.rows[0].fcff = {
    ...reportedObservation("fcff"),
    state: "derived",
    source_ref: {
      kind: "artifact_computation",
      artifact_refs: structuredClone(bridge.payload._lineage.artifact_refs),
      market_snapshot_ids: structuredClone(bridge.payload._lineage.market_snapshot_ids),
      equation_id: "financial_bridge.fcff.v1",
    },
  };
  memo.payload.financial_bridge_ref.content_hash = canonicalArtifactPayloadHash(bridge.payload);
  assert.strictEqual(
    assertModelWorkspace(workspace, { evidenceArtifact: expectedEvidence }),
    workspace,
  );

  for (const [label, mutate] of [
    ["extra computation key", (source) => { source.fabricated = true; }],
    ["missing equation", (source) => { delete source.equation_id; }],
    ["wrong parent hash", (source) => { source.artifact_refs[0].content_hash = modelHash("f"); }],
    ["wrong market lineage", (source) => { source.market_snapshot_ids = [modelUuid(97)]; }],
  ]) {
    const next = structuredClone(workspace);
    const source = next.artifacts.find((artifact) => artifact.kind === "financial_bridge")
      .payload.rows[0].fcff.source_ref;
    mutate(source);
    assert.throws(
      () => assertModelWorkspace(next, { evidenceArtifact: expectedEvidence }),
      /financial_bridge payload/u,
      label,
    );
  }
});

test("model workspace binds every lineage parent and the final reviewed evidence head", () => {
  const { expectedEvidence, ...workspace } = modelWorkspace();
  for (const [label, mutate] of [
    ["wrong lineage id", (next) => { next.artifacts.find((item) => item.kind === "business_map").payload._lineage.artifact_refs[0].artifact_id = modelUuid(99); }],
    ["wrong lineage hash", (next) => { next.artifacts.find((item) => item.kind === "memo").payload._lineage.artifact_refs[0].content_hash = modelHash("f"); }],
    ["wrong lineage order", (next) => { next.artifacts.find((item) => item.kind === "judgment_context").payload._lineage.artifact_refs.reverse(); }],
  ]) {
    const next = structuredClone(workspace);
    mutate(next);
    assert.throws(() => assertModelWorkspace(next, { evidenceArtifact: expectedEvidence }), /artifact lineage/u, label);
  }
  const wrongEvidence = structuredClone(expectedEvidence);
  wrongEvidence.id = modelUuid(98);
  assert.throws(
    () => assertModelWorkspace(workspace, { evidenceArtifact: wrongEvidence }),
    /reviewed evidence head/u,
  );
  assert.throws(() => assertModelWorkspace(workspace), /expected reviewed evidence/u);
});

test("model workspace validates full shared market bindings and governed model sources", () => {
  for (const [label, mutate, pattern] of [
    ["extra binding field", (next) => {
      for (const artifact of next.artifacts.filter((item) => item.payload._lineage)) {
        artifact.payload._lineage.market_snapshot_bindings[0].fabricated = true;
      }
    }, /artifact lineage/u],
    ["bad binding hash", (next) => {
      for (const artifact of next.artifacts.filter((item) => item.payload._lineage)) {
        artifact.payload._lineage.market_snapshot_bindings[0].capture_content_hash = "junk";
      }
    }, /artifact lineage/u],
    ["cross-artifact binding drift", (next) => {
      next.artifacts.find((item) => item.kind === "driver_map")
        .payload._lineage.market_snapshot_bindings[0].source_ref.source_locator = "drifted";
    }, /artifact lineage/u],
    ["consistent fabricated model source", (next) => {
      const fabricated = {
        raw_hash: modelHash("f"), source_locator: "fabricated", source_role: "third_party",
        source_url: "https://attacker.invalid/source",
      };
      for (const artifact of next.artifacts.filter((item) => [
        "business_map", "driver_map", "financial_bridge", "scenario_set", "judgment_context", "memo",
      ].includes(item.kind))) artifact.source_refs.push(structuredClone(fabricated));
    }, /model artifact source refs/u],
  ]) {
    const { expectedEvidence, ...workspace } = modelWorkspace();
    mutate(workspace);
    assert.throws(
      () => assertModelWorkspace(workspace, { evidenceArtifact: expectedEvidence }),
      pattern,
      label,
    );
  }
});

test("model workspace rejects non-canonical evidence dates and decimals even when the expected head matches", () => {
  for (const [label, mutate] of [
    ["date", (evidence) => { evidence.payload.cutoff = "2026-08-30T00:00:00"; }],
    ["decimal", (evidence) => { evidence.payload.facts[0].observation.value = "NaN"; }],
  ]) {
    const { expectedEvidence, ...workspace } = modelWorkspace();
    const evidence = workspace.artifacts.find((artifact) => artifact.kind === "evidence_index");
    mutate(evidence);
    mutate(expectedEvidence);
    assert.throws(
      () => assertModelWorkspace(workspace, { evidenceArtifact: expectedEvidence }),
      /reviewed evidence head|evidence_index payload/u,
      label,
    );
  }
});

test("traffic audit retains only local API method, path, and status metadata", () => {
  const audit = createTrafficAudit("http://127.0.0.1:42000", "must-not-be-recorded");
  audit.recordRequest(
    "POST",
    "http://127.0.0.1:42000/api/underwriting/v1/product/company-research/preview?secret=query",
    { authorization: "Bearer must-not-be-recorded" },
    "must-not-be-recorded",
  );
  audit.recordResponse(
    200,
    "POST",
    "http://127.0.0.1:42000/api/underwriting/v1/product/company-research/preview?secret=query",
  );

  assert.deepEqual(audit.snapshot(), {
    requests: [["POST", "/api/underwriting/v1/product/company-research/preview"]],
    responses: [[200, "POST", "/api/underwriting/v1/product/company-research/preview"]],
  });
  assert.equal(JSON.stringify(audit.snapshot()).includes("must-not-be-recorded"), false);
  assert.equal(JSON.stringify(audit.snapshot()).includes("secret=query"), false);
});

test("traffic audit rejects external requests, request failures, and non-2xx API responses", () => {
  const audit = createTrafficAudit("http://127.0.0.1:42000");

  assert.throws(
    () => audit.recordRequest("GET", "https://example.com/tracker", { authorization: "secret" }),
    /external request/u,
  );
  assert.throws(
    () => audit.recordRequestFailure("GET", "http://127.0.0.1:42000/api/x", "secret failure text"),
    /API request failed.*GET \/api\/x/u,
  );
  assert.throws(
    () => audit.recordResponse(500, "GET", "http://127.0.0.1:42000/api/x"),
    /API response 500 GET \/api\/x/u,
  );
});

test("traffic audit enforces exact singleton request cardinality", () => {
  const pathname = "/api/underwriting/v1/product/company-research/initializations";
  const audit = createTrafficAudit("http://127.0.0.1:42000");
  assert.throws(() => audit.assertSingleton("POST", pathname), /saw 0/u);

  audit.recordRequest("POST", `http://127.0.0.1:42000${pathname}`);
  assert.doesNotThrow(() => audit.assertSingleton("POST", pathname));
  assert.equal(audit.requestCount("POST", pathname), 1);

  audit.recordRequest("POST", `http://127.0.0.1:42000${pathname}`);
  assert.throws(() => audit.assertSingleton("POST", pathname), /saw 2/u);
  assert.equal(audit.requestCount("POST", pathname), 2);
});

test("pending observers expose late duplicate writes only after a bounded drain", async () => {
  assert.equal(typeof supportModule.createPendingObserverTracker, "function");
  const pathname = "/api/underwriting/v1/product/company-research/initializations";
  const audit = createTrafficAudit("http://127.0.0.1:42000");
  const observers = supportModule.createPendingObserverTracker();
  audit.recordRequest("POST", `http://127.0.0.1:42000${pathname}`);
  assert.doesNotThrow(() => audit.assertSingleton("POST", pathname));
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  observers.track(async () => {
    await gate;
    audit.recordRequest("POST", `http://127.0.0.1:42000${pathname}`);
  });
  release();
  await observers.drain({ timeoutMs: 100 });
  assert.throws(() => audit.assertSingleton("POST", pathname), /saw 2/u);
});

test("pending observer drain is bounded and reports parse failure without body diagnostics", async () => {
  assert.equal(typeof supportModule.createPendingObserverTracker, "function");
  const observers = supportModule.createPendingObserverTracker();
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  observers.track(async () => {
    await gate;
    throw new Error("secret response body must not escape");
  });
  const draining = observers.drain({ timeoutMs: 100 });
  release();
  await assert.rejects(draining, (error) => {
    assert.match(error.message, /pending browser observer failed/u);
    assert.equal(error.message.includes("secret response body"), false);
    return true;
  });

  const stalled = supportModule.createPendingObserverTracker();
  stalled.track(() => new Promise(() => {}));
  await assert.rejects(stalled.drain({ timeoutMs: 10 }), /pending browser observer drain timed out/u);
});

test("traffic audit authorizes only exact local HTTP and WebSocket origins before send", () => {
  const audit = createTrafficAudit("http://127.0.0.1:42000");
  assert.doesNotThrow(() => audit.assertAllowedRequest("http://127.0.0.1:42000/research/new"));
  assert.doesNotThrow(() => audit.assertAllowedRequest("data:text/plain,local"));
  assert.doesNotThrow(() => audit.assertAllowedWebSocket("ws://127.0.0.1:42000/hmr"));

  for (const url of [
    "https://127.0.0.1:42000/research/new",
    "http://127.0.0.1:42001/research/new",
    "http://localhost:42000/research/new",
    "http://2130706433:42000/research/new",
    "https://example.com/collect?secret=hidden",
  ]) assert.throws(() => audit.assertAllowedRequest(url), /external request/u);
  for (const url of [
    "wss://127.0.0.1:42000/hmr",
    "ws://127.0.0.1:42001/hmr",
    "ws://example.com/socket?secret=hidden",
  ]) assert.throws(() => audit.assertAllowedWebSocket(url), /external WebSocket/u);
});

test("browser failure collector drains failures recorded after an earlier clean check", () => {
  assert.equal(typeof supportModule.createBrowserFailureCollector, "function");
  const failures = supportModule.createBrowserFailureCollector();
  assert.doesNotThrow(() => failures.throwIfAny());
  failures.capture(() => { throw new Error("late sanitized browser failure"); });
  assert.throws(() => failures.throwIfAny(), /late sanitized browser failure/u);
});

function reviewArtifacts() {
  const evidence = modelWorkspace().expectedEvidence;
  const before = structuredClone(evidence);
  before.id = modelUuid(30);
  before.version = 7;
  before.input_hash = modelHash("1");
  before.content_hash = modelHash("2");
  delete before.payload.facts[0].review_decision;
  const second = structuredClone(before.payload.facts[0]);
  second.fact_key = "alphabet.margin.2025";
  second.metric_key = "company.margin";
  second.observation.key = "company.margin";
  second.observation.source_ref.fact_key = second.fact_key;
  before.payload.facts.push(second);
  const after = structuredClone(before);
  after.id = modelUuid(31);
  after.version = 8;
  after.payload.facts[0].review_decision = "confirmed";
  after.input_hash = canonicalPayloadHash({
    parent: before.content_hash,
    fact_key: before.payload.facts[0].fact_key,
    decision: "confirmed",
  });
  after.content_hash = modelHash("4");
  return { before, after };
}

test("review successor advances exactly one fact and one version", () => {
  const { before, after } = reviewArtifacts();
  const factKey = before.payload.facts[0].fact_key;

  assert.strictEqual(assertExactReviewSuccessor(before, after, factKey), after);
  assert.throws(() => assertExactReviewSuccessor(before, { ...after, id: before.id }, factKey), /identity/u);
  assert.throws(() => assertExactReviewSuccessor(before, { ...after, version: 9 }, factKey), /version/u);
  assert.throws(() => assertExactReviewSuccessor(before, {
    ...after,
    payload: { facts: after.payload.facts.map((fact) => ({ ...fact, review_decision: "confirmed" })) },
  }, factKey), /unrelated fact|fact payload/u);
  for (const [field, value] of [
    ["schema_version", "underwriting.v2"],
    ["project_id", modelUuid(90)],
    ["kind", "research_gaps"],
    ["source_refs", [{ ...modelSourceRef(), source_role: "company_material" }]],
  ]) {
    assert.throws(
      () => assertExactReviewSuccessor(before, { ...after, [field]: value }, factKey),
      new RegExp(field.replace("_", " "), "u"),
    );
  }
});

test("review successor preserves exact fact cardinality, identities, and contents", () => {
  const { before, after: validAfter } = reviewArtifacts();
  const factKey = before.payload.facts[0].fact_key;

  for (const invalidAfter of [
    { ...validAfter, payload: { facts: validAfter.payload.facts.slice(0, 1) } },
    { ...validAfter, payload: { facts: [...validAfter.payload.facts, structuredClone(validAfter.payload.facts[1])] } },
    { ...validAfter, payload: { facts: [validAfter.payload.facts[0], { ...validAfter.payload.facts[1], fact_key: factKey }] } },
    { ...validAfter, payload: { facts: [validAfter.payload.facts[0], { ...validAfter.payload.facts[1], metric_key: "changed" }] } },
    { ...validAfter, payload: { facts: [...validAfter.payload.facts].reverse() } },
    { ...validAfter, payload: { ...validAfter.payload, unrelated: "drift" } },
  ]) {
    assert.throws(() => assertExactReviewSuccessor(before, invalidAfter, factKey), /fact|unrelated/u);
  }
  assert.throws(() => assertExactReviewSuccessor(before, validAfter, "missing"), /reviewed fact/u);
});

test("review successor requires the exact authenticated artifact envelope and new hashes", () => {
  const { before, after } = reviewArtifacts();
  const factKey = before.payload.facts[0].fact_key;
  for (const [label, mutate, pattern] of [
    ["extra key", (next) => { next.untrusted = true; }, /review artifact envelope/u],
    ["invalid id", (next) => { next.id = "successor"; }, /review artifact identity/u],
    ["missing input hash", (next) => { delete next.input_hash; }, /review artifact envelope/u],
    ["bad content hash", (next) => { next.content_hash = "junk"; }, /review artifact hash/u],
    ["stable input hash", (next) => { next.input_hash = before.input_hash; }, /input hash/u],
    ["stable content hash", (next) => { next.content_hash = before.content_hash; }, /content hash/u],
    ["fresh but incorrect input hash", (next) => { next.input_hash = modelHash("5"); }, /input hash/u],
    ["source ref extra key", (next) => { next.source_refs[0].extra = true; }, /source refs/u],
  ]) {
    const next = structuredClone(after);
    mutate(next);
    assert.throws(() => assertExactReviewSuccessor(before, next, factKey), pattern, label);
  }
});

test("authenticated artifact binding compares the complete reviewed head", () => {
  assert.equal(typeof supportModule.assertSameArtifactHead, "function");
  const { after } = reviewArtifacts();
  assert.strictEqual(supportModule.assertSameArtifactHead(after, structuredClone(after)), after);
  for (const [label, mutate] of [
    ["id", (next) => { next.id = modelUuid(77); }],
    ["input hash", (next) => { next.input_hash = modelHash("8"); }],
    ["content hash", (next) => { next.content_hash = modelHash("9"); }],
    ["source refs", (next) => { next.source_refs[0].source_locator = "different"; }],
    ["payload", (next) => { next.payload.facts[0].metric_key = "different"; }],
  ]) {
    const next = structuredClone(after);
    mutate(next);
    assert.throws(() => supportModule.assertSameArtifactHead(after, next), /artifact head mismatch/u, label);
  }
});

test("Alphabet binding validates every expected foundation security identity", () => {
  assert.equal(typeof supportModule.assertAlphabetIdentityBinding, "function");
  const foundation = {
    schema_version: "product.foundation-identities.v1",
    content_hash: "a".repeat(64),
    companies: [{ external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc." }],
    securities: [
      { external_key: "NASDAQ:GOOGL", company_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Class A", symbol: "GOOGL", exchange: "NASDAQ", currency: "USD", share_class: "Class A" },
      { external_key: "NASDAQ:GOOG", company_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Class C", symbol: "GOOG", exchange: "NASDAQ", currency: "USD", share_class: "Class C" },
    ],
  };
  const company = { schema_version: "underwriting.v1", kind: "company", object_id: "00000000-0000-4000-8000-000000000001", external_key: "US:ALPHABET:COMPANY", canonical_name: "Alphabet Inc." };
  const securities = foundation.securities.map((security, index) => ({
    schema_version: "underwriting.v1",
    kind: "security",
    object_id: `00000000-0000-4000-8000-00000000000${index + 2}`,
    external_key: security.external_key,
    canonical_name: security.canonical_name,
    symbol: security.symbol,
    exchange: security.exchange,
    share_class: security.share_class,
    trading_currency: security.currency,
  }));
  const search = { items: [company, ...securities] };
  const preview = {
    company: { ...company },
    securities: securities.map(({ kind: _kind, ...security }) => security),
  };
  assert.strictEqual(supportModule.assertAlphabetIdentityBinding({ foundation, search, preview }), company);

  for (const [field, value] of [
    ["external_key", "NASDAQ:WRONG"], ["object_id", company.object_id],
    ["symbol", "WRONG"], ["share_class", "Wrong"], ["exchange", "NYSE"],
    ["trading_currency", "CNY"],
  ]) {
    const mutated = structuredClone(preview);
    mutated.securities[0][field] = value;
    assert.throws(
      () => supportModule.assertAlphabetIdentityBinding({ foundation, search, preview: mutated }),
      /security identity/u,
    );
  }
});

function createTerminationHarness(outcomes) {
  let now = 0;
  let nextTimerId = 1;
  let state;
  const signals = [];
  const timers = new Map();

  const schedule = (delayMs, callback) => {
    const id = nextTimerId;
    nextTimerId += 1;
    timers.set(id, { at: now + delayMs, callback });
  };
  const pump = () => {
    if (state.effect === "signal") {
      signals.push(state.signal);
      const accepted = outcomes.shift();
      state = supportModule.advanceTerminationState(state, {
        type: accepted ? "signal-accepted" : "signal-refused",
      });
      pump();
    } else if (state.effect === "wait") {
      schedule(state.timeoutMs, () => {
        state = supportModule.advanceTerminationState(state, { type: "deadline" });
        pump();
      });
    }
  };

  state = supportModule.advanceTerminationState(undefined, { type: "start" });
  pump();
  return {
    advanceBy(milliseconds) {
      const target = now + milliseconds;
      while (true) {
        const due = [...timers.entries()]
          .filter(([, timer]) => timer.at <= target)
          .sort((left, right) => left[1].at - right[1].at)[0];
        if (!due) break;
        const [id, timer] = due;
        timers.delete(id);
        now = timer.at;
        timer.callback();
      }
      now = target;
    },
    get pendingTimers() { return timers.size; },
    get state() { return state; },
    signals,
  };
}

const TEST_PYTHON = execFileSync("which", ["python3"], { encoding: "utf8" }).trim();
const TEST_CLEANUP_HELPER = Object.freeze({
  pythonExecutable: TEST_PYTHON,
  helperPath: path.resolve(process.cwd(), "../backend/app/scripts/remove_private_runtime_contents.py"),
});

function createTestRuntime() {
  return createPrivateRuntime({ cleanupHelper: TEST_CLEANUP_HELPER });
}

function liveStackProcessIds() {
  const output = execFileSync("ps", ["-axo", "pid=,command="], { encoding: "utf8" });
  const markers = [
    "app.scripts.run_company_research_worker --loop --poll-seconds 0.1",
    "uvicorn app.main:app --host 127.0.0.1 --port",
    "node_modules/vite/bin/vite.js --host 127.0.0.1 --port",
  ];
  return new Set(output.split("\n").filter((line) => markers.some((marker) => line.includes(marker)))
    .map((line) => line.trim().split(/\s+/u)[0]));
}

async function existingRepositoryPython(repositoryRoot) {
  for (const venvRoot of supportModule.repositoryPythonVenvRoots(repositoryRoot)) {
    const launcher = path.join(venvRoot, "bin", "python");
    try {
      await lstat(launcher);
      return launcher;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  throw new Error("repository backend Python is unavailable for verifier probe");
}

test("createPrivateRuntime preflights cleanup before creating any runtime directory", async () => {
  const helperDirectory = await mkdtemp(path.join(os.tmpdir(), "live-company-research-preflight-first-"));
  const invalidHelper = path.join(helperDirectory, "invalid.py");
  await writeFile(invalidHelper, "def broken(:\n");
  const prefix = "fund-engine-live-company-research-";
  const before = new Set((await readdir(os.tmpdir())).filter((entry) => entry.startsWith(prefix)));
  let runtime = null;
  let failure = null;
  try {
    runtime = await createPrivateRuntime({
      cleanupHelper: { pythonExecutable: TEST_PYTHON, helperPath: invalidHelper },
    });
  } catch (error) {
    failure = error;
  }
  try {
    assert.equal(runtime, null);
    assert.match(failure?.message ?? "", /preflight/u);
    const after = (await readdir(os.tmpdir())).filter((entry) => entry.startsWith(prefix));
    assert.deepEqual(after.filter((entry) => !before.has(entry)), []);
  } finally {
    if (runtime) await rm(runtime.directory, { recursive: true, force: true });
    await rm(helperDirectory, { recursive: true, force: true });
  }
});

test("verifier rejects hostile PYTHON before secrets or private runtime creation", async () => {
  const hostileRoot = await mkdtemp(path.join(os.tmpdir(), "live-company-research-hostile-python-"));
  const hostilePython = path.join(hostileRoot, "python");
  const observed = path.join(hostileRoot, "observed");
  const privateTmp = path.join(hostileRoot, "tmp");
  await mkdir(privateTmp, { mode: 0o700 });
  await writeFile(hostilePython, `#!/bin/sh\n/usr/bin/touch '${observed}'\nexit 0\n`);
  await chmod(hostilePython, 0o700);
  try {
    const result = spawnSync(
      process.execPath,
      [path.resolve(process.cwd(), "scripts/verify-live-company-research-ui.mjs"), "--timeout-seconds", "30"],
      {
        cwd: process.cwd(),
        env: { PATH: process.env.PATH ?? "", PYTHON: hostilePython, TMPDIR: privateTmp },
        encoding: "utf8",
        timeout: 10_000,
      },
    );
    assert.notEqual(result.status, 0);
    await assert.rejects(lstat(observed), { code: "ENOENT" });
    assert.deepEqual(await readdir(privateTmp), []);
  } finally {
    await rm(hostileRoot, { recursive: true, force: true });
  }
});

test("repository Python venv policy resolves normal checkout and worktree layouts", () => {
  assert.equal(typeof supportModule.repositoryPythonVenvRoots, "function");
  assert.deepEqual(
    supportModule.repositoryPythonVenvRoots("/workspace/fund-engine"),
    ["/workspace/fund-engine/backend/.venv"],
  );
  assert.deepEqual(
    supportModule.repositoryPythonVenvRoots("/workspace/fund-engine/.worktrees/topic"),
    [
      "/workspace/fund-engine/.worktrees/topic/backend/.venv",
      "/workspace/fund-engine/backend/.venv",
    ],
  );
});

test("verifier missing-browser failure cleans its private temp root and owned stack", async () => {
  const probeRoot = await mkdtemp(path.join(os.tmpdir(), "live-company-research-missing-browser-"));
  const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
  const trustedPython = await existingRepositoryPython(repositoryRoot);
  const processesBefore = liveStackProcessIds();
  try {
    const result = spawnSync(
      process.execPath,
      [path.resolve(process.cwd(), "scripts/verify-live-company-research-ui.mjs"), "--timeout-seconds", "30"],
      {
        cwd: process.cwd(),
        env: {
          PATH: process.env.PATH ?? "",
          PYTHON: trustedPython,
          PW_BROWSER_CHANNEL: "definitely-missing-browser",
          TMPDIR: probeRoot,
        },
        encoding: "utf8",
        timeout: 20_000,
      },
    );
    assert.notEqual(result.status, 0);
    assert.equal(result.signal, null);
    assert.match(`${result.stdout}\n${result.stderr}`, /owned browser launch failed/u);
    assert.deepEqual(await readdir(probeRoot), []);
    const leakedProcesses = [...liveStackProcessIds()].filter((pid) => !processesBefore.has(pid));
    assert.deepEqual(leakedProcesses, []);
  } finally {
    await rm(probeRoot, { recursive: true, force: true });
  }
});

test("owned browser launch confines paths and leaves no sibling temporary directories on failure", async () => {
  assert.equal(typeof supportModule.startOwnedBrowser, "function");
  const runtime = await createTestRuntime();
  const siblingPrefixes = ["playwright-artifacts-", "playwright_chromiumdev_profile-"];
  const siblingsBefore = new Set((await readdir(runtime.parent)).filter((entry) =>
    siblingPrefixes.some((prefix) => entry.startsWith(prefix))));
  let launchOptions;
  let launchTmpdir;
  const hostTmpdir = os.tmpdir();
  const browserType = {
    async launchServer(options) {
      launchOptions = options;
      launchTmpdir = os.tmpdir();
      throw new Error("injected missing browser");
    },
    async connect() { throw new Error("must not connect"); },
  };
  try {
    await assert.rejects(
      supportModule.startOwnedBrowser(browserType, runtime, { env: { PATH: "/tools" }, timeoutMs: 50 }),
      /browser launch/u,
    );
    for (const key of ["artifactsDir", "downloadsPath", "tracesDir"]) {
      assert.equal(path.dirname(launchOptions[key]).startsWith(runtime.directory), true);
    }
    for (const key of ["HOME", "TMPDIR", "TEMP", "TMP", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"]) {
      assert.equal(launchOptions.env[key].startsWith(runtime.directory), true);
    }
    assert.equal(launchTmpdir, launchOptions.env.TMPDIR);
    assert.equal(os.tmpdir(), hostTmpdir);
  } finally {
    await removePrivateRuntime(runtime);
  }
  const siblingsAfter = (await readdir(runtime.parent)).filter((entry) =>
    siblingPrefixes.some((prefix) => entry.startsWith(prefix)) && !siblingsBefore.has(entry));
  assert.deepEqual(siblingsAfter, []);
});

test("owned browser close timeout kills the exact retained browser server capability", async () => {
  assert.equal(typeof supportModule.startOwnedBrowser, "function");
  assert.equal(typeof supportModule.closeOwnedBrowser, "function");
  const runtime = await createTestRuntime();
  let killed = 0;
  const browserServer = {
    close: () => new Promise(() => {}),
    async kill() { killed += 1; },
    wsEndpoint() { return "ws://127.0.0.1:41000/owned"; },
  };
  const browserType = {
    async launchServer() { return browserServer; },
    async connect() { return { newContext(options) { return options; } }; },
  };
  try {
    const owned = await supportModule.startOwnedBrowser(
      browserType, runtime, { env: { PATH: "/tools" }, timeoutMs: 50 },
    );
    const contextOptions = await supportModule.newOwnedBrowserContext(
      owned, { serviceWorkers: "allow" },
    );
    assert.equal(contextOptions.serviceWorkers, "block");
    await assert.rejects(
      supportModule.closeOwnedBrowser(owned, { timeoutMs: 20 }),
      /graceful close timed out/u,
    );
    assert.equal(killed, 1);
  } finally {
    await removePrivateRuntime(runtime);
  }
});

test("owned browser close falls back to the exact retained child when server kill rejects", async () => {
  const runtime = await createTestRuntime();
  const child = new EventEmitter();
  let signal = null;
  child.exitCode = null;
  child.signalCode = null;
  child.kill = (nextSignal) => {
    signal = nextSignal;
    child.signalCode = nextSignal;
    queueMicrotask(() => child.emit("exit", null, nextSignal));
    return true;
  };
  const browserServer = {
    close: () => new Promise(() => {}),
    async kill() { throw new Error("injected kill rejection"); },
    process() { return child; },
    wsEndpoint() { return "ws://127.0.0.1:41000/owned"; },
  };
  const browserType = {
    async launchServer() { return browserServer; },
    async connect() { return { newContext() {} }; },
  };
  try {
    const owned = await supportModule.startOwnedBrowser(
      browserType, runtime, { env: { PATH: "/tools" }, timeoutMs: 50 },
    );
    await assert.rejects(
      supportModule.closeOwnedBrowser(owned, { timeoutMs: 20 }),
      /direct SIGKILL/u,
    );
    assert.equal(signal, "SIGKILL");
  } finally {
    await removePrivateRuntime(runtime);
  }
});

async function waitForProcessExit(owned, timeoutMs = 1_000) {
  const deadline = Date.now() + timeoutMs;
  while (owned.child.exitCode === null && owned.child.signalCode === null) {
    if (Date.now() >= deadline) throw new Error("process did not exit");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

test("parseVerifierArgs accepts only the bounded two-token timeout option", () => {
  assert.deepEqual(parseVerifierArgs([]), { timeoutMs: 180_000 });
  assert.deepEqual(parseVerifierArgs(["--timeout-seconds", "90"]), { timeoutMs: 90_000 });

  for (const args of [
    ["--timeout-seconds=90"],
    ["--timeout-seconds"],
    ["--timeout-seconds", "29"],
    ["--timeout-seconds", "301"],
    ["--unknown", "1"],
  ]) {
    assert.throws(() => parseVerifierArgs(args), /usage:/);
  }
});

test("buildVerifierEnvironment exposes only the verifier's closed environment", () => {
  const hostEnvironment = {
    PATH: "/tools",
    HOME: "/hostile/home",
    DATABASE_URL: "postgresql://hostile/database",
    RESEARCH_TENANT_TOKENS: "hostile-token",
    VITE_RESEARCH_CLIENT: "mock",
    LLM_API_KEY: "hostile-key",
    BASH_ENV: "/hostile/bash-env",
    PYTHONPATH: "/hostile/pythonpath",
    TMPDIR: "",
  };

  const environment = buildVerifierEnvironment({
    host: hostEnvironment,
    databaseUrl: "sqlite:////private/live.sqlite",
    token: "test-only-token",
    backendUrl: "http://127.0.0.1:41001",
  });

  assert.deepEqual(environment, {
    PATH: "/tools",
    APP_ENV: "production",
    DATABASE_URL: "sqlite:////private/live.sqlite",
    RESEARCH_TENANT_TOKENS: JSON.stringify({ "test-only-token": "live-company-research-verifier" }),
    VITE_BACKEND_URL: "http://127.0.0.1:41001",
    RESEARCH_BEARER_TOKEN: "test-only-token",
    VITE_RESEARCH_CLIENT: "",
    NO_PROXY: "127.0.0.1,localhost",
    no_proxy: "127.0.0.1,localhost",
  });
  for (const key of ["HOME", "LLM_API_KEY", "BASH_ENV", "PYTHONPATH"]) {
    assert.equal(key in environment, false, `${key} must not be inherited`);
  }
  assert.equal("TMPDIR" in environment, false, "empty allowlisted values must not be inherited");
});

test("assertLoopbackUrl permits only credential-free HTTP on 127.0.0.1", () => {
  const accepted = "http://127.0.0.1:41001/api/underwriting/v1/product/objects";
  const parsed = assertLoopbackUrl(accepted);
  assert.ok(parsed instanceof URL);
  assert.equal(parsed.hostname, "127.0.0.1");

  for (const url of [
    "https://example.com/a",
    "https://127.0.0.1/a",
    "http://localhost/a",
    "http://localhost.evil.test/a",
    "http://user:pass@127.0.0.1/a",
    "http://user@127.0.0.1/a",
    "http://:pass@127.0.0.1/a",
    "http://:@127.0.0.1/a",
    "not a URL",
    "http://127.1/a",
    "http://2130706433/a",
    "http://0177.0.0.1/a",
    "http://0x7f000001/a",
    "http://127.0.0.1:99999/a",
    null,
  ]) {
    assert.throws(() => assertLoopbackUrl(url), /loopback/);
  }
});

test("assertWorkspace rejects snapshot drift while preserving a valid workspace", () => {
  const expected = {
    projectId: "project-1",
    companyId: "company-1",
    status: "awaiting_evidence_review",
    progress: 25,
  };
  const workspace = {
    schema_version: "underwriting.v1",
    project_id: "project-1",
    company: {
      id: "company-1",
      object_id: "company-1",
    },
    preparation: {
      status: "awaiting_evidence_review",
      progress: 25,
    },
    draft: {
      lock_version: 2,
    },
  };

  assert.strictEqual(assertWorkspace(workspace, expected), workspace);

  const projectDrift = structuredClone(workspace);
  projectDrift.project_id = "project-2";
  assert.throws(() => assertWorkspace(projectDrift, expected), /project identity/);

  const progressDrift = structuredClone(workspace);
  progressDrift.preparation.progress = 24;
  assert.throws(() => assertWorkspace(progressDrift, expected), /progress/);

  const schemaDrift = structuredClone(workspace);
  schemaDrift.schema_version = "underwriting.v2";
  assert.throws(() => assertWorkspace(schemaDrift, expected), /schema/);

  const companyIdDrift = structuredClone(workspace);
  companyIdDrift.company.id = "company-2";
  assert.throws(() => assertWorkspace(companyIdDrift, expected), /company identity/);

  const companyObjectIdDrift = structuredClone(workspace);
  companyObjectIdDrift.company.object_id = "company-2";
  assert.throws(() => assertWorkspace(companyObjectIdDrift, expected), /company identity/);

  const unknownState = structuredClone(workspace);
  unknownState.preparation.status = "unexpected";
  assert.throws(() => assertWorkspace(unknownState, expected), /status/);

  const allowedButUnexpectedState = structuredClone(workspace);
  allowedButUnexpectedState.preparation.status = "building_model";
  assert.throws(() => assertWorkspace(allowedButUnexpectedState, expected), /status/);

  for (const [nestedRecord, pattern] of [
    ["company", /company identity/],
    ["preparation", /status/],
    ["draft", /draft lock/],
  ]) {
    const missingNestedRecord = structuredClone(workspace);
    delete missingNestedRecord[nestedRecord];
    assert.throws(() => assertWorkspace(missingNestedRecord, expected), pattern);
  }

  for (const lockVersion of [0, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    const invalidLockVersion = structuredClone(workspace);
    invalidLockVersion.draft.lock_version = lockVersion;
    assert.throws(() => assertWorkspace(invalidLockVersion, expected), /draft lock/);
  }
});

test("createPrivateRuntime creates a private owned directory and removePrivateRuntime is idempotent", async () => {
  const runtime = await createTestRuntime();
  const sentinel = `${runtime.directory}/owned-sentinel`;

  assert.equal((await lstat(runtime.directory)).mode & 0o777, 0o700);
  await writeFile(sentinel, "owned");
  await removePrivateRuntime(runtime);
  await removePrivateRuntime(runtime);
  await assert.rejects(lstat(runtime.directory), { code: "ENOENT" });
});

test("createPrivateRuntime rejects a non-Python cleanup executable before allocation", async () => {
  const helperDirectory = await mkdtemp(path.join(os.tmpdir(), "live-company-research-helper-"));
  const invalidPython = path.join(helperDirectory, "not-python");
  await writeFile(invalidPython, "not an executable format");
  await chmod(invalidPython, 0o700);
  try {
    await assert.rejects(createPrivateRuntime({
      cleanupHelper: { pythonExecutable: invalidPython, helperPath: TEST_CLEANUP_HELPER.helperPath },
    }));
  } finally {
    await rm(helperDirectory, { recursive: true, force: true });
  }
});

test("helper self-test rejects invalid source and stalled preflight before allocation", async () => {
  const helperDirectory = await mkdtemp(path.join(os.tmpdir(), "live-company-research-helper-source-"));
  const invalidHelper = path.join(helperDirectory, "invalid.py");
  const stalledHelper = path.join(helperDirectory, "stalled.py");
  await writeFile(invalidHelper, "def broken(:\n");
  await writeFile(stalledHelper, "while True: pass\n");

  try {
    for (const helperPath of [invalidHelper, stalledHelper]) {
      await assert.rejects(createPrivateRuntime({
        cleanupHelper: { pythonExecutable: TEST_PYTHON, helperPath, timeoutMs: 100 },
      }), /preflight.*(?:exited|timed out)/);
    }
  } finally {
    await rm(helperDirectory, { recursive: true, force: true });
  }
});

test("stalled fd-relative cleanup helper is terminated without deleting a replacement", async () => {
  const helperDirectory = await mkdtemp(path.join(os.tmpdir(), "live-company-research-stalled-cleanup-"));
  const helperPath = path.join(helperDirectory, "cleanup.py");
  await writeFile(helperPath, "import sys\nif sys.argv[1:] == ['--self-test']: raise SystemExit(0)\nwhile True: pass\n");
  const runtime = await createPrivateRuntime({
    cleanupHelper: { pythonExecutable: TEST_PYTHON, helperPath, timeoutMs: 100 },
  });
  const sentinel = `${runtime.directory}/sentinel`;
  await writeFile(sentinel, "preserve");
  let quarantine;
  try {
    await assert.rejects(removePrivateRuntime(runtime), /cleanup helper timed out/);
    quarantine = (await readdir(runtime.parent)).find((entry) =>
      entry.startsWith(`.${path.basename(runtime.directory)}.cleanup-`));
    assert.ok(quarantine);
    assert.equal(await readFile(path.join(runtime.parent, quarantine, "sentinel"), "utf8"), "preserve");
  } finally {
    await rm(runtime.directory, { recursive: true, force: true });
    if (quarantine) await rm(path.join(runtime.parent, quarantine), { recursive: true, force: true });
    await rm(helperDirectory, { recursive: true, force: true });
  }
});

test("helper subprocess diagnostics preserve UTF-8 code points at the bound", async () => {
  const helperDirectory = await mkdtemp(path.join(os.tmpdir(), "live-company-research-unicode-helper-"));
  const helperPath = path.join(helperDirectory, "cleanup.py");
  await writeFile(helperPath, "import sys\nif sys.argv[1:] == ['--self-test']: raise SystemExit(0)\nsys.stderr.write('火' * 10000)\nraise SystemExit(1)\n");
  const runtime = await createPrivateRuntime({ cleanupHelper: { pythonExecutable: TEST_PYTHON, helperPath } });
  const quarantinePrefix = `.${path.basename(runtime.directory)}.cleanup-`;
  try {
    await assert.rejects(removePrivateRuntime(runtime), (error) => {
      assert.equal(error.message.includes("\uFFFD"), false);
      assert.ok(Buffer.byteLength(error.message) <= 16_384);
      assert.ok((error.message.match(/火/gu) ?? []).length > 5_400);
      return true;
    });
    const quarantine = (await readdir(runtime.parent)).filter((entry) => entry.startsWith(quarantinePrefix));
    assert.equal(quarantine.length, 1);
  } finally {
    await rm(runtime.directory, { recursive: true, force: true });
    const quarantines = (await readdir(runtime.parent)).filter((entry) => entry.startsWith(quarantinePrefix));
    await Promise.all(quarantines.map((entry) =>
      rm(path.join(runtime.parent, entry), { recursive: true, force: true })));
    await rm(helperDirectory, { recursive: true, force: true });
  }
});

test("atomic cleanup claim uses this runtime's exact prefix under a hostile TMPDIR", async () => {
  const hostileParent = await mkdtemp(path.join(os.tmpdir(), "hostile-live-company-research-"));
  const priorTmpdir = process.env.TMPDIR;
  process.env.TMPDIR = hostileParent;
  let runtime;
  try {
    runtime = await createTestRuntime();
    await Promise.all(Array.from({ length: 200 }, (_, index) =>
      writeFile(`${runtime.directory}/owned-${index}`, "owned")));
    const cleanup = removePrivateRuntime(runtime);
    let claimed = false;
    const exactPrefix = `.${path.basename(runtime.directory)}.cleanup-`;
    for (let attempt = 0; attempt < 100 && !claimed; attempt += 1) {
      claimed = (await readdir(hostileParent)).some((entry) => entry.startsWith(exactPrefix));
      if (!claimed) await new Promise((resolve) => setTimeout(resolve, 1));
    }
    assert.equal(claimed, true);
    await cleanup;
  } finally {
    if (priorTmpdir === undefined) delete process.env.TMPDIR;
    else process.env.TMPDIR = priorTmpdir;
    await rm(hostileParent, { recursive: true, force: true });
  }
});

test("removePrivateRuntime refuses a directory substituted after creation", async () => {
  const runtime = await createTestRuntime();
  const movedDirectory = `${runtime.directory}.moved`;
  const victimSentinel = `${runtime.directory}/victim-sentinel`;
  let quarantinedVictim;

  try {
    await rename(runtime.directory, movedDirectory);
    await mkdir(runtime.directory, { mode: 0o700 });
    await writeFile(victimSentinel, "victim");

    await assert.rejects(removePrivateRuntime(runtime), /identity changed/);
    const expectedPrefix = `.${path.basename(runtime.directory)}.cleanup-`;
    const quarantine = (await readdir(runtime.parent)).find((entry) => entry.startsWith(expectedPrefix));
    assert.ok(quarantine);
    quarantinedVictim = path.join(runtime.parent, quarantine);
    assert.equal(await readFile(`${quarantinedVictim}/victim-sentinel`, "utf8"), "victim");
  } finally {
    await rm(runtime.directory, { recursive: true, force: true });
    await rm(movedDirectory, { recursive: true, force: true });
    if (quarantinedVictim) await rm(quarantinedVictim, { recursive: true, force: true });
  }
});

test("removePrivateRuntime leaves a victim created after its atomic cleanup claim", async () => {
  const runtime = await createTestRuntime();
  const victimSentinel = `${runtime.directory}/victim-sentinel`;
  await Promise.all(Array.from({ length: 200 }, (_, index) =>
    writeFile(`${runtime.directory}/owned-${index}`, "owned")));

  const cleanup = removePrivateRuntime(runtime);
  try {
    let claimed = false;
    for (let attempt = 0; attempt < 100 && !claimed; attempt += 1) {
      const entries = await readdir(runtime.parent);
      claimed = entries.some((entry) =>
        entry.startsWith(`.${path.basename(runtime.directory)}.cleanup-`));
      if (!claimed) await new Promise((resolve) => setTimeout(resolve, 1));
    }
    assert.equal(claimed, true, "cleanup must atomically quarantine its owned directory");
    await mkdir(runtime.directory, { mode: 0o700 });
    await writeFile(victimSentinel, "victim");
    await cleanup;
    assert.equal(await readFile(victimSentinel, "utf8"), "victim");
  } finally {
    await cleanup.catch(() => {});
    await rm(runtime.directory, { recursive: true, force: true });
  }
});

test("waitUntil times out for a running child and reports unexpected child exits", async () => {
  const sleeper = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "sleeper",
  });

  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "never ready",
        timeoutMs: 30,
        intervalMs: 5,
        processes: [sleeper],
      }),
      /timed out/,
    );
  } finally {
    await stopOwnedProcess(sleeper);
  }

  const worker = startOwnedProcess(process.execPath, ["-e", "process.exit(7)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "worker",
  });
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "never ready",
        timeoutMs: 1_000,
        intervalMs: 5,
        processes: [worker],
      }),
      /worker exited with 7/,
    );
  } finally {
    await stopOwnedProcess(worker);
  }
});

test("chooseRunError preserves a workflow failure over cleanup failures", () => {
  const workflowError = new Error("workflow failed");
  const cleanupError = new Error("cleanup failed");

  assert.strictEqual(chooseRunError(workflowError, [cleanupError]), workflowError);
  assert.strictEqual(chooseRunError(null, [cleanupError]), cleanupError);
  assert.strictEqual(chooseRunError(null, []), null);
});

test("removePrivateRuntime rejects forged handles without touching their targets", async () => {
  const runtime = await createTestRuntime();
  const sentinel = `${runtime.directory}/owned-sentinel`;
  await writeFile(sentinel, "owned");
  const forged = { ...runtime };

  try {
    assert.equal(Object.isFrozen(runtime), true);
    await assert.rejects(removePrivateRuntime(forged), /identity changed/);
    assert.equal(await readFile(sentinel, "utf8"), "owned");
  } finally {
    await removePrivateRuntime(runtime);
  }
});

test("waitUntil enforces its deadline when a probe never settles", { timeout: 500 }, async () => {
  await assert.rejects(
    waitUntil(() => new Promise(() => {}), {
      label: "stalled probe",
      timeoutMs: 30,
      intervalMs: 5,
    }),
    /stalled probe timed out/,
  );
});

test("waitUntil fails when a child exits while a probe later reports ready", async () => {
  const worker = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => process.exit(7), 5)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "worker",
  });

  try {
    await assert.rejects(
      waitUntil(async () => {
        await new Promise((resolve) => setTimeout(resolve, 200));
        return true;
      }, {
        label: "ready",
        timeoutMs: 1_000,
        processes: [worker],
      }),
      /worker exited with 7/,
    );
  } finally {
    await stopOwnedProcess(worker);
  }
});

test("supervision diagnostics bound untrusted labels, names, and probe errors", async () => {
  const huge = `line\n${"x".repeat(20_000)}`;
  await assert.rejects(
    waitUntil(() => {
      throw new Error(huge);
    }, {
      label: huge,
      timeoutMs: 5,
      intervalMs: 1,
    }),
    (error) => {
      assert.ok(error.message.length <= 16_384);
      assert.equal(error.message.includes("\n"), false);
      assert.equal(error.message.includes("timed out"), true);
      return true;
    },
  );

  const worker = startOwnedProcess(process.execPath, ["-e", "process.exit(7)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: huge,
  });
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "ready",
        timeoutMs: 500,
        intervalMs: 5,
        processes: [worker],
      }),
      (error) => {
        assert.ok(error.message.length <= 16_384);
        assert.equal(error.message.includes("\n"), false);
        assert.equal(error.message.includes("exited with 7"), true);
        return true;
      },
    );
  } finally {
    await stopOwnedProcess(worker);
  }
});

test("stopOwnedProcess exposes no mutable child lifecycle capability", async () => {
  const sleeper = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "sleeper",
  });
  try {
    assert.equal(typeof sleeper.child.kill, "undefined");
    assert.equal(typeof sleeper.child.emit, "undefined");
    assert.equal(Object.isFrozen(sleeper.child), true);
    await stopOwnedProcess(sleeper);
  } finally {
    if (!sleeper.exited) process.kill(sleeper.child.pid, "SIGKILL");
  }
  assert.doesNotThrow(() => assertProcessesRunning([sleeper]));
});

test("intentional worker stop rejects an already-exited worker before marking it expected", async () => {
  assert.equal(typeof supportModule.assertProcessRunningBeforeIntentionalStop, "function");
  const worker = startOwnedProcess(process.execPath, ["-e", "process.exit(7)"], {
    cwd: process.cwd(), env: { PATH: process.env.PATH ?? "" }, name: "company-research-worker",
  });
  await waitForProcessExit(worker);
  assert.equal(worker.expectedStop, false);
  assert.throws(
    () => supportModule.assertProcessRunningBeforeIntentionalStop(worker),
    /company-research-worker exited with 7/u,
  );
  assert.equal(worker.expectedStop, false);
  await stopOwnedProcess(worker);
});

test("supervision fails closed for spawn errors and signal-only exits", async () => {
  const missing = startOwnedProcess("/definitely-not-a-live-company-research-command", [], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "missing",
  });
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "ready",
        timeoutMs: 500,
        intervalMs: 5,
        processes: [missing],
      }),
      /missing exited with ENOENT/,
    );
  } finally {
    await stopOwnedProcess(missing);
  }

  const signaled = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(),
    env: { PATH: process.env.PATH ?? "" },
    name: "signaled",
  });
  while (!signaled.child.pid) await new Promise((resolve) => setTimeout(resolve, 1));
  process.kill(signaled.child.pid, "SIGTERM");
  try {
    await assert.rejects(
      waitUntil(() => false, {
        label: "ready",
        timeoutMs: 500,
        intervalMs: 5,
        processes: [signaled],
      }),
      /signaled exited with SIGTERM/,
    );
  } finally {
    await stopOwnedProcess(signaled);
  }
});

test("removePrivateRuntime preserves a nonempty victim substituted at its quarantine target", async () => {
  const runtime = await createTestRuntime();
  const ownedMoved = `${runtime.directory}.owned-moved`;
  await Promise.all(Array.from({ length: 400 }, (_, index) =>
    writeFile(`${runtime.directory}/owned-${index}`, "owned")));
  const expectedPrefix = `.${path.basename(runtime.directory)}.cleanup-`;
  const cleanup = removePrivateRuntime(runtime);
  const cleanupRejected = assert.rejects(cleanup, /private runtime identity changed|ENOTEMPTY/);
  cleanupRejected.catch(() => {});
  let quarantine;

  try {
    for (let attempt = 0; attempt < 200 && !quarantine; attempt += 1) {
      quarantine = (await readdir(runtime.parent)).find((entry) => entry.startsWith(expectedPrefix));
      if (!quarantine) await new Promise((resolve) => setTimeout(resolve, 1));
    }
    assert.ok(quarantine, "cleanup must expose only this runtime's unique quarantine name");
    const quarantinePath = path.join(runtime.parent, quarantine);
    await rename(quarantinePath, ownedMoved);
    await mkdir(quarantinePath, { mode: 0o700 });
    const victim = `${quarantinePath}/victim-sentinel`;
    await writeFile(victim, "victim");

    await cleanupRejected;
    assert.equal(await readFile(victim, "utf8"), "victim");
  } finally {
    await cleanupRejected.catch(() => {});
    const quarantines = (await readdir(runtime.parent)).filter((entry) => entry.startsWith(expectedPrefix));
    await Promise.all(quarantines.map((entry) =>
      rm(path.join(runtime.parent, entry), { recursive: true, force: true })));
    await rm(runtime.directory, { recursive: true, force: true });
    await rm(ownedMoved, { recursive: true, force: true });
  }
});

test("removePrivateRuntime normalizes symlink, file, and mode substitutions", async () => {
  const substitutions = [
    async ({ runtime, moved }) => {
      const victim = `${runtime.directory}.symlink-victim`;
      await mkdir(victim, { mode: 0o700 });
      await writeFile(`${victim}/sentinel`, "victim");
      await symlink(victim, runtime.directory);
      return { victim, sentinel: `${victim}/sentinel` };
    },
    async ({ runtime }) => {
      await writeFile(runtime.directory, "victim");
      return { victim: runtime.directory, sentinel: runtime.directory };
    },
    async ({ runtime }) => {
      await mkdir(runtime.directory, { mode: 0o700 });
      await chmod(runtime.directory, 0o000);
      return { victim: runtime.directory, sentinel: null };
    },
  ];

  for (const substitute of substitutions) {
    const runtime = await createTestRuntime();
    const moved = `${runtime.directory}.moved`;
    let victim;
    let quarantined;
    try {
      await rename(runtime.directory, moved);
      victim = await substitute({ runtime, moved });
      await assert.rejects(removePrivateRuntime(runtime), /private runtime identity changed; refusing cleanup/);
      quarantined = (await readdir(runtime.parent)).find((entry) =>
        entry.startsWith(`.${path.basename(runtime.directory)}.cleanup-`));
      assert.ok(quarantined);
      if (victim.sentinel) {
        const preservedSentinel = victim.victim === runtime.directory
          ? path.join(runtime.parent, quarantined)
          : victim.sentinel;
        assert.equal(await readFile(preservedSentinel, "utf8"), "victim");
      }
    } finally {
      if (quarantined) await chmod(path.join(runtime.parent, quarantined), 0o700).catch(() => {});
      if (quarantined) await rm(path.join(runtime.parent, quarantined), { recursive: true, force: true });
      if (victim?.victim && victim.victim !== runtime.directory) {
        await rm(victim.victim, { recursive: true, force: true });
      }
      await rm(runtime.directory, { recursive: true, force: true });
      await rm(moved, { recursive: true, force: true });
    }
  }
});

test("owned process handles cannot be redirected to another child", async () => {
  const first = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(), env: { PATH: process.env.PATH ?? "" }, name: "first",
  });
  const second = startOwnedProcess(process.execPath, ["-e", "setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(), env: { PATH: process.env.PATH ?? "" }, name: "second",
  });
  const firstChild = first.child;
  const secondChild = second.child;

  try {
    try {
      first.child = secondChild;
      first.name = "redirected";
    } catch {
      // Frozen public capabilities reject mutation in ESM strict mode.
    }
    await stopOwnedProcess(first);
    await waitForProcessExit(first);
    assert.equal(secondChild.exitCode, null);
    assert.equal(secondChild.signalCode, null);
  } finally {
    await stopOwnedProcess(second);
  }
});

test("waitUntil aborts a stalled probe's owned resource at its deadline", async () => {
  let aborted = false;
  let resource;
  await assert.rejects(
    waitUntil(({ signal }) => new Promise((resolve) => {
      resource = setTimeout(resolve, 60_000);
      signal.addEventListener("abort", () => {
        aborted = true;
        clearTimeout(resource);
        resolve(false);
      }, { once: true });
    }), {
      label: "abortable stalled probe",
      timeoutMs: 30,
    }),
    /timed out/,
  );
  assert.equal(aborted, true);
});

test("waitUntil discards a stale probe exception after a later falsy result", async () => {
  let first = true;
  await assert.rejects(
    waitUntil(() => {
      if (first) {
        first = false;
        throw new Error("stale failure");
      }
      return false;
    }, { label: "reset latest", timeoutMs: 10, intervalMs: 1 }),
    (error) => {
      assert.equal(error.message.includes("stale failure"), false);
      assert.equal(error.message.includes("probe returned a falsy value"), true);
      return true;
    },
  );
});

test("bounded diagnostics count UTF-8 bytes without splitting code points", async () => {
  const huge = "火".repeat(20_000);
  await assert.rejects(
    waitUntil(() => {
      throw new Error(huge);
    }, { label: huge, timeoutMs: 5, intervalMs: 1 }),
    (error) => {
      assert.ok(Buffer.byteLength(error.message) <= 16_384);
      assert.equal(error.message.includes("\uFFFD"), false);
      assert.equal(error.message.includes("timed out"), true);
      return true;
    },
  );
});

test("stopOwnedProcess escalates a SIGTERM-ignoring child to SIGKILL", async () => {
  const sleeper = startOwnedProcess(process.execPath, ["-e", "process.on('SIGTERM', () => {}); process.stdout.write('ready'); setTimeout(() => {}, 60_000)"], {
    cwd: process.cwd(), env: { PATH: process.env.PATH ?? "" }, name: "term-ignoring",
  });
  while (!sleeper.stdout.toString("utf8").includes("ready")) {
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  const started = Date.now();
  await stopOwnedProcess(sleeper);
  assert.ok(Date.now() - started >= 4_500);
  assert.equal(sleeper.child.signalCode, "SIGKILL");
});

test("support exports no stop hook that accepts raw process authority", () => {
  assert.equal("__testOnlyStopAuthority" in supportModule, false);
});

test("stopOwnedProcess rejects raw process authority without signaling", async () => {
  let signaled = false;
  await assert.rejects(stopOwnedProcess({
    kill() { signaled = true; },
  }), /unowned process handle/);
  assert.equal(signaled, false);
});

test("SIGTERM refusal reaches a terminal state without delayed signals", () => {
  const harness = createTerminationHarness([false]);
  assert.equal(harness.state.failure, "refused-sigterm");
  assert.deepEqual(harness.signals, ["SIGTERM"]);
  harness.advanceBy(15_000);
  assert.deepEqual(harness.signals, ["SIGTERM"]);
  assert.equal(harness.pendingTimers, 0);
});

test("SIGKILL refusal reaches a terminal state without delayed signals", () => {
  const harness = createTerminationHarness([true, false]);
  harness.advanceBy(5_000);
  assert.equal(harness.state.failure, "refused-sigkill");
  assert.deepEqual(harness.signals, ["SIGTERM", "SIGKILL"]);
  harness.advanceBy(15_000);
  assert.deepEqual(harness.signals, ["SIGTERM", "SIGKILL"]);
  assert.equal(harness.pendingTimers, 0);
});

test("post-SIGKILL timeout reaches a terminal state without delayed signals", () => {
  const harness = createTerminationHarness([true, true]);
  harness.advanceBy(10_000);
  assert.equal(harness.state.failure, "sigkill-timeout");
  assert.deepEqual(harness.signals, ["SIGTERM", "SIGKILL"]);
  harness.advanceBy(15_000);
  assert.deepEqual(harness.signals, ["SIGTERM", "SIGKILL"]);
  assert.equal(harness.pendingTimers, 0);
});
