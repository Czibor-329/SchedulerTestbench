/** 结果表格分页边界、运行选择隔离、轮询保页与可访问操作契约。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  createResultTable,
  createResultTableState,
  projectResultTablePage,
  renderResultTable,
  resultTableMetricsCsv,
} = require(process.env.CT_RESULT_TABLE_TEST_BUILD);

/** 建立确定的轻量结果，只包含表格展示需要的字段。 */
function results(count) {
  return Array.from({ length: count }, (_, index) => ({
    testId: `test-${index + 1}`, testName: `测试 ${index + 1}`, status: "not-run",
    productionText: "—", recomputeText: "—", makespanText: "—", logUrl: "", logFileName: "日志.json",
    resultUrl: "", ganttUrl: "", errorText: "",
  }));
}

/** 建立包含稳定设备/组身份的显示上下文。 */
function context(selectedTestIds = null, scope = "device-1/group-1") {
  return { scope, selectedTestIds, expandedTestId: "" };
}

test("空表、整页与末页的范围正确，越界页码被夹到有效范围", () => {
  const empty = projectResultTablePage([], createResultTableState(), context());
  assert.deepEqual([empty.total, empty.pageCount, empty.start, empty.end], [0, 1, 0, 0]);
  const first = projectResultTablePage(results(21), createResultTableState(), context());
  assert.deepEqual([first.rows.length, first.start, first.end, first.pageCount], [10, 1, 10, 3]);
  const last = projectResultTablePage(results(21), { ...first.state, page: 99 }, context());
  assert.deepEqual([last.state.page, last.rows.length, last.start, last.end], [3, 1, 21, 21]);
  const negative = projectResultTablePage(results(20), { ...first.state, page: -4 }, context());
  assert.equal(negative.state.page, 1);
  const invalid = projectResultTablePage(results(20), { ...first.state, page: NaN, pageSize: 0 }, context());
  assert.deepEqual([invalid.state.page, invalid.state.pageSize], [1, 10]);
});

test("翻页只投影结果，跨页选择和原始结果顺序不被修改", () => {
  const rows = Object.freeze(results(31).map(Object.freeze));
  const selected = new Set(rows.filter((_, index) => index !== 2).map(row => row.testId));
  const originalSelection = [...selected];
  const first = projectResultTablePage(rows, createResultTableState(), context(selected));
  const second = projectResultTablePage(rows, { ...first.state, page: 2 }, context(selected));
  assert.equal(second.total, 30);
  assert.equal(second.rows[0].testId, "test-12");
  assert.deepEqual([...selected], originalSelection);
  assert.equal(selected.has("test-31"), true, "未显示的测试仍属于运行选择");
  assert.equal(rows[0].testId, "test-1");
});

test("轮询与选择集合重建保持当前页，切设备/组/筛选回到第一页", () => {
  const rows = results(35);
  const selected = new Set(rows.map(row => row.testId));
  const first = projectResultTablePage(rows, createResultTableState(), context(selected));
  const state = { ...first.state, page: 3 };
  const changedStatus = rows.map(row => ({ ...row, status: "succeeded" }));
  const poll = projectResultTablePage(changedStatus, state, context(new Set([...selected].reverse())));
  assert.equal(poll.state.page, 3);
  assert.equal(poll.rows[0].testId, "test-21");
  assert.equal(poll.rows[0].status, "succeeded");
  for (const scope of ["device-2/group-1", "device-1/group-2"]) {
    assert.equal(projectResultTablePage(rows, state, context(selected, scope)).state.page, 1);
  }
  const filtered = projectResultTablePage(rows, state, context(new Set(["test-21"])));
  assert.deepEqual([filtered.state.page, filtered.total, filtered.rows[0].testId], [1, 1, "test-21"]);
  const shrunk = projectResultTablePage(rows.slice(0, 12), state, context(selected));
  assert.equal(shrunk.state.page, 2, "同一选择的数据减少时夹到末页");
});

