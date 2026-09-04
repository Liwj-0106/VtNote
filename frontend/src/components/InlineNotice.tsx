import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type HTMLAttributes,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { MotionPresence } from "./MotionPresence";

const DEFAULT_TOAST_DURATION_MS = 4_000;
const DANGER_TOAST_DURATION_MS = 6_500;
const LOADING_TOAST_DURATION_MS = 5_000;

function toastRegion(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const existing = document.getElementById("vtnote-toast-region");
  if (existing) return existing;
  const region = document.createElement("div");
  region.id = "vtnote-toast-region";
  region.className = "toast-region";
  region.setAttribute("aria-label", "通知");
  document.body.append(region);
  return region;
}

export function InlineNotice({
  tone = "info",
  title,
  children,
  className = "",
  loading = false,
  onDismiss,
  role,
  ...rest
}: {
  tone?: "info" | "success" | "warning" | "danger";
  title?: string;
  children: ReactNode;
  loading?: boolean;
  onDismiss?: () => void;
} & Omit<HTMLAttributes<HTMLDivElement>, "title">) {
  const [present, setPresent] = useState(true);
  const [mounted, setMounted] = useState(true);
  const [swipeOffset, setSwipeOffset] = useState(0);
  const swipeStart = useRef<number | null>(null);
  const swipeDistance = useRef(0);
  const dismissed = useRef(false);
  const onDismissRef = useRef(onDismiss);
  const region = toastRegion();
  useEffect(() => {
    onDismissRef.current = onDismiss;
  }, [onDismiss]);
  const dismiss = useCallback(() => {
    if (dismissed.current) return;
    dismissed.current = true;
    setSwipeOffset(0);
    setPresent(false);
  }, []);
  useEffect(() => {
    const duration = loading
      ? LOADING_TOAST_DURATION_MS
      : tone === "danger"
        ? DANGER_TOAST_DURATION_MS
        : DEFAULT_TOAST_DURATION_MS;
    const timer = window.setTimeout(dismiss, duration);
    return () => window.clearTimeout(timer);
  }, [dismiss, loading, tone]);
  if (!mounted || !region) return null;
  const accessibleLabel =
    typeof rest["aria-label"] === "string"
      ? rest["aria-label"]
      : title ?? (typeof children === "string" ? children : undefined);
  const startSwipe = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    swipeStart.current = event.clientX;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const moveSwipe = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (swipeStart.current === null) return;
    const distance = Math.max(0, event.clientX - swipeStart.current);
    swipeDistance.current = distance;
    setSwipeOffset(distance);
  };
  const finishSwipe = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (swipeStart.current === null) return;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    swipeStart.current = null;
    const threshold = Math.min(
      96,
      Math.max(72, event.currentTarget.clientWidth * 0.3),
    );
    if (swipeDistance.current >= threshold) {
      dismiss();
      return;
    }
    swipeDistance.current = 0;
    setSwipeOffset(0);
  };
  return createPortal(
    <MotionPresence
      present={present}
      variant="toast"
      onExited={() => {
        setMounted(false);
        onDismissRef.current?.();
      }}
    >
      <div
        {...rest}
        aria-label={accessibleLabel}
        className={`inline-notice notice-${tone}${loading ? " toast-loading" : ""}${className ? ` ${className}` : ""}`}
        role={role ?? (tone === "danger" ? "alert" : "status")}
        data-swiping={swipeOffset > 0 ? "true" : "false"}
        style={{
          ...rest.style,
          opacity: Math.max(0.2, 1 - swipeOffset / 280),
          transform: `translateX(${swipeOffset}px)`,
        }}
        onPointerDown={startSwipe}
        onPointerMove={moveSwipe}
        onPointerUp={finishSwipe}
        onPointerCancel={(event) => {
          swipeStart.current = null;
          swipeDistance.current = 0;
          setSwipeOffset(0);
          rest.onPointerCancel?.(event);
        }}
      >
        <span className="notice-mark" aria-hidden="true" />
        <div className="notice-copy">
          {title && <strong>{title}</strong>}
          <div>{children}</div>
        </div>
      </div>
    </MotionPresence>,
    region,
  );
}
