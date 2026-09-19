/** 测试组分析 CSV 导出契约：表头带单位，数值单元格不含单位。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { testGroupSummaryCsv } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

function sampleCase(overrides = {}) {
  return {
    name: "产能样例",
    makespan: 40.5,
    baselineMakespan: 50,
    improvementPercent: 19,
    bottleneckResource: "PM1",
    bottleneckCandidateCount: 2,
    bottleneckUtilization: 0.8523,
    cpuTimeMs: 1500,
    averageRecomputeTimeMs: 12.5,
    throughputPerHour: 120.5,
    companyCapacityBaselineWph: 100,
    companyCapacityRatio: 1.205,
    departureIntervalCv: 0.12,
    processChamberDwellMeanSeconds: 1.5,
    robotWaferDwellMeanSeconds: 0.8,
    waferSystemResidenceMeanSeconds: 90.12,
    waferSystemResidenceCv: 0.05,
    validationPassed: true,
    ...overrides,
  };
}

function sampleSummary(overrides = {}) {
  const { caseOverrides, ...summaryOverrides } = overrides;
  return {
    cases: [sampleCase(caseOverrides)],
    ...summaryOverrides,
  };
}

test("导出 CSV 的表头带单位，指标单元格只写数字", () => {
  const [header, row] = testGroupSummaryCsv(sampleSummary()).split("\r\n");
  assert.match(header, /^测试,产能（片\/小时）,平均重算时间（ms）,产能基线（片\/小时）,产能比,瓶颈,Makespan（s）,校验结果,算法总耗时（ms）,利用率（%）,/);
  assert.equal(
    row,
    "产能样例,120.5,12.5,100.0,1.21,PM1 +1 个候选,40.50,通过,1500.0,85.23,0.12,1.50,0.80,90.12,0.05",
  );
  assert.doesNotMatch(row, / s| ms|%|片\/h|片\/小时|片\b/);
});

test("缺失的已选指标保持占位符，短 CPU 时间仍按毫秒导出数字", () => {
  const row = testGroupSummaryCsv(sampleSummary({
    selectedMetricIds: ["cpu_time", "throughput", "validation"],
    caseOverrides: {
      throughputPerHour: null,
      cpuTimeMs: 25,
      validationPassed: false,
      validation: "失败",
    },
  })).split("\r\n")[1];
  assert.equal(row, "产能样例,—,失败,25.0");
});

test("未勾选、未计算的指标不会出现在 CSV 列中", () => {
  const [header, row] = testGroupSummaryCsv(sampleSummary({
    selectedMetricIds: ["makespan", "cpu_time", "throughput", "departure_interval_cv", "validation"],
  })).split("\r\n");
  assert.equal(header, "测试,产能（片/小时）,Makespan（s）,校验结果,算法总耗时（ms）,出站 CV");
  assert.equal(row, "产能样例,120.5,40.50,通过,1500.0,0.12");
  assert.doesNotMatch(header, /Baseline|改善|瓶颈|利用率|驻留|系统停留|基线/);
});
