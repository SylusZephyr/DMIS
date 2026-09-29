"use client";
import { useEffect } from "react";
import { X } from "lucide-react";
import { Badge, Button } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api-typed";
import { num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Why = Schemas["WhyResponse"];

export const KIND_COLOR: Record<string, string> = {
  observed: "var(--good)", estimated: "var(--warn)", modeled: "var(--accent)", derived: "var(--ink-3)",
};

/** A value's kind as a small colored label: observed / estimated / modeled / derived (spec rule 3). */
export function KindBadge({ kind }: { kind: string }) {
  const { t } = useI18n();
  const key = `kn.kind${kind.charAt(0).toUpperCase()}${kind.slice(1)}`;
  return <Badge color={KIND_COLOR[kind] ?? "var(--ink-3)"}>{t(key)}</Badge>;
}

function value(v: number | null | undefined, unit?: string | null) {
  if (v == null) return "—";
  const digits = Math.abs(v) < 10 && !Number.isInteger(v) ? 2 : 0;
  return `${num(v, digits)}${unit ? ` ${unit}` : ""}`;
}

/** "Why does the system believe this?" -- current observations, their history and the evidence (spec 14). */
export function WhyDrawer({ market, entityType, entityId, metric, onClose }: {
  market: string; entityType: string; entityId: string; metric?: string; onClose: () => void;
}) {
  const { t } = useI18n();
  const q = new URLSearchParams({ entity_type: entityType, entity_id: entityId, ...(metric ? { metric } : {}) });
  const d = useApi<Why>(`/markets/${encodeURIComponent(market)}/why?${q}`, [market, entityType, entityId, metric]);
  const w = d.data;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" role="dialog" aria-modal="true" aria-label={t("kn.whyTitle")}
      onClick={onClose}>
      <aside className="h-full w-full max-w-xl overflow-y-auto border-l border-line bg-panel p-5 scrollbar-thin" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold">{t("kn.whyTitle")}</h2>
            <p className="mt-0.5 break-all font-mono text-xs text-ink-3">{entityType} · {entityId}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label={t("nav.close")}><X className="h-4 w-4" /></Button>
        </div>
        {d.error && <p className="text-sm text-bad">{d.error}</p>}
        {w && (
          <div className="space-y-5 text-sm">
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.current")}</h3>
              <ul className="space-y-2">
                {w.current.map((o, i) => (
                  <li key={i} className="rounded-lg border border-line p-2.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">{o.metric}</span>
                      <span className="flex items-center gap-2 font-mono tabular-nums">
                        {value(o.value, o.unit)}
                        {o.lo != null && o.hi != null && <span className="text-ink-3">({value(o.lo)}–{value(o.hi)})</span>}
                        <KindBadge kind={o.kind} />
                      </span>
                    </div>
                    {o.method && <p className="mt-1 text-xs text-ink-3">{t("kn.method")}: {o.method}</p>}
                    <p className="text-xs text-ink-3">{t("kn.source")}: {o.source ?? "—"} · {o.observed_at ?? "—"}</p>
                  </li>
                ))}
              </ul>
            </section>
            {w.history.length > w.current.length && (
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.history")}</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <tbody className="divide-y divide-line">
                      {w.history.map((o, i) => (
                        <tr key={i}><td className="py-1 pr-3 text-ink-3">{o.observed_at}</td><td className="pr-3">{o.metric}</td>
                          <td className="pr-3 font-mono tabular-nums">{value(o.value, o.unit)}</td><td><KindBadge kind={o.kind} /></td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-3">{t("kn.evidence")}</h3>
              {w.evidence.length === 0 ? <p className="text-ink-3">{t("kn.noEvidence")}</p> : (
                <ul className="space-y-1.5">
                  {w.evidence.slice(0, 60).map((e, i) => (
                    <li key={i} className="flex gap-2 text-xs">
                      <Badge color="var(--ink-3)">{e.evidence_type}{e.subject ? ` · ${e.subject}` : ""}</Badge>
                      <span className="text-ink-2">{e.text_excerpt}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}
