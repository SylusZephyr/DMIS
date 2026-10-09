"use client";
import { useState } from "react";
import { Languages } from "lucide-react";
import { post } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Item = { source: string; text: string | null; status: string; reason: string | null };
type Res = { target: string; status: string; items: Item[] };

/** Machine translation of one piece of listing text into the interface language. The original stays where it is;
 * the translation appears beneath it, labelled as machine output (or the reason there is none). */
export function TranslateText({ text, refId }: { text: string; refId?: string }) {
  const { t, lang } = useI18n();
  const [state, setState] = useState<"" | "busy" | "done" | "error">("");
  const [item, setItem] = useState<Item | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (!text.trim()) return null;
  const go = async () => {
    setState("busy"); setMsg(null);
    try {
      const r = await post<Res>("/translate", { texts: [text], target: lang, ref: refId }, { invalidate: false });
      setItem(r.items[0] ?? null); setState("done");
    } catch (e) { setState("error"); setMsg((e as Error).message); }
  };
  const ok = item && (item.status === "ok" || item.status === "cached");
  return (
    <div className="text-xs">
      {state !== "done" && (
        <button type="button" onClick={go} disabled={state === "busy"} aria-label={t("translate.button")}
          className="inline-flex items-center gap-1 text-ink-3 hover:text-accent disabled:opacity-60">
          <Languages className={`h-3.5 w-3.5 ${state === "busy" ? "animate-pulse" : ""}`} aria-hidden />{t("translate.button")}
        </button>
      )}
      {state === "error" && <span role="alert" className="ml-2 text-bad">{msg}</span>}
      {state === "done" && item && (ok ? (
        <p className="leading-snug text-ink-2"><span className="mr-1 text-ink-3">{t("translate.machine")}</span>{item.text}</p>
      ) : item.status === "already_target" ? (
        <p className="text-ink-3">{t("translate.already")}</p>
      ) : (
        <p className="text-ink-3" title={item.reason ?? ""}>{t(`translate.status.${item.status}`).startsWith("translate.")
          ? t("translate.status.error") : t(`translate.status.${item.status}`)}</p>
      ))}
    </div>
  );
}
