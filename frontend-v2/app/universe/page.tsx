"use client";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { Badge, Card, CardHeader, Empty, Meter } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import type { UniverseNode } from "@/lib/api";
import { opportunityColor } from "@/lib/colors";
import { moneyShort, num, pct } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";

const Universe = dynamic(() => import("@/components/three/universe"), { ssr: false });

export default function UniversePage() {
  const { t } = useI18n();
  const { data, error } = useApi<UniverseNode>("/universe");
  const [sel, setSel] = useState<UniverseNode | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const select = (n: UniverseNode) => {
    setSel(n);
    if (n.kind === "Category") setFocus(n.id);
  };
  const children = sel?.children ?? data?.children ?? [];
  return (
    <div className="flex h-screen flex-col">
      <PageHeader title={t("universe.title")} subtitle={t("universe.subtitle")} />
      <ErrorNote error={error} />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 px-6 pb-6 xl:grid-cols-[1fr_360px]">
        <Card className="relative min-h-[520px] overflow-hidden">
          {data && <Universe root={data} focus={focus} selected={sel?.id ?? null} onSelect={select} ariaLabel={t("a11y.view3dNote")} />}
        </Card>
        <Card className="flex min-h-0 flex-col">
          <CardHeader title={t(`universe.kind.${sel?.kind ?? "Industry"}`)} subtitle={sel?.label ?? data?.label} right={sel && sel.kind !== "Industry" &&
            <button className="text-xs text-ink-3 hover:text-ink" onClick={() => { setSel(null); setFocus(null); }}>{t("common.reset")}</button>} />
          {sel && sel.kind !== "Industry" && (
            <div className="grid grid-cols-2 gap-3 border-b border-line px-4 py-3 text-sm">
              <div><div className="text-[10px] uppercase text-ink-3">{t("universe.value")}</div><div className="font-mono">{moneyShort(sel.value)}/mo</div>{sel.value_lo != null && <div className="text-[10px] text-ink-3">{moneyShort(sel.value_lo)}–{moneyShort(sel.value_hi)}</div>}</div>
              <div><div className="text-[10px] uppercase text-ink-3">{t("universe.opportunity")}</div><div className="font-mono" style={{ color: opportunityColor(sel.opportunity) }}>{sel.opportunity?.toFixed(0) ?? "—"}</div></div>
              <div><div className="text-[10px] uppercase text-ink-3">{sel.growth != null ? t("universe.growth") : t("universe.momentum")}</div><div className="font-mono">{sel.growth != null ? pct(sel.growth) : sel.momentum != null ? pct(sel.momentum) : sel.growth_label ?? "—"}</div></div>
              <div><div className="text-[10px] uppercase text-ink-3">{sel.kind === "Segment" ? t("universe.listingsLabel") : t("home.products")}</div><div className="font-mono">{num(sel.products)}</div></div>
              {sel.market && (
                <div className="col-span-2 flex flex-wrap gap-3 text-xs">
                  <Link className="text-accent hover:underline" href={`/markets/${encodeURIComponent(sel.market)}`}>{t("universe.analytics")} →</Link>
                  <Link className="text-accent hover:underline" href={`/galaxy/${encodeURIComponent(sel.market)}${sel.segment_id ? `?segment=${sel.segment_id}` : ""}`}>{t("universe.galaxy")} →</Link>
                </div>
              )}
            </div>
          )}
          <div className="min-h-0 flex-1 divide-y divide-line overflow-y-auto scrollbar-thin">
            {children.length === 0 && <Empty title={t("universe.empty")} />}
            {[...children].sort((a, b) => (b.value ?? 0) - (a.value ?? 0)).map((c) => (
              <button key={c.id} onClick={() => select(c)} className="flex w-full items-center gap-3 px-4 py-2 text-left hover:bg-panel-2">
                <span className="h-3 w-3 rounded-full" style={{ background: c.kind === "Branch" ? "#8b7bff" : opportunityColor(c.opportunity) }} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm">{c.label}</div>
                  <Meter value={c.value ?? 0} max={Math.max(1, ...children.map((x) => x.value ?? 0))} />
                </div>
                <div className="text-right text-xs">
                  <div className="font-mono">{moneyShort(c.value)}</div>
                  {c.opportunity != null && <Badge>{c.opportunity.toFixed(0)}</Badge>}
                </div>
              </button>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
