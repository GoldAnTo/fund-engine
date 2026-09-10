import { describe, expect, it } from "vitest";

import { isValuationSetPayload } from "./investmentResearchApi";

const hash = "1".repeat(64);
const ids = {
  scenario: "00000000-0000-4000-8000-000000000001",
  financial: "00000000-0000-4000-8000-000000000002",
  price: "00000000-0000-4000-8000-000000000003",
  capital: "00000000-0000-4000-8000-000000000004",
  rights: "00000000-0000-4000-8000-000000000005",
  capturePrice: "00000000-0000-4000-8000-000000000006",
  captureCapital: "00000000-0000-4000-8000-000000000007",
  captureRights: "00000000-0000-4000-8000-000000000008",
  fx: "00000000-0000-4000-8000-000000000009",
  captureFx: "00000000-0000-4000-8000-000000000010",
};

const parents = [
  { artifact_id: ids.scenario, artifact_kind: "scenario_set", content_hash: hash },
  { artifact_id: ids.financial, artifact_kind: "financial_bridge", content_hash: hash },
];
const source = {
  fact_key: "catl_market_fact",
  source_role: "exchange_historical_quote",
  source_url: "https://www.szse.cn/api/report/ShowReport",
  source_locator: "300750 close on 2025-11-06",
  raw_hash: hash,
};
const bindings = [
  [ids.price, "price", "SZSE:300750", ids.capturePrice],
  [ids.capital, "capital_structure", null, ids.captureCapital],
  [ids.rights, "security_rights", "SZSE:300750", ids.captureRights],
].map(([snapshotId, snapshotKind, securityExternalKey, captureId]) => ({
  snapshot_id: snapshotId,
  snapshot_kind: snapshotKind,
  snapshot_content_hash: hash,
  security_external_key: securityExternalKey,
  source_ref: source,
  capture_envelope_id: captureId,
  capture_content_hash: hash,
  provenance_role: "primary",
  provider_policy_version: "catl-frozen.v1",
  raw_components: [],
}));
const lineage = {
  artifact_refs: parents,
  market_snapshot_ids: bindings.map((item) => item.snapshot_id),
  market_snapshot_bindings: bindings,
};
const computation = {
  kind: "artifact_computation",
  artifact_refs: parents,
  market_snapshot_ids: lineage.market_snapshot_ids,
  equation_id: "valuation_set.v1",
};
const derived = (key: string, value: string, unit = "CNY_per_share", currency = "CNY") => ({
  key, value, unit, currency, period: "as_of:2025-11-06T15:59:59+00:00",
  state: "derived", source_ref: computation, gap_key: null, assumption_key: null,
});
const assumed = (key: string, value: string, assumptionKey: string) => ({
  key, value, unit: "ratio", currency: "N/A", period: "as_of:2025-11-06T15:59:59+00:00",
  state: "assumption", source_ref: null, gap_key: null, assumption_key: assumptionKey,
});
const range = (key: string, low: string, high: string, unit = "CNY_per_share", currency = "CNY") => ({
  minimum: derived(`${key}_minimum`, low, unit, currency),
  maximum: derived(`${key}_maximum`, high, unit, currency),
});

function catlValuation() {
  const requiredReturn = assumed("required_return", "0.1", "catl.v1:required_return");
  return {
    scenario_dcf_values: ["base", "bull", "bear"].map((scenarioId) => ({
      scenario_id: scenarioId,
      enterprise_value: derived(`${scenarioId}_enterprise_value`, "100", "CNY_million", "CNY"),
    })),
    reverse_dcf: null,
    security_value_ranges: [{
      security_external_key: "SZSE:300750",
      value_per_share: range("value_per_share", "180", "230"),
      value_currency: "CNY",
      base_currency_return: range("base_currency_return", "-0.1", "0.2", "ratio", "CNY"),
    }],
    required_return: requiredReturn,
    required_return_comparisons: [{
      security_external_key: "SZSE:300750",
      required_return: requiredReturn,
      achieved_return_range: range("achieved_return", "-0.1", "0.2", "ratio", "CNY"),
      meets_required_return: false,
    }],
    sensitivity_analyses: [{
      variable_key: "required_return",
      low_input: assumed("required_return_low_input", "0.09", "catl.v1:required_return_low"),
      high_input: assumed("required_return_high_input", "0.11", "catl.v1:required_return_high"),
      security_values: [{
        security_external_key: "SZSE:300750",
        low_input_value_per_share: derived("required_return_low_value_per_share", "240"),
        high_input_value_per_share: derived("required_return_high_value_per_share", "170"),
      }],
      value_currency: "CNY",
      equation_id: "dcf_sensitivity.v1",
    }],
    _lineage: lineage,
  };
}

describe("company-research valuation wire decoder", () => {
  it("accepts currency-neutral CATL v2 values without an FX binding", () => {
    const payload = catlValuation();

    expect(isValuationSetPayload(payload)).toBe(true);
    expect(payload.security_value_ranges[0]).toMatchObject({
      value_currency: "CNY",
      value_per_share: expect.any(Object),
      base_currency_return: expect.any(Object),
    });
  });

  it("normalizes legacy Alphabet v1 value names only while reading", () => {
    const payload: any = catlValuation();
    const item = payload.security_value_ranges[0];
    item.usd_per_share = item.value_per_share;
    item.cny_return = item.base_currency_return;
    delete item.value_per_share;
    delete item.value_currency;
    delete item.base_currency_return;
    delete payload.sensitivity_analyses;
    const fxBinding = {
      ...bindings[1],
      snapshot_id: ids.fx,
      snapshot_kind: "fx",
      capture_envelope_id: ids.captureFx,
    };
    payload._lineage.market_snapshot_ids.push(ids.fx);
    payload._lineage.market_snapshot_bindings.push(fxBinding);

    expect(isValuationSetPayload(payload)).toBe(true);
    expect(payload.security_value_ranges[0]).toMatchObject({
      value_currency: "USD",
      value_per_share: expect.any(Object),
      base_currency_return: expect.any(Object),
    });
    expect(payload.sensitivity_analyses).toEqual([]);
  });
});
