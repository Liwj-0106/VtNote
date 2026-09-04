import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { SelectMenu } from "./SelectMenu";

const options = [
  { value: "markdown", label: "Markdown" },
  { value: "disabled", label: "不可用", disabled: true },
  { value: "txt", label: "TXT" },
];

function SelectHarness() {
  const [value, setValue] = useState("markdown");
  return (
    <SelectMenu
      ariaLabel="总结格式"
      value={value}
      options={options}
      onChange={setValue}
    />
  );
}

describe("SelectMenu", () => {
  it("shows a selected option and changes it from the popup", async () => {
    render(<SelectHarness />);

    const trigger = screen.getByRole("combobox", { name: "总结格式" });
    expect(trigger).toHaveTextContent("Markdown");
    await userEvent.click(trigger);

    expect(screen.getByRole("option", { name: "Markdown" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("option", { name: "不可用" })).toBeDisabled();
    await userEvent.click(screen.getByRole("option", { name: "TXT" }));

    expect(trigger).toHaveTextContent("TXT");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("supports arrow, Home, End, Enter and Escape keys", async () => {
    render(<SelectHarness />);
    const trigger = screen.getByRole("combobox", { name: "总结格式" });

    trigger.focus();
    await userEvent.keyboard("{ArrowDown}{End}{Enter}");
    expect(trigger).toHaveTextContent("TXT");

    await userEvent.keyboard("{Enter}{Home}{Escape}");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(trigger).toHaveFocus();
  });

  it("closes when the user clicks outside", async () => {
    render(
      <div>
        <SelectHarness />
        <button type="button">外部按钮</button>
      </div>,
    );

    const trigger = screen.getByRole("combobox", { name: "总结格式" });
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("button", { name: "外部按钮" }));
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });
});
