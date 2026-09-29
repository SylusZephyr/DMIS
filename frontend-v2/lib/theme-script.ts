// Server-safe (no "use client"): imported by app/layout.tsx to inline in <head>.
export const THEME_KEY = "dmis_theme";

/** Resolves the theme before the first paint (stored choice, else the system preference). Tiny and dependency-free. */
export const THEME_INIT_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t!=="light"&&t!=="dark")t=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="dark"}`;
