import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SegmentedTabs, segmentedTabId } from "./SegmentedTabs";

function Harness() {
  const [value, setValue] = useState("all");
  return (
    <>
      <SegmentedTabs
        id="status"
        ariaLabel="总结状态"
        value={value}
        onValueChange={setValue}
        items={[
          { value: "all", label: "全部", panelId: "status-results" },
          { value: "completed", label: "已完成", panelId: "status-results" },
          { value: "running", label: "处理中", panelId: "status-results", disabled: true },
          { value: "failed", label: "失败", panelId: "status-results" },
        ]}
      />
      <div
        id="status-results"
        role="tabpanel"
        aria-labelledby={segmentedTabId("status", value)}
      >
        当前：{value}
      </div>
    </>
  );
}

describe("SegmentedTabs", () => {
  it("uses roving focus and keeps the selected tab associated with its panel", async () => {
    render(<Harness />);
    const all = screen.getByRole("tab", { name: "全部" });
    const completed = screen.getByRole("tab", { name: "已完成" });
    const running = screen.getByRole("tab", { name: "处理中" });
    const failed = screen.getByRole("tab", { name: "失败" });
    const panel = screen.getByRole("tabpanel");

    expect(all).toHaveAttribute("tabindex", "0");
    expect(completed).toHaveAttribute("tabindex", "-1");
    expect(all).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", all.id);

    all.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(completed).toHaveFocus();
    expect(completed).toHaveAttribute("aria-selected", "true");
    expect(completed).toHaveAttribute("tabindex", "0");
    expect(all).toHaveAttribute("tabindex", "-1");
    expect(panel).toHaveAttribute("aria-labelledby", completed.id);

    await userEvent.keyboard("{ArrowRight}");
    expect(failed).toHaveFocus();
    expect(running).toBeDisabled();

    await userEvent.keyboard("{Home}");
    expect(all).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(failed).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(all).toHaveFocus();
  });
});
