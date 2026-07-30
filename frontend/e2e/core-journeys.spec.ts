import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("create workspace is compact, accessible, and keeps only sidebar preference", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "把视频变成可继续处理的文字" }),
  ).toBeVisible();
  await expect(page.getByText("无法读取本地配置")).toHaveCount(0);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((violation) =>
      ["serious", "critical"].includes(violation.impact ?? ""),
    ),
  ).toEqual([]);

  await page.getByRole("button", { name: "收起侧边栏" }).click();
  await expect(
    page.getByRole("button", { name: "展开侧边栏" }),
  ).toBeVisible();
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([
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

test("settings exposes only domestic providers and metadata-only diagnostics", async ({
  page,
}) => {
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "设置" })).toBeVisible();
  await expect(page.getByText("腾讯云与百炼")).toBeVisible();
  await expect(page.getByText("诊断包")).toBeVisible();
  await expect(page.getByText(/OpenAI|国外 API|中转站/i)).toHaveCount(0);
});
