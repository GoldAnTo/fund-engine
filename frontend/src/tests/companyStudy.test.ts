import { describe, it, expect, vi } from "vitest";
import { HttpGatewayClient } from "@/gateway/HttpGatewayClient";
import { parseStudyDetail } from "@/gateway/companyStudy";

export const studyId = "22222222-2222-4222-8222-222222222222";
export const activityId = "33333333-3333-4333-8333-333333333333";
export const studyWire = {
  id: studyId,
  name: "公司档案测试",
  symbol: null,
  market: "CN",
  focus: "持续核验收入与现金流",
  revision: 0,
  created_at: "2026-09-09T02:00:00Z",
  updated_at: "2026-09-09T02:00:00Z",
};
export const activityWire = {
  id: activityId,
  study_id: studyId,
  kind: "event",
  title: "财报变化",
  text: "核验财报变化",
  status: "queued",
  conversation_id: null,
  run_spec_id: null,
  error_code: null,
  created_at: "2026-09-09T02:00:00Z",
  updated_at: "2026-09-09T02:00:00Z",
};
export const detailWire = { study: studyWire, activities: [activityWire], revisions: [], monitor: null };

describe("private continuous company research transport", () => {
  it("recognizes only the safe confirmed version conflict code", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            schema_version: "v1",
            error: { code: "conflict", message: "company_study_revision_changed" },
          }),
          { status: 409 },
        ),
    );
    const client = new HttpGatewayClient({ fetch: fetcher });
    await expect(
      client.adoptStudyRevision(studyId, { activity_id: activityId, expected_revision: 0, note: "核验" }, "key"),
    ).rejects.toMatchObject({ status: 409, code: "company_study_revision_changed" });
  });
  it("reads a company dossier through the same private transport", async () => {
    const fetcher = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(JSON.stringify(detailWire), { status: 200 }),
    );
    const client = new HttpGatewayClient({ fetch: fetcher });
    expect((await client.getStudy(studyId)).activities[0]?.status).toBe("queued");
    expect(fetcher.mock.calls[0]?.[0]).toContain(`/company-studies/${studyId}`);
  });
  it("rejects activity records belonging to another dossier and extra raw fields", () => {
    expect(() =>
      parseStudyDetail({ ...detailWire, activities: [{ ...activityWire, study_id: activityId }] }, studyId),
    ).toThrow();
    expect(() => parseStudyDetail({ ...detailWire, secret: "raw-provider-output" }, studyId)).toThrow();
    expect(() => parseStudyDetail(detailWire, activityId)).toThrow();
  });
  it("sends activity intent with its retained idempotency key and no actor override", async () => {
    const fetcher = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(JSON.stringify(activityWire), { status: 200 }),
    );
    const client = new HttpGatewayClient({ fetch: fetcher });
    await client.createStudyActivity(studyId, { kind: "event", text: "核验财报变化" }, "retained-key");
    const init = fetcher.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("Idempotency-Key")).toBe("retained-key");
    expect(JSON.parse(String(init.body))).toEqual({ kind: "event", text: "核验财报变化" });
  });
});
