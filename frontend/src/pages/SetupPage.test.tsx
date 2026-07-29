import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { RouterProvider } from "../app/router";
import { SetupPage } from "./SetupPage";

describe("SetupPage", () => {
  it("keeps available capabilities usable when YouTube or local ASR is missing", async () => {
    vi.spyOn(api, "request").mockResolvedValueOnce({
      status: "partial",
      core: {
        database: true,
        data_storage: true,
        runtime_storage: true,
        ffmpeg: true,
      },
      capabilities: {
        local_files: true,
        bilibili_url: true,
        youtube_url: false,
        local_asr: false,
      },
      local_model_state: "not_installed",
      limits: {
        max_task_sources: 1,
        max_media_bytes: 1000,
        max_subtitle_bytes: 100,
      },
    });
    render(
      <RouterProvider initialPath="/setup">
        <SetupPage />
      </RouterProvider>,
    );
    expect(await screen.findByText("YouTube 公开链接")).toBeInTheDocument();
    expect(screen.getAllByText("尚不可用")).toHaveLength(2);
    expect(
      screen.getByRole("link", { name: "继续使用可用功能" }),
    ).toHaveAttribute("href", "/");
    vi.restoreAllMocks();
  });
});
