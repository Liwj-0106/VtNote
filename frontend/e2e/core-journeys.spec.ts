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

  await page
    .getByRole("button", { name: "收起侧边栏", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "展开侧边栏", exact: true }),
  ).toBeVisible();

  await page.goto("/settings");
  await expect(page.getByRole("complementary", { name: "主导航" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "通用", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "外观" })).toBeVisible();
  await expect(page.locator(".settings-workspace-header > .settings-eyebrow")).toHaveCount(0);
  await expect(page.locator(".settings-workspace-header > p")).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBe(0);
  const sidebarSettingsPosition = await page
    .getByRole("link", { name: "设置", exact: true })
    .evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, top: rect.top };
    });
  await page.getByRole("radio", { name: /深色/u }).check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await page.getByRole("link", { name: "导出", exact: true }).click();
  expect(
    await page
      .getByRole("link", { name: "设置", exact: true })
      .evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, top: rect.top };
      }),
  ).toEqual(sidebarSettingsPosition);
  await expect(page.getByRole("heading", { name: "导出", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "默认导出" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "导出格式" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "导出目录" })).toBeVisible();
  await page.getByRole("checkbox", { name: "总结" }).uncheck();
  await page.goto("/settings/models");
  expect(
    await page
      .getByRole("link", { name: "设置", exact: true })
      .evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, top: rect.top };
      }),
  ).toEqual(sidebarSettingsPosition);
  await expect(page.getByRole("heading", { name: "模型", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "语音模型" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "总结模型" })).toBeVisible();
  expect((await page.evaluate(() => Object.keys(localStorage))).sort()).toEqual([
    "vtnote.interface.v1",
    "vtnote.preferences.v1",
    "vtnote.sidebar.collapsed",
    "vtnote.taskQueue.ids",
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
