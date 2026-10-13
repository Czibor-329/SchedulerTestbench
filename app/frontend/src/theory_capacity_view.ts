/** 理论产能页签：固定 PM 组合、Spark ApexCharts 曲线与简略代入公式。
 * 设备目录与请求由宿主注入；清洁滑块复用 Cycle 批次求解，并固定同批次无清洁参照。
 * 不读取路线或测试索引。保留旧图表纯函数供已有接口消费者测试。
 */

import type {
  TheoryCapacityCleanType,
  TheoryCapacityDerivation,
  TheoryCapacityDerivationResponse,
  TheoryCapacityFacts,
  TheoryCapacityRouteEntry,
  TheoryCapacityRouteGroup,
  TheoryCapacityRouteIndex,
  TheoryCapacityRouteIndexResponse,
  TheoryCapacityScanResponse,
  TheoryCapacityTestPoint,
} from "./analysis_contracts";
import { THEORY_CLEAN_TYPES, THEORY_LOCK_MODES, THEORY_ROUTE_KINDS } from "./analysis_contracts";

/** 扫描区间；与后端默认值保持一致。 */
export interface TheoryCapacityWindow {
  firstSeconds: number;
  lastSeconds: number;
  stepSeconds: number;
}

export const DEFAULT_SCAN_WINDOW: TheoryCapacityWindow = {
  firstSeconds: 0,
  lastSeconds: 300,
  stepSeconds: 10,
};

/** 筛选条件；空字符串表示该维度不筛选。 */
export interface TheoryCapacityFilters {
  kind: string;
  lock: string;
  clean: string;
}

export const EMPTY_FILTERS: TheoryCapacityFilters = { kind: "", lock: "", clean: "" };

/** 图表画布尺寸（viewBox 单位）；实际显示宽度自适应容器。 */
export const CHART_WIDTH = 1000;
export const CHART_HEIGHT = 420;
export const CHART_PADDING_LEFT = 78;
export const CHART_PADDING_TOP = 18;
export const CHART_PADDING_RIGHT = 24;
export const CHART_PADDING_BOTTOM = 46;

/** 曲线配色；与前端的品牌色系保持一致的可读顺序。 */
export const CHART_PALETTE = [
  "#0b3d2c", "#2f7d4f", "#7aa53f", "#c08a2e", "#b4553a",
  "#3f6f8f", "#6a5acd", "#8a6d3b",
] as const;

/** 图上的数据窗口（数据坐标，x 为加工秒数，y 为片/h）。 */
export interface ChartWindow {
  x0: number;
  x1: number;
  y0: number;
  y1: number;
}

