"use client";
import { MarketGrid } from "@/components/market-picker";
import { PageHeader } from "@/components/page";
import { useI18n } from "@/lib/i18n";

export default function GalaxyIndex() {
  const { t } = useI18n();
  return (
    <div>
      <PageHeader title={t("galaxy.title")} subtitle={t("galaxy.indexSubtitle")} />
      <MarketGrid base="/galaxy" cta={t("galaxy.enter")} />
    </div>
  );
}
