import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DropdownMenu } from "./DropdownMenu";

function Harness() {
  return (
    <DropdownMenu ariaLabel="下载格式" trigger="下载">
      {(close) => (
        <>
          <button type="button" role="menuitem" onClick={close}>Markdown</button>
          <button type="button" role="menuitem" onClick={close}>Text</button>
        </>
      )}
    </DropdownMenu>
  );
}

describe("DropdownMenu", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shares keyboard navigation and restores trigger focus on Escape", async () => {
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "下载" });
    trigger.focus();

    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Text" })).toHaveFocus();

    await userEvent.keyboard("{Home}{Escape}");
    expect(screen.queryByRole("menu", { name: "下载格式" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes after an action and when clicking outside", async () => {
    const outside = vi.fn();
    render(
      <>
        <Harness />
        <button type="button" onClick={outside}>外部</button>
      </>,
    );

    await userEvent.click(screen.getByRole("button", { name: "下载" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Markdown" }));
    expect(screen.queryByRole("menu", { name: "下载格式" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "下载" }));
    await userEvent.click(screen.getByRole("button", { name: "外部" }));
    expect(outside).toHaveBeenCalledOnce();
    expect(screen.queryByRole("menu", { name: "下载格式" })).not.toBeInTheDocument();
  });

  it("opens upward and nudges inside the viewport near an edge", async () => {
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 1_200,
    });
    Object.defineProperty(window, "innerHeight", {
      configurable: true,
      value: 800,
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function getBounds(this: HTMLElement) {
        if (this.classList.contains("dropdown-menu")) {
          return {
            bottom: 744,
            height: 44,
            left: 1_100,
            right: 1_180,
            top: 700,
            width: 80,
            x: 1_100,
            y: 700,
            toJSON: () => ({}),
          };
        }
        return {
          bottom: 908,
          height: 200,
          left: 1_100,
          right: 1_360,
          top: 708,
          width: 260,
          x: 1_100,
          y: 708,
          toJSON: () => ({}),
        };
      },
    );

    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "下载" }));
    const menu = screen.getByRole("menu", { name: "下载格式" });

    await waitFor(() => expect(menu).toHaveClass("opens-up"));
    expect(menu).toHaveAttribute("data-motion-variant", "popover-up");
    expect(menu).toHaveStyle({ left: "-172px" });
  });
});
