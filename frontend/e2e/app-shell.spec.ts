import { test, expect } from "@playwright/test";

// 新应用外壳（PrototypeShell）：主题为一等公民的导航、面包屑、全局搜索。
// 所有断言只针对结构与可访问性锚点，不依赖 mock / 真实后端的具体数据。

test.describe("App shell: navigation and routing", () => {
  test("root renders the theme index inside the prototype shell", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("theme-index-screen")).toBeVisible();
    const nav = page.getByRole("navigation", { name: "主导航" }).first();
    await expect(nav).toBeVisible();
    for (const label of ["主题", "工作台", "数据中心", "审核中心"]) {
      await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
    }
  });

  test("unknown routes redirect to the theme index instead of a blank page", async ({ page }) => {
    await page.goto("/no-such-page");
    await expect(page).toHaveURL(/\/themes$/);
    await expect(page.getByTestId("theme-index-screen")).toBeVisible();
  });

  test("breadcrumbs reflect the current module and page", async ({ page }) => {
    await page.goto("/data");
    const breadcrumbs = page.getByLabel("当前位置");
    await expect(breadcrumbs).toBeVisible();
    await expect(breadcrumbs).toContainText("数据中心");
  });

  test("primary nav links reach their screens", async ({ page }) => {
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: "主导航" }).first();
    await nav.getByRole("link", { name: "工作台" }).click();
    await expect(page.getByTestId("overview-screen")).toBeVisible();
    await nav.getByRole("link", { name: "数据中心" }).click();
    await expect(page.getByTestId("data-center-screen")).toBeVisible();
    await nav.getByRole("link", { name: "审核中心" }).click();
    await expect(page.getByTestId("review-screen")).toBeVisible();
  });
});

test.describe("App shell: global search", () => {
  test("search box opens a result listbox after two characters", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("searchbox", { name: "搜索研究、命题和证据" });
    await input.fill("AI");
    const listbox = page.getByRole("listbox");
    await expect(listbox).toBeVisible();
    // mock 与真实后端都必须给出结果或诚实的空态
    await expect(
      listbox.getByRole("option").first(),
    ).toBeVisible();
  });

  test("nonsense query yields an honest empty state", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("searchbox", { name: "搜索研究、命题和证据" });
    await input.fill("绝不存在的词");
    await expect(page.getByRole("listbox").getByText("无匹配结果")).toBeVisible();
  });

  test("Escape clears the query and closes the listbox", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("searchbox", { name: "搜索研究、命题和证据" });
    await input.fill("AI");
    await expect(page.getByRole("listbox")).toBeVisible();
    await input.press("Escape");
    await expect(page.getByRole("listbox")).toHaveCount(0);
    await expect(input).toHaveValue("");
  });
});
