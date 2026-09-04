import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useInterfacePreferences } from "./interfacePreferences";
import { Sidebar } from "./Sidebar";
import { useRouter } from "./router";

const SIDEBAR_KEY = "vtnote.sidebar.collapsed";

function initialCollapsed(): boolean {
  return localStorage.getItem(SIDEBAR_KEY) === "true";
}

export function AppShell({
  children,
}: {
  children: ReactNode;
}) {
  const { path } = useRouter();
  const pathname = path.split("?")[0];
  const settingsMode = pathname.startsWith("/settings");
  const { text } = useInterfacePreferences();
  const [collapsed, setCollapsed] = useState(initialCollapsed);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [initialPathname] = useState(pathname);
  const [hasNavigated, setHasNavigated] = useState(false);
  const closeMobile = useCallback(() => setMobileOpen(false), []);
  const mainContent = useRef<HTMLElement>(null);
  const previousPathname = useRef(pathname);
  const animateRoute = hasNavigated || pathname !== initialPathname;

  useEffect(() => {
    if (pathname !== initialPathname) setHasNavigated(true);
  }, [initialPathname, pathname]);

  useEffect(() => {
    if (previousPathname.current === pathname) return;
    previousPathname.current = pathname;
    const frame = window.requestAnimationFrame(() => {
      mainContent.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pathname]);

  const toggleCollapse = () => {
    setCollapsed((current) => {
      const next = !current;
      localStorage.setItem(SIDEBAR_KEY, String(next));
      return next;
    });
  };

  return (
    <div
      className={`app-shell ${settingsMode ? "settings-shell" : ""}`}
      data-sidebar={collapsed ? "collapsed" : "expanded"}
      data-testid="app-shell"
    >
      <a className="skip-link" href="#main-content">
        {text("a11y.skipContent")}
      </a>
      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileOpen}
        onToggleCollapse={toggleCollapse}
        onOpenMobile={() => setMobileOpen(true)}
        onCloseMobile={closeMobile}
      />
      <main
        ref={mainContent}
        id="main-content"
        className={`main-content ${settingsMode ? "settings-main-content" : ""}`}
        tabIndex={-1}
      >
        <div
          key={pathname}
          className="route-content"
          data-route-motion={animateRoute ? "enter" : "idle"}
          data-testid="route-content"
        >
          {children}
        </div>
      </main>
    </div>
  );
}
