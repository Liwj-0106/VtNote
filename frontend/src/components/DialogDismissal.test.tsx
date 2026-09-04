import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";
import { FormDialog } from "./FormDialog";

const dialogBounds = {
  bottom: 500,
  height: 400,
  left: 200,
  right: 800,
  top: 100,
  width: 600,
  x: 200,
  y: 100,
  toJSON: () => ({}),
};

describe("dialog dismissal", () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = function showModal() {
      this.open = true;
    };
    HTMLDialogElement.prototype.close = function close() {
      this.open = false;
    };
  });

  it("closes a form dialog only when the pointer is on the outside backdrop", () => {
    const onClose = vi.fn();
    render(
      <FormDialog open title="设置" onClose={onClose}>
        <button type="button">弹窗内容</button>
      </FormDialog>,
    );

    const dialog = screen.getByRole("dialog", { name: "设置" });
    vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue(dialogBounds);

    fireEvent.click(screen.getByRole("button", { name: "弹窗内容" }), {
      button: 0,
      clientX: 300,
      clientY: 200,
    });
    fireEvent.click(dialog, { button: 0, clientX: 300, clientY: 200 });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.click(dialog, { button: 0, clientX: 120, clientY: 80 });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps a busy confirmation dialog open", () => {
    const onClose = vi.fn();
    render(
      <ConfirmDialog
        open
        title="删除总结"
        description="删除后无法恢复。"
        confirmLabel="删除"
        busy
        onConfirm={vi.fn()}
        onClose={onClose}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "删除总结" });
    vi.spyOn(dialog, "getBoundingClientRect").mockReturnValue(dialogBounds);
    fireEvent.click(dialog, { button: 0, clientX: 120, clientY: 80 });
    fireEvent(dialog, new Event("cancel", { cancelable: true }));

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "返回" })).toBeDisabled();
  });
});
