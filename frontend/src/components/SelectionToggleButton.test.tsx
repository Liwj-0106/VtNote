import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SelectionToggleButton } from "./SelectionToggleButton";

describe("SelectionToggleButton", () => {
  it("uses a concise accessible label and toggles from all selected", async () => {
    const onClick = vi.fn();
    render(
      <SelectionToggleButton
        state="on"
        selectAllLabel="全选全部视频"
        clearAllLabel="取消全选全部视频"
        onClick={onClick}
      />,
    );

    const button = screen.getByRole("button", { name: "取消全选全部视频" });
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(button).toHaveTextContent("");
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("exposes the mixed selection state", () => {
    render(
      <SelectionToggleButton
        state="mixed"
        selectAllLabel="全选全部视频"
        clearAllLabel="取消全选全部视频"
        onClick={() => undefined}
      />,
    );

    expect(screen.getByRole("button", { name: "全选全部视频" })).toHaveAttribute(
      "aria-pressed",
      "mixed",
    );
  });
});
