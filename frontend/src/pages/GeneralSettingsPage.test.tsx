import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  InterfacePreferencesProvider,
  loadInterfacePreferences,
} from "../app/interfacePreferences";
import { GeneralSettingsPage } from "./GeneralSettingsPage";

describe("GeneralSettingsPage", () => {
  it("switches and persists the theme", async () => {
    render(
      <InterfacePreferencesProvider>
        <GeneralSettingsPage />
      </InterfacePreferencesProvider>,
    );

    expect(
      screen.queryByText("选择界面语言与显示方式，修改会立即生效。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("让 VtNote 适应你的工作环境。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("选择浅色、深色，或自动跟随系统。"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("选择设置与导航使用的界面语言。"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("适合日间使用")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("radio", { name: /深色/u }));

    expect(loadInterfacePreferences().theme).toBe("dark");
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
  });

  it("switches the settings language immediately", async () => {
    render(
      <InterfacePreferencesProvider>
        <GeneralSettingsPage />
      </InterfacePreferencesProvider>,
    );

    await userEvent.click(screen.getByRole("radio", { name: /^English/u }));

    expect(screen.getByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(screen.getByText("Appearance")).toBeInTheDocument();
    expect(loadInterfacePreferences().language).toBe("en");
  });

  it("supports palette pickers, manual hex values, and resetting colors", async () => {
    render(
      <InterfacePreferencesProvider>
        <GeneralSettingsPage />
      </InterfacePreferencesProvider>,
    );

    fireEvent.change(screen.getByLabelText("强调色 色盘"), {
      target: { value: "#cc7d5e" },
    });
    const backgroundValue = screen.getByLabelText("背景 颜色值");
    fireEvent.change(backgroundValue, { target: { value: "F9F9F7" } });
    fireEvent.blur(backgroundValue);
    fireEvent.change(screen.getByLabelText("前景 色盘"), {
      target: { value: "#2d2d2b" },
    });

    expect(loadInterfacePreferences()).toEqual(
      expect.objectContaining({
        accentColor: "#cc7d5e",
        backgroundColor: "#f9f9f7",
        foregroundColor: "#2d2d2b",
      }),
    );
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe(
      "#cc7d5e",
    );
    expect(document.documentElement.style.getPropertyValue("--canvas")).toBe(
      "#f9f9f7",
    );
    expect(document.documentElement.style.getPropertyValue("--ink")).toBe(
      "#2d2d2b",
    );

    await userEvent.click(
      screen.getByRole("button", { name: "恢复默认" }),
    );

    expect(loadInterfacePreferences().accentColor).toBeNull();
    expect(loadInterfacePreferences().backgroundColor).toBeNull();
    expect(loadInterfacePreferences().foregroundColor).toBeNull();
    expect(document.documentElement.style.getPropertyValue("--accent")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--canvas")).toBe("");
    expect(document.documentElement.style.getPropertyValue("--ink")).toBe("");
  });
});
