import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MotionPresence } from "./MotionPresence";

function Notice({ open }: { open: boolean }) {
  return (
    <MotionPresence present={open}>
      <p role="status">状态变化</p>
    </MotionPresence>
  );
}

function StaticNotice({ open }: { open: boolean }) {
  return (
    <MotionPresence present={open} initial={false} variant="toast">
      <p role="status">静态首屏</p>
    </MotionPresence>
  );
}

describe("MotionPresence", () => {
  it("retains an inert element for its exit animation and then removes it", async () => {
    const view = render(<Notice open />);
    const entered = screen.getByRole("status");
    expect(entered).toHaveAttribute("data-motion-presence", "enter");

    view.rerender(<Notice open={false} />);
    const exiting = screen.getByRole("status", { hidden: true });
    expect(exiting).toHaveAttribute("data-motion-presence", "exit");
    expect(exiting).toHaveAttribute("inert");

    fireEvent.animationEnd(exiting);
    await waitFor(() =>
      expect(screen.queryByText("状态变化")).not.toBeInTheDocument(),
    );
  });

  it("keeps an element when an exit is interrupted by reopening", () => {
    const view = render(<Notice open />);
    view.rerender(<Notice open={false} />);
    view.rerender(<Notice open />);

    const reopened = screen.getByRole("status");
    expect(reopened).toHaveAttribute("data-motion-presence", "enter");
    fireEvent.animationEnd(reopened);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("can skip only the initial entrance and exposes an explicit variant", () => {
    const view = render(<StaticNotice open />);
    const initial = screen.getByRole("status");
    expect(initial).toHaveAttribute("data-motion-presence", "idle");
    expect(initial).toHaveAttribute("data-motion-variant", "toast");

    view.rerender(<StaticNotice open={false} />);
    fireEvent.animationEnd(screen.getByRole("status", { hidden: true }));
    view.rerender(<StaticNotice open />);
    expect(screen.getByRole("status")).toHaveAttribute(
      "data-motion-presence",
      "enter",
    );
  });
});
