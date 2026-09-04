import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { LibraryMetadata, LibrarySearchResult, Task } from "../api/types";
import { RouterProvider } from "../app/router";
import { CollectionDetailPage } from "./CollectionDetailPage";

const collectionId = "collection-1";

const metadata: LibraryMetadata = {
  total_count: 2,
  unclassified_count: 1,
  tags: [],
  collections: [{ id: collectionId, name: "租房", task_count: 1 }],
};

function completedTask(id: string, title: string): Task {
  return {
    id,
    status: "completed",
    options: {},
    pipeline_snapshot: {},
    terminal_reason_code: null,
    created_at: "2026-08-23T08:00:00Z",
    updated_at: "2026-08-23T08:00:00Z",
    items: [{
      id: `${id}-item`,
      position: 0,
      source_kind: "bilibili",
      source_locator: `https://www.bilibili.com/video/${id}`,
      source_display_name: title,
      status: "completed",
      title,
      stage_runs: [],
      created_at: "2026-08-23T08:00:00Z",
      updated_at: "2026-08-23T08:00:00Z",
    }],
  };
}

function renderPage() {
  return render(
    <RouterProvider initialPath={`/collections/${collectionId}`}>
      <CollectionDetailPage collectionId={collectionId} />
    </RouterProvider>,
  );
}

describe("CollectionDetailPage", () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a dedicated collection page instead of the summary-record scope", async () => {
    const task = completedTask("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "毕业租房攻略");
    const result: LibrarySearchResult = {
      task,
      match: null,
      collections: metadata.collections,
      tags: [],
    };
    const request = vi.spyOn(api, "request").mockImplementation(async (path) => {
      if (path === "/api/library/meta") return metadata;
      if (path === "/api/library/search?limit=100&collection_id=collection-1") return [result];
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [], nextCursor: null });

    renderPage();

    expect(await screen.findByRole("heading", { name: "租房" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "我的合集" })).toHaveAttribute("href", "/collections");
    expect(screen.getByRole("button", { name: "批量添加" })).toBeInTheDocument();
    expect(screen.getByText("1 项内容")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "毕业租房攻略" })).toBeInTheDocument();
    expect(screen.queryByText("暂无描述")).not.toBeInTheDocument();
    expect(screen.queryByText(/基于合集/)).not.toBeInTheDocument();
    expect(screen.queryByText(/正在查看合集/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "批量添加" }));
    expect(await screen.findByRole("heading", { name: "添加内容" })).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveClass("collection-task-picker-dialog");
    expect(request).toHaveBeenCalledWith(
      "/api/library/search?limit=100&collection_id=collection-1",
    );
  });

  it("adds selected summaries from the empty collection page", async () => {
    const candidate = completedTask("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "上海租房技巧");
    const request = vi.spyOn(api, "request").mockImplementation(async (path, options) => {
      if (path === "/api/library/meta") return metadata;
      if (path === "/api/library/search?limit=100&collection_id=collection-1") return [];
      if (path === "/api/library/organize" && options?.method === "POST") return undefined;
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.spyOn(api, "requestPage").mockResolvedValue({ data: [candidate], nextCursor: null });

    renderPage();

    expect(await screen.findByRole("heading", { name: "这个合集还没有内容" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "添加内容" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "上海租房技巧" }));
    await userEvent.click(screen.getByRole("button", { name: "添加（1）" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith("/api/library/organize", {
        method: "POST",
        body: {
          task_ids: [candidate.id],
          collection_ids: [collectionId],
          tag_ids: [],
          operation: "add",
        },
      });
    });
  });
});
