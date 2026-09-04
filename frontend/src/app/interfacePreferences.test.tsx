import { act, renderHook } from "@testing-library/react";
import { type ReactNode } from "react";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_INTERFACE_PREFERENCES,
  InterfacePreferencesProvider,
  loadInterfacePreferences,
  useInterfacePreferences,
} from "./interfacePreferences";

function wrapper({ children }: { children: ReactNode }) {
  return <InterfacePreferencesProvider>{children}</InterfacePreferencesProvider>;
}

describe("interface preferences", () => {
  it("uses system and Chinese defaults", () => {
    expect(loadInterfacePreferences()).toEqual(DEFAULT_INTERFACE_PREFERENCES);
  });

  it("persists language and theme and applies them to the document", () => {
    const { result } = renderHook(useInterfacePreferences, { wrapper });

    act(() => result.current.setTheme("dark"));
    act(() => result.current.setLanguage("en"));

    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(document.documentElement).toHaveAttribute("lang", "en");
    expect(loadInterfacePreferences()).toEqual({
      language: "en",
      theme: "dark",
      accentColor: null,
      backgroundColor: null,
      foregroundColor: null,
    });
  });

  it("repairs unsupported stored values", () => {
    localStorage.setItem(
      "vtnote.interface.v1",
      JSON.stringify({ language: "fr", theme: "sepia" }),
    );

    expect(loadInterfacePreferences()).toEqual(DEFAULT_INTERFACE_PREFERENCES);
  });
});
