import type { APIRequestContext, Page, TestInfo } from "@playwright/test";
import { expect } from "@playwright/test";

import { authenticatedApi } from "../fixtures/auth";
import { caseIdFromUrl } from "./workflow";


export type RestartService = "api" | "acquisition-worker" | "research-worker" | "scheduler";

export type RestartWorkflow = {
  orchestration_id: string;
  research_run_id: string | null;
  research_execution: null | {
    job_id: string;
    status: string;
    step: string | null;
    attempt: number;
    failure_count: number;
    started_at: string | null;
    finished_at: string | null;
    recovery_count: number;
    last_recovered_at: string | null;
  };
  event: { case_id: string; title: string; research_question: string };
  scope: { id: string; version: number };
  state: string;
  version: number;
  user_action: unknown | null;
  events: Array<{ id: string; sequence: number; transition: string }>;
  acquisition: {
    rounds: Array<{
      job_id: string;
      query_plan_id: string;
      status: string;
      stage: string;
      attempt: number;
      round: number;
      goal_id: string;
    }>;
  };
  monitor: null | {
    id: string;
    version: number;
    status: string;
    frequency: string;
    factor_ids: string[];
    source_types: string[];
    next_verification_event: string;
  };
};

export type RestartLedgerItem = {
  record_id: string;
  record_type: string;
  attempt_id: string | null;
  evidence_link_id: string | null;
  source_url: string | null;
  final_url: string | null;
  content_sha256: string | null;
};

export async function createLiveCase(page: Page): Promise<{
  caseId: string;
  orchestrationId: string;
  researchRunId: string;
  scopeVersionId: string;
}> {
  await page.goto("/events/new");
  await page.getByLabel("来源接入方式").selectOption("pasted_snapshot");
  await page.getByLabel("来源权威性").selectOption("user_supplied");
  await page.getByLabel("事件原始输入").fill(
    "皇马科技（603181.SH）披露2026年上半年营业收入与净利润增长。请核验电子化学品、供应链以及可能的替代解释。",
  );
  await page.getByRole("button", { name: "识别事件与研究问题" }).click();
  await expect(page.getByRole("heading", { name: "确认可验证的研究范围" })).toBeVisible();
  const confirmationResponse = page.waitForResponse((response) =>
    response.url().endsWith("/workflow/confirm") && response.status() === 202,
  );
  await page.getByRole("button", { name: "建立 Case，进入资料核验" }).click();
  const confirmation = await (await confirmationResponse).json() as {
    orchestration_id: string;
    research_run_id: string | null;
    scope_version_id: string;
  };
  await page.waitForURL(/\/events\/[0-9a-f-]{36}$/i);
  if (!confirmation.research_run_id) throw new Error("workflow confirmation returned no research run");
  return {
    caseId: caseIdFromUrl(page),
    orchestrationId: confirmation.orchestration_id,
    researchRunId: confirmation.research_run_id,
    scopeVersionId: confirmation.scope_version_id,
  };
}

export async function waitForWorkflow(
  page: Page,
  caseId: string,
  predicate: (workflow: RestartWorkflow) => boolean,
): Promise<RestartWorkflow> {
  let latest: RestartWorkflow | null = null;
  await expect.poll(async () => {
    const response = await authenticatedApi<RestartWorkflow>(
      page,
      `/api/v1/event-research/${caseId}/workflow`,
    );
    if (response.status !== 200) return false;
    latest = response.body;
    return predicate(response.body);
  }, {
    timeout: Number(process.env.LIVE_WORKFLOW_TIMEOUT_MS ?? "900000"),
    intervals: [250, 500, 1000, 2000],
    message: `workflow ${caseId} did not reach the required persisted state`,
  }).toBe(true);
  if (!latest) throw new Error("workflow polling completed without a snapshot");
  return latest;
}

export async function readCompleteLedger(
  page: Page,
  caseId: string,
): Promise<RestartLedgerItem[]> {
  const items: RestartLedgerItem[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) query.set("cursor", cursor);
    const response = await authenticatedApi<{
      items: RestartLedgerItem[];
      has_more: boolean;
      next_cursor: string | null;
    }>(page, `/api/v1/event-research/${caseId}/workflow/ledger?${query.toString()}`);
    expect(response.status, response.text).toBe(200);
    items.push(...response.body.items);
    if (response.body.has_more && !response.body.next_cursor) {
      throw new Error("ledger page claims more records without a cursor");
    }
    cursor = response.body.has_more ? response.body.next_cursor : null;
  } while (cursor);
  return items;
}

