import { api } from "../../api/client";
import type { Task } from "../../api/types";
import type { TaskCreationOptions } from "./model";

const MAX_BATCH_SOURCES = 100;

export async function createUrlTasksInBatches(
  locators: readonly string[],
  options: TaskCreationOptions,
): Promise<Task[]> {
  const createdTasks: Task[] = [];
  for (let start = 0; start < locators.length; start += MAX_BATCH_SOURCES) {
    const batch = locators.slice(start, start + MAX_BATCH_SOURCES);
    const created = await api.request<Task[]>("/api/tasks/batch", {
      method: "POST",
      body: {
        sources: batch.map((locator) => ({ kind: "url", locator })),
        ...options,
      },
    });
    createdTasks.push(...created);
  }
  return createdTasks;
}
