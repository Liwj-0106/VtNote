import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  foregroundPollDelay,
  isTerminalStatus,
  retryPollDelay,
} from "../api/client";
import type { Task } from "../api/types";

export function useApiResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    if (path === null) return null;
    const current = ++requestId.current;
    setLoading(true);
    try {
      const result = await api.request<T>(path);
      if (current === requestId.current) {
        setData(result);
        setError(null);
      }
      return result;
    } catch (caught) {
      if (current === requestId.current) {
        setError(caught instanceof Error ? caught : new Error("请求失败"));
      }
      return null;
    } finally {
      if (current === requestId.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    const controller = new AbortController();
    if (path === null) {
      setLoading(false);
      return () => controller.abort();
    }
    const current = ++requestId.current;
    setLoading(true);
    api
      .request<T>(path, { signal: controller.signal })
      .then((result) => {
        if (current === requestId.current) {
          setData(result);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (
          current === requestId.current &&
          !(caught instanceof DOMException && caught.name === "AbortError")
        ) {
          setError(caught instanceof Error ? caught : new Error("请求失败"));
        }
      })
      .finally(() => {
        if (current === requestId.current) setLoading(false);
      });
    return () => controller.abort();
  }, [path]);

  return { data, error, loading, refresh, setData };
}

export function useTaskPolling(taskId: string) {
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let canceled = false;
    let timer: number | undefined;
    let inFlight: AbortController | null = null;
    let startedAt = performance.now();
    let failures = 0;

    const schedule = (delay: number) => {
      if (!canceled) timer = window.setTimeout(load, delay);
    };
    const load = async () => {
      if (canceled || inFlight !== null) return;
      inFlight = new AbortController();
      try {
        const next = await api.request<Task>(`/api/tasks/${taskId}`, {
          signal: inFlight.signal,
        });
        if (canceled) return;
        setTask(next);
        setError(null);
        failures = 0;
        if (isTerminalStatus(next.status)) return;
        const delay = document.hidden
          ? 15_000
          : foregroundPollDelay(performance.now() - startedAt);
        schedule(delay);
      } catch (caught) {
        if (canceled) return;
        failures += 1;
        setError(caught instanceof Error ? caught : new Error("请求失败"));
        schedule(retryPollDelay(failures));
      } finally {
        inFlight = null;
      }
    };
    const onVisibility = () => {
      if (!document.hidden) {
        if (timer !== undefined) window.clearTimeout(timer);
        void load();
      }
    };
    startedAt = performance.now();
    void load();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      canceled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      inFlight?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [taskId, refreshNonce]);

  return {
    task,
    error,
    refresh: () => setRefreshNonce((value) => value + 1),
  };
}
