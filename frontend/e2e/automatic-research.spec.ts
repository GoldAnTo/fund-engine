import { expect, test } from "@playwright/test";

const automaticInputs = [
  {
    name: "research topic",
    value: "英伟达新产品会如何影响供应链？",
  },
  {
    name: "pasted source material",
    value: "公司公告：本季度新增产线已投产，但主要客户仍在调整订单节奏。",
  },
] as const;

for (const automaticInput of automaticInputs) {
  test(`one-click automatic research accepts ${automaticInput.name}`, async ({ page }) => {
    await page.goto("/events/new?client=mock");

    const input = page.getByLabel("研究主题或材料");
    await input.fill(automaticInput.value);
    await page.getByRole("button", { name: "开始自动研究" }).click();

    await expect(page).toHaveURL(/\/events\/automatic-case-\d+\/automatic-research$/);
    await expect(page.getByText("系统生成，未经人工审核")).toBeVisible();
    for (const stage of ["资料获取", "内容解析", "证据校验", "分析判断", "生成结论"]) {
      await expect(page.getByRole("heading", { name: stage })).toBeVisible();
    }

    const stats = page.getByRole("region", { name: "本次处理" });
    await expect(stats.getByText("资料来源", { exact: true }).locator("..")).toContainText("3");
    await expect(stats.getByText("跳过资料", { exact: true }).locator("..")).toContainText("1");
    const process = page.getByRole("main");
    await expect(process.getByRole("button", { name: /确认|审核|授权|发布/ })).toHaveCount(0);
    await expect(process.getByRole("link", { name: /确认|审核|授权|发布/ })).toHaveCount(0);
  });
}
