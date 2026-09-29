"use client";
import { useState } from "react";
import { CircleAlert, ShieldAlert, ShieldCheck } from "lucide-react";
import { Card } from "@/components/ui/primitives";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

type Check = { id: string; status: "pass" | "warn" | "fail"; detail: string };
type Report = { status: "pass" | "warn" | "fail"; counts: Record<string, number>; checks: Check[] };

/** The truth harness of one market: which displayed numbers disagree, break their bounds or lack support. */
export function IntegrityCard({ market }: { market: string }) {
  const { t } = useI18n();
  const r = useApi<Report>(`/markets/${encodeURIComponent(market)}/integrity`, [market]);
  const [open, setOpen] = useState(false);
  if (!r.data) return null;
  const d = r.data;
  const issues = d.checks.filter((c) => c.status !== "pass");
  const tone = d.status === "fail" ? "var(--bad)" : d.status === "warn" ? "var(--warn)" : "var(--good)";
  const Icon = d.status === "pass" ? ShieldCheck : ShieldAlert;
  return (
    <Card className="p-4" role="region" aria-label={t("integ.title")}>
      <div className="flex flex-wrap items-center gap-3">
        <Icon className="h-5 w-5 shrink-0" style={{ color: tone }} aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-ink">{t(`integ.status.${d.status}`)}</div>
          <div className="text-xs text-ink-3">{t("integ.counts", { f: d.counts.fail ?? 0, w: d.counts.warn ?? 0, p: d.counts.pass ?? 0 })}</div>
        </div>
        {issues.length > 0 && <button type="button" className="text-xs text-accent hover:underline" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? t("integ.hide") : t("integ.show")}</button>}
      </div>
      {open && (
        <ul className="mt-3 space-y-2 border-t border-line pt-3">
          {issues.map((c) => (
            <li key={c.id} className="flex gap-2 text-xs">
              <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" style={{ color: c.status === "fail" ? "var(--bad)" : "var(--warn)" }} aria-hidden />
              <div><span className="font-medium text-ink">{t(`integ.check.${c.id}`)}</span>
                <span className="text-ink-3"> — {c.detail}</span></div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
