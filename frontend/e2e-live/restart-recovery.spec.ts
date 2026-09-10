import { authenticatedApi, expect, test } from "./fixtures/auth";
import {
  attachRecoveryProof,
  createLiveCase,
  expectIdentityPreserved,
  planIdentity,
  planIds,
  readCompleteLedger,
  restartService,
  stopScheduler,
  waitForServiceHeartbeatAfter,
  waitForWorkflow,
} from "./helpers/restart";
import { assertLiveRuntimeReady, observeNoMockResponses } from "./helpers/workflow";


type AcquisitionJobEvent = {
  seq: number;
  status: string;
  stage: string;
  message: string;
  payload: { attempt?: number };
  created_at: string;
};


test("API restart reloads the same persisted workflow", async ({ alicePage: page, request }, testInfo) => {
  const noMock = observeNoMockResponses(page);
  await assertLiveRuntimeReady(page, testInfo);
  const identity = await createLiveCase(page);
  const before = await waitForWorkflow(page, identity.caseId, (workflow) =>
    workflow.state === "acquiring" && workflow.acquisition.rounds.length > 0,
  );
  expectIdentityPreserved(before, identity);
  const beforePlans = planIds(before);
  const beforeEvents = new Set(before.events.map((event) => event.id));

  const restart = await restartService(request, "api", testInfo);
  await page.reload();
  await expect(page.getByText("系统正在做什么")).toBeVisible();
  const after = await waitForWorkflow(page, identity.caseId, (workflow) =>
    [
      "planning_acquisition",
      "acquiring",
      "freezing_sources",
      "assessing_coverage",
      "synthesizing_evidence",
      "adjudicating_thesis",
      "generating_report",
      "monitoring",
      "retry_wait",
      "recovering",
    ].includes(workflow.state),
  );
  expectIdentityPreserved(after, identity);
  expect(after.event.case_id).toBe(identity.caseId);
  await expect(page.getByRole("heading", { name: after.event.title, exact: true })).toBeVisible();
  expect(restart.after).not.toBeNull();
  expect(after.version).toBeGreaterThanOrEqual(before.version);
  expect(
    [...beforeEvents].every((eventId) => after.events.some((event) => event.id === eventId)),
  ).toBe(true);
  for (const [jobId, queryPlanId] of beforePlans) {
    expect(planIds(after).get(jobId)).toBe(queryPlanId);
  }
  await attachRecoveryProof(testInfo, "api-restart", identity.caseId, after);
  await noMock.assertClean();
});

test("acquisition-worker restart recovers the frozen plan without duplicate durable output", async ({ alicePage: page, request }, testInfo) => {
  const noMock = observeNoMockResponses(page);
  await assertLiveRuntimeReady(page, testInfo);
  const identity = await createLiveCase(page);
  const running = await waitForWorkflow(page, identity.caseId, (workflow) =>
    workflow.acquisition.rounds.some((round) => round.status === "running"),
  );
  expectIdentityPreserved(running, identity);
  const affected = running.acquisition.rounds.find((round) => round.status === "running")!;
  const frozenPlans = planIds(running);
  const frozenIdentity = planIdentity(running);
  const frozenGoalRounds = new Set(
    running.acquisition.rounds.map((round) => `${round.goal_id}|${round.round}`),
  );
  const eventsBefore = await authenticatedApi<AcquisitionJobEvent[]>(
    page,
    `/api/v1/acquisition-jobs/${affected.job_id}/events`,
  );
  expect(eventsBefore.status, eventsBefore.text).toBe(200);
  const observedClaim = eventsBefore.body.find((event) =>
    event.status === "running"
      && event.message === "worker claimed acquisition"
      && event.payload.attempt === affected.attempt,
  );
  expect(observedClaim, "the running lease must have a public claim event").toBeTruthy();
  const lastEventSeqBeforeCrash = Math.max(
    0,
    ...eventsBefore.body.map((event) => event.seq),
  );

  const restart = await restartService(request, "acquisition-worker", testInfo);
  await waitForServiceHeartbeatAfter(
    page,
    "acquisition-worker",
    restart.after!.started_at,
  );
  const recovered = await waitForWorkflow(page, identity.caseId, (workflow) => {
    const sameJob = workflow.acquisition.rounds.find(
      (round) => round.job_id === affected.job_id,
    );
    return Boolean(sameJob && sameJob.attempt > affected.attempt);
  });
  const recoveredFrozenIdentity = planIdentity(recovered).filter((identityKey) => {
    const [goalId, round] = identityKey.split("|");
    return frozenGoalRounds.has(`${goalId}|${round}`);
  });
  expect(recoveredFrozenIdentity).toEqual(frozenIdentity);
  const eventsAfter = await authenticatedApi<AcquisitionJobEvent[]>(
    page,
    `/api/v1/acquisition-jobs/${affected.job_id}/events`,
  );
  expect(eventsAfter.status, eventsAfter.text).toBe(200);
  const recoveredClaim = eventsAfter.body.find((event) =>
    event.seq > lastEventSeqBeforeCrash
      && event.status === "running"
      && event.message === "worker claimed acquisition"
      && (event.payload.attempt ?? 0) > affected.attempt,
  );
  expect(recoveredClaim, "the expired lease must be claimed again after the crash").toBeTruthy();
  expect(
    eventsAfter.body.filter((event) =>
      event.seq > observedClaim!.seq
        && event.seq < recoveredClaim!.seq
        && event.status === "retry_wait"),
    "provider retry_wait must not be mistaken for crash lease recovery",
  ).toEqual([]);
  const completed = await waitForWorkflow(page, identity.caseId, (workflow) =>
    workflow.state === "monitoring",
  );
  expectIdentityPreserved(completed, identity);
  for (const [jobId, queryPlanId] of frozenPlans) {
    expect(planIds(completed).get(jobId)).toBe(queryPlanId);
  }

  const ledger = await readCompleteLedger(page, identity.caseId);
  const artifacts = ledger.filter((item) => item.record_type === "retrieval_artifact");
  expect(artifacts.length).toBeGreaterThan(0);
  expect(artifacts.every((item) => Boolean(item.attempt_id))).toBe(true);
  const artifactAttemptKeys = artifacts.map((item) =>
    [item.attempt_id, item.source_url, item.final_url, item.content_sha256].join("|"),
  );
  expect(new Set(artifactAttemptKeys).size).toBe(artifactAttemptKeys.length);
  const evidenceIds = ledger
    .map((item) => item.evidence_link_id)
    .filter((value): value is string => Boolean(value));
  expect(new Set(evidenceIds).size).toBe(evidenceIds.length);
  expect(completed.acquisition.rounds.every((round) => round.attempt >= 1)).toBe(true);
  await attachRecoveryProof(
    testInfo,
    "acquisition-worker-restart",
    identity.caseId,
    completed,
  );
  await noMock.assertClean();
});

