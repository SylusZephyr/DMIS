"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Search } from "lucide-react";
import { Badge, Button, Card, CardHeader, Empty, Input } from "@/components/ui/primitives";
import { ErrorNote, PageHeader } from "@/components/page";
import type { Schemas } from "@/lib/api-typed";
import { useApi } from "@/lib/hooks";
import { useI18n } from "@/lib/i18n";
import { useMarketName } from "@/lib/market-name";
import { hitHref } from "@/lib/search";

type Resp = Schemas["SearchResponse"];

const KINDS = ["product", "segment", "taxonomy", "brand", "supplier"] as const;

function View() {
  const mn = useMarketName();
  const { t } = useI18n();
  const router = useRouter();
  const q = useSearchParams().get("q") ?? "";
  const [text, setText] = useState(q);
  const d = useApi<Resp>(q ? `/search?q=${encodeURIComponent(q)}` : null, [q]);
  const total = d.data ? KINDS.reduce((a, k) => a + (d.data?.hits[k]?.length ?? 0), 0) : 0;
  return (
    <div className="space-y-5 p-4 md:p-6">
      <PageHeader title={t("search.title")} subtitle={t("search.subtitle")} />
      <form role="search" className="flex max-w-2xl gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) router.push(`/search?q=${encodeURIComponent(text.trim())}`); }}>
        <Input aria-label={t("search.label")} placeholder={t("search.placeholder")} value={text} onChange={(e) => setText(e.target.value)} className="flex-1" />
        <Button type="submit"><Search className="h-4 w-4" />{t("search.go")}</Button>
      </form>
      <ErrorNote error={d.error} />
      {d.data && !d.data.semantic && <p className="text-xs text-ink-3">{t("search.noSemantic")}</p>}
      {q && d.data && total === 0 && <Card><Empty title={t("search.none", { q })} /></Card>}
      <div className="grid gap-4 lg:grid-cols-2">
        {KINDS.filter((k) => (d.data?.hits[k]?.length ?? 0) > 0).map((k) => (
          <Card key={k}>
            <CardHeader title={t(`search.kind.${k}`)} subtitle={String(d.data?.hits[k]?.length ?? 0)} />
            <ul className="divide-y divide-line">
              {(d.data?.hits[k] ?? []).map((h) => (
                <li key={`${h.kind}|${h.market}|${h.id}`}>
                  <Link href={hitHref(h)} className="flex items-center gap-3 px-4 py-2 text-sm hover:bg-panel-2">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-ink" title={h.label}>{h.label}</span>
                      {h.detail && <span className="block truncate text-xs text-ink-3">{h.detail}</span>}
                    </span>
                    {h.market && <Badge color="var(--ink-3)">{mn(h.market)}</Badge>}
                    {h.score != null && <span className="w-12 text-right font-mono text-xs tabular-nums text-ink-3">{h.score.toFixed(2)}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
      {d.data && <p className="text-[11px] text-ink-3">{t("search.note")}</p>}
    </div>
  );
}

export default function SearchPage() {
  return <Suspense><View /></Suspense>;
}
