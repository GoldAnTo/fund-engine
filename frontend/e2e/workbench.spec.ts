import { expect, test } from "@playwright/test";

test("clearly identifies the demo and never reports unsupported actions as successful", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/?client=mock");
  await expect(page.getByText(/演示模式：未连接 API/)).toBeVisible();
  await expect(page.getByRole("button", { name: /分享/ })).toBeDisabled();
  await expect(page.getByRole("button", { name: /导出/ })).toBeDisabled();
  await expect(page.getByRole("heading", { name: "科创板半导体设备国产化机会研究" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("switching threads isolates research evidence and local messages", async ({ page }) => {
  await page.goto("/?client=mock");
  await page.getByLabel("向研究团队发送消息").fill("浏览器回归：核验订单");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("浏览器回归：核验订单", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /AI 算力产业链投资机会 已归档/ }).click();
  await expect(page.getByRole("heading", { name: "AI 算力产业链投资机会", exact: true })).toBeVisible();
  await expect(page.getByText("浏览器回归：核验订单", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: /中微公司：刻蚀设备/ })).toHaveCount(0);
  await page.getByRole("button", { name: /科创板半导体设备国产化机会研究 进行中/ }).click();
  await expect(page.getByText("浏览器回归：核验订单", { exact: true })).toBeVisible();
});

test("search, pending filter and keyboard evidence tabs work", async ({ page }) => {
  await page.goto("/?client=mock");
  await page.getByRole("searchbox").fill("存储");
  await expect(page.getByRole("button", { name: /存储芯片周期与供需研究/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /海外半导体设备龙头跟踪/ })).toHaveCount(0);
  await page.getByRole("checkbox", { name: "仅看待办" }).check();
  await expect(page.getByRole("heading", { name: "研究目标", exact: true })).toHaveCount(0);
  const evidence = page.getByRole("tab", { name: "证据与来源", exact: true });
  await evidence.press("ArrowRight");
  await expect(page.getByRole("tab", { name: /待解决问题/ })).toBeFocused();
  await expect(page.getByRole("tabpanel", { name: /待解决问题/ })).toBeVisible();
});

test("layout remains within viewport and focus can reach research", async ({ page }) => {
  await page.goto("/?client=mock");
  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "跳到研究内容" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
});
