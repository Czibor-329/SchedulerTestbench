/** 固定分析双栏契约：产能始终可见，右侧仅显示当前选择，不存在第三行视图。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { isAnalysisViewVisible, mountAnalysisWorkspace } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

test("左侧产能不受右侧选择影响", () => {
  for (const selected of ["bottleneck", "residence"]) assert.equal(isAnalysisViewVisible("throughput", selected), true);
});
test("默认瓶颈视图隐藏驻留", () => {
  assert.equal(isAnalysisViewVisible("bottleneck", "bottleneck"), true);
  assert.equal(isAnalysisViewVisible("residence", "bottleneck"), false);
});
test("切换驻留隐藏瓶颈且仍只有两项分析可见", () => {
  assert.deepEqual(["throughput", "bottleneck", "residence"].filter(name => isAnalysisViewVisible(name, "residence")), ["throughput", "residence"]);
});

test("展开后隐藏页面缩放不写入零尺寸，重新显示和滚动恢复工作区边界", () => {
  // 最小 DOM 夹具仅模拟布局边界和已挂载标题栏，不模拟浏览器排版。
  const originalObserver = global.ResizeObserver;
  let observer;
  global.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.targets = []; observer = this; }
    observe(target) { this.targets.push(target); }
    disconnect() { this.targets = []; }
  };
  const style = () => ({ setProperty(name, value) { this[name] = value; } });
  let visible = true;
  let bounds = { left: 252, right: 1252, width: 1000 };
  const events = {};
  const viewEvents = {};
  const toggle = { textContent: "", setAttribute() {}, focus() {} };
  const windows = ["throughput", "bottleneck", "residence"].map(name => ({
    dataset: { analysisWindow: name }, style: style(), hidden: false,
    body: { hidden: true, scrollTop: 0, children: [], getBoundingClientRect: () => ({ top: 100 }) },
    header: { getBoundingClientRect: () => ({ height: 36 }) },
    querySelector(selector) { return selector === ".analysis-window-body" ? this.body : this.header; },
    querySelectorAll() { return []; },
    removeAttribute() { this.style = style(); },
  }));
  const panel = {
    classList: { add() {} }, style: style(),
    ownerDocument: { defaultView: { innerWidth: 1280, addEventListener(name, callback) { viewEvents[name] = callback; } } },
    addEventListener(name, callback) { events[name] = callback; },
    getClientRects: () => visible ? [bounds] : [],
    getBoundingClientRect: () => visible ? bounds : { left: 0, right: 0, width: 0 },
    querySelector(selector) { return selector.includes('="bottleneck"') ? windows[1] : windows[0]; },
    querySelectorAll(selector) { return selector === "[data-analysis-window]" ? windows : selector === "[data-analysis-toggle]" ? [toggle] : []; },
  };
  try {
    mountAnalysisWorkspace(panel, () => {});
    assert.ok(observer.targets.includes(panel), "必须观察工作区以检测页面重新显示");
    events.click({ target: { closest: () => toggle } });
    const previousLeft = windows[1].style["--analysis-overlay-left"];
    const previousHeight = panel.style["--analysis-overlay-height"];
    visible = false;
    viewEvents.resize();
    observer.callback();
    assert.equal(windows[1].style["--analysis-overlay-left"], previousLeft);
    assert.equal(panel.style["--analysis-overlay-height"], previousHeight);
    visible = true;
    bounds = { left: 252, right: 1452, width: 1200 };
    observer.callback();
    assert.equal(parseFloat(windows[0].style["--analysis-overlay-left"]), bounds.left);
    assert.equal(parseFloat(windows[1].style["--analysis-overlay-left"]), bounds.right - bounds.width / 2.15);
    bounds = { left: 152, right: 1352, width: 1200 };
    viewEvents.scroll();
    assert.equal(parseFloat(windows[0].style["--analysis-overlay-left"]), 152);
    panel.ownerDocument.defaultView.innerWidth = 1000;
    viewEvents.resize();
    assert.equal(windows[1].style["--analysis-overlay-left"], "152px");
    assert.equal(windows[1].style["--analysis-overlay-width"], "1200px");
  } finally {
    global.ResizeObserver = originalObserver;
  }
});
