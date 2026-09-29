"use client";
import Link from "next/link";
import { Badge, Card, Empty } from "@/components/ui/primitives";
import { headlineOf, ModelledNote } from "@/components/v2/market-size";
import type { MarketRow } from "@/lib/api";
import { opportunityColor } from "@/lib/colors";
import { moneyShort, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

const GRADE: Record<string, string> = { A: "var(--good)", B: "#3987e5", C: "var(--warn)", D: "var(--ink-3)" };

export function MarketGrid({ base, cta }: { base: string; cta: string }) {
  const mn = useMarketName();
  const { t } = useI18n();
  const { data } = useApi<MarketRow[]>("/markets");
  if (data && data.length === 0) return <Empty title={t("picker.none")}>{t("picker.noneSub")}</Empty>;
  if (!data) return (
    <div className="grid grid-cols-1 gap-4 px-6 pb-6 sm:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((i) => <div key={i} className="h-40 animate-pulse rounded-xl border border-line bg-panel" />)}
    </div>
  );
  const maxRev = Math.max(1, ...data.map((m) => m.revenue_hi ?? m.revenue_est ?? m.monthly_revenue ?? 0));
  return (
    <div className="grid grid-cols-1 gap-4 px-6 pb-6 sm:grid-cols-2 xl:grid-cols-3">
      {data.map((m) => {
        const opp = m.top_opportunity_score ?? m.top_segment?.opportunity_score ?? null;   // the one opportunity score (explainable engine)
        const est = m.revenue_est;
        const head = headlineOf(m);
        return (
          <Link key={m.name} href={`${base}/${encodeURIComponent(m.name)}`}>
            <Card className="h-full p-4 transition-colors hover:border-accent">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-medium">{mn(m.name)}</div>
                  <div className="mt-0.5 text-xs text-ink-3">{m.industry_branch} · {num(m.products)} {t("common.products")} · {num(m.segments)} {t("common.segments")}</div>
                </div>
                <div className="text-right">
                  <div className="font-mono text-lg leading-none" style={{ color: opportunityColor(opp) }}>{opp?.toFixed(0) ?? "—"}</div>
                  <div className="text-[10px] text-ink-3">{t("picker.topOpp")}</div>
                </div>
              </div>
              <div className="mt-3 flex items-baseline gap-2">
                <span className="font-mono text-base" title={t("size.observedTip")}>{moneyShort(head)}<span className="text-xs text-ink-3">/mo · {t("size.observed")}</span></span>
                {m.evidence_grade && <Badge className="ml-auto" color={GRADE[m.evidence_grade]}>{t("common.evidenceGrade")} {m.evidence_grade}</Badge>}
              </div>
              {m.revenue_lo != null && m.revenue_hi != null && est != null && (
                <div className="relative mt-1.5 h-1.5 rounded bg-line" title={t("picker.intervalTip")}>
                  <div className="absolute inset-y-0 rounded bg-[#3987e5]/40" style={{ left: `${(m.revenue_lo / maxRev) * 100}%`, width: `${((m.revenue_hi - m.revenue_lo) / maxRev) * 100}%` }} />
                  <div className="absolute -top-0.5 h-2.5 w-0.5 bg-[#3987e5]" style={{ left: `${(est / maxRev) * 100}%` }} />
                  {head != null && <div className="absolute -top-1 h-3.5 w-0.5 bg-ink" title={t("size.observedTip")} style={{ left: `${(head / maxRev) * 100}%` }} />}
                </div>
              )}
              <ModelledNote m={m} className="mt-1 block text-[11px] text-ink-3" />
              {m.top_segment && <div className="mt-3 truncate text-xs text-ink-2">{t("home.topSegment")}: {m.top_segment.segment_label}</div>}
              <div className="mt-1 flex items-center justify-between text-xs">
                <span className="text-ink-3">HHI {num(m.hhi)}</span>
                <span className="text-accent">{cta} →</span>
              </div>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
