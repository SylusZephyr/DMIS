"use client";
import dynamic from "next/dynamic";
import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import { TOKEN_NAMES, themeOption, type Tokens } from "@/lib/chart-theme";
import { useI18n } from "@/lib/i18n";
import { cssVar, useTheme } from "@/lib/theme";

// Lazy: the ECharts core + registered charts download only on pages that render a chart.
const ReactECharts = dynamic(() => import("./echart-core"), { ssr: false });

// Shared look: recessive axes/grid, text in ink tokens (never series colours), tooltips on by default.
// Colours are written as var(--token) and resolved for the current theme (lib/chart-theme.ts).
export const baseOption: EChartsOption = {
  backgroundColor: "transparent",
  textStyle: { color: "var(--ink-2)", fontFamily: "ui-sans-serif, system-ui, sans-serif" },
  grid: { left: 56, right: 24, top: 28, bottom: 44, containLabel: false },
  tooltip: { backgroundColor: "var(--panel)", borderColor: "var(--line)", textStyle: { color: "var(--ink)" } },
};
export const axis = {
  axisLine: { lineStyle: { color: "var(--line)" } },
  axisTick: { show: false },
  splitLine: { lineStyle: { color: "var(--chart-split)" } },
  axisLabel: { color: "var(--ink-3)" },
  nameTextStyle: { color: "var(--ink-3)" },
};

/** `label` names the chart for assistive technology (defaults to the series names). */
export function EChart({ option, height = 320, onEvents, label }: {
  option: EChartsOption; height?: number; onEvents?: Record<string, (p: unknown) => void>; label?: string;
}) {
  const theme = useTheme();
  const { t } = useI18n();
  const themed = useMemo(() => {
    const tokens: Tokens = {};
    for (const n of TOKEN_NAMES) tokens[n] = cssVar(n);
    const series = ([] as { name?: unknown }[]).concat((option.series ?? []) as { name?: unknown }[]);
    const name = label ?? (series.map((s) => s.name).filter((n): n is string => typeof n === "string").join(", ") || "");
    return themeOption({ ...baseOption, ...option,
      aria: { enabled: true, label: { description: t("a11y.chartSummary", { title: name }) } } } as EChartsOption, tokens, theme);
  }, [option, theme, label, t]);
  return <ReactECharts option={themed} style={{ height }} notMerge onEvents={onEvents} />;
}
