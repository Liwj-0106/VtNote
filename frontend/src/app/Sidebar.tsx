import { useCallback, useEffect, useRef, useState } from "react";
import {
  CloseIcon,
  CollectionIcon,
  FolderIcon,
  MenuIcon,
  PanelIcon,
  SettingsIcon,
  SparkIcon,
} from "./icons";
import { BrandMark } from "./BrandMark";
import { useInterfacePreferences } from "./interfacePreferences";
import { AppLink, useRouter } from "./router";
import { MotionPresence } from "../components/MotionPresence";

interface SidebarProps {
  collapsed: boolean;
  mobileOpen: boolean;
  onToggleCollapse: () => void;
  onOpenMobile: () => void;
  onCloseMobile: () => void;
}

const MOBILE_NAVIGATION_QUERY = "(max-width: 767px)";

function useMobileNavigationViewport(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia?.(MOBILE_NAVIGATION_QUERY).matches ?? false,
  );

  useEffect(() => {
    const media = window.matchMedia?.(MOBILE_NAVIGATION_QUERY);
    if (!media) return;
    const update = () => setIsMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  return isMobile;
}

function navCurrent(path: string, target: string): boolean {
  if (target === "/") return path === "/";
  if (target === "/tasks") return path.startsWith("/tasks");
  return path.startsWith(target);
}

export function Sidebar({
  collapsed,
  mobileOpen,
  onToggleCollapse,
  onOpenMobile,
  onCloseMobile,
}: SidebarProps) {
  const { path } = useRouter();
  const { text } = useInterfacePreferences();
  const closeButton = useRef<HTMLButtonElement>(null);
  const openButton = useRef<HTMLButtonElement>(null);
  const wasMobileOpen = useRef(false);
  const isMobileNavigation = useMobileNavigationViewport();
  const requestMobileClose = useCallback(() => {
    if (mobileOpen) openButton.current?.focus();
    onCloseMobile();
  }, [mobileOpen, onCloseMobile]);

  useEffect(() => {
    if (!mobileOpen) {
      const drawer = closeButton.current?.closest("aside");
      if (
        wasMobileOpen.current ||
        (isMobileNavigation && drawer?.contains(document.activeElement))
      ) {
        openButton.current?.focus();
      }
      wasMobileOpen.current = false;
      return;
    }
    wasMobileOpen.current = true;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") requestMobileClose();
      if (event.key !== "Tab") return;
      const drawer = closeButton.current?.closest("aside");
      const focusable = drawer?.querySelectorAll<HTMLElement>(
        'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.classList.add("drawer-open");
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.classList.remove("drawer-open");
    };
  }, [isMobileNavigation, mobileOpen, requestMobileClose]);

  const links = [
    { to: "/", label: text("sidebar.newSummary"), icon: SparkIcon },
    { to: "/tasks", label: text("sidebar.library"), icon: FolderIcon },
    { to: "/collections", label: text("sidebar.collections"), icon: CollectionIcon },
  ];
  return (
    <>
      <button
        ref={openButton}
        className="mobile-menu-button icon-button"
        type="button"
        aria-label={text("a11y.openNavigation")}
        onClick={onOpenMobile}
      >
        <MenuIcon />
      </button>
      <MotionPresence present={mobileOpen} variant="fade">
        <button
          type="button"
          className="drawer-scrim"
          aria-label={text("a11y.closeNavigation")}
          onClick={requestMobileClose}
        />
      </MotionPresence>
      <aside
        id="primary-sidebar"
        className="sidebar"
        aria-label={text("sidebar.navigation")}
        aria-hidden={isMobileNavigation && !mobileOpen ? true : undefined}
        data-mobile-open={mobileOpen ? "true" : "false"}
        inert={isMobileNavigation && !mobileOpen ? true : undefined}
      >
        <button
          className="sidebar-edge-toggle"
          type="button"
          aria-controls="primary-sidebar"
          aria-expanded={!collapsed}
          aria-label={
            collapsed
              ? text("sidebar.edgeExpand")
              : text("sidebar.edgeCollapse")
          }
          title={collapsed ? text("sidebar.expand") : text("sidebar.collapse")}
          onClick={onToggleCollapse}
        />
        <div className="sidebar-brand">
          <AppLink
            to="/"
            className="wordmark"
            aria-label={text("sidebar.home")}
            onClick={requestMobileClose}
          >
            <BrandMark className="brand-mark" />
            <span className="wordmark-name sidebar-label" aria-hidden="true">
              <strong>Vt</strong>Note
            </span>
          </AppLink>
          <button
            ref={closeButton}
            className="mobile-close icon-button"
            type="button"
            aria-label={text("a11y.closeNavigation")}
            onClick={requestMobileClose}
          >
            <CloseIcon />
          </button>
          <button
            className="desktop-collapse icon-button"
            type="button"
            aria-label={collapsed ? text("sidebar.expand") : text("sidebar.collapse")}
            title={collapsed ? text("sidebar.expand") : text("sidebar.collapse")}
            onClick={onToggleCollapse}
          >
            <PanelIcon direction={collapsed ? "right" : "left"} />
          </button>
        </div>
        <nav className="sidebar-nav" aria-label={text("sidebar.primaryPages")}>
          {links.map(({ to, label, icon: Icon }) => (
            <AppLink
              key={to}
              to={to}
              className={`nav-link ${navCurrent(path, to) ? "is-current" : ""}`}
              aria-label={label}
              aria-current={navCurrent(path, to) ? "page" : undefined}
              title={collapsed ? label : undefined}
              onClick={requestMobileClose}
            >
              <Icon />
              <span className="sidebar-label">{label}</span>
            </AppLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <AppLink
            to="/settings/general"
            className={`nav-link ${navCurrent(path, "/settings") ? "is-current" : ""}`}
            aria-label={text("sidebar.settings")}
            aria-current={
              navCurrent(path, "/settings") ? "page" : undefined
            }
            title={collapsed ? text("sidebar.settings") : undefined}
            onClick={requestMobileClose}
          >
            <SettingsIcon />
            <span className="sidebar-label">{text("sidebar.settings")}</span>
          </AppLink>
        </div>
      </aside>
    </>
  );
}
