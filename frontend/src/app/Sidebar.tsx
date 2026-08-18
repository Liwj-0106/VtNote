import { useEffect, useRef } from "react";
import {
  CloseIcon,
  MenuIcon,
  PanelIcon,
  PlusIcon,
  SettingsIcon,
  TasksIcon,
} from "./icons";
import { AppLink, useRouter } from "./router";

interface SidebarProps {
  collapsed: boolean;
  mobileOpen: boolean;
  onToggleCollapse: () => void;
  onOpenMobile: () => void;
  onCloseMobile: () => void;
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
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!mobileOpen) return;
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseMobile();
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
  }, [mobileOpen, onCloseMobile]);

  const links = [
    { to: "/", label: "新建处理", icon: PlusIcon },
    { to: "/tasks", label: "内容库", icon: TasksIcon },
  ];
  return (
    <>
      <button
        className="mobile-menu-button icon-button"
        type="button"
        aria-label="打开导航"
        onClick={onOpenMobile}
      >
        <MenuIcon />
      </button>
      {mobileOpen && (
        <button
          type="button"
          className="drawer-scrim"
          aria-label="关闭导航"
          onClick={onCloseMobile}
        />
      )}
      <aside
        className="sidebar"
        aria-label="主导航"
        data-mobile-open={mobileOpen ? "true" : "false"}
      >
        <div className="sidebar-brand">
          <AppLink to="/" className="wordmark" onClick={onCloseMobile}>
            <span className="wordmark-mark" aria-hidden="true">
              V
            </span>
            <span className="sidebar-label">VtNote</span>
          </AppLink>
          <button
            ref={closeButton}
            className="mobile-close icon-button"
            type="button"
            aria-label="关闭导航"
            onClick={onCloseMobile}
          >
            <CloseIcon />
          </button>
          <button
            className="desktop-collapse icon-button"
            type="button"
            aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
            title={collapsed ? "展开侧边栏" : "收起侧边栏"}
            onClick={onToggleCollapse}
          >
            <PanelIcon />
          </button>
        </div>
        <nav className="sidebar-nav" aria-label="主要页面">
          {links.map(({ to, label, icon: Icon }) => (
            <AppLink
              key={to}
              to={to}
              className={`nav-link ${navCurrent(path, to) ? "is-current" : ""}`}
              aria-current={navCurrent(path, to) ? "page" : undefined}
              title={collapsed ? label : undefined}
              onClick={onCloseMobile}
            >
              <Icon />
              <span className="sidebar-label">{label}</span>
            </AppLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <AppLink
            to="/settings"
            className={`nav-link ${navCurrent(path, "/settings") ? "is-current" : ""}`}
            aria-current={
              navCurrent(path, "/settings") ? "page" : undefined
            }
            title={collapsed ? "设置" : undefined}
            onClick={onCloseMobile}
          >
            <SettingsIcon />
            <span className="sidebar-label">设置</span>
          </AppLink>
        </div>
      </aside>
    </>
  );
}
