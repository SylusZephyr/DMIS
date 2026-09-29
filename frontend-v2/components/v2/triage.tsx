"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Keyboard } from "lucide-react";
import { useI18n } from "@/lib/i18n";

/** Keyboard triage over a list: j/k (or arrows) move, x selects, and each action has its own key.
 *  Keys are ignored while typing, with modifiers, and when another handler already used the key
 *  (the shell's "g then letter" jumps call preventDefault first). */
export function useTriage(count: number, actions: Record<string, (index: number) => void>) {
  const [focus, setFocus] = useState(0);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const refs = useRef<(HTMLElement | null)[]>([]);
  const act = useRef(actions);
  useEffect(() => { act.current = actions; });
  // the list changed length (a decided row left it): keep focus in range, drop a selection of old indices
  const [seen, setSeen] = useState(count);
  if (seen !== count) {
    setSeen(count);
    setFocus((f) => Math.min(f, Math.max(0, count - 1)));
    setSelected(new Set());
  }

  const move = useCallback((to: number) => {
    const i = Math.max(0, Math.min(count - 1, to));
    setFocus(i);
    refs.current[i]?.scrollIntoView?.({ block: "nearest" });
  }, [count]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
      if (e.defaultPrevented || typing || e.metaKey || e.ctrlKey || e.altKey || count === 0) return;
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); move(focus + 1); return; }
      if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); move(focus - 1); return; }
      if (e.key === "x") {
        e.preventDefault();
        setSelected((s) => { const n = new Set(s); if (n.has(focus)) n.delete(focus); else n.add(focus); return n; });
        return;
      }
      const fn = act.current[e.key];
      if (fn) { e.preventDefault(); fn(focus); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, focus, move]);

  const toggle = (i: number) => setSelected((s) => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  const all = () => setSelected((s) => (s.size === count ? new Set() : new Set(Array.from({ length: count }, (_, i) => i))));
  const clear = () => setSelected(new Set());
  const bind = (i: number) => ({
    ref: (el: HTMLElement | null) => { refs.current[i] = el; },
    onClick: () => setFocus(i),
    "data-focused": focus === i ? "true" : undefined,
    "aria-current": focus === i ? ("true" as const) : undefined,
  });
  return { focus, setFocus: move, selected, toggle, all, clear, bind };
}

/** The key legend shown above a triage list. */
export function KeyHints({ keys }: { keys: [string, string][] }) {
  const { t } = useI18n();
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-3">
      <Keyboard className="h-3.5 w-3.5" aria-hidden />
      {[["j / k", t("triage.move")], ["x", t("triage.select")], ...keys].map(([k, label]) => (
        <span key={k}><kbd className="rounded border border-line bg-panel-2 px-1 font-mono text-[10px] text-ink-2">{k}</kbd> {label}</span>
      ))}
    </p>
  );
}
