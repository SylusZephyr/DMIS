"use client";
// Small per-viewer preferences kept in this browser (recent pages, pinned pages, a collapsed panel).
// They are conveniences only: when storage is unavailable they last until the page reloads.
import { useSyncExternalStore } from "react";

type Store<T> = { useValue: () => T; set: (v: T) => void; get: () => T };

export function persisted<T>(key: string, fallback: T): Store<T> {
  const evt = `dmis-pref:${key}`;
  const fallbackRaw = JSON.stringify(fallback);
  let mem: string | null = null;
  let cacheRaw: string | null = null;
  let cacheVal: T = fallback;
  const raw = () => {
    try { return window.localStorage.getItem(key) ?? mem ?? fallbackRaw; } catch { return mem ?? fallbackRaw; }
  };
  // parse once per distinct string, so useSyncExternalStore sees a stable snapshot
  const get = (): T => {
    const r = raw();
    if (r !== cacheRaw) {
      cacheRaw = r;
      try { cacheVal = JSON.parse(r) as T; } catch { cacheVal = fallback; }
    }
    return cacheVal;
  };
  const set = (v: T) => {
    mem = JSON.stringify(v);
    try { window.localStorage.setItem(key, mem); } catch { /* storage unavailable: kept in memory */ }
    window.dispatchEvent(new Event(evt));
  };
  const subscribe = (cb: () => void) => {
    const on = (e: Event) => { if (!(e instanceof StorageEvent) || e.key === key) cb(); };
    window.addEventListener(evt, on);
    window.addEventListener("storage", on);
    return () => { window.removeEventListener(evt, on); window.removeEventListener("storage", on); };
  };
  return { get, set, useValue: () => useSyncExternalStore(subscribe, get, () => fallback) };
}

export const recentPages = persisted<string[]>("dmis_v2_recent", []);
export const pinnedPages = persisted<string[]>("dmis_v2_pinned", []);
export const panelCollapsed = persisted<boolean>("dmis_v2_panel_collapsed", false);

export function pushRecent(href: string) {
  const cur = recentPages.get().filter((h) => h !== href);
  recentPages.set([href, ...cur].slice(0, 8));
}
export function togglePinned(href: string) {
  const cur = pinnedPages.get();
  pinnedPages.set(cur.includes(href) ? cur.filter((h) => h !== href) : [...cur, href].slice(0, 12));
}
