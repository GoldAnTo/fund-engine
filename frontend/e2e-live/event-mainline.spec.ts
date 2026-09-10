import path from "node:path";
import { fileURLToPath } from "node:url";

import { authenticatedApi, expect, test } from "./fixtures/auth";
import {
  assertLiveRuntimeReady,
  caseIdFromUrl,
  observeNoMockResponses,
} from "./helpers/workflow";


const fixture = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "fixtures",
  "huangma-2026h1-intake.txt",
);
const workflowTimeout = Number(process.env.LIVE_WORKFLOW_TIMEOUT_MS ?? "600000");

type LedgerItem = {
  record_id: string;
  record_type: string;
  status: string;
  adapter_key: string | null;
  attempt_id: string | null;
  source_url: string | null;
  final_url: string | null;
  retrieved_at: string | null;
  content_sha256: string | null;
  publication_key: string | null;
  dedup_relation: string | null;
  admission_outcome: string | null;
  evidence_link_id: string | null;
};

type WorkflowProof = {
  orchestration_id: string;
  research_run_id: string;
  event: { case_id: string };
  scope: { id: string; version: number };
  state: string;
  acquisition: {
    rounds: Array<{
      series_id: string;
      query_plan_id: string;
      job_id: string;
      goal_id: string;
      ordered_query_count: number;
      planner_version: string;
      policy_version: string;
      status: string;
    }>;
  };
  source_ledger: {
    counts: { total: number; automatically_admitted: number; deduplicated: number };
    items: LedgerItem[];
    has_more: boolean;
  };
  coverage: Array<{
    goal_id: string;
    thesis_id: string | null;
    objective: string;
    contrary_search_completed: boolean;
    evidence_link_ids: string[];
    observed_authority_count: number;
    observed_independent_source_count: number;
  }>;
  conclusion: null | {
    evidence_link_ids: string[];
    citations: LedgerItem[];
    system_generated: boolean;
    human_reviewed: boolean;
  };
};

type LedgerPage = {
  items: LedgerItem[];
  has_more: boolean;
  next_cursor: string | null;
};

async function loadCompleteLedger(page: Parameters<typeof authenticatedApi>[0], caseId: string) {
  const items: LedgerItem[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor) query.set("cursor", cursor);
    const result = await authenticatedApi<LedgerPage>(
      page,
      `/api/v1/event-research/${caseId}/workflow/ledger?${query.toString()}`,
    );
    expect(result.status, result.text).toBe(200);
    items.push(...result.body.items);
    cursor = result.body.has_more ? result.body.next_cursor : null;
  } while (cursor);
  return items;
}

