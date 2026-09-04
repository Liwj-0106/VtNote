import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { LibraryMetadata } from "../../api/types";
import { emptyLibraryFilters, hasLibraryFilters, type LibraryFilters } from "./types";

export function useLibraryDiscovery(initialFilters: Partial<LibraryFilters> = {}) {
  const [metadata, setMetadata] = useState<LibraryMetadata>({ collections: [], tags: [] });
  const [filters, setFilters] = useState<LibraryFilters>(() => ({
    ...emptyLibraryFilters,
    ...initialFilters,
  }));
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [deferredQuery, setDeferredQuery] = useState(filters.query);
  useEffect(() => {
    const timer = window.setTimeout(() => setDeferredQuery(filters.query), 240);
    return () => window.clearTimeout(timer);
  }, [filters.query]);
  const effectiveFilters = useMemo(
    () => ({ ...filters, query: deferredQuery }),
    [deferredQuery, filters],
  );
  const searchActive = hasLibraryFilters(effectiveFilters);
  const discoveryQuery = useMemo(() => {
    if (!searchActive) return "";
    const query = new URLSearchParams({ limit: "100" });
    if (effectiveFilters.query.trim()) query.set("q", effectiveFilters.query.trim());
    if (effectiveFilters.source) query.set("source", effectiveFilters.source);
    if (effectiveFilters.status) query.set("status", effectiveFilters.status);
    if (effectiveFilters.collectionId) query.set("collection_id", effectiveFilters.collectionId);
    if (effectiveFilters.unclassified) query.set("unclassified", "true");
    if (effectiveFilters.tagId) query.set("tag_id", effectiveFilters.tagId);
    if (effectiveFilters.excerptsOnly) query.set("excerpts_only", "true");
    return query.toString();
  }, [effectiveFilters, searchActive]);

  useEffect(() => {
    const controller = new AbortController();
    api.request<LibraryMetadata>("/api/library/meta", { signal: controller.signal })
      .then((value) => {
        if (value && Array.isArray(value.collections) && Array.isArray(value.tags)) {
          setMetadata(value);
        }
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setMetadataError(caught instanceof ApiError ? caught.message : "无法读取合集与标签。");
        }
      });
    return () => controller.abort();
  }, []);

  return {
    filters,
    setFilters,
    metadata,
    setMetadata,
    metadataError,
    searchActive,
    discoveryQuery,
  };
}
