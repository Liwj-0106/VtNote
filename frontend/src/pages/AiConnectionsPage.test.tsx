import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ConnectionView } from "../api/types";
import { RouterProvider } from "../app/router";
import { AiConnectionsPage } from "./AiConnectionsPage";

const tokenHubConnection: ConnectionView = {
  id: "tokenhub-connection",
  name: "腾讯云 TokenHub",
  protocol: "tencent_tokenhub",
  base_url: "https://tokenhub.tencentmaas.com/v1",
  parameters: {},
  has_secret: true,
  configured_fields: { api_key: true },
  revision: 1,
  tested: true,
  test_ok: true,
  test_message: "ok",
  cleanup_pending: false,
};

const unverifiedTokenHubConnection: ConnectionView = {
  ...tokenHubConnection,
  tested: false,
  test_ok: null,
  test_message: null,
};

function renderPage() {
  render(
    <RouterProvider initialPath="/settings/ai-connections">
      <AiConnectionsPage />
    </RouterProvider>,
  );
}

describe("AiConnectionsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  it("adds a TokenHub connection with only the API Key", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/connections" && options?.method === "POST") {
        return tokenHubConnection;
      }
      if (path === "/api/connections") return [];
      throw new Error(`unexpected ${path}`);
    });

    renderPage();
    await screen.findByRole("heading", { name: "添加 AI 模型" });
    await userEvent.type(screen.getByLabelText("API Key"), "sk-local-test");
    await userEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/connections", {
        method: "POST",
        body: {
          name: "腾讯云 TokenHub",
          protocol: "tencent_tokenhub",
          base_url: "https://tokenhub.tencentmaas.com/v1",
          parameters: {},
          credentials: { api_key: "sk-local-test" },
        },
      });
    });
  });

  it("verifies the model and confirms before deleting", async () => {
    let verified = false;
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/connections") {
        return [verified ? tokenHubConnection : unverifiedTokenHubConnection];
      }
      if (path.endsWith("/verify-chat")) {
        verified = true;
        return {};
      }
      if (path.includes("?cascade_profiles=true")) return undefined;
      throw new Error(`unexpected ${path}`);
    });

    renderPage();
    await screen.findByText("腾讯云 TokenHub");
    expect(screen.getByText("未验证")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "验证连接" }));
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/api/connections/tokenhub-connection/verify-chat",
        {
          method: "POST",
          body: {
            acknowledge_billable_request: true,
            authorize_chat_data_upload: true,
          },
        },
      );
    });
    expect(await screen.findByText("验证通过")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(
      screen.getByRole("heading", { name: "删除这个 AI 模型？" }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/api/connections/tokenhub-connection?cascade_profiles=true",
        { method: "DELETE" },
      );
    });
  });
});
