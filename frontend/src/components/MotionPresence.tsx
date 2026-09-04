import {
  cloneElement,
  useEffect,
  useState,
  type AnimationEvent,
  type ReactElement,
} from "react";

export type MotionVariant =
  | "dialog"
  | "fade"
  | "popover"
  | "popover-up"
  | "surface"
  | "toast";

type PresenceElementProps = {
  "aria-hidden"?: boolean;
  "data-motion-presence"?: "enter" | "exit" | "idle";
  "data-motion-variant"?: MotionVariant;
  inert?: boolean;
  onAnimationEnd?: (event: AnimationEvent<HTMLElement>) => void;
};

const FALLBACK_BY_VARIANT: Record<MotionVariant, number> = {
  dialog: 220,
  fade: 150,
  popover: 180,
  "popover-up": 180,
  surface: 200,
  toast: 180,
};

function prefersReducedMotion() {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function MotionPresence({
  present,
  children,
  onExited,
  fallbackMs,
  initial = true,
  variant = "surface",
}: {
  present: boolean;
  children: ReactElement<PresenceElementProps> | null;
  onExited?: () => void;
  fallbackMs?: number;
  initial?: boolean;
  variant?: MotionVariant;
}) {
  const [animateEnter, setAnimateEnter] = useState(initial);
  const [retained, setRetained] = useState(present && children !== null);
  const [retainedChild, setRetainedChild] =
    useState<ReactElement<PresenceElementProps> | null>(children);

  useEffect(() => {
    if (!present) setAnimateEnter(true);
  }, [present]);

  useEffect(() => {
    if (present && children !== null) {
      setRetainedChild(children);
      setRetained(true);
      return;
    }
    if (!retained) return;
    const timer = window.setTimeout(() => {
      setRetained(false);
      setRetainedChild(null);
      onExited?.();
    }, prefersReducedMotion() ? 0 : (fallbackMs ?? FALLBACK_BY_VARIANT[variant]));
    return () => window.clearTimeout(timer);
  }, [children, fallbackMs, onExited, present, retained, variant]);

  const visibleChild = present && children !== null ? children : retainedChild;
  if ((!present && !retained) || visibleChild === null) return null;

  const previousAnimationEnd = visibleChild.props.onAnimationEnd;
  const phase = present ? (animateEnter ? "enter" : "idle") : "exit";
  return cloneElement(visibleChild, {
    "aria-hidden": present ? visibleChild.props["aria-hidden"] : true,
    "data-motion-presence": phase,
    "data-motion-variant": variant,
    inert: present ? visibleChild.props.inert : true,
    onAnimationEnd: (event: AnimationEvent<HTMLElement>) => {
      previousAnimationEnd?.(event);
      if (
        present ||
        event.currentTarget !== event.target
      ) {
        return;
      }
      setRetained(false);
      setRetainedChild(null);
      onExited?.();
    },
  });
}
