import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { isTerminalStatus } from "../../api/client";
import type { Task } from "../../api/types";

export interface TaskSelectionOptions {
  shift?: boolean;
  additive?: boolean;
  toggle?: boolean;
}

export function useTaskLibrarySelection({
  tasks,
  visibleTasks,
  listRef,
  suspendKeyboard,
}: {
  tasks: Task[];
  visibleTasks: Task[];
  listRef: RefObject<HTMLDivElement | null>;
  suspendKeyboard: boolean;
}) {
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set());
  const anchorIdRef = useRef<string | null>(null);
  const visibleSelectableTasks = useMemo(
    () => visibleTasks.filter((task) => isTerminalStatus(task.status)),
    [visibleTasks],
  );
  const allVisibleSelected =
    visibleSelectableTasks.length > 0 &&
    visibleSelectableTasks.every((task) => selectedTaskIds.has(task.id));

  useEffect(() => {
    const taskIds = new Set(tasks.map((task) => task.id));
    setSelectedTaskIds(
      (current) => new Set([...current].filter((taskId) => taskIds.has(taskId))),
    );
    if (anchorIdRef.current !== null && !taskIds.has(anchorIdRef.current)) {
      anchorIdRef.current = null;
    }
  }, [tasks]);

  const selectTask = (
    taskId: string,
    { shift = false, additive = false, toggle = false }: TaskSelectionOptions = {},
  ) => {
    const taskIndex = visibleSelectableTasks.findIndex((task) => task.id === taskId);
    if (taskIndex < 0) return;
    const anchorIndex = anchorIdRef.current
      ? visibleSelectableTasks.findIndex((task) => task.id === anchorIdRef.current)
      : -1;

    if (shift && anchorIndex >= 0) {
      const start = Math.min(anchorIndex, taskIndex);
      const end = Math.max(anchorIndex, taskIndex);
      const rangeIds = visibleSelectableTasks
        .slice(start, end + 1)
        .map((task) => task.id);
      setSelectedTaskIds((current) => {
        const next = new Set(current);
        const shouldSelect = !current.has(taskId);
        for (const rangeId of rangeIds) {
          if (shouldSelect) next.add(rangeId);
          else next.delete(rangeId);
        }
        return next;
      });
      return;
    }

    anchorIdRef.current = taskId;
    if (toggle || additive) {
      setSelectedTaskIds((current) => {
        const next = new Set(current);
        if (next.has(taskId)) next.delete(taskId);
        else next.add(taskId);
        return next;
      });
      return;
    }
    setSelectedTaskIds(new Set([taskId]));
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (suspendKeyboard) return;
      if (event.key === "Escape" && selectedTaskIds.size > 0) {
        event.preventDefault();
        anchorIdRef.current = null;
        setSelectedTaskIds(new Set());
        return;
      }
      if (
        event.key.toLocaleLowerCase() === "a" &&
        (event.ctrlKey || event.metaKey) &&
        listRef.current?.contains(document.activeElement)
      ) {
        event.preventDefault();
        setSelectedTaskIds(new Set(visibleSelectableTasks.map((task) => task.id)));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [listRef, selectedTaskIds.size, suspendKeyboard, visibleSelectableTasks]);

  const toggleVisible = () => {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      for (const task of visibleSelectableTasks) {
        if (allVisibleSelected) next.delete(task.id);
        else next.add(task.id);
      }
      return next;
    });
  };

  return {
    selectedTaskIds,
    setSelectedTaskIds,
    allVisibleSelected,
    selectTask,
    toggleVisible,
  };
}
