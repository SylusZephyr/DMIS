"use client";
import { useI18n } from "@/lib/i18n";

/** First focusable element on every page: jumps keyboard users past the navigation to <main id="main">. */
export function SkipLink() {
  const { t } = useI18n();
  return (
    <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[60] focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-sm focus:text-on-accent">
      {t("a11y.skip")}
    </a>
  );
}