/** 绘图区在 SVG 坐标系中的矩形。 */
export interface ChartPlotBox {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface ChartPoint {
  x: number;
  y: number;
}

/** 一条可绘制的等价路线曲线。 */
export interface ChartCurve {
  groupKey: string;
  label: string;
  routeNames: string[];
  color: string;
  visible: boolean;
  routeKind: string;
  lockMode: string;
  chamberCounts: number[];
  roundsPerCycle: number | null;
  unitsPerCycle: number | null;
  representative: string;
  matchedTests: number;
  processSeconds: number[];
  wafersPerHour: number[];
  tests: TheoryCapacityTestPoint[];
}

/** 图表渲染输入。 */
export interface ChartRenderModel {
  curves: ChartCurve[];
  window: ChartWindow;
  readout: { x: number; y: number; label: string } | null;
}

export interface TheoryCapacityViewOptions {
  root: HTMLElement;
  listDevices: () => Promise<Array<{ id: string; name: string }>>;
  requestPreset: (deviceId: string, values: Record<string, string>) => Promise<any>;
  /** 程序化修改下拉框后请宿主刷新紧凑下拉的显示；缺省时忽略。 */
  syncSelect?: (select: HTMLSelectElement) => void;
}

export interface TheoryCapacityView {
  initialize: () => void;
  destroy: () => void;
}

const LOCK_MODE_LABELS: Record<string, string> = {
  "shared-bidirectional": "共享双通道",
  "dedicated-entry-exit": "单进单出",
  "single-bidirectional": "单通道",
};

const ROUTE_KIND_LABELS: Record<string, string> = {
  "single-node": "单节点",
  parallel: "并行",
  serial: "串行",
  mixed: "混合",
  reentrant: "重入",
};

const CLEAN_TYPE_LABELS: Record<string, string> = {
  preclean: "Pre",
  postclean: "Post",
  wacclean: "Wac",
  dummy: "Dummy",
  dummywac: "Dummy Wac",
  none: "No Clean",
};

const SERVICE_CATEGORY_LABELS: Record<string, string> = {
  "vacuum-swap": "真空换片",
  "process-swap": "加工换片",
  pressure: "压力转换",
  transfer: "转位",
  atmosphere: "大气服务",
};

/** 把锁分工标识翻译为界面用语。 */
export function lockModeLabel(mode: string): string {
  return LOCK_MODE_LABELS[mode] || mode || "—";
}

/** 把路线形态标识翻译为界面用语。 */
export function routeKindLabel(kind: string): string {
  return ROUTE_KIND_LABELS[kind] || kind || "—";
}

/** 把清洁类型标识翻译为界面用语。 */
export function cleanTypeLabel(value: string): string {
  return CLEAN_TYPE_LABELS[value] || value || "—";
}

/** 把服务类别标识翻译为界面用语。 */
export function serviceCategoryLabel(category: string): string {
  return SERVICE_CATEGORY_LABELS[category] || category || "—";
}

/** 把限制类型翻译为界面用语。 */
export function limitingKindLabel(kind: string): string {
  if (kind === "resource") return "资源负荷";
  if (kind === "waiting-chain") return "闭合等待链";
  return "—";
}

/** 返回路线状态徽标文案。 */
export function routeStatusLabel(status: string): string {
  if (status === "supported") return "可建图";
  if (status === "unsupported") return "模板不适用";
  if (status === "error") return "计算失败";
  return "未知";
}

/** 返回状态徽标的样式修饰类。 */
export function routeStatusClass(status: string): string {
  if (status === "supported") return "ok";
  if (status === "error") return "error";
  return "blocked";
}

/** 按固定小数位格式化产能，缺失时返回占位符。 */
export function formatCapacity(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** 按固定小数位格式化秒数，缺失时返回占位符。 */
export function formatSeconds(value: number | null | undefined, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

/** 把一组清洁类型翻译为界面用语；空集合表示无清洁。 */
export function cleanTypesLabel(values: readonly string[]): string {
  if (!values.length) return cleanTypeLabel("none");
  return values.map(cleanTypeLabel).join(" + ");
}

/** 返回第 index 条曲线的配色。 */
export function curveColor(index: number): string {
  return CHART_PALETTE[((index % CHART_PALETTE.length) + CHART_PALETTE.length) % CHART_PALETTE.length];
}

/** 把筛选条件转换为接口参数；空值不下发。 */
export function filterQuery(filters: TheoryCapacityFilters): {
  kinds: string;
  locks: string;
  cleans: string;
} {
  return {
    kinds: filters.kind || "",
    locks: filters.lock || "",
    cleans: filters.clean || "",
  };
}

/**
 * 由路线分类索引生成筛选器选项。
 *
 * :param index: 服务端返回的分类计数。
 * :return: 三个下拉框的选项数组，首项为“全部”。
 */
export function routeIndexOptions(index: TheoryCapacityRouteIndex | null | undefined): {
  kinds: Array<{ value: string; label: string }>;
  locks: Array<{ value: string; label: string }>;
  cleans: Array<{ value: string; label: string }>;
} {
  const countOf = (rows: Array<{ value: string; count: number }> | undefined, value: string): number => (
    rows?.find(row => row.value === value)?.count ?? 0
  );
  return {
    kinds: THEORY_ROUTE_KINDS.map(value => ({
      value,
      label: `${routeKindLabel(value)}（${countOf(index?.route_kind, value)}）`,
    })),
    locks: THEORY_LOCK_MODES.map(value => ({
      value,
      label: `${lockModeLabel(value)}（${countOf(index?.lock_mode, value)}）`,
    })),
    cleans: THEORY_CLEAN_TYPES.map((value: TheoryCapacityCleanType) => ({
      value,
      label: cleanTypeLabel(value),
    })),
  };
}

/** 返回等价分组的标题：多条等价路线用形态名，单条路线直接用路线名。 */
export function groupTitle(group: TheoryCapacityRouteGroup): string {
  if (group.route_names.length <= 1) {
    return group.representative || group.route_names[0] || "";
  }
  const chambers = group.chamber_counts.length ? `${group.chamber_counts.join("/")} 腔` : "";
  return [routeKindLabel(group.route_kind), chambers].filter(Boolean).join(" · ");
}

/** 返回等价分组的路线名清单文本。 */
export function groupRouteNames(group: TheoryCapacityRouteGroup): string {
  return group.route_names.join(" / ");
}

/** 取出属于某条路线（或某个等价分组）的测试数据点。 */
export function pointsForRoute(
  points: readonly TheoryCapacityTestPoint[],
  routeName: string,
  groupKey = "",
): TheoryCapacityTestPoint[] {
  return points.filter(point => (
    groupKey ? point.group_key === groupKey : point.route_name === routeName
  ));
}

/**
 * 把扫描结果整理成图表曲线。
 *
 * 有等价分组时按分组出曲线（分组内路线的曲线逐点相同）；载荷缺少分组时按路线名回退，
 * 保证旧服务端也能出图。
 *
 * :param scan: 扫描响应。
 * :return: 曲线数组，分组内测试点挂在曲线上。
 */
export function buildChartCurves(scan: TheoryCapacityScanResponse | null): ChartCurve[] {
  if (!scan) return [];
  const routes = scan.routes ?? [];
  const points = scan.tests ?? [];
  const groups = scan.groups?.length
    ? scan.groups
    : routes
      .filter(route => route.status === "supported")
      .map(route => ({
        group_key: route.group_key || route.route_name,
        route_kind: route.facts?.route_kind ?? "",
        lock_mode: route.facts?.lock_mode ?? "",
        rounds_per_cycle: route.facts?.rounds_per_cycle ?? null,
        units_per_cycle: route.facts?.units_per_cycle ?? null,
        chamber_counts: route.facts?.chamber_counts ?? [],
        pressure_domains: route.facts?.pressure_domains ?? [],
        route_names: [route.route_name],
        representative: route.route_name,
        start_wafers_per_hour: null,
        end_wafers_per_hour: null,
        best_wafers_per_hour: null,
        matched_tests: 0,
      }));
  return groups.flatMap((group, index) => {
    const representative = routes.find(route => route.route_name === group.representative)
      ?? routes.find(route => group.route_names.includes(route.route_name));
    if (!representative || representative.status !== "supported") return [];
    const processSeconds = representative.points?.process_seconds ?? [];
    const wafersPerHour = representative.points?.physical_wafers_per_hour ?? [];
    if (!processSeconds.length || !wafersPerHour.length) return [];
    return [{
      groupKey: group.group_key,
      label: groupTitle(group),
      routeNames: group.route_names,
      color: curveColor(index),
      visible: true,
      routeKind: group.route_kind,
      lockMode: group.lock_mode,
      chamberCounts: group.chamber_counts,
      roundsPerCycle: group.rounds_per_cycle,
      unitsPerCycle: group.units_per_cycle,
      representative: representative.route_name,
      matchedTests: points.filter(point => point.group_key === group.group_key).length,
      processSeconds: [...processSeconds],
      wafersPerHour: [...wafersPerHour],
      tests: pointsForRoute(points, representative.route_name, group.group_key),
    }];
  });
}

/**
 * 返回曲线的显示窗口。
 *
 * 横轴取扫描区间本身：个别测试的加工时间可能远大于扫描区间，若把它并入横轴，主曲线会被
 * 压扁；这类数据点落在窗口外由裁剪隐藏，仍会出现在「匹配测试」表里。
 *
 * :param curves: 全部曲线。
 * :return: 横轴为扫描区间、纵轴为 0 到区间内峰值（含区间内测试点）留 8% 余量的窗口。
 */
export function chartWindowForCurves(curves: readonly ChartCurve[]): ChartWindow {
  const xs: number[] = [];
  let peak = 0;
  for (const curve of curves) {
    for (const value of curve.processSeconds) if (Number.isFinite(value)) xs.push(value);
    const count = Math.min(curve.processSeconds.length, curve.wafersPerHour.length);
    for (let index = 0; index < count; index += 1) {
      peak = Math.max(peak, Number(curve.wafersPerHour[index]) || 0);
    }
  }
  const x0 = xs.length ? Math.min(...xs) : 0;
  const x1 = xs.length ? Math.max(...xs) : 1;
  for (const curve of curves) {
    for (const test of curve.tests) {
      const x = Number(test.key_process_seconds);
      const y = Number(test.physical_wafers_per_hour);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (x >= x0 && x <= x1) peak = Math.max(peak, y);
    }
  }
  return {
    x0,
    x1: x1 > x0 ? x1 : x0 + 1,
    y0: 0,
    y1: peak > 0 ? peak * 1.08 : 1,
  };
}

/** 把数据坐标映射到 SVG 坐标。 */
export function dataToPixel(window: ChartWindow, plot: ChartPlotBox, point: ChartPoint): ChartPoint {
  const spanX = window.x1 - window.x0 || 1;
  const spanY = window.y1 - window.y0 || 1;
  return {
    x: plot.left + ((point.x - window.x0) / spanX) * (plot.right - plot.left),
    y: plot.bottom - ((point.y - window.y0) / spanY) * (plot.bottom - plot.top),
  };
}

/** 把 SVG 坐标映射回数据坐标。 */
export function pixelToData(window: ChartWindow, plot: ChartPlotBox, x: number, y: number): ChartPoint {
  const width = plot.right - plot.left || 1;
  const height = plot.bottom - plot.top || 1;
  return {
    x: window.x0 + ((x - plot.left) / width) * (window.x1 - window.x0),
    y: window.y0 + ((plot.bottom - y) / height) * (window.y1 - window.y0),
  };
}

/** 生成“好看”的刻度值（1/2/5×10ⁿ 步长），覆盖整个区间。 */
export function niceTicks(minimum: number, maximum: number, target = 6): number[] {
  if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || maximum <= minimum) {
    return [minimum];
  }
  const count = Math.max(2, Math.floor(target));
  const rawStep = (maximum - minimum) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const step = (normalized >= 5 ? 5 : normalized >= 2 ? 2 : 1) * magnitude;
  const ticks: number[] = [];
  const first = Math.ceil(minimum / step) * step;
  for (let value = first; value <= maximum + step * 1e-6; value += step) {
    ticks.push(Number(value.toFixed(10)));
  }
  return ticks;
}

/**
 * 在可见曲线上查找光标附近的采样点，用于显示读数。
 *
 * :param curves: 全部曲线；只考虑可见曲线。
 * :param plot: 绘图区矩形。
 * :param window: 当前窗口。
 * :param x: 光标的 SVG 横坐标。
 * :return: 最近的采样点与所属曲线；没有曲线时返回 null。
 */
export function nearestChartSample(
  curves: readonly ChartCurve[],
  plot: ChartPlotBox,
  window: ChartWindow,
  x: number,
): { curve: ChartCurve; processSeconds: number; wafersPerHour: number; distance: number } | null {
  let best: { curve: ChartCurve; processSeconds: number; wafersPerHour: number; distance: number } | null = null;
  for (const curve of curves) {
    if (!curve.visible) continue;
    const count = Math.min(curve.processSeconds.length, curve.wafersPerHour.length);
    for (let index = 0; index < count; index += 1) {
      const point = dataToPixel(window, plot, {
        x: curve.processSeconds[index],
        y: curve.wafersPerHour[index],
      });
      const distance = Math.abs(point.x - x);
      if (!best || distance < best.distance) {
        best = {
          curve,
          processSeconds: curve.processSeconds[index],
          wafersPerHour: curve.wafersPerHour[index],
          distance,
        };
      }
    }
  }
  return best;
}

/** 当前画布使用的绘图区矩形。 */
export function chartPlotBox(): ChartPlotBox {
  return {
    left: CHART_PADDING_LEFT,
    top: CHART_PADDING_TOP,
    right: CHART_WIDTH - CHART_PADDING_RIGHT,
    bottom: CHART_HEIGHT - CHART_PADDING_BOTTOM,
  };
}

/** 转义要写入 HTML 的文本，避免路线名或服务说明注入标记。 */
export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 生成裁剪到当前窗口的折线路径；两端各补一个窗口外采样点保证边界正确。 */
function curvePath(
  curve: ChartCurve,
  window: ChartWindow,
  plot: ChartPlotBox,
): string {
  const count = Math.min(curve.processSeconds.length, curve.wafersPerHour.length);
  const commands: string[] = [];
  for (let index = 0; index < count; index += 1) {
    const point = dataToPixel(window, plot, {
      x: curve.processSeconds[index],
      y: curve.wafersPerHour[index],
    });
    commands.push(`${index === 0 ? "M" : "L"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`);
  }
  return commands.join(" ");
}

/**
 * 渲染可交互扫描曲线。
 *
 * :param model: 曲线、窗口、读数与框选状态。
 * :return: 含坐标轴、网格、曲线、测试数据点与读数的 SVG 字符串。
 *     绘图区带 ``data-theory-plot`` 供控制器换算指针坐标。
 */
export function renderInteractionChart(model: ChartRenderModel): string {
  const plot = chartPlotBox();
  const window = model.window;
  const xTicks = niceTicks(window.x0, window.x1, 6);
  const yTicks = niceTicks(window.y0, window.y1, 5);
  const grid = [
    ...xTicks.map(value => {
      const point = dataToPixel(window, plot, { x: value, y: window.y0 });
      return `<line class="theory-chart-grid" x1="${point.x.toFixed(2)}" y1="${plot.top}" x2="${point.x.toFixed(2)}" y2="${plot.bottom}" />
      <text class="theory-chart-tick" x="${point.x.toFixed(2)}" y="${plot.bottom + 22}" text-anchor="middle">${escapeHtml(formatSeconds(value, value % 1 === 0 ? 0 : 1))}</text>`;
    }),
    ...yTicks.map(value => {
      const point = dataToPixel(window, plot, { x: window.x0, y: value });
      return `<line class="theory-chart-grid" x1="${plot.left}" y1="${point.y.toFixed(2)}" x2="${plot.right}" y2="${point.y.toFixed(2)}" />
      <text class="theory-chart-tick" x="${plot.left - 12}" y="${(point.y + 4).toFixed(2)}" text-anchor="end">${escapeHtml(formatCapacity(value, value % 1 === 0 ? 0 : 1))}</text>`;
    }),
  ].join("");
  const curves = model.curves
    .filter(curve => curve.visible)
    .map(curve => `<path class="theory-chart-line" d="${curvePath(curve, window, plot)}" style="--curve-color:${escapeHtml(curve.color)}" />`)
    .join("");
  const markers = model.curves
    .filter(curve => curve.visible)
    .flatMap(curve => curve.tests
      .filter(test => test.status === "supported"
        && Number.isFinite(Number(test.key_process_seconds))
        && Number.isFinite(Number(test.physical_wafers_per_hour)))
      .map(test => {
        const point = dataToPixel(window, plot, {
          x: Number(test.key_process_seconds),
          y: Number(test.physical_wafers_per_hour),
        });
        const label = `${test.name}：关键工序 ${formatSeconds(test.key_process_seconds, 1)} 秒，`
          + `理论产能 ${formatCapacity(test.physical_wafers_per_hour)} 片/h`;
        return `<circle class="theory-chart-marker" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="5"
          style="--curve-color:${escapeHtml(curve.color)}" data-theory-point="${escapeHtml(test.id)}">
          <title>${escapeHtml(label)}</title>
        </circle>`;
      }))
    .join("");
  const readout = model.readout
    ? `<line class="theory-chart-crosshair" x1="${dataToPixel(window, plot, { x: model.readout.x, y: 0 }).x.toFixed(2)}" y1="${plot.top}" x2="${dataToPixel(window, plot, { x: model.readout.x, y: 0 }).x.toFixed(2)}" y2="${plot.bottom}" />
      <line class="theory-chart-crosshair" x1="${plot.left}" y1="${dataToPixel(window, plot, { x: 0, y: model.readout.y }).y.toFixed(2)}" x2="${plot.right}" y2="${dataToPixel(window, plot, { x: 0, y: model.readout.y }).y.toFixed(2)}" />
      <circle class="theory-chart-cursor" cx="${dataToPixel(window, plot, { x: model.readout.x, y: model.readout.y }).x.toFixed(2)}" cy="${dataToPixel(window, plot, { x: model.readout.x, y: model.readout.y }).y.toFixed(2)}" r="4" />
      ${(() => {
        const point = dataToPixel(window, plot, { x: model.readout.x, y: model.readout.y });
        const boxWidth = Math.max(150, (model.readout.label.length * 7.2) + 18);
        const left = Math.min(Math.max(plot.left, point.x + 12), plot.right - boxWidth);
        const top = Math.min(Math.max(plot.top + 4, point.y - 34), plot.bottom - 26);
        return `<g class="theory-chart-readout-box">
          <rect x="${left.toFixed(2)}" y="${top.toFixed(2)}" width="${boxWidth.toFixed(2)}" height="24" rx="5" />
          <text x="${(left + 9).toFixed(2)}" y="${(top + 16).toFixed(2)}">${escapeHtml(model.readout.label)}</text>
        </g>`;
      })()}`
    : "";
  return `<svg viewBox="0 0 ${CHART_WIDTH} ${CHART_HEIGHT}" role="img" aria-label="加工时间与理论产能扫描曲线" preserveAspectRatio="xMidYMid meet">
    <rect class="theory-chart-plot" data-theory-plot="1" x="${plot.left}" y="${plot.top}"
      width="${plot.right - plot.left}" height="${plot.bottom - plot.top}" />
    ${grid}
    <line class="theory-chart-axis" x1="${plot.left}" y1="${plot.bottom}" x2="${plot.right}" y2="${plot.bottom}" />
    <line class="theory-chart-axis" x1="${plot.left}" y1="${plot.top}" x2="${plot.left}" y2="${plot.bottom}" />
    <text class="theory-chart-axis-title" x="${plot.right}" y="${plot.bottom + 40}" text-anchor="end">加工时间（秒）</text>
    <text class="theory-chart-axis-title" x="${14}" y="${plot.top + 4}" text-anchor="start">理论产能（片/h）</text>
    <g clip-path="url(#theoryChartClip)">${curves}${markers}</g>
    <defs><clipPath id="theoryChartClip"><rect x="${plot.left}" y="${plot.top}"
      width="${plot.right - plot.left}" height="${plot.bottom - plot.top}" /></clipPath></defs>
    ${readout}
  </svg>`;
}

/** 将图内横向位置换算为加工秒数；限制在扫描区间内，保留 0.1 秒精度。 */
export function processSecondsAtPosition(clientX: number, left: number, width: number, first: number, last: number): number {
  if (width <= 0 || last <= first) return first;
  const fraction = Math.max(0, Math.min(1, (clientX - left) / width));
  return Math.round((first + fraction * (last - first)) * 10) / 10;
}

/** 论文周期推导：展示每周期产出、资源负荷、关键闭合等待链及数值代入。 */
export function renderTeachingDerivation(entry: TheoryCapacityDerivation, name: string): string {
  if (entry.model_kind === "cycle-batch") return renderBatchDerivation(entry);
  if (entry.model_kind === "cycle-periodic") return renderPeriodicDerivation(entry);
  const facts = entry.facts;
  const units = facts?.units_per_cycle ?? 0;
  const period = entry.period_seconds ?? 0;
  const rounds = facts?.rounds_per_cycle || 1;
  const chain = entry.critical_chain ?? [];
  const delay = chain.reduce((sum, edge) => sum + edge.minimum_delay_seconds, 0);
  const shift = chain.reduce((sum, edge) => sum + edge.cycle_shift, 0);
  const floor = entry.resource_period_lower_bound_seconds ?? 0;
  const loads = Object.entries(entry.resource_work_seconds ?? {});
  const services = entry.services ?? [];
  const atmosphereStages: Record<string, {label: string; seconds: number}> = {
    "atm-receive": {label: "收成品并回程", seconds: 0}, "atm-home": {label: "回盒", seconds: 0},
    "atm-raw": {label: "取生片并送锁", seconds: 0}, "atm-feed": {label: "补片并转位", seconds: 0}
  };
  // ATM → ATM 的主线边已包含取放和转位，不把锁恢复边的压力等待重复计入。
  for (const edge of entry.edges ?? []) {
    if (!edge.source.startsWith("ATM_") || !edge.target.startsWith("ATM_")) continue;
    const stage = atmosphereStages[edge.label.split(":")[0]];
    if (stage) stage.seconds += edge.minimum_delay_seconds;
  }
  const atmosphereTotal = Object.values(atmosphereStages).reduce((sum, stage) => sum + stage.seconds, 0);
  const atmosphereDetails = atmosphereTotal > 0 ? `<p>ATM：${Object.values(atmosphereStages).map(stage => `${stage.label} ${formatSeconds(stage.seconds)}`).join(" + ")} = ${formatSeconds(atmosphereTotal)} 秒</p>` : "";
  const processServices = services.filter(service => service.key.startsWith("process-cycle:"));
  const chamberDetails = processServices.map(service => {
    const load = entry.resource_work_seconds?.[`加工腔 ${service.station}`];
    if (load == null || service.seconds <= 0) return "";
    return `<p>${escapeHtml(service.station)}：${formatCapacity(load / service.seconds)} 次 × (${service.parts.map(part => `${escapeHtml(part.label)} ${formatSeconds(part.seconds)}`).join(" + ")}) = ${formatSeconds(load)} 秒</p>`;
  }).join("");
  const serviceDetails = `<details class="theory-timing-details"><summary>查看取放、开关门、转位与切压的时间代入</summary>${services.map(service => `<p>${escapeHtml(service.detail || service.key)}：${service.parts.map(part => `${escapeHtml(part.label)} ${formatSeconds(part.seconds)}`).join(" + ")} = ${formatSeconds(service.seconds)} 秒</p>`).join("")}</details>`;
  return `<p class="theory-calculation-inputs">${escapeHtml(name)} · 加工时间 ${formatSeconds(entry.process_seconds?.[0])} 秒 · 无清洁稳态模型</p>
    <div class="theory-formula-step"><span class="theory-step-number">1</span><div><h3>确定重复周期</h3><div class="theory-formula-equation">一次大气手交替产出 ${facts?.physical_members_per_unit ?? "—"} 片；${rounds} 次交替组成相位周期</div><p>锁与加工腔的轮转相位同时复位，才构成完整周期。</p></div></div>
    <div class="theory-formula-step"><span class="theory-step-number">2</span><div><h3>计算周期下界</h3>${atmosphereDetails}${chamberDetails}<details class="theory-timing-details"><summary>各资源每相位周期的工作量</summary><table class="theory-timing-table"><tbody>${loads.map(([resource, seconds]) => `<tr><th>${escapeHtml(resource)}</th><td>${formatSeconds(seconds)} 秒</td></tr>`).join("")}</tbody></table></details><div class="theory-formula-equation">T资源 = max(Wᵣ) = ${formatSeconds(floor)} 秒<br>T等待 = Σd ÷ Σλ = ${formatSeconds(delay)} ÷ ${shift} = ${shift > 0 ? formatSeconds(delay / shift) : "—"} 秒</div><details class="theory-timing-details"><summary>关键等待链：${escapeHtml(entry.limiting_label || "查看约束")}</summary>${chain.map(edge => `<p>${escapeHtml(edge.detail || edge.label)}：${formatSeconds(edge.minimum_delay_seconds)} 秒，跨 ${edge.cycle_shift} 周期</p>`).join("")}<p>每条边满足 xⱼ − xᵢ ≥ dᵢⱼ − λᵢⱼT；沿闭环相加，得到 T ≥ Σd / Σλ。</p></details>${serviceDetails}</div></div>
    <div class="theory-formula-step"><span class="theory-step-number">3</span><div><h3>周期时间与产能</h3><strong>T相位 = max(${formatSeconds(floor)}, ${shift > 0 ? formatSeconds(delay / shift) : "—"}) = ${formatSeconds(period)} 秒</strong><div class="theory-formula-equation">一次交替 T = ${formatSeconds(period)} ÷ ${rounds} = <b>${formatSeconds(period / rounds)} 秒</b><br>平均出片间隔 = T相位 ÷ q = ${formatSeconds(period)} ÷ ${units} = ${formatSeconds(entry.seconds_per_unit)} 秒 / 片<br>每小时产能 = 3600 × ${units} ÷ ${formatSeconds(period)} = ${formatCapacity(entry.physical_wafers_per_hour)} 片 / 小时</div><p>1 小时有 3600 秒；周期由事件图验证可行相位，不含启动与收尾。</p></div></div>`;
}

/** 解释连续中段周期：动作分项与重复边界一致，跨 Job 相位按每 Job 换算。 */
export function renderPeriodicDerivation(entry: TheoryCapacityDerivation): string {
  const timing = entry.period_timing!;
  const period = entry.period_seconds ?? 0;
  const job = timing.basis === "job";
  const basis = job ? "一个 Job · 13 片产品" : `WAC 重复周期 · ${timing.output_wafers} 片产品`;
  return `<p class="theory-calculation-inputs">${basis}</p>
    <div class="theory-formula-step"><span class="theory-step-number">1</span><div><h3>确定周期边界</h3><p>${job ? "连续生产中，取相邻 Job 的同一阶段边界。" : "WAC 计数、腔室轮转及 LoadLock 相位共同复位。"}</p>
    ${!job && timing.phase_factors ? `<div class="theory-formula-equation">轮数 r = lcm(${timing.phase_factors.join(", ")}) = ${timing.candidate_rounds}<br>周期产出 q = ${timing.candidate_rounds} × ${timing.physical_members_per_unit} = ${timing.output_wafers} 片</div>` : ""}
    <div class="theory-formula-equation">T = (t结束 − t开始) ÷ ${timing.divisor}<br>= (${formatSeconds(timing.end_seconds)} − ${formatSeconds(timing.start_seconds)}) ÷ ${timing.divisor} = <b>${formatSeconds(period)} 秒</b></div>
    <details class="theory-timing-details"><summary>周期边界与重复验证</summary><p>边界：${escapeHtml(timing.reference)}。连续 ${timing.verified_repeats} 个窗口的动作与时刻重复。</p><p>${job && timing.phase_count > 1 ? `${timing.phase_count} 个 Job 后完整相位复位，${formatSeconds(timing.full_period_seconds)} 秒 ÷ ${timing.phase_count}，得到每 Job 的等效周期。` : "统计窗口位于无限连续供片模型中段，不含空机启动和最终排空。"}</p><p>入口按无限供片缓冲展开，PM、LoadLock 与 Dummy 库存沿用设备配置。这是 Cycle 重复排程值；无清洁曲线是论文事件图参照。</p></details></div></div>
    <div class="theory-formula-step"><span class="theory-step-number">2</span><div><h3>周期时间如何组成</h3><p>沿 ${escapeHtml(timing.resource)} 时间线展开，重叠动作只计一次。</p><table class="theory-timing-table"><tbody>${timing.segments.map(row => `<tr><th>${escapeHtml(row.label)}</th><td>${row.terms?.map(term => `${term.count} × ${formatSeconds(term.seconds)}`).join(" + ") || "剩余时间"}</td><td>${formatSeconds(row.seconds)} 秒</td></tr>`).join("")}</tbody></table>
    <div class="theory-formula-equation">T = ${timing.segments.map(row => formatSeconds(row.seconds)).join(" + ")} = <b>${formatSeconds(period)} 秒</b></div></div></div>
    <div class="theory-formula-step"><span class="theory-step-number">3</span><div><h3>换算产能</h3><strong>Q = 3600 × ${timing.output_wafers} ÷ ${formatSeconds(period)} = ${formatCapacity(entry.physical_wafers_per_hour)} 片 / 小时</strong></div></div>`;
}

/** 按真实回盒事件解释旧批次接口；页面使用稳态或连续中段周期入口。 */
export function renderBatchDerivation(entry: TheoryCapacityDerivation): string {
  const timing = entry.batch_timing;
  const count = entry.batch_wafers ?? 0;
  const duration = entry.period_seconds ?? 0;
  const cleanNames: Record<string, string> = {preclean: "PreClean", postclean: "PostClean", wacclean: "WAC", dummy: "Dummy", dummywac: "DummyWAC"};
  const cleaning = (entry.cleaning_types ?? []).map(type => cleanNames[type] ?? type).join(" + ") || "No Clean";
  const setting = `<p class="theory-calculation-inputs">${escapeHtml(cleaning)} · 加工 ${formatSeconds(entry.process_seconds?.[0])} 秒 · ${entry.job_count} 个 Job × ${entry.wafers_per_job} 片 = ${count} 片产品</p>`;
  const breakdown = timing ? `<table class="theory-timing-table"><tbody>
    <tr><th>首片回盒 t₁</th><td>${formatSeconds(timing.first_return_seconds)} 秒</td><td>从空机启动到首片完成回盒</td></tr>
    <tr><th>后续出片 ΣΔt</th><td>${formatSeconds(timing.remaining_returns_seconds)} 秒</td><td>末片回盒 − 首片回盒</td></tr>
    <tr><th>收尾 t尾</th><td>${formatSeconds(timing.tail_seconds)} 秒</td><td>全部动作结束 − 末片回盒</td></tr>
    </tbody></table><div class="theory-formula-equation">T = (t₁ + ΣΔt + t尾)${timing.repeat_count > 1 ? ` × ${timing.repeat_count}` : ""}<br>
    = (${formatSeconds(timing.first_return_seconds)} + ${formatSeconds(timing.remaining_returns_seconds)} + ${formatSeconds(timing.tail_seconds)})${timing.repeat_count > 1 ? ` × ${timing.repeat_count}` : ""} = <b>${formatSeconds(duration)} 秒</b></div>
    <details class="theory-timing-details"><summary>出片间隔明细（${timing.return_intervals.reduce((sum, row) => sum + row.count, 0)} 个间隔）</summary>
    <p>Δtᵢ = 第 i 片回盒时刻 − 上一片回盒时刻；同时回盒记 0 秒。</p>
    <div>${timing.return_intervals.map(row => `${row.count} × ${formatSeconds(row.seconds)}`).join(" + ")} ≈ ${formatSeconds(timing.remaining_returns_seconds)} 秒</div></details>`
    : `<div class="theory-formula-equation">T = 全部动作最晚结束时刻 − 0 = ${formatSeconds(duration)} 秒</div><p>重启服务后可查看首片、后续出片及收尾明细。</p>`;
  const cleanRows = (entry.cleaning_types?.length && entry.cleaning_summary?.length) ? `<details class="theory-timing-details"><summary>${escapeHtml(cleaning)} 如何计入周期</summary>${entry.cleaning_summary.map(row => `<p>${escapeHtml(row.station)} · ${escapeHtml(row.task)}：${row.count} 次，累计 ${formatSeconds(row.seconds)} 秒</p>`).join("")}<p>清洁按实际排程进入首片、出片间隔或收尾；并行时间不重复相加。</p></details>` : "";
  return `${setting}<div class="theory-formula-step"><span class="theory-step-number">1</span><div><h3>计算批次周期 T</h3>${breakdown}${cleanRows}<p>由 Cycle 搬运、加工和压力切换排程得到；此处是完整批次耗时。</p></div></div>
    <div class="theory-formula-step"><span class="theory-step-number">2</span><div><h3>换算产能</h3><div class="theory-formula-equation">Q = 3600 × 产品数 ÷ T</div><strong>3600 × ${count} ÷ ${formatSeconds(duration)} = ${formatCapacity(entry.physical_wafers_per_hour)} 片 / 小时</strong></div></div>`;
}

/** 清洁参数的页面字段；时间限制为 0～200 秒，离散数量提供 1、2、3 三档。 */
const CLEANING_PARAMETER_INPUTS = ["theoryPreSeconds", "theoryPostSeconds", "theoryWacSeconds",
  "theoryWacInterval", "theoryDummySeconds", "theoryDummyVisits", "theoryDummyInventory"];
const MAX_CLEANING_CONTROL_SECONDS = 200;
const MAX_CLEANING_CONTROL_COUNT = 3;
const CAPACITY_AXIS_HEADROOM = 1.1;

/** 固定组合页签控制器：注入设备目录和只读接口，销毁时解除事件及图表资源。 */
export function createTheoryCapacityView(options: TheoryCapacityViewOptions): TheoryCapacityView {
  const root = options.root;
  const select = (id: string) => root.querySelector<HTMLSelectElement>(`#${id}`)!;
  const device = select("theoryCapacityDevice");
  const kind = select("theoryCapacityKind");
  const lock = select("theoryCapacityLock");
  const button = root.querySelector<HTMLButtonElement>("#theoryCapacityApply")!;
  const notice = root.querySelector<HTMLElement>("#theoryCapacityNotice")!;
  const results = root.querySelector<HTMLElement>("#theoryCapacityResults")!;
  const abort = new AbortController();
  let generation = 0;
  let chart: any = null;
  let activated = false;
  let destroyed = false;
  let dragPointer: number | null = null;
  let previewSamples: Array<{x: number; y: number}> = [];
  let baselineSamples: Array<{x: number; y: number}> = [];
  let baselineKey = "";
  let capacityAxisMaximum = 0;
  /** 拖动仅预览采样曲线上的位置；插值不用于公式，松手才请求精确求解。 */
  function simulate(seconds: number): void {
    generation++;
    root.setAttribute?.("aria-busy", "false");
    root.setAttribute?.("data-result-stale", "true");
    button.disabled = false; button.textContent = "重新计算";
    root.querySelector("#theoryCapacityResultState")!.textContent = "待更新";
    root.querySelector<HTMLInputElement>("#theoryCapacitySlider")!.value = String(seconds);
    root.querySelector("#theoryProcessReadout")!.textContent = `${formatSeconds(seconds)} 秒`;
    if (!chart || !previewSamples.length) return;
    const upperIndex = previewSamples.findIndex(point => point.x >= seconds);
    const right = previewSamples[upperIndex < 0 ? previewSamples.length - 1 : upperIndex];
    const left = previewSamples[Math.max(0, upperIndex - 1)];
    const fraction = right.x > left.x ? Math.max(0, Math.min(1, (seconds - left.x) / (right.x - left.x))) : 0;
    const rate = upperIndex < 0 ? right.y : left.y + fraction * (right.y - left.y);
    void chart.updateOptions({annotations: {points: [{x: seconds, y: rate,
      marker: {size: 8, fillColor: "#B4F105", strokeColor: "#072F1F"}}]}}, false, false);
  }
  /** 根据图表实际绘图区宽度读取拖动位置，适配窗口缩放和侧栏折叠。 */
  function dragTo(event: PointerEvent): void {
    const grid = root.querySelector("#theoryCapacityChart .apexcharts-grid");
    if (!grid) return;
    const bounds = grid.getBoundingClientRect();
    const inputs = values();
    simulate(processSecondsAtPosition(event.clientX, bounds.left, bounds.width, Number(inputs.first), Number(inputs.last)));
  }

  /** 读取输入，保留零值与非法值供服务端明确校验。 */
  function values(): Record<string, string> {
    const input = (id: string) => root.querySelector<HTMLInputElement>(`#${id}`)!.value;
    const clean = select("theoryCleaningType").value || "";
    const cleaning: Record<string, string> = {};
    if (clean === "preclean") cleaning.pre_seconds = input("theoryPreSeconds");
    if (clean === "postclean") cleaning.post_seconds = input("theoryPostSeconds");
    if (clean === "wacclean" || clean === "dummywac") cleaning.wac_seconds = input("theoryWacSeconds");
    if (clean === "wacclean") cleaning.wac_interval = input("theoryWacInterval");
    if (clean === "dummy" || clean === "dummywac") {
      cleaning.dummy_seconds = input("theoryDummySeconds");
      cleaning.dummy_visits = input("theoryDummyVisits");
      cleaning.dummy_inventory = input("theoryDummyInventory");
    }
    // 无清洁按论文交替周期，WAC 按计数周期，其余清洁按连续中段 Job 周期。
    return {kind: kind.value, lock: lock.value, first: input("theoryCapacityFirstSeconds"),
      last: input("theoryCapacityLastSeconds"), step: input("theoryCapacityStepSeconds"),
      process: input("theoryCapacitySlider"), mode: clean ? "periodic" : "steady", clean, ...cleaning};
  }
  /** 仅显示所选清洁的参数；隐藏字段不提交，避免残留值影响另一类型。 */
  function syncCleaningControls(): void {
    const clean = select("theoryCleaningType").value || "";
    root.querySelector<HTMLElement>(".theory-cleaning-experiment")!.hidden = !clean;
    root.querySelector<HTMLElement>("#theoryPreSettings")!.hidden = clean !== "preclean";
    root.querySelector<HTMLElement>("#theoryPostSettings")!.hidden = clean !== "postclean";
    root.querySelector<HTMLElement>("#theoryDummySettings")!.hidden = !["dummy", "dummywac"].includes(clean);
    root.querySelector<HTMLElement>("#theoryWacSettings")!.hidden = !["wacclean", "dummywac"].includes(clean);
    root.querySelector<HTMLElement>("#theoryWacIntervalLabel")!.hidden = clean !== "wacclean";
    for (const id of CLEANING_PARAMETER_INPUTS) {
      const input = root.querySelector<HTMLInputElement>(`#${id}`)!;
      const slider = root.querySelector<HTMLInputElement>(`#${id}Slider`)!;
      input.max = slider.max = String(id.endsWith("Seconds") ? MAX_CLEANING_CONTROL_SECONDS : MAX_CLEANING_CONTROL_COUNT);
      slider.value = input.value;
    }
  }
  /** 编辑时保留曲线供对比，但明确提示当前读数尚未完成新参数求解。 */
  function markChanged(): void {
    generation++;
    root.setAttribute?.("data-result-stale", "true");
    root.setAttribute?.("aria-busy", "false");
    button.disabled = false; button.textContent = "重新计算";
    root.querySelector("#theoryCapacityResultState")!.textContent = "待更新";
  }
  /** 显示最新请求的错误，不保留上一组输入的图表。 */
  function showError(error: unknown): void {
    notice.textContent = error instanceof Error ? error.message : String(error);
    notice.hidden = false;
  }
  /** 请求曲线与单点计算；preserveBaseline 为真时保留同一设备及扫描区间的参照与纵轴。
   * 请求序号防止旧响应覆盖新选择；失败时清空图表并保留清洁调节控件。
   */
  async function calculate(preserveBaseline = false): Promise<void> {
    const processInput = root.querySelector<HTMLInputElement>("#theoryCapacitySlider");
    if (processInput?.checkValidity && !processInput.checkValidity()) {
      showError(new Error("加工时间必须为非负数"));
      return;
    }
    const request = ++generation;
    root.setAttribute?.("aria-busy", "true");
    root.querySelector("#theoryCapacityResultState")!.textContent = "计算中…";
    notice.hidden = true;
    button.disabled = true; button.textContent = "计算中…";
    try {
      if (!device.value) throw new Error("请选择设备");
      for (const id of CLEANING_PARAMETER_INPUTS) {
        const input = root.querySelector<HTMLInputElement>(`#${id}`)!;
        if (input.closest?.("[hidden]")) continue;
        if (input.checkValidity && !input.checkValidity()) throw new Error("清洁参数超出允许范围，请检查数值框");
      }
      const requestValues = values();
      const payload = await options.requestPreset(device.value, requestValues);
      if (request !== generation || destroyed) return;
      const entry = payload.derivation as TheoryCapacityDerivation;
      // 旧服务会忽略清洁参数并返回稳态协议，不能将其展示为本次清洁计算。
      if (requestValues.clean && (entry?.model_kind !== "cycle-periodic" || !payload.baseline_points)) {
        throw new Error("当前服务未返回清洁计算结果，请重启调度平台服务后重试");
      }
      const q = entry.facts?.units_per_cycle ?? 0;
      const period = entry.period_seconds ?? 0;
      const seconds = entry.process_seconds?.[0] ?? 0;
      const chain = entry.critical_chain ?? [];
      const delay = chain.reduce((sum, edge) => sum + edge.minimum_delay_seconds, 0);
      const shift = chain.reduce((sum, edge) => sum + edge.cycle_shift, 0);
      const floor = entry.resource_period_lower_bound_seconds ?? 0;
      const batch = entry.model_kind === "cycle-batch";
      root.setAttribute?.("data-result-stale", "false");
      root.setAttribute?.("data-result-unavailable", "false");
      root.querySelector<HTMLElement>("#theoryCapacityChartSection")!.hidden = false;
      root.querySelector("#theoryCapacityResultState")!.textContent = "";
      root.querySelector("#theoryCapacityComparison")!.textContent = `加工 ${formatSeconds(seconds)} 秒 · ${entry.period_timing ? (entry.period_timing.basis === "wac" ? `WAC 周期 ${entry.period_timing.output_wafers} 片` : "连续生产 · 13 片 / Job") : "大气手交替周期"}`;
      root.querySelector("#theoryCapacityScope")!.textContent = entry.scope_note ?? "论文固定周期模型的理论值。";
      root.querySelector("#theoryCapacityDerivation")!.innerHTML = renderTeachingDerivation(entry, payload.name);
      root.querySelector("#theoryCapacityCurrentRate")!.textContent = formatCapacity(entry.physical_wafers_per_hour);
      root.querySelector("#theoryCapacityResources")!.innerHTML = Object.entries(entry.resource_work_seconds ?? {}).map(([name, seconds]) =>
        `<div><small>${escapeHtml(name)}</small><strong>${formatSeconds(seconds)} <span>秒 / ${batch ? "批次" : "周期"}</span></strong><progress max="${period}" value="${Math.min(seconds, period)}" aria-label="${escapeHtml(name)}占总耗时的比例"></progress></div>`).join("");
      root.querySelector("#theoryCapacityFloor")!.textContent = `最繁忙资源 ${formatSeconds(floor)} 秒 / ${batch ? "批次" : "周期"}`;

      results.hidden = false;
      const ApexCharts = (window as any).ApexCharts;
      if (!ApexCharts) throw new Error("模板图表组件未加载，请刷新页面");
      // Spark 首页 area sparkline 的线宽、填充、品牌色，结合 revenue-chart 坐标网格。
      previewSamples = payload.points.process_seconds.map((x: number, index: number) => ({x, y: payload.points.physical_wafers_per_hour[index]}));
      // 清洁参数与观察时间不改变参照曲线；设备、模板或扫描区间改变时重新建立基线。
      const nextBaselineKey = JSON.stringify([device.value, requestValues.kind, requestValues.lock,
        requestValues.first, requestValues.last, requestValues.step]);
      if (!preserveBaseline || nextBaselineKey !== baselineKey) {
        baselineKey = nextBaselineKey;
        baselineSamples = payload.baseline_points?.process_seconds.map((x: number, index: number) =>
          ({x, y: payload.baseline_points.physical_wafers_per_hour[index]})) ?? previewSamples.map(point => ({...point}));
        capacityAxisMaximum = Math.max(1, ...baselineSamples.map(point => point.y), ...previewSamples.map(point => point.y)) * CAPACITY_AXIS_HEADROOM;
      }
      const chartOptions = {
        series: [{ name: requestValues.clean ? "带清洁" : "不带清洁", data: previewSamples },
          ...(requestValues.clean && baselineSamples.length ? [{name: "不带清洁", data: baselineSamples}] : [])],
        chart: {type: "area", height: "100%", parentHeightOffset: 0, toolbar: {show: false}, zoom: {enabled: false},
          animations: {enabled: false}, fontFamily: "Plus Jakarta Sans, sans-serif"},
        colors: ["#072F1F", "#b4553a"], stroke: {curve: "straight", width: 2, dashArray: [0, 6]},
        fill: {opacity: 0.1, type: "solid"}, dataLabels: {enabled: false},
        grid: {borderColor: "#E9EFEF", strokeDashArray: 4, xaxis: {lines: {show: false}}},
        xaxis: {type: "numeric", min: Number(requestValues.first), max: Number(requestValues.last), tickAmount: 4, title: {text: "加工时间（秒）"},
          axisBorder: {show: false}, axisTicks: {show: false},
          labels: {formatter: (value: number) => Number(value).toFixed(0), style: {colors: "#6C7E75", fontSize: "14px"}}},
        yaxis: {min: 0, max: capacityAxisMaximum, labels: {formatter: (value: number) => value.toFixed(0), style: {colors: "#6C7E75", fontSize: "14px"}}},
        tooltip: {theme: "dark", x: {formatter: (value: number) => `${value} 秒`},
          y: {formatter: (value: number) => `${value.toFixed(2)} 片/h`}},
        annotations: {points: [{x: seconds, y: entry.physical_wafers_per_hour,
          marker: {size: 8, fillColor: "#B4F105", strokeColor: "#072F1F"}},
          ...(requestValues.clean ? [{x: seconds, y: entry.baseline_wafers_per_hour,
            marker: {size: 5, fillColor: "#ffffff", strokeColor: "#b4553a"}}] : [])]},
      };
      if (chart) await chart.updateOptions(chartOptions, false, false);
      else {
        chart = new ApexCharts(root.querySelector("#theoryCapacityChart"), chartOptions);
        await chart.render();
      }
      const slider = root.querySelector<HTMLInputElement>("#theoryCapacitySlider")!;
      slider.min = requestValues.first; slider.max = requestValues.last;
      slider.value = String(seconds);
      root.querySelector("#theoryProcessReadout")!.textContent = `${formatSeconds(seconds)} 秒`;

    } catch (error) {
      if (request === generation && !destroyed) {
        // 保留调节控件，清空失败前的读数与曲线，便于修正库存等参数后重试。
        results.hidden = !device.value; chart?.destroy(); chart = null;
        root.setAttribute?.("data-result-unavailable", "true");
        root.querySelector<HTMLElement>("#theoryCapacityChartSection")!.hidden = true;
        root.querySelector("#theoryCapacityCurrentRate")!.textContent = "—";
        root.querySelector("#theoryCapacityComparison")!.textContent = "请调整参数后重试";
        root.querySelector("#theoryCapacityResultState")!.textContent = "计算失败";
        showError(error);
      }
    } finally {
      if (request === generation && !destroyed) { root.setAttribute?.("aria-busy", "false"); button.disabled = false; button.textContent = "重新计算"; }
    }
  }
  /** 页签首次打开时加载设备后直接显示默认组合。 */
  async function activate(): Promise<void> {
    if (activated || !root.classList.contains("active")) return;
    activated = true;
    try {
      const devices = await options.listDevices();
      if (destroyed) return;
      device.replaceChildren(...devices.map(item => new Option(item.name, item.id)));
      options.syncSelect?.(device);
      await calculate();
    } catch (error) { showError(error); }
  }
  const observer = new MutationObserver(() => { void activate(); });
  return {
    initialize: () => {
      syncCleaningControls();
      for (const id of CLEANING_PARAMETER_INPUTS) {
        const input = root.querySelector<HTMLInputElement>(`#${id}`)!;
        const slider = root.querySelector<HTMLInputElement>(`#${id}Slider`)!;
        let inputEdited = false;
        slider.addEventListener("input", () => {
          input.value = slider.value;
          markChanged();
        }, {signal: abort.signal});
        slider.addEventListener("change", () => { void calculate(true); }, {signal: abort.signal});
        input.addEventListener("input", () => {
          inputEdited = true;
          syncCleaningControls();
          markChanged();
        }, {signal: abort.signal});
        input.addEventListener("change", () => { inputEdited = false; void calculate(true); }, {signal: abort.signal});
        // 精确输入在离焦或 Enter 时提交；change 已提交的值不重复触发扫描。
        input.addEventListener("blur", () => {
          if (inputEdited) { inputEdited = false; void calculate(true); }
        }, {signal: abort.signal});
        input.addEventListener("keydown", event => {
          if (event.key === "Enter" && inputEdited) {
            event.preventDefault(); inputEdited = false; void calculate(true);
          }
        }, {signal: abort.signal});
      }
      button.addEventListener("click", () => { void calculate(); }, {signal: abort.signal});
      const chartHost = root.querySelector<HTMLElement>("#theoryCapacityChart")!;
      chartHost.addEventListener("pointerdown", event => {
        if (!(event.target as Element).closest(".apexcharts-point-annotation-marker")) return;
        event.preventDefault(); dragPointer = event.pointerId;
        chartHost.setPointerCapture(event.pointerId); dragTo(event);
      }, {signal: abort.signal});
      chartHost.addEventListener("pointermove", event => {
        if (event.pointerId === dragPointer) dragTo(event);
      }, {signal: abort.signal});
      chartHost.addEventListener("pointerup", event => {
        if (event.pointerId !== dragPointer) return;
        dragTo(event); dragPointer = null; chartHost.releasePointerCapture(event.pointerId);
        void calculate(true);
      }, {signal: abort.signal});
      chartHost.addEventListener("pointercancel", () => { dragPointer = null; }, {signal: abort.signal});
      root.querySelector<HTMLInputElement>("#theoryCapacitySlider")!.addEventListener("input", event => {
        simulate(Number((event.target as HTMLInputElement).value));
      }, {signal: abort.signal});
      root.querySelector<HTMLInputElement>("#theoryCapacitySlider")!.addEventListener("change", () => { void calculate(true); }, {signal: abort.signal});
      for (const element of Array.from(root.querySelectorAll("select, .theory-capacity-filters input"))) {
        element.addEventListener("change", () => {
          if (element instanceof HTMLSelectElement) options.syncSelect?.(element);
          syncCleaningControls();
          markChanged();
        }, {signal: abort.signal});
      }
      observer.observe(root, {attributes: true, attributeFilter: ["class"]});
      void activate();
    },
    destroy: () => { destroyed = true; generation++; abort.abort(); observer.disconnect(); chart?.destroy(); },
  };
}