test("表格保留表头和原生行语义，提供键盘详情、运行和结果入口", () => {
  const row = { ...results(1)[0], testName: '测试 <A> "一"', status: "failed", productionText: "18.5/20.0", recomputeText: "3.20", makespanText: "12.34",
    logUrl: "/api/logs/result", resultUrl: "/api/results/result", ganttUrl: "/movelist_gantt_viewer.html?src=result", errorText: "失败" };
  const page = projectResultTablePage([row], createResultTableState(), context());
  const markup = renderResultTable(page, row.testId);
  assert.match(markup, /<table\b/);
  for (const header of ["测试名称", "状态", "产能", "平均重算", "Makespan", "操作"]) assert.ok(markup.includes(header));
  assert.doesNotMatch(markup, /产能<span>|平均重算<span>|Makespan<span>/, "表头不得拼写单位");
  assert.match(markup, /title="单位：片\/h">产能<\/th>/);
  assert.match(markup, /title="单位：s">平均重算<\/th>/);
  assert.match(markup, /title="单位：s">Makespan<\/th>/);
  assert.match(markup, /<td class="result-table-number">18\.5\/20\.0<\/td>/);
  assert.match(markup, /<tr[^>]+data-batch-test-card="test-1">/);
  assert.doesNotMatch(markup, /<tr[^>]+(?:role="button"|tabindex=)/);
  assert.match(markup, /data-result-table-details="test-1" aria-expanded="true" aria-controls="batchTestDetails"/);
  assert.match(markup, /data-result-table-run="test-1"/);
  assert.match(markup, /data-playback-result="\/api\/results\/result"/);
  assert.match(markup, /data-batch-error="test-1"/);
  assert.match(markup, /download="日志.json"/);
  assert.ok(markup.includes("18.5/20.0"));
  assert.ok(markup.includes("3.20"));
  assert.ok(markup.includes("12.34"));
  assert.ok(markup.includes("测试 &lt;A&gt; &quot;一&quot;"));
  assert.doesNotMatch(markup, /测试 <A>/);
  const injected = renderResultTable({ ...page, rows: [{ ...row, logUrl: "javascript:alert(1)", ganttUrl: "//remote.test/" }] });
  assert.doesNotMatch(injected, /javascript:|remote\.test/);
});

/** 取出带指定数据属性的行操作按钮，用于断言按钮只承载图标与可访问名称。 */
function actionButton(markup, attribute) {
  const match = new RegExp(`<button[^>]*\\b${attribute}="test-1"[^>]*>([\\s\\S]*?)</button>`).exec(markup);
  assert.ok(match, `结果行必须包含 ${attribute} 按钮`);
  return { markup: match[0], inner: match[1] };
}

