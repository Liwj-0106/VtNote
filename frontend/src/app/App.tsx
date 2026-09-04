import { type ReactNode } from "react";
import { CreateTaskPage } from "../pages/CreateTaskPage";
import { CollectionManagementPage } from "../pages/CollectionManagementPage";
import { CollectionDetailPage } from "../pages/CollectionDetailPage";
import { GeneralSettingsPage } from "../pages/GeneralSettingsPage";
import { SettingsPage } from "../pages/SettingsPage";
import { ModelSettingsPage } from "../pages/ModelSettingsPage";
import { SetupPage } from "../pages/SetupPage";
import { StoragePage } from "../pages/StoragePage";
import { TaskDetailPage } from "../pages/TaskDetailPage";
import { TaskHistoryPage } from "../pages/TaskHistoryPage";
import { EmptyState } from "../components/EmptyState";
import { TaskQueueProvider } from "../features/task-queue/TaskQueueProvider";
import { AppShell } from "./AppShell";
import { InterfacePreferencesProvider } from "./interfacePreferences";
import { SettingsLayout } from "./SettingsLayout";
import { RouterProvider, useRouter } from "./router";

const taskRoute = /^\/tasks\/([0-9a-f-]{36})$/iu;
const collectionRoute = /^\/collections\/([^/]+)$/u;

function RoutedApp() {
  const { path } = useRouter();
  const pathname = path.split("?")[0];
  let page: ReactNode;
  let settingsPage = false;
  if (pathname === "/") page = <CreateTaskPage />;
  else if (pathname === "/tasks") page = <TaskHistoryPage />;
  else if (pathname === "/collections") page = <CollectionManagementPage />;
  else if (pathname.match(collectionRoute)) {
    const collectionMatch = pathname.match(collectionRoute)!;
    page = <CollectionDetailPage collectionId={decodeURIComponent(collectionMatch[1])} />;
  }
  else if (pathname === "/setup") page = <SetupPage />;
  else if (pathname === "/settings" || pathname === "/settings/general") {
    page = <GeneralSettingsPage />;
    settingsPage = true;
  } else if (pathname === "/settings/export") {
    page = <SettingsPage />;
    settingsPage = true;
  } else if (
    [
      "/settings/models",
      "/settings/connections",
      "/settings/ai-connections",
    ].includes(pathname)
  ) {
    page = <ModelSettingsPage />;
    settingsPage = true;
  } else if (pathname === "/settings/storage") {
    page = <StoragePage />;
    settingsPage = true;
  } else {
    const taskMatch = pathname.match(taskRoute);
    page = taskMatch ? (
      <TaskDetailPage taskId={taskMatch[1]} />
    ) : (
      <div className="page">
        <EmptyState
          title="页面不存在"
          description="这个地址不属于 VtNote 的本地工作区。"
          actionLabel="返回新建任务"
        />
      </div>
    );
  }
  return (
    <AppShell>
      {settingsPage ? <SettingsLayout>{page}</SettingsLayout> : page}
    </AppShell>
  );
}

export function App() {
  return (
    <RouterProvider>
      <InterfacePreferencesProvider>
        <TaskQueueProvider>
          <RoutedApp />
        </TaskQueueProvider>
      </InterfacePreferencesProvider>
    </RouterProvider>
  );
}
