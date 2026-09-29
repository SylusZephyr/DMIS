"use client";
// Tree-shaken ECharts: only the chart types and components the app uses are registered (the full
// `echarts` bundle is ~1.1 MB raw). Loaded lazily by components/charts/echart.tsx.
// Adding a new series type or component to an option? Register it here, or ECharts logs
// "[ECharts] Component/Series X is used but not imported" and the chart renders without it.
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, MarkLineComponent, TitleComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsReactProps } from "echarts-for-react/lib/types";

echarts.use([BarChart, CustomChart, LineChart, ScatterChart, AriaComponent, GridComponent, LegendComponent, MarkLineComponent,
  TitleComponent, TooltipComponent, CanvasRenderer]);

export default function EChartsCore(props: EChartsReactProps) {
  return <ReactEChartsCore echarts={echarts} {...props} />;
}
