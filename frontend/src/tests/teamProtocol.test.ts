import { describe, expect, it, vi } from "vitest";
import { HttpGatewayClient, GatewaySchemaError } from "@/gateway/HttpGatewayClient";
import { teamWire, teamReceipt, teamConversationId as cid, teamRunId as rid, teamId } from "./teamFixtures";
import liveTeamWire from "./fixtures/fundclaw-team-http-final.json";

describe("professional team protocol", () => {
  it("accepts the live professional attempt DTO with repaired present and no extra keys", async () => {
    const wire = structuredClone(liveTeamWire);
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(wire)));
    const client = new HttpGatewayClient({ fetch });

    const team = await client.getTeam(wire.conversation_id, wire.run_spec_id);

    expect(team.tasks).toHaveLength(wire.tasks.length);
    expect(team.tasks.flatMap((task) => task.attempts).every((attempt) => attempt.repaired === false)).toBe(true);
    const invalid = structuredClone(wire);
    Object.assign(invalid.tasks[0]!.attempts[0]!, { unexpected_backend_key: true });
    await expect(new HttpGatewayClient({ fetch: vi.fn().mockResolvedValue(new Response(JSON.stringify(invalid))) }).getTeam(wire.conversation_id, wire.run_spec_id)).rejects.toBeInstanceOf(GatewaySchemaError);
  });

  it("reads the real scoped team and preserves unknown usage", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(teamWire())));
    const client = new HttpGatewayClient({ fetch });
    const team = await client.getTeam(cid, rid);
    expect(team.tasks).toHaveLength(4);
    expect(team.tasks[0]!.attempts[0]!.total_tokens).toBeNull();
    expect(String(fetch.mock.calls[0]![0])).toBe(`/api/v1/research-conversations/${cid}/runs/${rid}/team`);
    expect(fetch.mock.calls[0]![1]).toMatchObject({ cache: "no-store", credentials: "include" });
  });
  it.each(["scope", "withheld-output", "foreign-citation", "negative-tokens", "unknown-role", "future-revision", "duplicate-task", "foreign-dependency"])("rejects %s", async (variant) => {
    const wire = teamWire();
    if (variant === "scope") wire.run_spec_id = teamId(90);
    if (variant === "withheld-output") wire.tasks[0]!.output_state = "withheld";
    if (variant === "foreign-citation") wire.tasks[0]!.output.content.findings[0]!.citations[0]!.evidence_link_id = teamId(90);
    if (variant === "negative-tokens") Object.assign(wire.tasks[0]!.attempts[0]!, { total_tokens: -1 });
    if (variant === "unknown-role") wire.tasks[0]!.role = "administrator";
    if (variant === "future-revision") wire.tasks[0]!.revision = 2;
    if (variant === "duplicate-task") wire.tasks.push(wire.tasks[0]!);
    if (variant === "foreign-dependency") wire.tasks[0]!.dependency_ids = [teamId(90)];
    await expect(new HttpGatewayClient({ fetch: vi.fn().mockResolvedValue(new Response(JSON.stringify(wire))) }).getTeam(cid, rid)).rejects.toBeInstanceOf(GatewaySchemaError);
  });
  it("sends exactly the revision-bound directed message with its idempotency key", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(teamReceipt())));
    const client = new HttpGatewayClient({ fetch });
    await client.sendTeamMessage(cid, rid, { text: "请核对现金流", recipient: "finance", expected_revision: 1, idempotencyKey: "same-key" });
    expect(String(fetch.mock.calls[0]![0])).toMatch(/\/team\/messages$/);
    expect(JSON.parse(fetch.mock.calls[0]![1].body)).toEqual({ text: "请核对现金流", recipient: "finance", expected_revision: 1 });
    expect(new Headers(fetch.mock.calls[0]![1].headers).get("Idempotency-Key")).toBe("same-key");
  });
  it("posts control and human-review payloads without changing output IDs", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(teamReceipt())));
    const client = new HttpGatewayClient({ fetch });
    await client.commandTeam(cid, rid, { kind: "retry", task_id: teamId(10), expected_revision: 1, idempotencyKey: "retry-key" });
    const output_ids = [20, 21, 22, 23].map(teamId);
    await client.reviewTeam(cid, rid, { decision: "changes_requested", comment: "请补齐期间", output_ids, expected_revision: 1, idempotencyKey: "review-key" });
    expect(JSON.parse(fetch.mock.calls[0]![1].body)).toEqual({ kind: "retry", task_id: teamId(10), expected_revision: 1 });
    expect(JSON.parse(fetch.mock.calls[1]![1].body)).toEqual({ decision: "changes_requested", comment: "请补齐期间", output_ids, expected_revision: 1 });
  });
  it("rejects an acknowledgement for a different run", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...teamReceipt(), run_spec_id: teamId(99) })));
    await expect(new HttpGatewayClient({ fetch }).commandTeam(cid, rid, { kind: "pause", expected_revision: 1, idempotencyKey: "key" })).rejects.toBeInstanceOf(GatewaySchemaError);
  });
});
