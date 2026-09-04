import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LauncherSignal } from "./LauncherSignal";

const gsapMocks = vi.hoisted(() => {
  const revert = vi.fn();
  const fromTo = vi.fn();
  const timeline = { fromTo };
  fromTo.mockReturnValue(timeline);
  return {
    context: vi.fn((run: () => void) => {
      run();
      return { revert };
    }),
    fromTo,
    revert,
    set: vi.fn(),
    timeline: vi.fn(() => timeline),
  };
});

vi.mock("gsap", () => ({
  gsap: {
    context: gsapMocks.context,
    set: gsapMocks.set,
    timeline: gsapMocks.timeline,
  },
}));

function stubReducedMotion(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe("LauncherSignal", () => {
  beforeEach(() => {
    gsapMocks.context.mockClear();
    gsapMocks.fromTo.mockClear();
    gsapMocks.revert.mockClear();
    gsapMocks.set.mockClear();
    gsapMocks.timeline.mockClear();
    stubReducedMotion(false);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("stays out of the interface while the launcher is idle", () => {
    render(<LauncherSignal phase={null} text="" />);

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(gsapMocks.context).not.toHaveBeenCalled();
  });

  it("announces and animates an active launcher phase", () => {
    const view = render(
      <LauncherSignal phase="probing" text="正在检查链接" />,
    );

    expect(screen.getByRole("status")).toHaveAttribute(
      "data-launcher-signal",
      "probing",
    );
    expect(screen.getByText("正在检查链接")).toBeInTheDocument();
    expect(gsapMocks.timeline).toHaveBeenCalledOnce();
    expect(gsapMocks.fromTo).toHaveBeenCalledTimes(2);

    view.unmount();
    expect(gsapMocks.revert).toHaveBeenCalledOnce();
  });

  it("shows the status without a timeline when reduced motion is requested", () => {
    stubReducedMotion(true);

    render(<LauncherSignal phase="submitting" text="正在创建任务" />);

    expect(screen.getByText("正在创建任务")).toBeInTheDocument();
    expect(gsapMocks.set).toHaveBeenCalledOnce();
    expect(gsapMocks.timeline).not.toHaveBeenCalled();
  });
});
