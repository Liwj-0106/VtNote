import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InlineNotice } from "./InlineNotice";

describe("InlineNotice", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders without a close button and disappears automatically", () => {
    vi.useFakeTimers();
    const { container } = render(
      <InlineNotice tone="danger">模型验证失败</InlineNotice>,
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.getByLabelText("通知")).toContainElement(
      screen.getByRole("alert", { name: /模型验证失败/u }),
    );
    expect(screen.queryByRole("button", { name: "关闭通知" })).not.toBeInTheDocument();
    act(() => vi.advanceTimersByTime(6_500));
    const exiting = screen.getByText("模型验证失败").closest(".inline-notice");
    expect(exiting).toHaveAttribute("data-motion-presence", "exit");
    fireEvent.animationEnd(exiting!);
    expect(screen.queryByText("模型验证失败")).not.toBeInTheDocument();
  });

  it("dismisses a toast after a deliberate right swipe", () => {
    if (!("PointerEvent" in window)) {
      class PointerEventMock extends MouseEvent {
        pointerId: number;

        constructor(type: string, init: PointerEventInit = {}) {
          super(type, init);
          this.pointerId = init.pointerId ?? 0;
        }
      }
      Object.defineProperty(window, "PointerEvent", {
        configurable: true,
        value: PointerEventMock,
      });
    }
    const onDismiss = vi.fn();
    render(<InlineNotice onDismiss={onDismiss}>已保存</InlineNotice>);
    const toast = screen.getByRole("status", { name: "已保存" });

    fireEvent.pointerDown(toast, { button: 0, clientX: 10, pointerId: 1 });
    fireEvent.pointerMove(toast, { clientX: 120, pointerId: 1 });
    fireEvent.pointerUp(toast, { clientX: 120, pointerId: 1 });

    expect(toast).toHaveAttribute("data-motion-presence", "exit");
    fireEvent.animationEnd(toast);
    expect(screen.queryByText("已保存")).not.toBeInTheDocument();
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
