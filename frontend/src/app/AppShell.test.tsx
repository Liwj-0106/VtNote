import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";
import { RouterProvider } from "./router";

describe("AppShell", () => {
  it("collapses without navigation or network activity and stores one boolean", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(
      <RouterProvider initialPath="/tasks">
        <AppShell>
          <h1>任务</h1>
        </AppShell>
      </RouterProvider>,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "收起侧边栏" }),
    );

    expect(screen.getByTestId("app-shell")).toHaveAttribute(
      "data-sidebar",
      "collapsed",
    );
    expect(localStorage).toHaveLength(1);
    expect(localStorage.getItem("vtnote.sidebar.collapsed")).toBe("true");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("provides a skip link and closes the mobile drawer with Escape", async () => {
    render(
      <RouterProvider initialPath="/">
        <AppShell>
          <h1>新建任务</h1>
        </AppShell>
      </RouterProvider>,
    );

    expect(screen.getByRole("link", { name: "跳到主内容" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "打开导航" }),
    );
    expect(screen.getByRole("complementary", { name: "主导航" })).toHaveAttribute(
      "data-mobile-open",
      "true",
    );
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("complementary", { name: "主导航" })).toHaveAttribute(
      "data-mobile-open",
      "false",
    );
  });
});
