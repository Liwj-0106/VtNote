import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SecretField } from "./SecretField";

describe("SecretField", () => {
  it("never renders a stored secret or reversible mask", async () => {
    render(
      <SecretField
        id="secret-key"
        label="SecretKey"
        name="secret_key"
        hasSecret
      />,
    );
    expect(screen.getByText("已安全保存")).toBeInTheDocument();
    expect(screen.queryByLabelText("SecretKey")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("••");
    await userEvent.click(screen.getByRole("button", { name: "替换" }));
    expect(screen.getByLabelText("SecretKey")).toHaveValue("");
  });
});
