import {
  useLayoutEffect,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";

type PopoverAlign = "start" | "end";

interface Placement {
  left: number | null;
  maxHeight: number | null;
  opensUp: boolean;
}

const DEFAULT_PLACEMENT: Placement = {
  left: null,
  maxHeight: null,
  opensUp: false,
};

function samePlacement(current: Placement, next: Placement) {
  return (
    current.left === next.left &&
    current.maxHeight === next.maxHeight &&
    current.opensUp === next.opensUp
  );
}

export function useAnchoredPopover<
  TAnchor extends HTMLElement,
  TPopover extends HTMLElement,
>({
  open,
  anchorRef,
  popoverRef,
  align = "start",
  gap = 8,
  viewportPadding = 12,
  maxHeight = 420,
}: {
  open: boolean;
  anchorRef: RefObject<TAnchor | null>;
  popoverRef: RefObject<TPopover | null>;
  align?: PopoverAlign;
  gap?: number;
  viewportPadding?: number;
  maxHeight?: number;
}) {
  const [placement, setPlacement] = useState<Placement>(DEFAULT_PLACEMENT);

  useLayoutEffect(() => {
    if (!open) return;
    const anchor = anchorRef.current;
    const popover = popoverRef.current;
    if (!anchor || !popover) return;

    let animationFrame = 0;
    const update = () => {
      const anchorBounds = anchor.getBoundingClientRect();
      const popoverBounds = popover.getBoundingClientRect();
      const popoverWidth = Math.min(
        popoverBounds.width || popover.scrollWidth,
        Math.max(0, window.innerWidth - viewportPadding * 2),
      );
      const preferredLeft =
        align === "end"
          ? anchorBounds.right - popoverWidth
          : anchorBounds.left;
      const viewportLeft = Math.min(
        Math.max(preferredLeft, viewportPadding),
        Math.max(viewportPadding, window.innerWidth - viewportPadding - popoverWidth),
      );
      const roomAbove = Math.max(0, anchorBounds.top - gap - viewportPadding);
      const roomBelow = Math.max(
        0,
        window.innerHeight - anchorBounds.bottom - gap - viewportPadding,
      );
      const desiredHeight = Math.min(
        Math.max(popover.scrollHeight, popoverBounds.height),
        maxHeight,
      );
      const opensUp = roomBelow < desiredHeight && roomAbove > roomBelow;
      const availableHeight = opensUp ? roomAbove : roomBelow;
      const next: Placement = {
        left: Math.round(viewportLeft - anchorBounds.left),
        maxHeight: Math.max(44, Math.min(maxHeight, Math.floor(availableHeight))),
        opensUp,
      };
      setPlacement((current) => (samePlacement(current, next) ? current : next));
    };
    const scheduleUpdate = () => {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(update);
    };

    update();
    window.addEventListener("resize", scheduleUpdate);
    window.addEventListener("scroll", scheduleUpdate, true);
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(scheduleUpdate);
    observer?.observe(anchor);
    observer?.observe(popover);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", scheduleUpdate);
      window.removeEventListener("scroll", scheduleUpdate, true);
      observer?.disconnect();
    };
  }, [align, anchorRef, gap, maxHeight, open, popoverRef, viewportPadding]);

  const style: CSSProperties = {};
  if (placement.left !== null) {
    style.left = placement.left;
    style.right = "auto";
  }
  if (placement.maxHeight !== null) style.maxHeight = placement.maxHeight;

  return { opensUp: placement.opensUp, style };
}
