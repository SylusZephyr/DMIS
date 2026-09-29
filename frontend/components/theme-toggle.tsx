"use client";
import { Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { setTheme, useTheme } from "@/lib/theme";

/** Light / dark switch. Until the viewer picks one, the system preference applies.
 *  Mounted next to <LangSwitch /> in the nav footer and the mobile top bar (components/nav.tsx). */
export function ThemeToggle({ className }: { className?: string }) {
  const { t } = useI18n();
  const theme = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  const Icon = theme === "dark" ? Moon : Sun;
  return (
    <button type="button" onClick={() => setTheme(next)} aria-label={t(`theme.switchTo.${next}`)} title={t(`theme.switchTo.${next}`)}
      className={cn("inline-flex items-center justify-center rounded-md border border-line bg-panel p-1.5 text-ink-2 hover:text-ink", className)}>
      <Icon className="h-4 w-4" aria-hidden />
    </button>
  );
}
