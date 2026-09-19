/**
 * 测试组分析报告视图：负责逐测试指标表、报告操作区和 CSV 导出内容。
 * 本模块只生成展示标记，不持有结果页与分析页之间的导航状态。
 * CSV 只导出本次勾选并计算的指标列。表头带单位，数值单元格不含单位；页面表格仍附加单位以便阅读。
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
  "throughput", "average_recompute_time", "company_capacity_baseline",
  "company_capacity_ratio", "bottleneck_candidates", "makespan", "validation",
  "cpu_time", "resource_utilization", "departure_interval_cv",
  "process_chamber_dwell", "robot_wafer_dwell", "system_residence",
  "system_residence_cv",
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

type CsvColumn = {
  metricId: string;
  header: string;
  value: (item: TestGroupCasePerformance, summary: TestGroupPerformanceSummary) => string;
};

const CSV_COLUMNS: CsvColumn[] = [
  { metricId: "throughput", header: "产能（片/小时）", value: (item) => csvNumber(item.throughputPerHour, 1) },
  { metricId: "average_recompute_time", header: "平均重算时间（ms）", value: (item) => csvNumber(item.averageRecomputeTimeMs ?? null, 1) },
  { metricId: "company_capacity_baseline", header: "产能基线（片/小时）", value: (item) => csvNumber(item.companyCapacityBaselineWph ?? null, 1) },
  { metricId: "company_capacity_ratio", header: "产能比", value: (item) => csvNumber(item.companyCapacityRatio ?? null, 2) },
  { metricId: "bottleneck_candidates", header: "瓶颈", value: bottleneckText },
  { metricId: "makespan", header: "Makespan（s）", value: (item) => csvNumber(item.makespan, 2) },
  { metricId: "validation", header: "校验结果", value: validationText },
  { metricId: "cpu_time", header: "算法总耗时（ms）", value: (item) => csvNumber(item.cpuTimeMs, 1) },
  { metricId: "resource_utilization", header: "利用率（%）", value: (item) => csvPercent(item.bottleneckUtilization, true) },
  { metricId: "departure_interval_cv", header: "出站 CV", value: (item) => csvNumber(item.departureIntervalCv, 2) },
  { metricId: "process_chamber_dwell", header: "加工腔驻留均值（s）", value: (item) => csvNumber(item.processChamberDwellMeanSeconds, 2) },
  { metricId: "robot_wafer_dwell", header: "机器手驻留均值（s）", value: (item) => csvNumber(item.robotWaferDwellMeanSeconds, 2) },
  { metricId: "system_residence", header: "系统停留均值（s）", value: (item) => csvNumber(item.waferSystemResidenceMeanSeconds, 2) },
  { metricId: "system_residence_cv", header: "系统停留 CV", value: (item) => csvNumber(item.waferSystemResidenceCv, 2) },
  { metricId: "loadlock_wafers_per_cycle", header: "LoadLock 每周期晶圆（片）", value: (item) => csvNumber(item.loadLockWafersPerCycle, 2) },
  { metricId: "loadlock_full_cycle_ratio", header: "LoadLock 满载周期率（%）", value: (item) => csvPercent(item.loadLockFullCycleRatio, true) },
  { metricId: "loadlock_empty_cycle_ratio", header: "LoadLock 空载周期率（%）", value: (item) => csvPercent(item.loadLockEmptyCycleRatio, true) },
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
    if (selected.has("throughput")) cells.push(`<td>${finiteText(item.throughputPerHour, 1, " 片/h")}</td>`);
    if (selected.has("average_recompute_time")) cells.push(`<td>${durationText(item.averageRecomputeTimeMs ?? null)}</td>`);
    if (selected.has("company_capacity_baseline")) cells.push(`<td>${finiteText(item.companyCapacityBaselineWph ?? null, 1, " 片/h")}</td>`);
    if (selected.has("company_capacity_ratio")) {
      const ratio = item.companyCapacityRatio ?? null;
      cells.push(`<td class="${(ratio ?? 1) < 1 ? "loss" : "gain"}">${finiteText(ratio, 2)}</td>`);
    }
    if (selected.has("bottleneck_candidates")) cells.push(`<td>${escapeHtml(item.bottleneckResource || "—")}${item.bottleneckCandidateCount > 1 ? ` <small>+${item.bottleneckCandidateCount - 1} 个候选</small>` : ""}</td>`);
    if (selected.has("makespan")) cells.push(`<td>${finiteText(item.makespan, 2, " s")}</td>`);
    if (selected.has("validation")) cells.push(`<td>${item.analysisStatus && item.analysisStatus !== "completed"
      ? `<span class="group-fail">${escapeHtml(item.error || item.analysisStatus)}</span>`
      : item.validationPassed ? '<span class="group-pass">通过</span>' : `<span class="group-fail">${escapeHtml(item.validation || item.status)}</span>`}</td>`);
    if (selected.has("cpu_time")) cells.push(`<td>${durationText(item.cpuTimeMs)}</td>`);
    if (selected.has("resource_utilization")) cells.push(`<td>${percentText(item.bottleneckUtilization, true)}</td>`);
    if (selected.has("departure_interval_cv")) cells.push(`<td>${finiteText(item.departureIntervalCv, 2)}</td>`);
    if (selected.has("process_chamber_dwell")) cells.push(`<td>${finiteText(item.processChamberDwellMeanSeconds, 2, " s")}</td>`);
    if (selected.has("robot_wafer_dwell")) cells.push(`<td>${finiteText(item.robotWaferDwellMeanSeconds, 2, " s")}</td>`);
    if (selected.has("system_residence")) cells.push(`<td>${finiteText(item.waferSystemResidenceMeanSeconds, 2, " s")}</td>`);
    if (selected.has("system_residence_cv")) cells.push(`<td>${finiteText(item.waferSystemResidenceCv, 2)}</td>`);
    if (selected.has("loadlock_wafers_per_cycle")) cells.push(`<td>${finiteText(item.loadLockWafersPerCycle, 2, " 片")}</td>`);
    if (selected.has("loadlock_full_cycle_ratio")) cells.push(`<td>${percentText(item.loadLockFullCycleRatio, true)}</td>`);
    if (selected.has("loadlock_empty_cycle_ratio")) cells.push(`<td>${percentText(item.loadLockEmptyCycleRatio, true)}</td>`);
    const rowClasses = [
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
    throughput: "产能", average_recompute_time: "平均重算时间",
    company_capacity_baseline: "产能基线", company_capacity_ratio: "产能比",
    bottleneck_candidates: "瓶颈", makespan: "Makespan", validation: "校验结果",
    cpu_time: "算法总耗时", resource_utilization: "资源利用率",
    departure_interval_cv: "出站间隔 CV", process_chamber_dwell: "加工腔驻留",
    robot_wafer_dwell: "机器手驻留", system_residence: "系统停留",
    system_residence_cv: "系统停留 CV", loadlock_wafers_per_cycle: "LoadLock 每周期晶圆",
    loadlock_full_cycle_ratio: "LoadLock 满载周期率", loadlock_empty_cycle_ratio: "LoadLock 空载周期率",
  };
  const compactTable = selected.size <= 2;
  const tableHeaders = compactTable ? ["<th>测试</th>", "<th>指标结果</th>"] : ["<th>测试</th>"];
  if (!compactTable && selected.has("throughput")) tableHeaders.push("<th>产能</th>");
  if (!compactTable && selected.has("average_recompute_time")) tableHeaders.push("<th>平均重算时间</th>");
  if (!compactTable && selected.has("company_capacity_baseline")) tableHeaders.push("<th>产能基线</th>");
  if (!compactTable && selected.has("company_capacity_ratio")) tableHeaders.push("<th>产能比</th>");
  if (!compactTable && selected.has("bottleneck_candidates")) tableHeaders.push("<th>瓶颈</th>");
  if (!compactTable && selected.has("makespan")) tableHeaders.push("<th>Makespan</th>");
  if (!compactTable && selected.has("validation")) tableHeaders.push("<th>校验结果</th>");
  if (!compactTable && selected.has("cpu_time")) tableHeaders.push("<th>算法总耗时</th>");
  if (!compactTable && selected.has("resource_utilization")) tableHeaders.push("<th>利用率</th>");
  if (selected.has("departure_interval_cv")) tableHeaders.push("<th>出站 CV</th>");
  if (selected.has("process_chamber_dwell")) tableHeaders.push("<th>加工腔驻留均值</th>");
  if (selected.has("robot_wafer_dwell")) tableHeaders.push("<th>机器手驻留均值</th>");
  if (selected.has("system_residence")) tableHeaders.push("<th>系统停留均值</th>");
  if (selected.has("system_residence_cv")) tableHeaders.push("<th>系统停留 CV</th>");
  if (selected.has("loadlock_wafers_per_cycle")) tableHeaders.push("<th>LoadLock 每周期晶圆</th>");
  if (selected.has("loadlock_full_cycle_ratio")) tableHeaders.push("<th>LoadLock 满载周期率</th>");
  if (selected.has("loadlock_empty_cycle_ratio")) tableHeaders.push("<th>LoadLock 空载周期率</th>");
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
          <caption class="sr-only">${escapeHtml(groupName || "当前测试组")}逐测试指标</caption>
          <thead><tr>${tableHeaders.join("")}</tr></thead>
          <tbody>${resultTable(summary, selected, compactTable)}</tbody>
        </table>
      </div>
    </section>`;
}