test("real OIDC user creates a file-backed Case and observes the governed mainline", async ({ alicePage: page }, testInfo) => {
  const noMock = observeNoMockResponses(page);
  await assertLiveRuntimeReady(page, testInfo);

  await page.getByRole("banner").getByRole("link", { name: "＋ 从事件开始" }).click();
  await page.waitForURL(/\/events\/new$/);
  await page.getByLabel("来源接入方式").selectOption("uploaded_file");
  await page.getByLabel("来源权威性").selectOption("user_supplied");
  await page.getByLabel("选择上传原件文件").setInputFiles(fixture);
  await expect(page.getByText(/原件待冻结 · huangma-2026h1-intake.txt/)).toBeVisible();
  await page.getByRole("button", { name: "识别事件与研究问题" }).click();
  await expect(page.getByRole("button", { name: "正在识别…" })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "确认可验证的研究范围" })).toBeVisible();
  await expect(page.getByLabel("研究主体（公司、机构或行业）")).toHaveValue("皇马科技", {
    timeout: workflowTimeout,
  });
  await expect(page.getByLabel("研究问题")).not.toHaveValue("", { timeout: workflowTimeout });
  await expect(page.getByLabel("关键因素 1")).not.toHaveValue("");
  await expect(page.getByLabel("关键因素 2")).not.toHaveValue("");
  await expect(page.getByLabel("关键因素 3")).not.toHaveValue("");
  await page.getByRole("button", { name: "建立 Case，进入资料核验" }).click();
  await page.waitForURL(/\/events\/[0-9a-f-]{36}$/i);
  const caseId = caseIdFromUrl(page);

  await expect(page.getByText("你现在应该做什么")).toBeVisible();
  await expect(page.getByText("系统正在做什么")).toBeVisible();
  await expect(page.getByText(/系统已开始规划资料获取|统一研究流程已存在/)).toBeVisible();
  await expect(page.getByText("系统生成，未经人工审核", { exact: true })).toBeVisible({
    timeout: workflowTimeout,
  });
  await expect(
    page.getByRole("list", { name: "事件研究六阶段" }).locator('[aria-current="step"]'),
  ).toContainText("报告与监测");

  const workflow = await authenticatedApi<WorkflowProof>(
    page,
    `/api/v1/event-research/${caseId}/workflow`,
  );
  expect(workflow.status, workflow.text).toBe(200);
  expect(workflow.body).toMatchObject({
    event: { case_id: caseId },
    state: "monitoring",
  });
  expect(workflow.body.acquisition.rounds.length).toBeGreaterThan(0);
  expect(
    workflow.body.acquisition.rounds.every((round) =>
      round.job_id
      && round.query_plan_id
      && round.ordered_query_count > 0
      && round.planner_version
      && round.policy_version),
  ).toBe(true);

  const ledger = await loadCompleteLedger(page, caseId);
  const frozen = ledger.filter((item) => item.record_type === "retrieval_artifact");
  expect(frozen.length).toBeGreaterThan(0);
  expect(frozen.every((item) => {
    const auditableUrl = item.final_url ?? item.source_url ?? "";
    const hasAdapterSpecificUrl = item.adapter_key === "gildata"
      ? /^gildata:\/\//.test(auditableUrl)
      : /^https?:\/\//.test(auditableUrl);
    return Boolean(item.retrieved_at)
      && /^[a-f0-9]{64}$/i.test(item.content_sha256 ?? "")
      && hasAdapterSpecificUrl;
  })).toBe(true);
  const configuredOfficialAdapters = new Set(
    (process.env.LIVE_OFFICIAL_ADAPTERS ?? "sse,szse,gildata")
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean),
  );
  expect(frozen.every((item) =>
    Boolean(item.adapter_key) && configuredOfficialAdapters.has(item.adapter_key!),
  )).toBe(true);
  const admissionDecisions = ledger.filter(
    (item) => item.record_type === "automatic_admission_decision",
  );
  expect(admissionDecisions.length).toBeGreaterThan(0);
  expect(admissionDecisions.every((item) =>
    Boolean(item.publication_key)
    && Boolean(item.content_sha256)
    && ["created", "supersedes", "content_duplicate", "publication_duplicate"].includes(
      item.dedup_relation ?? "",
    )
    && ["admitted", "quarantined"].includes(item.admission_outcome ?? "")),
  ).toBe(true);
  expect(workflow.body.coverage.length).toBeGreaterThan(0);
  const thesisIds = new Set(
    workflow.body.coverage
      .filter((goal) => goal.objective === "support" && goal.thesis_id)
      .map((goal) => goal.thesis_id),
  );
  const contraryGoals = workflow.body.coverage.filter(
    (goal) => goal.objective === "contradict",
  );
  expect(contraryGoals.length).toBe(thesisIds.size);
  expect(contraryGoals.every((goal) =>
    Boolean(goal.thesis_id)
    && thesisIds.has(goal.thesis_id)
    && goal.contrary_search_completed,
  )).toBe(true);
  expect(workflow.body.conclusion?.system_generated).toBe(true);
  expect(workflow.body.conclusion?.human_reviewed).toBe(false);
  expect(workflow.body.conclusion?.citations.length ?? 0).toBeGreaterThan(0);
  const admittedEvidenceIds = new Set(
    admissionDecisions
      .filter((item) => item.admission_outcome === "admitted")
      .map((item) => item.evidence_link_id)
      .filter((value): value is string => Boolean(value)),
  );
  expect(
    workflow.body.conclusion?.citations.every((citation) =>
      Boolean(citation.evidence_link_id)
      && admittedEvidenceIds.has(citation.evidence_link_id!),
    ),
  ).toBe(true);

  const expandLedger = page.getByRole("button", { name: "展开完整资料台账" });
  if (await expandLedger.count()) await expandLedger.click();
  await expect(page.locator(".event-workflow-ledger__row").first()).toBeVisible();

  const proof = {
    caseId,
    orchestrationId: workflow.body.orchestration_id,
    researchRunId: workflow.body.research_run_id,
    scope: { id: workflow.body.scope.id, version: workflow.body.scope.version },
    state: workflow.body.state,
    acquisition: workflow.body.acquisition.rounds.map((round) => ({
      seriesId: round.series_id,
      queryPlanId: round.query_plan_id,
      jobId: round.job_id,
      goalId: round.goal_id,
      orderedQueryCount: round.ordered_query_count,
      plannerVersion: round.planner_version,
      policyVersion: round.policy_version,
      status: round.status,
    })),
    ledger: ledger.map((item) => ({
      recordId: item.record_id,
      recordType: item.record_type,
      status: item.status,
      adapterKey: item.adapter_key,
      attemptId: item.attempt_id,
      contentSha256: item.content_sha256,
      dedupRelation: item.dedup_relation,
      admissionOutcome: item.admission_outcome,
      evidenceLinkId: item.evidence_link_id,
    })),
    coverage: workflow.body.coverage.map((goal) => ({
      goalId: goal.goal_id,
      contrarySearchCompleted: goal.contrary_search_completed,
      observedAuthorityCount: goal.observed_authority_count,
      observedIndependentSourceCount: goal.observed_independent_source_count,
      evidenceLinkCount: goal.evidence_link_ids.length,
    })),
    conclusion: {
      evidenceLinkIds: workflow.body.conclusion?.evidence_link_ids ?? [],
      citationRecordIds: workflow.body.conclusion?.citations.map((item) => item.record_id) ?? [],
      citationHashes: workflow.body.conclusion?.citations.map((item) => item.content_sha256) ?? [],
      systemGenerated: workflow.body.conclusion?.system_generated,
      humanReviewed: workflow.body.conclusion?.human_reviewed,
    },
  };
  await testInfo.attach("live-case-proof.json", {
    body: JSON.stringify(proof, null, 2),
    contentType: "application/json",
  });
  await noMock.assertClean();
});
