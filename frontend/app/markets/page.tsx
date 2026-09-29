"use client";
import Link from "next/link";
import { Orbit } from "lucide-react";
import { MarketGrid } from "@/components/market-picker";
import { PageHeader } from "@/components/page";
import { useI18n } from "@/lib/i18n";

export default function MarketsIndex() {
  const { t } = useI18n();
  return (
    <div>
      <PageHeader title={t("marketsIdx.title")} subtitle={t("marketsIdx.subtitle")}
        right={<Link href="/universe" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-sm text-ink-2 hover:border-accent hover:text-accent">
          <Orbit className="h-4 w-4" />{t("nav.universe")}</Link>} />
      <MarketGrid base="/markets" cta={t("marketsIdx.cta")} />
    </div>
  );
}
