import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InterfacePreferencesProvider } from "./interfacePreferences";
import { RouterProvider } from "./router";
import { SettingsLayout } from "./SettingsLayout";

describe("SettingsLayout", () => {
  it("provides a predictable return and section navigation", () => {
    render(
      <RouterProvider initialPath="/settings/models">
        <InterfacePreferencesProvider>
          <SettingsLayout>
            <h2>模型内容</h2>
          </SettingsLayout>
        </InterfacePreferencesProvider>
      </RouterProvider>,
    );

    expect(screen.getByRole("heading", { name: "设置" })).toBeInTheDocument();
    expect(screen.queryByText("VtNote")).not.toBeInTheDocument();
    expect(
      screen.queryByText("调整 VtNote 的界面、导出与模型偏好。"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "通用" })).toHaveAttribute(
      "href",
      "/settings/general",
    );
    expect(screen.getByRole("link", { name: "导出" })).toHaveAttribute(
      "href",
      "/settings/export",
    );
    expect(screen.getByRole("link", { name: "模型" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
