/**
 * 测试组分析报告视图：负责逐测试指标表、报告操作区和 CSV 导出内容。
 * 本模块只生成展示标记，不持有结果页与分析页之间的导航状态。
 * CSV 只导出本次勾选并计算的指标列，数值单元格不含单位；页面表格仍附加单位以便阅读。
 */
import type { TestGroupPerformanceSummary } from "./analysis_contracts";

type TestGroupCasePerformance = TestGroupPerformanceSummary["cases"][number];

function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function finiteText(
  value: number | null,
  digits: number,
  suffix = "",
): string {
  return value === null || !Number.isFinite(value)
    ? "—"
    : `${value.toFixed(digits)}${suffix}`;
}

function percentText(value: number | null, fromRatio = false): string {
  const normalized = value === null ? null : value * (fromRatio ? 100 : 1);
  return finiteText(normalized, 2, "%");
}

function durationText(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return value >= 1000
    ? `${(value / 1000).toFixed(2)} s`
    : `${value.toFixed(1)} ms`;
}

/** 将有限数值格式化为不含单位的 CSV 单元格，供电子表格按数字处理。 */
function csvNumber(value: number | null, digits: number): string {
  return value === null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

/** 将比率或百分比格式化为不含百分号的数值单元格。 */
function csvPercent(value: number | null, fromRatio = false): string {
  const normalized = value === null ? null : value * (fromRatio ? 100 : 1);
  return csvNumber(normalized, 2);
}

function caseLabel(item: TestGroupCasePerformance, index: number): string {
  return item.name || `t${index + 1}`;
}

function csvEscape(value: string): string {
  return /[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

const DEFAULT_SELECTED_METRIC_IDS = [
  "validation", "makespan", "baseline_improvement", "cpu_time", "throughput",
  "departure_interval_cv", "process_chamber_dwell", "robot_wafer_dwell",
  "system_residence", "system_residence_cv", "resource_utilization", "bottleneck_candidates",
];

/** 解析本次分析实际勾选并计算的指标，缺省时与报告表默认列一致。 */
function selectedMetricIds(summary: TestGroupPerformanceSummary): Set<string> {
  return new Set(summary.selectedMetricIds ?? DEFAULT_SELECTED_METRIC_IDS);
}

function bottleneckText(item: TestGroupCasePerformance): string {
  return `${item.bottleneckResource || "—"}${item.bottleneckCandidateCount > 1 ? ` +${item.bottleneckCandidateCount - 1} 个候选` : ""}`;
}

function validationText(item: TestGroupCasePerformance): string {
  if (item.analysisStatus && item.analysisStatus !== "completed") {
    return item.error || item.analysisStatus || "—";
  }
  return item.validationPassed ? "通过" : (item.validation || item.status || "—");
}

function makespanReferenceText(
  item: TestGroupCasePerformance,
  summary: TestGroupPerformanceSummary,
): string {
  if (summary.referenceCaseId && item.id === summary.referenceCaseId) return "参考";
  if (item.referenceDeltas?.makespan?.percent === undefined) return "—";
  if (!item.referenceComparable) return "仅观察";
  return csvNumber(item.referenceDeltas.makespan.percent, 2);
}

type CsvColumn = {
  metricId: string;
  header: string;
  value: (item: TestGroupCasePerformance, summary: TestGroupPerformanceSummary) => string;
};

const CSV_COLUMNS: CsvColumn[] = [
  { metricId: "makespan", header: "Makespan", value: (item) => csvNumber(item.makespan, 2) },
  { metricId: "makespan", header: "相对参考", value: makespanReferenceText },
  { metricId: "baseline_improvement", header: "Baseline", value: (item) => csvNumber(item.baselineMakespan, 2) },
  { metricId: "baseline_improvement", header: "改善", value: (item) => csvNumber(item.improvementPercent, 2) },
  { metricId: "bottleneck_candidates", header: "瓶颈", value: bottleneckText },
  { metricId: "resource_utilization", header: "利用率", value: (item) => csvPercent(item.bottleneckUtilization, true) },
  { metricId: "cpu_time", header: "CPU Time", value: (item) => csvNumber(item.cpuTimeMs, 1) },
  { metricId: "average_recompute_time", header: "平均重算时间", value: (item) => csvNumber(item.averageRecomputeTimeMs ?? null, 1) },
  { metricId: "throughput", header: "产能", value: (item) => csvNumber(item.throughputPerHour, 1) },
  { metricId: "departure_interval_cv", header: "出站 CV", value: (item) => csvNumber(item.departureIntervalCv, 2) },
  { metricId: "process_chamber_dwell", header: "加工腔驻留均值", value: (item) => csvNumber(item.processChamberDwellMeanSeconds, 2) },
  { metricId: "robot_wafer_dwell", header: "机器手驻留均值", value: (item) => csvNumber(item.robotWaferDwellMeanSeconds, 2) },
  { metricId: "system_residence", header: "系统停留均值", value: (item) => csvNumber(item.waferSystemResidenceMeanSeconds, 2) },
  { metricId: "system_residence_cv", header: "系统停留 CV", value: (item) => csvNumber(item.waferSystemResidenceCv, 2) },
  { metricId: "loadlock_wafers_per_cycle", header: "LoadLock 每周期晶圆", value: (item) => csvNumber(item.loadLockWafersPerCycle, 2) },
  { metricId: "loadlock_full_cycle_ratio", header: "LoadLock 满载周期率", value: (item) => csvPercent(item.loadLockFullCycleRatio, true) },
  { metricId: "loadlock_empty_cycle_ratio", header: "LoadLock 空载周期率", value: (item) => csvPercent(item.loadLockEmptyCycleRatio, true) },
  { metricId: "validation", header: "校验", value: validationText },
];

/** 将本次勾选并计算的指标序列化为 CSV 文本（含中文表头，Excel 可直接打开）。 */
export function testGroupSummaryCsv(summary: TestGroupPerformanceSummary): string {
  const selected = selectedMetricIds(summary);
  const columns = CSV_COLUMNS.filter((column) => selected.has(column.metricId));
  const headers = ["测试", ...columns.map((column) => column.header)];
  const rows = summary.cases.map((item, index) => [
    caseLabel(item, index),
    ...columns.map((column) => column.value(item, summary)),
  ].map(csvEscape));
  return [headers.map(csvEscape).join(","), ...rows.map(row => row.join(","))].join("\r\n");
}

function resultTable(summary: TestGroupPerformanceSummary, selected: Set<string>, compact = false): string {
  return summary.cases.map((item, index) => {
    const cells = [`<th scope="row">${escapeHtml(caseLabel(item, index))}</th>`];
    if (selected.has("makespan")) {
      cells.push(`<td>${finiteText(item.makespan, 2, " s")}</td>`);
      cells.push(`<td>${summary.referenceCaseId && item.id === summary.referenceCaseId
        ? '<span class="group-reference">参考</span>'
        : item.referenceDeltas?.makespan?.percent === undefined
          ? "—"
          : item.referenceComparable
            ? `${item.referenceDeltas.makespan.percent > 0 ? "+" : ""}${item.referenceDeltas.makespan.percent.toFixed(2)}%`
            : '<span title="运行配置不同，只并列展示数值">仅观察</span>'}</td>`);
    }
    if (selected.has("baseline_improvement")) {
      cells.push(`<td>${finiteText(item.baselineMakespan, 2, " s")}</td>`);
      cells.push(`<td class="${(item.improvementPercent ?? 0) < 0 ? "loss" : "gain"}">${item.improvementPercent === null ? "—" : `${item.improvementPercent > 0 ? "+" : ""}${item.improvementPercent.toFixed(2)}%`}</td>`);
    }
    if (selected.has("bottleneck_candidates")) cells.push(`<td>${escapeHtml(item.bottleneckResource || "—")}${item.bottleneckCandidateCount > 1 ? ` <small>+${item.bottleneckCandidateCount - 1} 个候选</small>` : ""}</td>`);
    if (selected.has("resource_utilization")) cells.push(`<td>${percentText(item.bottleneckUtilization, true)}</td>`);
    if (selected.has("cpu_time")) cells.push(`<td>${durationText(item.cpuTimeMs)}</td>`);
    if (selected.has("average_recompute_time")) cells.push(`<td>${durationText(item.averageRecomputeTimeMs ?? null)}</td>`);
    if (selected.has("throughput")) cells.push(`<td>${finiteText(item.throughputPerHour, 1, " 片/h")}</td>`);
    if (selected.has("departure_interval_cv")) cells.push(`<td>${finiteText(item.departureIntervalCv, 2)}</td>`);
    if (selected.has("process_chamber_dwell")) cells.push(`<td>${finiteText(item.processChamberDwellMeanSeconds, 2, " s")}</td>`);
    if (selected.has("robot_wafer_dwell")) cells.push(`<td>${finiteText(item.robotWaferDwellMeanSeconds, 2, " s")}</td>`);
    if (selected.has("system_residence")) cells.push(`<td>${finiteText(item.waferSystemResidenceMeanSeconds, 2, " s")}</td>`);
    if (selected.has("system_residence_cv")) cells.push(`<td>${finiteText(item.waferSystemResidenceCv, 2)}</td>`);
    if (selected.has("loadlock_wafers_per_cycle")) cells.push(`<td>${finiteText(item.loadLockWafersPerCycle, 2, " 片")}</td>`);
    if (selected.has("loadlock_full_cycle_ratio")) cells.push(`<td>${percentText(item.loadLockFullCycleRatio, true)}</td>`);
    if (selected.has("loadlock_empty_cycle_ratio")) cells.push(`<td>${percentText(item.loadLockEmptyCycleRatio, true)}</td>`);
    if (selected.has("validation")) cells.push(`<td>${item.analysisStatus && item.analysisStatus !== "completed"
      ? `<span class="group-fail">${escapeHtml(item.error || item.analysisStatus)}</span>`
      : item.validationPassed ? '<span class="group-pass">通过</span>' : `<span class="group-fail">${escapeHtml(item.validation || item.status)}</span>`}</td>`);
    const rowClasses = [
      summary.referenceCaseId && item.id === summary.referenceCaseId ? "is-reference" : "",
      item.analysisStatus && item.analysisStatus !== "completed" ? "is-incomplete" : "",
    ].filter(Boolean).join(" ");
    const compactValues = cells.slice(1)
      .map(cell => cell.replace(/^<td(?:\s[^>]*)?>|<\/td>$/g, ""))
      .join('<span aria-hidden="true"> · </span>');
    const rowCells = compact ? [cells[0], `<td><div class="group-analysis-compact-values">${compactValues}</div></td>`] : cells;
    return `<tr${rowClasses ? ` class="${rowClasses}"` : ""}>${rowCells.join("")}</tr>`;
  }).join("");
}

/** 绘制测试组的多维结果分析，不生成跨量纲综合分数。 */
export function renderTestGroupAnalysis(
  summary: TestGroupPerformanceSummary,
  groupName: string,
): string {
  const selected = selectedMetricIds(summary);
  const selectedLabels: Record<string, string> = {
    validation: "校验结果", makespan: "Makespan", baseline_improvement: "Baseline 改善",
    cpu_time: "CPU Time", average_recompute_time: "平均重算时间", throughput: "产能",
    departure_interval_cv: "出站间隔 CV", process_chamber_dwell: "加工腔驻留",
    robot_wafer_dwell: "机器手驻留", system_residence: "系统停留",
    system_residence_cv: "系统停留 CV", resource_utilization: "资源利用率",
    bottleneck_candidates: "瓶颈候选", loadlock_wafers_per_cycle: "LoadLock 每周期晶圆",
    loadlock_full_cycle_ratio: "LoadLock 满载周期率", loadlock_empty_cycle_ratio: "LoadLock 空载周期率",
  };
  const compactTable = selected.size <= 2;
  const tableHeaders = compactTable ? ["<th>测试</th>", "<th>指标结果</th>"] : ["<th>测试</th>"];
  if (!compactTable && selected.has("makespan")) tableHeaders.push("<th>Makespan</th>", "<th>相对参考</th>");
  if (!compactTable && selected.has("baseline_improvement")) tableHeaders.push("<th>Baseline</th>", "<th>改善</th>");
  if (!compactTable && selected.has("bottleneck_candidates")) tableHeaders.push("<th>瓶颈</th>");
  if (!compactTable && selected.has("resource_utilization")) tableHeaders.push("<th>利用率</th>");
  if (!compactTable && selected.has("cpu_time")) tableHeaders.push("<th>CPU Time</th>");
  if (!compactTable && selected.has("average_recompute_time")) tableHeaders.push("<th>平均重算时间</th>");
  if (!compactTable && selected.has("throughput")) tableHeaders.push("<th>产能</th>");
  if (selected.has("departure_interval_cv")) tableHeaders.push("<th>出站 CV</th>");
  if (selected.has("process_chamber_dwell")) tableHeaders.push("<th>加工腔驻留均值</th>");
  if (selected.has("robot_wafer_dwell")) tableHeaders.push("<th>机器手驻留均值</th>");
  if (selected.has("system_residence")) tableHeaders.push("<th>系统停留均值</th>");
  if (selected.has("system_residence_cv")) tableHeaders.push("<th>系统停留 CV</th>");
  if (selected.has("loadlock_wafers_per_cycle")) tableHeaders.push("<th>LoadLock 每周期晶圆</th>");
  if (selected.has("loadlock_full_cycle_ratio")) tableHeaders.push("<th>LoadLock 满载周期率</th>");
  if (selected.has("loadlock_empty_cycle_ratio")) tableHeaders.push("<th>LoadLock 空载周期率</th>");
  if (selected.has("validation")) tableHeaders.push("<th>校验</th>");
  return `
    <div class="group-analysis-head">
      <div class="group-analysis-selection">${[...selected].map(metric => `<span>${escapeHtml(selectedLabels[metric] || metric)}</span>`).join("")}</div>
      <div class="group-analysis-actions">
        <button class="btn small group-analysis-back" type="button" data-return-run-results><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m10 7-5 5 5 5M5 12h14"/></svg><span>返回运行结果</span></button>
        <button class="btn small" type="button" data-reconfigure-analysis>重新选择指标与测试</button>
        <button type="button" class="btn small group-analysis-export" data-group-export-csv>导出 CSV</button>
      </div>
    </div>
    ${summary.timedOut ? '<div class="group-analysis-warning">已达到时间预算，以下报告保留完成部分；可减少指标或提高时间预算后继续。</div>' : ""}
    <section class="group-analysis-table-wrap">
      <div class="group-analysis-table-scroll">
        <table class="group-analysis-table">
          <caption class="sr-only">${escapeHtml(groupName || "当前测试组")}逐测试指标对比</caption>
          <thead><tr>${tableHeaders.join("")}</tr></thead>
          <tbody>${resultTable(summary, selected, compactTable)}</tbody>
        </table>
      </div>
    </section>`;
}
