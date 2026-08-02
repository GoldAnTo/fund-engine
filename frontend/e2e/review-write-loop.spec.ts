import { test, expect } from "@playwright/test";

// 核心写入闭环：审核决策写入 → 队列状态变化 → 审计入口 → 快照版本页。
//
// 通过 ?client=mock 强制内存 mock 适配器（见 main.tsx 测试钩子），
// 保证决策只写入浏览器内存，绝不触碰真实后端。
// mock 的 getVersionsView 是冻结 fixture，不随审核写入变化，
// 因此闭环断言到「审计入口可达快照版本页」为止；
// 版本数据随决策变化的路径由后端集成测试覆盖。

test.describe("Review decision write loop (mock client)", () => {
  test("submitting a decision shrinks the queue and unlocks the snapshot link", async ({ page }) => {
    await page.goto("/review?client=mock");
    const screen = page.getByTestId("review-screen");
    await expect(screen).toBeVisible();

    // mock fixture 队列有 2 项待审
    const queueHeading = screen.getByRole("heading", { name: /^\d+ 项等待$/ });
    await expect(queueHeading).toBeVisible();
    const before = Number((await queueHeading.textContent())?.match(/\d+/)?.[0]);
    expect(before).toBeGreaterThan(0);

    // 填写审核人必填项：署名 + 审核理由（关系默认「支持」，边界有默认值）
    await page.getByLabel("审核人署名").fill("e2e 审核人");
    await page
      .getByPlaceholder(/写下你的核对结果/)
      .fill("e2e：已核对冻结原文与 AI 提议，确认关系成立。");

    await page.getByRole("button", { name: "确认并写入审核记忆" }).click();

    // 写入成功后：审计计数出现，队列减一
    await expect(screen.getByText(/本次会话已写入 1 项人工决策/)).toBeVisible();
    await expect(
      screen.getByRole("heading", { name: new RegExp(`^${before - 1} 项等待$`) }),
    ).toBeVisible();

    // 审计入口：从审核页跳到快照版本页，闭环可达
    await screen.getByRole("link", { name: /去查看快照版本/ }).click();
    await expect(page).toHaveURL(/\/versions/);
    await expect(page.getByTestId("versions-screen")).toBeVisible();
  });

  test("mock mode never sends review writes to the live API", async ({ page }) => {
    const apiCalls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/") || req.url().includes("/v1/")) {
        apiCalls.push(req.url());
      }
    });
    await page.goto("/review?client=mock");
    await expect(page.getByTestId("review-screen")).toBeVisible();
    expect(apiCalls).toEqual([]);
  });
});
