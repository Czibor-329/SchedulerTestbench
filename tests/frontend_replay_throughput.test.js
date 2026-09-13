/** 回放产能投影契约：包含完成边界、倒放及无新事件时不重绘。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { completedThroughputCount, updateReplayThroughput } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

const points = [10, 20, 20, 40].map((completedAt, index) => ({
  wafer: `W${index + 1}`, completedAt, completedWaferIndex: index + 1, throughputPerHour: 100 + index,
}));

test("完成边界包含同刻出片、空样本及任意跳转", () => {
  assert.equal(completedThroughputCount([], 100), 0);
  assert.equal(completedThroughputCount(points, 0), 0);
  assert.equal(completedThroughputCount(points, 19), 1);
  assert.equal(completedThroughputCount(points, 20), 3);
  assert.equal(completedThroughputCount(points, 100), 4);
  assert.equal(completedThroughputCount(points, 10), 1);
});

test("曲线和 KPI 不泄露未来事件，倒放可恢复，无新出片不重绘", () => {
  const canvas = { innerHTML: "" };
  const value = { innerHTML: "" };
  const summary = { innerHTML: "" };
  const chart = {
    dataset: { throughputPoints: JSON.stringify(points), throughputChart: "rolling-5" },
    querySelector: () => canvas,
  };
  const panel = {
    querySelector: selector => selector.includes("summary") ? summary : null,
    querySelectorAll: () => [chart],
  };
  const root = { getElementById: () => panel, querySelector: () => value };
  let redraws = 0;
  const redraw = item => { redraws++; canvas.innerHTML = item.dataset.throughputPoints; };
  updateReplayThroughput(root, 20, redraw);
  assert.equal(JSON.parse(chart.dataset.throughputPoints).length, 3);
  assert.match(value.innerHTML, /102.0/);
  updateReplayThroughput(root, 25, redraw);
  assert.equal(redraws, 1);
  updateReplayThroughput(root, 0, redraw);
  assert.equal(chart.dataset.throughputPoints, "[]");
  assert.match(value.innerHTML, /—/);
  assert.match(canvas.innerHTML, /样本不足/);
  updateReplayThroughput(root, 100, redraw);
  assert.equal(JSON.parse(chart.dataset.throughputPoints).length, 4);
  assert.match(value.innerHTML, /103.0/);
});

test("切换口径和范围更新对应 KPI，不改变原始数据或图表可见状态", () => {
  const mode = { value: "rolling" };
  const range = { value: "wafer:30" };
  const window = { value: "5" };
  const value = { innerHTML: "" };
  const charts = ["cumulative", "rolling-5", "rolling-2"].map((key, index) => ({
    hidden: key !== "rolling-5",
    dataset: { throughputChart: key, throughputPoints: JSON.stringify(points.map(point => ({ ...point, throughputPerHour: 200 + index }))) },
    querySelector: () => ({ innerHTML: "" }),
  }));
  const panel = {
    querySelector: selector => selector === "#throughputMetricSelect" ? mode
      : selector === "#throughputRangeSelect" ? range : selector === "#throughputWindowSize" ? window : null,
    querySelectorAll: () => charts,
  };
  const root = { getElementById: () => panel, querySelector: () => value };
  const ranges = [];
  const redraw = (_, selectedRange) => ranges.push(selectedRange);
  updateReplayThroughput(root, 20, redraw);
  assert.match(value.innerHTML, /201.0/);
  mode.value = "cumulative";
  updateReplayThroughput(root, 20, redraw);
  assert.match(value.innerHTML, /200.0/);
  assert.equal(ranges.length, 3);
  mode.value = "rolling";
  window.value = "2";
  range.value = "all";
  updateReplayThroughput(root, 20, redraw);
  assert.match(value.innerHTML, /202.0/);
  assert.deepEqual(ranges.slice(3), ["all", "all", "all"]);
  assert.deepEqual(charts.map(chart => chart.hidden), [true, false, true]);
  assert.equal(points.length, 4);
});
