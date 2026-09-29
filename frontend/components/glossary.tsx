"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BookOpen } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export const GLOSSARY = ["hhi", "pTop", "fdr", "interval95", "evidenceGrade", "opportunityIndex", "entrant", "lift", "floor"] as const;
export type GlossaryId = (typeof GLOSSARY)[number];

/** A key term with its definition on hover, keyboard focus or tap (EN / 中文 from the dictionaries). */
export function Term({ id, children }: { id: GlossaryId; children?: React.ReactNode }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ left: 0, top: 0 });
  const ref = useRef<HTMLSpanElement>(null);
  // rendered in <body> (cards use backdrop-filter, which would capture a fixed child), at a fixed
  // position clamped to the viewport so the card is never cut off at a screen edge
  const show = () => {
    const r = ref.current?.getBoundingClientRect();
    if (r) {
      const w = Math.min(288, window.innerWidth * 0.8);
      setPos({ left: Math.max(8, Math.min(r.left, window.innerWidth - w - 8)), top: r.bottom + 6 });
    }
    setOpen(true);
  };
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("scroll", close, true);
    return () => window.removeEventListener("scroll", close, true);
  }, [open]);
  return (
    <span ref={ref} className="relative inline-flex" onMouseEnter={show} onMouseLeave={() => setOpen(false)}>
      <button type="button" onFocus={show} onBlur={() => setOpen(false)} onClick={show}
        aria-expanded={open} aria-describedby={open ? `gloss-${id}` : undefined}
        className="cursor-help border-b border-dotted [text-transform:inherit] border-ink-3 text-inherit hover:border-accent hover:text-accent focus:outline-none focus-visible:text-accent">
        {children ?? t(`gloss.${id}.name`)}
      </button>
      {open && createPortal(
        <span id={`gloss-${id}`} role="tooltip"
          style={{ left: pos.left, top: pos.top }}
          className="fixed z-50 w-72 max-w-[80vw] rounded-lg border border-line bg-panel p-3 text-left text-xs font-normal normal-case tracking-normal text-ink-2 shadow-xl">
          <span className="block text-[11px] font-semibold text-accent">{t(`gloss.${id}.name`)}</span>
          <span className="mt-1 block leading-relaxed">{t(`gloss.${id}.def`)}</span>
        </span>, document.body,
      )}
    </span>
  );
}

/** A row of key terms for a page header. */
export function GlossaryBar({ terms }: { terms: readonly GlossaryId[] }) {
  const { t } = useI18n();
  return (
    <span className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-3">
      <span className="inline-flex items-center gap-1"><BookOpen className="h-3 w-3" />{t("gloss.title")}:</span>
      {terms.map((id) => <Term key={id} id={id} />)}
    </span>
  );
}
