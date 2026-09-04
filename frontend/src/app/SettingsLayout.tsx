import type { ReactNode } from "react";
import { useInterfacePreferences } from "./interfacePreferences";
import { AppLink, useRouter } from "./router";

function isCurrent(
  path: string,
  section: "general" | "export" | "models",
): boolean {
  const pathname = path.split("?")[0];
  if (section === "general") {
    return pathname === "/settings" || pathname === "/settings/general";
  }
  if (section === "export") {
    return pathname === "/settings/export";
  }
  return [
    "/settings/models",
    "/settings/connections",
    "/settings/ai-connections",
  ].includes(pathname);
}

export function SettingsLayout({ children }: { children: ReactNode }) {
  const { path } = useRouter();
  const { text } = useInterfacePreferences();
  const sections = [
    {
      key: "general" as const,
      to: "/settings/general",
      label: text("settings.general"),
    },
    {
      key: "export" as const,
      to: "/settings/export",
      label: text("settings.export"),
    },
    {
      key: "models" as const,
      to: "/settings/models",
      label: text("settings.models"),
    },
  ];

  return (
    <section className="settings-layout" aria-labelledby="settings-title">
      <header className="settings-workspace-header">
        <h1 id="settings-title">{text("settings.title")}</h1>
      </header>
      <div className="settings-workspace-body">
        <nav className="settings-nav" aria-label={text("settings.navigation")}>
          {sections.map((section) => {
            const current = isCurrent(path, section.key);
            return (
              <AppLink
                key={section.key}
                to={section.to}
                className={`settings-nav-link ${current ? "is-current" : ""}`}
                aria-current={current ? "page" : undefined}
              >
                {section.label}
              </AppLink>
            );
          })}
        </nav>
        <div className="settings-content">{children}</div>
      </div>
    </section>
  );
}
