import { useLayoutEffect, useRef } from "react";
import { gsap } from "gsap";

export type LauncherSignalPhase = "probing" | "submitting";

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function LauncherSignal({
  phase,
  text,
}: {
  phase: LauncherSignalPhase | null;
  text: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const root = rootRef.current;
    if (phase === null || root === null) return;
    const bars = Array.from(
      root.querySelectorAll<HTMLElement>(".launcher-signal-mark > span"),
    );
    const label = root.querySelector<HTMLElement>(".launcher-signal-text");
    if (label === null) return;

    const context = gsap.context(() => {
      if (prefersReducedMotion()) {
        gsap.set([...bars, label], { clearProps: "opacity,transform" });
        return;
      }

      gsap
        .timeline({ defaults: { overwrite: "auto" } })
        .fromTo(
          bars,
          { opacity: 0, x: -4 },
          {
            opacity: 1,
            x: 0,
            duration: 0.14,
            stagger: 0.025,
            ease: "power1.out",
          },
        )
        .fromTo(
          label,
          { opacity: 0, y: 3 },
          { opacity: 1, y: 0, duration: 0.18, ease: "power2.out" },
          "<0.03",
        );
    }, root);

    return () => context.revert();
  }, [phase, text]);

  if (phase === null) return null;

  return (
    <div
      ref={rootRef}
      className="launcher-signal"
      data-launcher-signal={phase}
      role="status"
      aria-label={text}
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="launcher-signal-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span className="launcher-signal-text">{text}</span>
    </div>
  );
}
