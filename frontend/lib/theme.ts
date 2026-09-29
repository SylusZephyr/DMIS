"use client";
import { useSyncExternalStore } from "react";
import { THEME_KEY } from "./theme-script";

// Colour theme of the interface. The viewer's explicit choice is stored in this browser; without one the
// system preference (prefers-color-scheme) wins. The resolved theme is written to <html data-theme>, which
// globals.css keys its token sets on; THEME_INIT_SCRIPT (lib/theme-script.ts) does the same before first paint (no flash).
export type Theme = "light" | "dark";
export { THEME_KEY };
const EVT = "dmis-theme";

export function storedTheme(): Theme | null {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch { return null; }
}
function systemTheme(): Theme {
  try { return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"; } catch { return "dark"; }
}
export function resolvedTheme(): Theme {
  return storedTheme() ?? systemTheme();
}

export function setTheme(t: Theme | null) {
  try {
    if (t) window.localStorage.setItem(THEME_KEY, t);
    else window.localStorage.removeItem(THEME_KEY);
  } catch { /* storage unavailable: the choice lasts for this page only */ }
  document.documentElement.dataset.theme = t ?? systemTheme();
  window.dispatchEvent(new Event(EVT));
}

function subscribe(cb: () => void) {
  const mq = window.matchMedia("(prefers-color-scheme: light)");
  // system preference changed, or another tab stored a choice: re-resolve and re-apply
  const sync = () => { document.documentElement.dataset.theme = resolvedTheme(); cb(); };
  window.addEventListener(EVT, cb);
  window.addEventListener("storage", sync);
  mq.addEventListener("change", sync);
  return () => { window.removeEventListener(EVT, cb); window.removeEventListener("storage", sync); mq.removeEventListener("change", sync); };
}

/** The theme in effect (server render assumes dark, the historical default). */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, resolvedTheme, () => "dark" as Theme);
}

/** Current value of a CSS custom property (e.g. "--ink-3") on <html>; "" on the server. */
export function cssVar(name: string): string {
  if (typeof window === "undefined") return "";
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** True when the viewer asked the OS for reduced motion (auto-rotation and transitions should stop). */
export function prefersReducedMotion(): boolean {
  try { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
}
function subscribeMotion(cb: () => void) {
  const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
  mq.addEventListener("change", cb);
  return () => mq.removeEventListener("change", cb);
}
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeMotion, prefersReducedMotion, () => false);
}
