"use client";
import { useState } from "react";
import { Badge, Card, CardHeader, Empty, Select } from "@/components/ui/primitives";
import type { MarketRow } from "@/lib/api";
import { num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

type Conf = { n: number; dental: number; non_dental: number; review: number; true_dental: number; missed_dental: number;
  true_non_dental: number; false_dental: number; dental_recall: number | null; dental_precision: number | null;
  non_dental_precision: number | null; review_share: number | null; source?: string; labelled_by?: string[]; note?: string; matched?: number };
type Stab = { status: string; ranked: number; permutations?: number; weight_spread?: number; median_spearman?: number;
  scopes?: { scope: string; scope_id: string; label: string; rank: number; rank_min: number; rank_max: number; top3_share: number }[] };
type KA = { market: string; dental: { gold_benchmark: Conf | null; human_labels: Conf | null };
  identity: { precision: number | null; recall: number | null; labelled_pairs: number } | null;
  taxonomy_decisions: Record<string, number>; ranking_stability: Stab | null; note: string };

function ConfRow({ title, c }: { title: string; c: Conf | null }) {
  const { t } = useI18n();
  if (!c || !c.n) return <div className="text-xs text-ink-3">{title}: {t("ka.none")}</div>;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-sm">{title}
        {c.labelled_by?.length ? <Badge color="var(--warn)">{t("ka.modelLabelled")}</Badge> : null}</div>
      <div className="grid grid-cols-2 gap-2 font-mono text-xs md:grid-cols-4">
        <div>{t("ka.recall")} <b>{pct(c.dental_recall)}</b></div>
        <div>{t("ka.precision")} <b>{pct(c.dental_precision)}</b></div>
        <div>{t("ka.review")} <b>{pct(c.review_share)}</b></div>
        <div>n <b>{num(c.n)}</b> ({num(c.dental)} / {num(c.non_dental)})</div>
      </div>
    </div>
  );
}

/** Accuracy of the knowledge layer for one market, and the stability of its opportunity ranking. */
export function KnowledgeAccuracyCard() {
  const mn = useMarketName();
  const { t } = useI18n();
  const markets = useApi<MarketRow[]>("/markets");
  const [picked, setPicked] = useState("");
  const m = picked || markets.data?.[0]?.name || "";
  const d = useApi<KA>(m ? `/markets/${encodeURIComponent(m)}/knowledge/accuracy` : null, [m]);
  const st = d.data?.ranking_stability;
  return (
    <Card>
      <CardHeader title={t("ka.title")} subtitle={t("ka.sub")}
        right={<Select aria-label={t("kn.marketName")} value={m} onChange={(e) => setPicked(e.target.value)}>
          {(markets.data ?? []).map((x) => <option key={x.name} value={x.name}>{mn(x.name)}</option>)}</Select>} />
      {!d.data ? <div className="h-24 animate-pulse" /> : (
        <div className="space-y-4 p-4">
          <ConfRow title={t("ka.gold")} c={d.data.dental.gold_benchmark} />
          <ConfRow title={t("ka.human")} c={d.data.dental.human_labels} />
          <div className="text-sm">{t("ka.identity")}: {d.data.identity?.labelled_pairs
            ? <span className="font-mono">{t("kn.precision")} {pct(d.data.identity.precision)} · {t("kn.recall")} {pct(d.data.identity.recall)} · n {num(d.data.identity.labelled_pairs)}</span>
            : <span className="text-ink-3">{t("ka.none")}</span>}</div>
          <div className="text-sm">{t("ka.taxonomy")}: {Object.keys(d.data.taxonomy_decisions).length
            ? Object.entries(d.data.taxonomy_decisions).map(([k, v]) => <Badge key={k} color="var(--ink-3)">{k} · {v}</Badge>)
            : <span className="text-ink-3">{t("ka.none")}</span>}</div>
          <div>
            <div className="text-sm">{t("ka.stability")}{st?.status === "ok" &&
              <span className="font-mono"> · Spearman {st.median_spearman?.toFixed(2)} ({t("ka.spread", { s: pct(st.weight_spread ?? 0, 0), n: st.permutations ?? 0 })})</span>}</div>
            {st?.status === "ok" ? (
              <div className="overflow-x-auto" tabIndex={0}>
                <table className="mt-1 w-full text-xs">
                  <thead className="text-left text-[10px] uppercase text-ink-3"><tr>
                    <th scope="col" className="py-1">{t("kn.opportunity")}</th><th scope="col" className="text-right">{t("ka.rank")}</th>
                    <th scope="col" className="text-right">{t("ka.range")}</th><th scope="col" className="text-right">{t("ka.top3")}</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {(st.scopes ?? []).slice(0, 8).map((s) => (
                      <tr key={`${s.scope}|${s.scope_id}`}><td className="max-w-[16rem] truncate py-1" title={s.label}>{s.label}</td>
                        <td className="text-right font-mono">{s.rank}</td><td className="text-right font-mono">{s.rank_min}–{s.rank_max}</td>
                        <td className="text-right font-mono">{pct(s.top3_share, 0)}</td></tr>))}
                  </tbody>
                </table>
              </div>
            ) : <Empty title={t("ka.tooFew")} />}
          </div>
          <p className="text-[11px] text-ink-3">{t("ka.note")}</p>
        </div>
      )}
    </Card>
  );
}
