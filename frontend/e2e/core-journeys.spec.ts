import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("desktop core navigation is accessible and persists supported preferences", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "把音视频变成字幕和笔记" }),
  ).toBeVisible();
  await expect(page.getByText("无法读取本地配置")).toHaveCount(0);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact ?? ""),
    ),
  ).toEqual([]);

  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await expect(page.getByRole("button", { name: "展开侧边栏" })).toBeVisible();

  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "处理与导出" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "语音识别" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "AI 笔记" })).toBeVisible();
  await page.getByRole("checkbox", { name: "AI 笔记" }).uncheck();
  expect((await page.evaluate(() => Object.keys(localStorage))).sort()).toEqual([
    "vtnote.preferences.v1",
    "vtnote.sidebar.collapsed",
  ]);
});

test("mobile drawer traps initial focus, closes with Escape, and never overflows", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.locator(".mobile-close")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.locator(".sidebar")).toHaveAttribute(
    "data-mobile-open",
    "false",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
});
