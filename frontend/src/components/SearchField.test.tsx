import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SearchField } from "./SearchField";

function SearchFieldHarness() {
  const [value, setValue] = useState("");
  return (
    <SearchField
      label="搜索内容"
      clearLabel="清除内容搜索"
      value={value}
      placeholder="搜索内容"
      onChange={(event) => setValue(event.currentTarget.value)}
      onClear={() => setValue("")}
    />
  );
}

describe("SearchField", () => {
  it("uses one accessible clear control and restores input focus", async () => {
    render(<SearchFieldHarness />);
    const input = screen.getByRole("textbox", { name: "搜索内容" });

    expect(input).toHaveAttribute("inputmode", "search");
    expect(screen.queryByRole("button", { name: "清除内容搜索" })).not.toBeInTheDocument();

    await userEvent.type(input, "租房");
    await userEvent.click(screen.getByRole("button", { name: "清除内容搜索" }));

    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
  });

  it("clears with Escape", async () => {
    render(<SearchFieldHarness />);
    const input = screen.getByRole("textbox", { name: "搜索内容" });
    await userEvent.type(input, "字幕{Escape}");
    expect(input).toHaveValue("");
  });
});
