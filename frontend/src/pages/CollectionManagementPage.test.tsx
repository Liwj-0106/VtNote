import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { LibraryMetadata } from "../api/types";
import { RouterProvider } from "../app/router";
import { CollectionManagementPage } from "./CollectionManagementPage";

const metadata: LibraryMetadata = {
  total_count: 7,
  unclassified_count: 3,
  tags: [],
  collections: [
    { id: "collection-1", name: "租房", task_count: 4 },
  ],
};

function renderPage() {
  return render(
    <RouterProvider initialPath="/collections">
      <CollectionManagementPage />
    </RouterProvider>,
  );
}

describe("CollectionManagementPage", () => {
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

  it("renders folder, overview, and collection card links with counts", async () => {
    vi.spyOn(api, "request").mockResolvedValue(metadata);
    renderPage();

    expect(await screen.findByRole("heading", { name: "我创建的合集" })).toBeInTheDocument();
    expect(screen.queryByText("Collections")).not.toBeInTheDocument();
    expect(screen.queryByText("按主题整理视频总结，让常用内容保持清晰可找。")).not.toBeInTheDocument();
    expect(screen.queryByText("VtNote collection")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "文件夹" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /所有总结/ })).toHaveAttribute("href", "/tasks");
    expect(screen.getByRole("link", { name: /未分类/ })).toHaveAttribute(
      "href",
      "/tasks?unclassified=true",
    );
    expect(screen.getByText("7 个视频")).toBeInTheDocument();
    expect(screen.getByText("3 个视频")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开合集 租房" })).toHaveAttribute(
      "href",
      "/collections/collection-1",
    );
  });

  it("creates a new folder and refreshes collection metadata", async () => {
    const request = vi.spyOn(api, "request");
    let metadataReads = 0;
    request.mockImplementation(async (path, options) => {
      if (path === "/api/library/meta") {
        metadataReads += 1;
        return metadataReads === 1
          ? metadata
          : {
              ...metadata,
              collections: [
                ...metadata.collections,
                { id: "collection-2", name: "AI 学习", task_count: 0 },
              ],
            };
      }
      if (path === "/api/library/collections" && options?.method === "POST") {
        return { id: "collection-2", name: "AI 学习" };
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    renderPage();

    await screen.findByRole("link", { name: "打开合集 租房" });
    await userEvent.click(screen.getByRole("button", { name: "新建文件夹" }));
    await userEvent.type(screen.getByLabelText("名称"), "AI 学习");
    await userEvent.click(screen.getByRole("button", { name: "创建合集" }));

    await waitFor(() => expect(screen.getAllByText("AI 学习").length).toBeGreaterThan(0));
    expect(request).toHaveBeenCalledWith("/api/library/collections", {
      method: "POST",
      body: { name: "AI 学习" },
    });
  });

  it("renames a collection from its card action", async () => {
    const request = vi.spyOn(api, "request");
    let renamed = false;
    request.mockImplementation(async (path, options) => {
      if (path === "/api/library/meta") {
        return renamed
          ? { ...metadata, collections: [{ id: "collection-1", name: "居住指南", task_count: 4 }] }
          : metadata;
      }
      if (path === "/api/library/collections/collection-1" && options?.method === "PATCH") {
        renamed = true;
        return { id: "collection-1", name: "居住指南" };
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "重命名 租房" }));
    const input = screen.getByLabelText("名称");
    await userEvent.clear(input);
    await userEvent.type(input, "居住指南");
    await userEvent.click(screen.getByRole("button", { name: "保存名称" }));

    await waitFor(() => expect(screen.getAllByText("居住指南").length).toBeGreaterThan(0));
  });
});