test("行操作按钮不带文字，只显示图标并提供可访问名称", () => {
  const row = { ...results(1)[0], status: "succeeded", productionText: "18.5", recomputeText: "3.20", makespanText: "12.34" };
  const page = projectResultTablePage([row], createResultTableState(), context());
  const markup = renderResultTable(page, "");
  const attributes = [
    ["data-result-table-details", "查看 测试 1 的详情"],
    ["data-result-table-run", "运行 测试 1"],
    ["data-result-table-more", "测试 1 的更多操作"],
  ];
  for (const [attribute, label] of attributes) {
    const button = actionButton(markup, attribute);
    assert.match(button.inner, /^<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false">[\s\S]*<\/svg>$/);
    assert.doesNotMatch(button.inner, /[\u4e00-\u9fa5]/, `${attribute} 按钮不得包含可见文字`);
    assert.ok(button.markup.includes(`aria-label="${label}"`), `${attribute} 按钮必须提供可访问名称`);
    assert.match(button.markup, /title="[^"]+"/, `${attribute} 按钮必须提供悬浮提示`);
  }
  const expanded = actionButton(renderResultTable(page, "test-1"), "data-result-table-details");
  assert.notEqual(expanded.inner, actionButton(markup, "data-result-table-details").inner, "展开状态使用收起图标");
});

test("指标列宽度固定并强调真实数字，行操作沿用模板图标按钮几何", () => {
  const css = fs.readFileSync(path.join(__dirname, "../app/frontend/assets/result_table.css"), "utf8");
  const tableRule = /\.result-table\s*\{([^}]*)\}/.exec(css);
  assert.ok(tableRule, "结果表必须定义表格规则");
  assert.match(tableRule[1], /table-layout:\s*fixed/, "固定列宽才能让状态与指标列保持紧凑");
  for (const [column, width] of [
    ["result-table-status-column", "110px"],
    ["result-table-metric-column", "120px"],
    ["result-table-actions-column", "132px"],
  ]) {
    const rule = new RegExp(`\\.result-table \\.${column}\\s*\\{([^}]*)\\}`).exec(css);
    assert.ok(rule, `结果表必须定义 ${column} 宽度`);
    assert.match(rule[1], new RegExp(`width:\\s*${width}`));
  }
  const numberRule = /\.result-table \.result-table-number\s*\{([^}]*)\}/.exec(css);
  assert.ok(numberRule, "结果表必须定义指标数值样式");
  assert.match(numberRule[1], /font-weight:\s*700/);
  assert.match(numberRule[1], /color:\s*var\(--ink\)/);
  const emptyRule = /\.result-table \.result-table-number\.is-empty\s*\{([^}]*)\}/.exec(css);
  assert.ok(emptyRule, "无值占位必须有独立样式");
  assert.match(emptyRule[1], /font-weight:\s*400/);
  // 结果行全部直接展示：卡片内不再有纵向滚动与吸顶表头。
  const scrollRule = /\.result-table-scroll\s*\{([^}]*)\}/.exec(css);
  assert.ok(scrollRule, "结果表滚动容器必须定义规则");
  assert.doesNotMatch(scrollRule[1], /max-height/, "结果表不得在卡片内限制高度");
  assert.doesNotMatch(scrollRule[1], /overflow:\s*auto/, "结果表只保留横向滚动");
  assert.doesNotMatch(css, /\.result-table thead th \{[^}]*position:\s*sticky/, "结果表不再使用吸顶表头");

  const emptyMarkup = renderResultTable(projectResultTablePage(results(1), createResultTableState(), context()), "");
  assert.match(emptyMarkup, /<td class="result-table-number is-empty">—<\/td>/);

  const template = fs.readFileSync(path.join(__dirname, "../app/frontend/assets/spark_table.css"), "utf8");
  const actionRule = /\.table-btn-action\s*\{([^}]*)\}/.exec(template);
  assert.ok(actionRule, "spark 表格模板必须定义行操作按钮");
  assert.match(actionRule[1], /width:\s*32px/);
  assert.match(actionRule[1], /height:\s*32px/);
});

test("指标 CSV 导出三项指标，缺失值保留占位并转义文本", () => {
  const rows = [
    { ...results(1)[0], testId: "t1", testName: '测试 "一"', status: "succeeded", productionText: "18.5/20.0", recomputeText: "3.20", makespanText: "412.55" },
    { ...results(1)[0], testId: "t2", testName: "test2", status: "queued" },
  ];
  const lines = resultTableMetricsCsv(rows).split("\r\n");
  assert.equal(lines[0], "测试名称,状态,产能（片/h）,平均重算（s）,Makespan（s）");
  assert.equal(lines[1], '"测试 ""一""",成功,18.5/20.0,3.20,412.55');
  assert.equal(lines[2], "test2,等待中,—,—,—");
});

test("指标 CSV 导出当前筛选范围，不受分页影响", () => {
  const container = tableContainer();
  const controller = createResultTable(container, { showDetails() {}, runTest() {} });
  const rows = results(25).map((row, index) => ({ ...row, status: "succeeded", productionText: String(index + 1) }));
  controller.update(rows, context(new Set(["test-3", "test-4"])));
  const lines = controller.metricsCsv().split("\r\n");
  assert.equal(lines.length, 3, "只导出当前筛选范围内的测试");
  assert.ok(lines[1].startsWith("测试 3,"));
  assert.ok(!controller.metricsCsv().includes("测试 5"));
  controller.dispose();
});

