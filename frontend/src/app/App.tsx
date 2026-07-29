import { type ReactNode, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Task } from "../api/types";
import { ConnectionsPage } from "../pages/ConnectionsPage";
import { CreateTaskPage } from "../pages/CreateTaskPage";
import { SettingsPage } from "../pages/SettingsPage";
import { SetupPage } from "../pages/SetupPage";
import { StoragePage } from "../pages/StoragePage";
import { TaskDetailPage } from "../pages/TaskDetailPage";
import { TaskHistoryPage } from "../pages/TaskHistoryPage";
import { EmptyState } from "../components/EmptyState";
import { AppShell } from "./AppShell";
import { RouterProvider, useRouter } from "./router";

const taskRoute = /^\/tasks\/([0-9a-f-]{36})$/iu;

function RoutedApp() {
  const { path } = useRouter();
  const pathname = path.split("?")[0];
  const [recentTasks, setRecentTasks] = useState<Task[]>([]);

  useEffect(() => {
    const controller = new AbortController();
    api
      .request<Task[]>("/api/tasks?limit=5", { signal: controller.signal })
      .then(setRecentTasks)
      .catch(() => {
        // Recent tasks are optional navigation context.
      });
    return () => controller.abort();
  }, [pathname]);

  let page: ReactNode;
  if (pathname === "/") page = <CreateTaskPage />;
  else if (pathname === "/tasks") page = <TaskHistoryPage />;
  else if (pathname === "/setup") page = <SetupPage />;
  else if (pathname === "/settings") page = <SettingsPage />;
  else if (pathname === "/settings/connections") page = <ConnectionsPage />;
  else if (pathname === "/settings/storage") page = <StoragePage />;
  else {
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
  return <AppShell recentTasks={recentTasks}>{page}</AppShell>;
}

export function App() {
  return (
    <RouterProvider>
      <RoutedApp />
    </RouterProvider>
  );
}
