import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./AppShell";
import { InterfacePreferencesProvider } from "./interfacePreferences";
import { RouterProvider } from "./router";

function TestShell({ path, children }: { path: string; children: React.ReactNode }) {
  return (
    <RouterProvider initialPath={path}>
      <InterfacePreferencesProvider>
        <AppShell>{children}</AppShell>
      </InterfacePreferencesProvider>
    </RouterProvider>
  );
}

describe("AppShell", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("collapses without navigation or network activity and stores one boolean", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(
      <TestShell path="/tasks">
        <h1>任务</h1>
      </TestShell>,
    );

    const collapseButton = screen.getByRole("button", { name: "收起侧边栏" });
    expect(collapseButton.querySelector("svg")).toHaveAttribute(
      "data-direction",
      "left",
    );

    await userEvent.click(collapseButton);

    expect(screen.getByTestId("app-shell")).toHaveAttribute(
      "data-sidebar",
      "collapsed",
    );
    expect(localStorage).toHaveLength(1);
    expect(localStorage.getItem("vtnote.sidebar.collapsed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "展开侧边栏" }).querySelector("svg"),
    ).toHaveAttribute("data-direction", "right");
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("toggles the desktop sidebar from its edge", async () => {
    render(
      <TestShell path="/tasks">
        <h1>任务</h1>
      </TestShell>,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "从边缘收起侧边栏" }),
    );
    expect(screen.getByTestId("app-shell")).toHaveAttribute(
      "data-sidebar",
      "collapsed",
    );

    await userEvent.click(
      screen.getByRole("button", { name: "从边缘展开侧边栏" }),
    );
    expect(screen.getByTestId("app-shell")).toHaveAttribute(
      "data-sidebar",
      "expanded",
    );
    expect(localStorage.getItem("vtnote.sidebar.collapsed")).toBe("false");
  });

  it("provides a skip link and closes the mobile drawer with Escape", async () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn((query: string) => ({
        matches: query === "(max-width: 767px)",
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    );
    render(
      <TestShell path="/">
        <h1>新建任务</h1>
      </TestShell>,
    );

    expect(screen.getByRole("link", { name: "跳到主内容" })).toHaveAttribute(
      "href",
      "#main-content",
    );
    const openButton = screen.getByRole("button", { name: "打开导航" });
    const drawer = document.getElementById("primary-sidebar");
    expect(drawer).toHaveAttribute("inert");
    expect(screen.queryByRole("link", { name: "VtNote 首页" })).toBeNull();

    await userEvent.click(openButton);
    expect(drawer).toHaveAttribute(
      "data-mobile-open",
      "true",
    );
    expect(drawer).not.toHaveAttribute("inert");
    expect(within(drawer!).getByRole("link", { name: "VtNote 首页" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(within(drawer!).getByRole("link", { name: "合集管理" })).toHaveAttribute(
      "href",
      "/collections",
    );
    expect(within(drawer!).getByRole("button", { name: "关闭导航" })).toHaveFocus();

    await userEvent.keyboard("{Escape}");
    expect(drawer).toHaveAttribute(
      "data-mobile-open",
      "false",
    );
    expect(drawer).toHaveAttribute("inert");
    expect(openButton).toHaveFocus();
  });

  it("moves focus into main and animates only after a route change", async () => {
    render(
      <TestShell path="/">
        <h1>新建任务</h1>
      </TestShell>,
    );

    const main = screen.getByRole("main");
    expect(main).not.toHaveFocus();
    expect(screen.getByTestId("route-content")).toHaveAttribute(
      "data-route-motion",
      "idle",
    );

    await userEvent.click(screen.getByRole("link", { name: "总结记录" }));

    await waitFor(() => expect(main).toHaveFocus());
    expect(screen.getByTestId("route-content")).toHaveAttribute(
      "data-route-motion",
      "enter",
    );
  });

  it("keeps the primary sidebar visible in settings", () => {
    render(
      <TestShell path="/settings/export">
        <h1>导出</h1>
      </TestShell>,
    );

    expect(
      screen.getByRole("complementary", { name: "主导航" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveClass("settings-main-content");
  });
});