/** 最小事件容器只模拟分页边界，避免测试依赖真实浏览器或旧构建文件。 */
function tableContainer() {
  const listeners = new Map();
  let html = "";
  let writes = 0;
  return {
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; writes += 1; },
    get writes() { return writes; },
    contains() { return true; },
    querySelector() { return { focus() {} }; },
    querySelectorAll() { return []; },
    addEventListener(name, handler) { listeners.set(name, handler); },
    removeEventListener(name) { listeners.delete(name); },
    emit(name, event) { listeners.get(name)?.(event); },
    dispatch(name, dataset, value = "") {
      let stopped = false;
      const control = { dataset, value, disabled: false };
      listeners.get(name)?.({ target: { closest: selector => selector.includes("[data-result-table-menu]") ? null : control }, stopPropagation() { stopped = true; } });
      return stopped;
    },
  };
}

test("分页控制器隔离行操作、保留轮询页码，每页固定 10 项", () => {
  const container = tableContainer();
  const details = [];
  const runs = [];
  const controller = createResultTable(container, { showDetails: id => details.push(id), runTest: id => runs.push(id) });
  const rows = results(60);
  const selected = new Set(rows.map(row => row.testId));
  controller.update(rows, context(selected));
  const initialWrites = container.writes;
  controller.update(rows, context(selected));
  assert.equal(container.writes, initialWrites, "未变化的轮询不重建DOM");
  assert.equal(container.dispatch("click", { resultTablePage: "3" }), true);
  controller.update(rows.map(row => ({ ...row, status: "succeeded" })), context(selected));
  assert.match(container.innerHTML, /第 21–30 条，共 60 条/);
  assert.equal(container.dispatch("click", { resultTableDetails: "test-22" }), true);
  assert.equal(container.dispatch("click", { resultTableRun: "test-22" }), true);
  assert.deepEqual(details, ["test-22"]);
  assert.deepEqual(runs, ["test-22"]);
  assert.doesNotMatch(container.innerHTML, /data-result-table-size/, "分页底栏不再提供每页条数选择");
  assert.match(container.innerHTML, /第 21–30 条，共 60 条/);
  assert.equal(selected.size, 60);
  controller.dispose();
});

test("更多菜单使用顶层浮层，轮询保留操作焦点，Escape返回触发按钮", () => {
  const container = tableContainer();
  const document = { activeElement: null, defaultView: { innerWidth: 1440, innerHeight: 900 } };
  const attributes = new Map([["aria-controls", "result-menu"]]);
  const moreButton = {
    dataset: { resultTableMore: "test-1" }, disabled: false,
    getAttribute: name => attributes.get(name), setAttribute: (name, value) => attributes.set(name, value),
    getBoundingClientRect: () => ({ top: 780, bottom: 820, right: 1000 }),
    focus() { document.activeElement = this; },
  };
  const playback = {
    dataset: { resultTableMenuAction: "playback" },
    closest() { return null; },
    focus() { document.activeElement = this; },
  };
  const menu = {
    style: {}, offsetWidth: 180, offsetHeight: 160, opened: false,
    showPopover() { this.opened = true; }, hidePopover() { this.opened = false; },
    matches() { return this.opened; }, contains: element => element === playback,
    querySelector: () => playback, querySelectorAll: () => [playback],
  };
  document.getElementById = () => menu;
  container.ownerDocument = document;
  container.querySelectorAll = () => [moreButton];
  const controller = createResultTable(container, { showDetails() {}, runTest() {} });
  const rows = results(1);
  controller.update(rows, context());
  let stopped = false;
  container.emit("click", {
    target: { closest: selector => selector.startsWith("[data-result-table-menu]") ? null : moreButton },
    stopPropagation() { stopped = true; },
  });
  assert.equal(stopped, true);
  assert.equal(menu.opened, true);
  assert.equal(attributes.get("aria-expanded"), "true");
  assert.equal(document.activeElement, playback);
  assert.equal(menu.style.top, "614px", "靠近视口底部时向上展开菜单");
  controller.update([{ ...rows[0], productionText: "20.0" }], context());
  assert.equal(menu.opened, true);
  assert.equal(document.activeElement, playback, "轮询重绘仍保持菜单操作焦点");
  container.emit("keydown", { target: playback, key: "Escape", preventDefault() {}, stopPropagation() {} });
  assert.equal(menu.opened, false);
  assert.equal(attributes.get("aria-expanded"), "false");
  assert.equal(document.activeElement, moreButton);
  controller.dispose();
});
