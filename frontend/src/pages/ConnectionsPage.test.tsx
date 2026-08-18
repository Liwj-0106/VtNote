import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { ConnectionView } from "../api/types";
import { RouterProvider } from "../app/router";
import { ConnectionsPage } from "./ConnectionsPage";

const tencentConnection: ConnectionView = {
  id: "tencent-connection",
  name: "腾讯云 ASR",
  protocol: "tencent_recording_asr",
  base_url: "https://asr.tencentcloudapi.com",
  parameters: { asr_region: "ap-guangzhou", cos_configured: false },
  has_secret: true,
  configured_fields: { secret_id: true, secret_key: true },
  revision: 1,
  tested: true,
  test_ok: true,
  test_message: "ok",
  cleanup_pending: false,
};

const unverifiedTencentConnection: ConnectionView = {
  ...tencentConnection,
  tested: false,
  test_ok: null,
  test_message: null,
};

const bailianConnection: ConnectionView = {
  ...tencentConnection,
  id: "bailian-connection",
  name: "百炼",
  protocol: "aliyun_bailian",
  base_url: "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
};

function renderPage() {
  render(
    <RouterProvider initialPath="/settings/connections">
      <ConnectionsPage />
    </RouterProvider>,
  );
}

describe("ConnectionsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  it("shows a compact Tencent-only ASR page", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/connections") {
        return [tencentConnection, bailianConnection];
      }
      throw new Error(`unexpected ${path}`);
    });

    renderPage();

    expect(
      await screen.findByRole("heading", { name: "ASR 配置" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "添加 ASR" })).toBeInTheDocument();
    expect(screen.getByLabelText("SecretId")).toBeInTheDocument();
    expect(screen.getByLabelText("SecretKey")).toBeInTheDocument();
    expect(screen.queryByLabelText("配置名称")).not.toBeInTheDocument();
    expect(screen.queryByText("百炼")).not.toBeInTheDocument();
    expect(screen.queryByText("字幕配置")).not.toBeInTheDocument();
    expect(screen.queryByText("运行能力测试")).not.toBeInTheDocument();
  });

  it("adds Tencent ASR with only SecretId and SecretKey", async () => {
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/connections" && options?.method === "POST") {
        return tencentConnection;
      }
      if (path === "/api/connections") return [];
      throw new Error(`unexpected ${path}`);
    });

    renderPage();
    await screen.findByRole("heading", { name: "添加 ASR" });
    await userEvent.type(screen.getByLabelText("SecretId"), "AKID-example");
    await userEvent.type(screen.getByLabelText("SecretKey"), "secret-example");
    await userEvent.click(screen.getByRole("button", { name: "添加" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/connections", {
        method: "POST",
        body: {
          name: "腾讯云 ASR",
          protocol: "tencent_recording_asr",
          base_url: "https://asr.tencentcloudapi.com",
          parameters: {
            asr_region: "ap-guangzhou",
            cos_configured: false,
          },
          credentials: {
            secret_id: "AKID-example",
            secret_key: "secret-example",
          },
        },
      });
    });
  });

  it("verifies with the built-in sample and confirms before deleting", async () => {
    let verified = false;
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/connections") {
        return [verified ? tencentConnection : unverifiedTencentConnection];
      }
      if (path.endsWith("/verify-asr")) {
        verified = true;
        return {};
      }
      if (path.includes("?cascade_profiles=true")) return undefined;
      throw new Error(`unexpected ${path}`);
    });

    renderPage();
    await screen.findByText("腾讯云 ASR");
    expect(screen.getByText("未验证")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "验证连接" }));
    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/api/connections/tencent-connection/verify-asr",
        {
          method: "POST",
          body: {
            acknowledge_billable_request: true,
            authorize_task_audio_upload: true,
          },
        },
      );
    });
    expect(await screen.findByText("验证通过")).toBeInTheDocument();
    expect(
      screen.queryByText("连接可用，内置测试音频识别正确。"),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(
      screen.getByRole("heading", { name: "删除这个 ASR？" }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/api/connections/tencent-connection?cascade_profiles=true",
        { method: "DELETE" },
      );
    });
    expect(screen.queryByText("ASR 已删除。")).not.toBeInTheDocument();
  });
});