test("research-worker and scheduler restarts resume checkpoints without manual continuation", async ({ alicePage: page, request }, testInfo) => {
  const noMock = observeNoMockResponses(page);
  await assertLiveRuntimeReady(page, testInfo);
  const identity = await createLiveCase(page);
  const synthesizing = await waitForWorkflow(page, identity.caseId, (workflow) =>
    ["synthesizing_evidence", "adjudicating_thesis", "generating_report"].includes(
      workflow.state,
    )
      && workflow.research_execution?.status === "running"
      && Boolean(workflow.research_execution.started_at),
  );
  expectIdentityPreserved(synthesizing, identity);
  const frozenPlans = planIds(synthesizing);
  const execution = synthesizing.research_execution!;

  const researchRestart = await restartService(request, "research-worker", testInfo);
  await waitForServiceHeartbeatAfter(
    page,
    "research-worker",
    researchRestart.after!.started_at,
  );
  const recovered = await waitForWorkflow(page, identity.caseId, (workflow) =>
    workflow.research_execution?.job_id === execution.job_id
      && workflow.research_execution.attempt > execution.attempt
      && workflow.research_execution.recovery_count > execution.recovery_count,
  );
  expectIdentityPreserved(recovered, identity);

  const monitoring = await waitForWorkflow(page, identity.caseId, (workflow) =>
    workflow.state === "monitoring",
  );
  expectIdentityPreserved(monitoring, identity);
  expect(monitoring.user_action).toBeNull();
  for (const [jobId, queryPlanId] of frozenPlans) {
    expect(planIds(monitoring).get(jobId)).toBe(queryPlanId);
  }

  await stopScheduler(request, testInfo);
  const monitorBefore = await authenticatedApi<{
    monitor: null | {
      id: string;
      version: number;
      factor_ids: string[];
      allowed_source_types: Array<"licensed_provider" | "company_disclosure" | "uploaded_file" | "pasted_snapshot">;
      next_verification_event: string;
      budget: number;
    };
    latest_run: null | { id: string };
  }>(page, `/api/v1/research-cases/${identity.caseId}/monitor`);
  expect(monitorBefore.status, monitorBefore.text).toBe(200);
  expect(monitorBefore.body.monitor).not.toBeNull();
  const savedMonitor = await authenticatedApi<{ id: string; version: number }>(
    page,
    `/api/v1/research-cases/${identity.caseId}/monitor`,
    {
      method: "PUT",
      body: {
        frequency: "acceptance_every_5_minutes",
        factor_ids: monitorBefore.body.monitor!.factor_ids,
        allowed_source_types: monitorBefore.body.monitor!.allowed_source_types,
        next_verification_event: "浏览器主链验证 scheduler 重启后真实调度",
        budget: monitorBefore.body.monitor!.budget,
        change_reason: "无数据库直写地验证 scheduler 重启与真实调度",
      },
    },
  );
  expect(savedMonitor.status, savedMonitor.text).toBe(200);
  expect(savedMonitor.body.version).toBeGreaterThan(monitorBefore.body.monitor!.version);

  const schedulerRestart = await restartService(request, "scheduler", testInfo);
  await waitForServiceHeartbeatAfter(
    page,
    "scheduler",
    schedulerRestart.after!.started_at,
  );
  let scheduledRunId: string | null = null;
  await expect.poll(async () => {
    const detail = await authenticatedApi<{
      latest_run: null | { id: string };
    }>(page, `/api/v1/research-cases/${identity.caseId}/monitor`);
    if (detail.status !== 200) return false;
    scheduledRunId = detail.body.latest_run?.id ?? null;
    return Boolean(
      scheduledRunId
      && scheduledRunId !== monitorBefore.body.latest_run?.id,
    );
  }, {
    timeout: 120_000,
    intervals: [250, 500, 1000, 2000],
    message: "scheduler did not create a new durable monitor run after restart",
  }).toBe(true);
  const runEvents = await authenticatedApi<{
    items: Array<{ stage: string | null; details: Record<string, unknown> }>;
  }>(page, `/api/v1/research-runs/${scheduledRunId}/events`);
  expect(runEvents.status, runEvents.text).toBe(200);
  expect(
    runEvents.body.items.some((event) =>
      event.stage === "scope" && event.details.trigger === "schedule"),
  ).toBe(true);
  await page.reload();
  await expect(page.getByText("当前无需操作")).toBeVisible();
  await expect(page.getByText("系统生成，未经人工审核", { exact: true })).toBeVisible();
  await attachRecoveryProof(
    testInfo,
    "research-scheduler-restart",
    identity.caseId,
    monitoring,
  );
  await noMock.assertClean();
});
