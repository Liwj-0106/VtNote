import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { savePreferences } from "../app/preferences";
import { OutputExportDialog } from "./OutputExportDialog";

describe("OutputExportDialog", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:vtnote"),
      revokeObjectURL: vi.fn(),
    });
  });

  it("preselects available defaults and exports every selected result", async () => {
    vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path.endsWith("/outcomes")) {
        return { audio: true, transcript: true, notes: false };
      }
      throw new Error(`unexpected ${path}`);
    });
    const download = vi.spyOn(api, "download").mockResolvedValue(new Blob(["ok"]));

    render(
      <OutputExportDialog
        itemId="11111111-1111-4111-8111-111111111111"
        title="课程视频"
        open
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByRole("checkbox", { name: "音频" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "字幕原文" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "AI 笔记" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "AI 笔记" })).not.toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "导出所选（2）" }));

    await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
    expect(download.mock.calls.map(([path]) => path)).toEqual([
      "/api/items/11111111-1111-4111-8111-111111111111/audio?format=m4a",
      "/api/items/11111111-1111-4111-8111-111111111111/export?variant=original&format=srt",
    ]);
  });

  it("disables export when none of the preferred results exist", async () => {
    savePreferences({
      defaultExportItems: ["notes"],
      audioFormat: "m4a",
      subtitleFormat: "srt",
      noteFormat: "markdown",
    });
    vi.spyOn(api, "request").mockResolvedValue({
      audio: true,
      transcript: false,
      notes: false,
    });

    render(
      <OutputExportDialog
        itemId="11111111-1111-4111-8111-111111111111"
        title="课程视频"
        open
        onClose={vi.fn()}
      />,
    );

    expect(await screen.findByRole("button", { name: "导出所选（0）" })).toBeDisabled();
  });
});
