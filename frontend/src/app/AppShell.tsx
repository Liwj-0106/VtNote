import { type ReactNode, useCallback, useState } from "react";
import { Sidebar } from "./Sidebar";

const SIDEBAR_KEY = "vtnote.sidebar.collapsed";

function initialCollapsed(): boolean {
  return localStorage.getItem(SIDEBAR_KEY) === "true";
}

export function AppShell({
  children,
}: {
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(initialCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const closeMobile = useCallback(() => setMobileOpen(false), []);

  const toggleCollapse = () => {
    setCollapsed((current) => {
      const next = !current;
      localStorage.setItem(SIDEBAR_KEY, String(next));
      return next;
    });
  };

  return (
    <div
      className="app-shell"
      data-sidebar={collapsed ? "collapsed" : "expanded"}
      data-testid="app-shell"
    >
      <a className="skip-link" href="#main-content">
        跳到主内容
      </a>
      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onToggleCollapse={toggleCollapse}
        onOpenMobile={() => setMobileOpen(true)}
        onCloseMobile={closeMobile}
      />
      <main id="main-content" className="main-content" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
