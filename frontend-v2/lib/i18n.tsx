"use client";
import { createContext, useCallback, useContext, useEffect, useSyncExternalStore } from "react";
import { en } from "@/messages/en";
import { zh } from "@/messages/zh";

export type Lang = "en" | "zh";
type Dict = { [k: string]: string | Dict };
const DICTS: Record<Lang, Dict> = { en: en as unknown as Dict, zh: zh as unknown as Dict };
const KEY = "dmis_lang";

function lookup(d: Dict, key: string): string | undefined {
  let cur: string | Dict | undefined = d;
  for (const part of key.split(".")) {
    if (cur == null || typeof cur === "string") return undefined;
    cur = cur[part];
  }
  return typeof cur === "string" ? cur : undefined;
}

type Ctx = { lang: Lang; setLang: (l: Lang) => void; t: (key: string, vars?: Record<string, string | number>) => string };
const I18n = createContext<Ctx>({ lang: "en", setLang: () => {}, t: (k) => k });

/** Language of the interface (data values such as titles and brands are never translated). Stored per viewer. */
const EVT = "dmis-lang";
function readLang(): Lang {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "zh" ? "zh" : "en";
  } catch { return "en"; }
}
function subscribe(cb: () => void) {
  window.addEventListener(EVT, cb);
  window.addEventListener("storage", cb);
  return () => { window.removeEventListener(EVT, cb); window.removeEventListener("storage", cb); };
}

/** Language of the interface (data values such as titles and brands are never translated). Stored per viewer. */
export function LangProvider({ children }: { children: React.ReactNode }) {
  const lang = useSyncExternalStore(subscribe, readLang, () => "en" as Lang);
  useEffect(() => { document.documentElement.lang = lang === "zh" ? "zh-CN" : "en"; }, [lang]);
  const setLang = useCallback((l: Lang) => {
    try { window.localStorage.setItem(KEY, l); } catch { /* storage unavailable */ }
    window.dispatchEvent(new Event(EVT));
  }, []);
  const t = useCallback((key: string, vars?: Record<string, string | number>) => {
    let s = lookup(DICTS[lang], key) ?? lookup(DICTS.en, key) ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
    return s;
  }, [lang]);
  return <I18n.Provider value={{ lang, setLang, t }}>{children}</I18n.Provider>;
}

export function useI18n() {
  return useContext(I18n);
}

export function LangSwitch() {
  const { lang, setLang, t } = useI18n();
  return (
    <div role="group" aria-label={t("a11y.language")} className="inline-flex overflow-hidden rounded-md border border-line text-[11px]">
      {(["en", "zh"] as Lang[]).map((l) => (
        <button key={l} type="button" onClick={() => setLang(l)} aria-pressed={l === lang} lang={l === "zh" ? "zh-CN" : "en"}
          className={l === lang ? "bg-accent/15 px-2 py-0.5 text-accent" : "px-2 py-0.5 text-ink-3 hover:text-ink"}>
          {l === "en" ? "EN" : "中文"}
        </button>
      ))}
    </div>
  );
}
