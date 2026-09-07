import { expect, test } from "@playwright/test";

test("a human decision reviews a source-bound candidate through the real preparation worker", async ({ page, request }) => {
  const response = await request.post("/api/v1/event-research", { data: {
    event_title: "人工审核浏览器验收",
    raw_input: "测试企业披露本季度收入增长百分之十；此材料仅用于隔离测试。",
    research_question: "收入增长是否可持续？",
    candidate_factors: ["订单延续", "产品价格", "产能利用"],
    source_type: "pasted_snapshot", created_by: "浏览器测试", research_protocol_required: true,
  } });
  expect(response.status()).toBe(201);
  const { case_id: caseId } = await response.json();
  await expect.poll(async () => {
    const prep = await request.get(`/api/v1/event-research/${caseId}/preparation`);
    expect(prep.ok()).toBeTruthy();
    return (await prep.json()).review.claims.state;
  }, { timeout: 20_000 }).toBe("awaiting_review");
  await page.goto(`/?caseId=${caseId}`);
  await page.getByLabel("操作人", { exact: true }).fill("人工审核验收");
  await expect(page.getByRole("combobox", { name: "陈述 1 审核决定", exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "陈述 1 审核决定", exact: true }).selectOption("confirmed");
  await page.getByLabel("陈述 1 审核理由", { exact: true }).fill("已核对页面所展示的原文和引用范围。");
  await page.screenshot({ path: "test-results/preparation-review.png", fullPage: true });
  const confirmed = page.waitForResponse((r) => r.url().endsWith(`/event-research/${caseId}/preparation/claims/confirm`) && r.request().method() === "POST");
  await page.getByRole("button", { name: "提交陈述审核", exact: true }).click();
  expect((await confirmed).status()).toBe(200);
  const persisted = await request.get(`/api/v1/event-research/${caseId}/preparation`);
  expect((await persisted.json()).review.claims.state).toBe("confirmed");
  await page.reload();
  await expect(page.getByRole("heading", { name: "人工审核浏览器验收", exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "陈述 1 审核决定", exact: true })).toHaveCount(0);
});
