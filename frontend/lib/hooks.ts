"use client";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { get } from "./api";

/** Query-cache key of a GET made through useApi (exported so mutations and tests can target it). */
export const apiKey = (path: string, deps: unknown[] = []) => ["api", path, ...deps] as const;

/** Fetch `path` from the platform API through the shared request cache (lib/query.tsx).
 *  `deps` are extra values that make a distinct cache entry (and so a refetch) when they change.
 *  Identical requests made at the same time are de-duplicated; revisiting a page within the stale window
 *  renders from cache. While a new key loads, the previous key's data stays visible (`loading` is true). */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const q = useQuery<T, Error>({
    queryKey: apiKey(path ?? "", deps),
    queryFn: ({ signal }) => get<T>(path as string, { signal }),
    enabled: path != null,
    placeholderData: keepPreviousData,
  });
  const { refetch } = q;
  const reload = useCallback(() => { void refetch(); }, [refetch]);
  return {
    data: (q.data ?? null) as T | null,
    error: q.error ? q.error.message : null,
    loading: path != null && (q.isPending || q.isPlaceholderData || q.isRefetching),
    reload,
  };
}
