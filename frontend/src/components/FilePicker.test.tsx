import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FilePicker } from "./FilePicker";

function renderPicker(onChange = vi.fn()) {
  render(
    <FilePicker
      id="media-file"
      accept=".mp4,.mp3"
      files={[]}
      limitBytes={null}
      onChange={onChange}
    />,
  );
  return {
    onChange,
    target: screen.getByText("上传文件").closest("label")!,
  };
}

describe("FilePicker", () => {
  it("accepts a supported file dropped onto the picker", () => {
    const { onChange, target } = renderPicker();
    const media = new File(["media"], "sample.mp4", { type: "video/mp4" });
    const dataTransfer = { files: [media], types: ["Files"] };

    fireEvent.dragEnter(target, { dataTransfer });
    expect(target).toHaveClass("is-dragging");

    fireEvent.drop(target, { dataTransfer });
    expect(onChange).toHaveBeenCalledWith([media]);
    expect(target).not.toHaveClass("is-dragging");
  });

  it("keeps every supported file in a multi-file drop", () => {
    const { onChange, target } = renderPicker();
    const first = new File(["one"], "one.mp4", { type: "video/mp4" });
    const second = new File(["two"], "two.mp3", { type: "audio/mpeg" });

    fireEvent.drop(target, {
      dataTransfer: { files: [first, second], types: ["Files"] },
    });

    expect(onChange).toHaveBeenCalledWith([first, second]);
  });

  it("ignores a dropped file outside the accepted types", () => {
    const { onChange, target } = renderPicker();
    const documentFile = new File(["text"], "notes.txt", {
      type: "text/plain",
    });

    fireEvent.drop(target, {
      dataTransfer: { files: [documentFile], types: ["Files"] },
    });

    expect(onChange).not.toHaveBeenCalled();
  });
});
