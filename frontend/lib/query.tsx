"use client";
import { QueryClient, QueryClientProvider, isServer } from "@tanstack/react-query";
import { useEffect } from "react";
import { ApiError, setMutationListener } from "./api";

// Request cache for the platform API. Data only changes when someone ingests or edits (a POST through
// lib/api.ts, which invalidates the cache), so responses stay fresh for 5 minutes and are kept for 30.
export const STALE_MS = 5 * 60_000;

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: STALE_MS,
        gcTime: 30 * 60_000,
        refetchOnWindowFocus: false,
        // A 4xx will not change on retry (bad request, not found, auth); network errors and 5xx get 2 retries.
        retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      },
    },
  });
}

let browserClient: QueryClient | undefined;
export function getQueryClient() {
  if (isServer) return makeQueryClient(); // never share a cache between server renders
  return (browserClient ??= makeQueryClient());
}

export function QueryProvider({ children }: { children: React.ReactNode }) {
  const client = getQueryClient();
  useEffect(() => {
    setMutationListener(() => { void client.invalidateQueries({ queryKey: ["api"] }); });
    return () => setMutationListener(null);
  }, [client]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
