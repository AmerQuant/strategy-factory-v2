import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent,
  VisualMapComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useMemo } from "react";
import { useTheme } from "@/lib/theme";
import { cssVar } from "@/lib/utils";

echarts.use([LineChart, BarChart, ScatterChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, VisualMapComponent, MarkLineComponent, CanvasRenderer]);

export type Option = echarts.EChartsCoreOption;

/** ECharts with the app's tokens: axis / grid / tooltip colours follow light and dark mode. */
export function Chart({ option, height = 280 }: { option: (c: Palette) => Option; height?: number }) {
  const { theme } = useTheme();
  const opt = useMemo(() => {
    const c = palette();
    const o = option(c) as Record<string, unknown>;
    return {
      textStyle: { fontFamily: "IBM Plex Sans, sans-serif", color: c.muted },
      tooltip: { trigger: "axis", backgroundColor: c.surface, borderColor: c.line, textStyle: { color: c.ink } },
      grid: { left: 56, right: 20, top: 28, bottom: 36 },
      animationDuration: 500,
      ...o,
    };
  }, [option, theme]);
  return <ReactEChartsCore echarts={echarts} option={opt} notMerge style={{ height }} />;
}

export interface Palette { ink: string; muted: string; line: string; surface: string; accent: string; pos: string;
  neg: string; fam: Record<string, string> }

export function palette(): Palette {
  return {
    ink: cssVar("--color-ink"), muted: cssVar("--color-muted"), line: cssVar("--color-line"),
    surface: cssVar("--color-surface"), accent: cssVar("--color-accent"), pos: cssVar("--color-pos"),
    neg: cssVar("--color-neg"),
    fam: Object.fromEntries(["MR", "TF", "VOL", "XS", "CAL", "EV", "ENS"].map((f) => [f, cssVar(`--color-fam-${f.toLowerCase()}`)])),
  };
}

export const axis = (c: Palette, extra: Record<string, unknown> = {}) => ({
  axisLine: { lineStyle: { color: c.line } }, axisTick: { show: false }, axisLabel: { color: c.muted },
  splitLine: { lineStyle: { color: c.line, opacity: 0.6 } }, ...extra,
});
