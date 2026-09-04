import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { InterfacePreferencesProvider } from "../app/interfacePreferences";
import { loadPreferences } from "../app/preferences";
import { RouterProvider } from "../app/router";
import { SettingsPage } from "./SettingsPage";

describe("SettingsPage", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/export-settings") {
        return {
          directory: "D:\\Workspace\\Project\\VtNote\\exports",
          default_directory: "D:\\Workspace\\Project\\VtNote\\exports",
          is_default: true,
        };
      }
      throw new Error(`unexpected ${path}`);
    });
  });

  it("uses a skeleton while the export directory is being read", () => {
    vi.spyOn(api, "request").mockImplementation(
      () => new Promise(() => undefined),
    );

    render(
      <RouterProvider initialPath="/settings/export">
        <InterfacePreferencesProvider>
          <SettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    expect(
      screen.getByRole("status", { name: "正在读取…" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("正在读取…")).not.toBeInTheDocument();
  });

  it("shows one export preference per row and persists changes", async () => {
    render(
      <RouterProvider initialPath="/settings/export">
        <InterfacePreferencesProvider>
          <SettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    expect(screen.getByRole("heading", { name: "导出" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "默认导出" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "导出格式" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/新任务只生成/u)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "原文" }));
    await userEvent.click(screen.getByRole("combobox", { name: "音频格式" }));
    await userEvent.click(screen.getByRole("option", { name: "MP3" }));
    await userEvent.click(screen.getByRole("combobox", { name: "原文格式" }));
    await userEvent.click(screen.getByRole("option", { name: "TXT" }));
    await userEvent.click(screen.getByRole("combobox", { name: "总结格式" }));
    await userEvent.click(screen.getByRole("option", { name: "TXT" }));

    expect(loadPreferences()).toEqual({
      defaultExportItems: ["audio", "notes"],
      audioFormat: "mp3",
      subtitleFormat: "txt",
      noteFormat: "txt",
    });
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });

  it("keeps at least one default export selected", async () => {
    render(
      <RouterProvider initialPath="/settings/export">
        <InterfacePreferencesProvider>
          <SettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(screen.getByRole("checkbox", { name: "原文" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "总结" }));

    expect(screen.getByRole("checkbox", { name: "音频" })).toBeDisabled();
    expect(loadPreferences().defaultExportItems).toEqual(["audio"]);
  });

  it("selects and persists a native export directory without a toast", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/export-settings" && !options?.method) {
        return {
          directory: "D:\\Workspace\\Project\\VtNote\\exports",
          default_directory: "D:\\Workspace\\Project\\VtNote\\exports",
          is_default: true,
        };
      }
      if (path === "/api/system/pick-directory") {
        return { canceled: false, directory: "D:\\My Exports" };
      }
      if (path === "/api/export-settings" && options?.method === "PATCH") {
        return {
          directory: "D:\\My Exports",
          default_directory: "D:\\Workspace\\Project\\VtNote\\exports",
          is_default: false,
        };
      }
      throw new Error(`unexpected ${path}`);
    });
    render(
      <RouterProvider initialPath="/settings/export">
        <InterfacePreferencesProvider>
          <SettingsPage />
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    await userEvent.click(await screen.findByRole("button", { name: "选择" }));

    expect(await screen.findByText("D:\\My Exports")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith(
      "/api/export-settings",
      expect.objectContaining({ method: "PATCH", body: { directory: "D:\\My Exports" } }),
    );
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
  });
});
