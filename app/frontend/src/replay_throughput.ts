/**
 * 回放产能投影：复用服务端计算的逐片产能，不在浏览器重复定义指标规则。
 * 按完成时刻截取已知数据；二分定位、缓存解析和事件数量去重避免逐帧重绘。
 * 同一份完整数据支持播放、倒放和任意跳转，不改变最终结果分析口径。
 */
import type { ThroughputTimelinePoint } from "./analysis_contracts";

const chartSources = new WeakMap<HTMLElement, { points: ThroughputTimelinePoint[]; key: string }>();

/** 返回不晚于 time 的事件数；points 必须按 completedAt 升序，包含同刻全部事件，不修改输入。 */
export function completedThroughputCount(points: ThroughputTimelinePoint[], time: number): number {
  let left = 0;
  let right = points.length;
  while (left < right) {
    const middle = Math.floor((left + right) / 2);
    if (points[middle].completedAt <= time) left = middle + 1;
    else right = middle;
  }
  return left;
}

/**
 * 更新回放产能图及右侧产能；只有完成事件数或显示范围改变时才重绘。
 * root 为工作台文档，time 为仿真秒数，redraw 复用现有 SVG 范围渲染器。
 * 无返回值；修改产能图数据、摘要和 KPI DOM，不请求服务端或修改原始指标。
 */
export function updateReplayThroughput(
  root: Document,
  time: number,
  redraw: (chart: HTMLElement, range: string) => void,
): void {
  const panel = root.getElementById("visualPerformance");
  if (!panel) return;
  const range = panel.querySelector<HTMLSelectElement>("#throughputRangeSelect")?.value ?? "wafer:30";
  const mode = panel.querySelector<HTMLSelectElement>("#throughputMetricSelect")?.value ?? "rolling";
  const windowSize = panel.querySelector<HTMLSelectElement>("#throughputWindowSize")?.value ?? "5";
  const activeKey = mode === "rolling" ? `rolling-${windowSize}` : "cumulative";
  let currentValue: number | undefined;
  panel.querySelectorAll<HTMLElement>("[data-throughput-points]").forEach(chart => {
    let source = chartSources.get(chart);
    if (!source) {
      source = { points: JSON.parse(chart.dataset.throughputPoints!) as ThroughputTimelinePoint[], key: "" };
      chartSources.set(chart, source);
    }
    const count = completedThroughputCount(source.points, time);
    const latest = count ? source.points[count - 1] : undefined;
    if (chart.dataset.throughputChart === activeKey) currentValue = latest?.throughputPerHour;
    const canvas = chart.querySelector<HTMLElement>(".throughput-chart-canvas");
    const key = `${count}:${range}:${canvas?.clientWidth ?? 0}`;
    if (source.key === key) return;
    source.key = key;
    const points = source.points.slice(0, count);
    chart.dataset.throughputPoints = JSON.stringify(points);
    if (points.length) redraw(chart, range);
    else if (canvas) canvas.innerHTML = '<div class="analysis-empty-state">当前时刻样本不足</div>';
    const summary = panel.querySelector<HTMLElement>(`[data-throughput-summary="${chart.dataset.throughputChart}"]`);
    if (summary) {
      const average = points.length ? points.reduce((sum, point) => sum + point.throughputPerHour, 0) / points.length : 0;
      summary.innerHTML = latest
        ? `<span><small>截至当前</small><b>${latest.throughputPerHour.toFixed(1)}</b><em>片/h</em></span><span><small>平均</small><b>${average.toFixed(1)}</b><em>片/h</em></span>`
        : '<span><small>截至当前</small><b>—</b><em>样本不足</em></span>';
    }
  });
  const value = root.querySelector<HTMLElement>("#visualReplayKpis .is-primary .performance-kpi-value");
  if (value) {
    const content = `<strong>${currentValue === undefined ? "—" : currentValue.toFixed(1)}</strong>${currentValue === undefined ? "" : "<small>片/h</small>"}`;
    if (value.innerHTML !== content) value.innerHTML = content;
  }
}
