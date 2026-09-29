"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Badge, Card, CardHeader, Empty } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import type { HNode } from "@/lib/api";
import { opportunityColor } from "@/lib/colors";
import { money, moneyShort, num, truncate } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";

const LEVEL_COLOR: Record<string, string> = { Category: "var(--accent)", Family: "var(--accent-2)", Segment: "var(--warn)", Model: "var(--good)", Variant: "var(--ink)", Listing: "var(--ink-3)" };

function Row({ n, depth, open0 }: { n: HNode; depth: number; open0: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(open0);
  const kids = n.children ?? [];
  return (
    <div>
      <div className="flex items-center gap-2 border-b border-line/50 py-1.5 pr-4 text-sm hover:bg-panel-2" style={{ paddingLeft: 12 + depth * 18 }}>
        {kids.length ? <button onClick={() => setOpen(!open)} className="text-ink-3">{open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}</button>
          : <span className="w-3.5" />}
        <span className="w-16 shrink-0 text-[10px] uppercase tracking-wider" style={{ color: LEVEL_COLOR[n.kind] }}>{t(`hier.kind.${n.kind}`).startsWith("hier.") ? n.kind : t(`hier.kind.${n.kind}`)}</span>
        {n.kind === "Variant" ? (
          <Link href={`/products/${n.id}`} className="min-w-0 flex-1 truncate hover:text-accent" title={n.title}>
            <b>{n.label}</b> <span className="text-ink-3">· {truncate(n.title, 70)}</span>
          </Link>
        ) : n.kind === "Listing" ? (
          <span className="min-w-0 flex-1 truncate font-mono text-xs">{n.label}{n.best && <Badge className="ml-2" color="var(--accent)">{t("hier.bestSelling")}</Badge>}</span>
        ) : (
          <span className="min-w-0 flex-1 truncate">{n.label}{n.kind === "Model" && n.basis && <span className="ml-2 text-[10px] text-ink-3">({n.basis === "none" ? t("hier.unassigned") : t("hier.basisModel", { b: n.basis })})</span>}</span>
        )}
        {n.products != null && <span className="w-20 text-right font-mono text-xs text-ink-3">{num(n.products)} {t("hier.prod")}</span>}
        {n.kind === "Listing" ? <span className="w-28 text-right font-mono text-xs">{money(n.price, 2)} · {num(n.sales)}{t("hier.perMo")}</span>
          : <span className="w-28 text-right font-mono text-xs">{moneyShort(n.monthly_revenue)}{t("hier.perMo")}</span>}
        <span className="w-10 text-right font-mono text-xs" style={{ color: opportunityColor(n.opportunity ?? null) }}>{n.opportunity != null ? n.opportunity.toFixed(0) : ""}</span>
        <span className="w-12 text-right font-mono text-[11px] text-ink-3" title={t("hier.dataConfidence")}>{n.confidence != null ? `${n.confidence.toFixed(0)}%` : ""}</span>
      </div>
      {open && kids.map((k) => <Row key={`${k.kind}-${k.id}`} n={k} depth={depth + 1} open0={false} />)}
    </div>
  );
}

export default function Hierarchy() {
  const mn = useMarketName();
  const { t } = useI18n();
  const market = decodeURIComponent(useParams<{ market: string }>().market);
  const { data, error } = useApi<HNode>(`/markets/${encodeURIComponent(market)}/hierarchy`);
  return (
    <div className="pb-8">
      <PageHeader title={`${t("hier.title")} · ${mn(market)}`}
        subtitle={t("hier.subtitle")}
        right={<Link className="text-sm text-accent hover:underline" href={`/markets/${encodeURIComponent(market)}`}>← {t("hier.back")}</Link>} />
      <ErrorNote error={error} />
      <Card className="mx-6">
        <CardHeader title={t("hier.card")} subtitle={t("hier.cardSub")} />
        {!data ? <Empty title={t("common.loading")} /> : <Row n={data} depth={0} open0 />}
      </Card>
    </div>
  );
}
