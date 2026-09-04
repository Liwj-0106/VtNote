import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { Task } from "../../api/types";
import { BulkExportMenu } from "./BulkExportMenu";

const mocks = vi.hoisted(() => ({
  notify: vi.fn(),
}));

vi.mock("../task-queue/TaskQueueProvider", () => ({
  useTaskQueue: () => ({ notify: mocks.notify }),
}));

describe("BulkExportMenu", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mocks.notify.mockReset();
  });

  it("exports original Markdown and reports completion through shared toast", async () => {
    const request = vi.spyOn(api, "request").mockResolvedValue({
      directory: "D:\\Exports",
      files: [{ kind: "transcript", filename: "video-transcript.md" }],
    });
    render(
      <BulkExportMenu
        tasks={[
          { id: "11111111-1111-4111-8111-111111111111" } as Task,
        ]}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "批量导出" }));
    await userEvent.click(
      screen.getByRole("menuitem", { name: /导出原文（Markdown）/ }),
    );

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/tasks/bulk-export", {
        method: "POST",
        body: {
          task_ids: ["11111111-1111-4111-8111-111111111111"],
          mode: "original_markdown",
        },
      });
    });
    expect(mocks.notify).toHaveBeenCalledWith("已保存至 D:\\Exports");
    expect(screen.queryByText("已保存至 D:\\Exports")).not.toBeInTheDocument();
  });
});
