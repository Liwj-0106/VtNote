import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownNote } from "./MarkdownNote";

describe("MarkdownNote", () => {
  it("hides internal evidence nodes from new and legacy notes", () => {
    render(
      <MarkdownNote
        markdown={[
          "# 测试总结",
          "",
          "摘要 [00:12](vtnote://cue/seg_000006)",
          "",
          "- 亮点 [seg_000022 @ 00:36.270–00:38.930]",
        ].join("\n")}
      />,
    );

    expect(screen.getByText("摘要")).toBeInTheDocument();
    expect(screen.getByText("亮点")).toBeInTheDocument();
    expect(screen.queryByText(/seg_000|00:12|00:36/u)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查看原文/u })).not.toBeInTheDocument();
  });
});