export async function restartService(
  request: APIRequestContext,
  service: RestartService,
  testInfo: TestInfo,
) {
  const controlUrl = process.env.LIVE_CONTROL_URL?.replace(/\/$/, "");
  const token = process.env.LIVE_CONTROL_TOKEN;
  if (!controlUrl || !token) throw new Error("LIVE_CONTROL_URL and LIVE_CONTROL_TOKEN are required");
  const response = await request.post(`${controlUrl}/restart/${service}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const text = await response.text();
  expect(response.status(), text).toBe(200);
  const result = JSON.parse(text) as {
    service: RestartService;
    operation: "restart";
    requested_at: string;
    completed_at: string;
    result: string;
    before: null | {
      id: string;
      started_at: string;
      restart_count: number;
      instances: Array<{ id: string; started_at: string; restart_count: number }>;
    };
    after: null | {
      id: string;
      started_at: string;
      restart_count: number;
      instances: Array<{ id: string; started_at: string; restart_count: number }>;
    };
  };
  expect(result).toMatchObject({
    service,
    operation: "restart",
    result: "container_healthy_after_restart",
  });
  expect(result.before, "restart proof must include the original container").not.toBeNull();
  expect(result.after, "restart proof must include the replacement container").not.toBeNull();
  expect(result.before!.instances.length).toBeGreaterThan(0);
  expect(result.after!.instances).toHaveLength(result.before!.instances.length);
  expect(result.after!.instances.every((instance) =>
    Date.parse(instance.started_at) >= Date.parse(result.requested_at),
  )).toBe(true);
  expect(Date.parse(result.after!.started_at)).toBeGreaterThanOrEqual(
    Date.parse(result.requested_at),
  );
  await testInfo.attach(`${service}-restart.json`, {
    body: JSON.stringify(result, null, 2),
    contentType: "application/json",
  });
  return result;
}

export async function stopScheduler(
  request: APIRequestContext,
  testInfo: TestInfo,
) {
  const controlUrl = process.env.LIVE_CONTROL_URL?.replace(/\/$/, "");
  const token = process.env.LIVE_CONTROL_TOKEN;
  if (!controlUrl || !token) throw new Error("LIVE_CONTROL_URL and LIVE_CONTROL_TOKEN are required");
  const response = await request.post(`${controlUrl}/stop/scheduler`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const text = await response.text();
  expect(response.status(), text).toBe(200);
  const result = JSON.parse(text) as {
    service: "scheduler";
    operation: "stop";
    requested_at: string;
    completed_at: string;
    result: "stopped";
  };
  expect(result).toMatchObject({
    service: "scheduler",
    operation: "stop",
    result: "stopped",
  });
  await testInfo.attach("scheduler-stop.json", {
    body: JSON.stringify(result, null, 2),
    contentType: "application/json",
  });
  return result;
}

export async function waitForServiceHeartbeatAfter(
  page: Page,
  service: Exclude<RestartService, "api">,
  containerStartedAt: string,
) {
  let latest: string | null = null;
  await expect.poll(async () => {
    const response = await authenticatedApi<{
      services: Array<{
        name: string;
        status: string;
        state: string | null;
        last_seen_at: string | null;
      }>;
    }>(page, "/api/v1/runtime-status");
    if (response.status !== 200) return false;
    const runtime = response.body.services.find((item) => item.name === service);
    latest = runtime?.last_seen_at ?? null;
    return runtime?.status === "healthy"
      && latest !== null
      && Date.parse(latest) > Date.parse(containerStartedAt);
  }, {
    timeout: 120_000,
    intervals: [250, 500, 1000, 2000],
    message: `${service} did not publish a fresh post-restart heartbeat`,
  }).toBe(true);
  return latest;
}

export function expectIdentityPreserved(
  workflow: RestartWorkflow,
  identity: {
    orchestrationId: string;
    researchRunId: string;
    scopeVersionId: string;
  },
) {
  expect(workflow.orchestration_id).toBe(identity.orchestrationId);
  expect(workflow.research_run_id).toBe(identity.researchRunId);
  expect(workflow.scope.id).toBe(identity.scopeVersionId);
}

export function planIds(workflow: RestartWorkflow): Map<string, string> {
  return new Map(
    workflow.acquisition.rounds.map((round) => [round.job_id, round.query_plan_id]),
  );
}

export function planIdentity(workflow: RestartWorkflow): string[] {
  return workflow.acquisition.rounds
    .map((round) => [round.goal_id, round.round, round.job_id, round.query_plan_id].join("|"))
    .sort();
}

export async function attachRecoveryProof(
  testInfo: TestInfo,
  label: string,
  caseId: string,
  workflow: RestartWorkflow,
) {
  await testInfo.attach(`${label}-workflow-proof.json`, {
    body: JSON.stringify({
      caseId,
      orchestrationId: workflow.orchestration_id,
      researchRunId: workflow.research_run_id,
      scopeVersionId: workflow.scope.id,
      scopeVersion: workflow.scope.version,
      state: workflow.state,
      version: workflow.version,
      eventIds: workflow.events.map((event) => event.id),
      latestEventSequence: Math.max(0, ...workflow.events.map((event) => event.sequence)),
      rounds: workflow.acquisition.rounds.map((round) => ({
        jobId: round.job_id,
        queryPlanId: round.query_plan_id,
        status: round.status,
        stage: round.stage,
        attempt: round.attempt,
      })),
      researchExecution: workflow.research_execution,
    }, null, 2),
    contentType: "application/json",
  });
}
