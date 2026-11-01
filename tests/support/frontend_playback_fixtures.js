/** 回放加载测试的最小 DOM 与 HTTP 夹具，不模拟浏览器布局或改写真实数据。 */
"use strict";

/** 创建回放控制器需要的具名页面节点；查询未建模的子元素时返回空。 */
function playbackDocument() {
  const identifiers = [
    "visualToolbar", "testGroupAnalysisPanel", "visualEmpty", "visualPlaybackEmpty",
    "visualContent", "visualTopologyPlayback", "visualDeviceStage", "visualFrontSlotOverview",
    "visualDecisionLens", "visualActiveMoves", "visualSource", "visualCurrentTime",
    "visualTotalTime", "visualProgressText", "visualMoveText", "visualWaferText",
    "visualTimeline", "visualPlayButton", "visualSpeed", "visualFileInput", "visualOpenGantt",
    "visualPerformance", "performanceWindow", "visualReplayKpis", "visualReplayObjectDetails",
  ];
  const elements = new Map(identifiers.map(id => {
    const attributes = new Map();
    return [id, {
      id, dataset: {}, hidden: false, innerHTML: "", textContent: "", value: "", disabled: false,
      style: { setProperty() {}, getPropertyValue: () => "" },
      classList: { add() {}, remove() {}, toggle() {} },
      addEventListener() {},
      setAttribute: (name, value) => attributes.set(name, value),
      getAttribute: name => attributes.get(name),
      querySelector: () => null,
      querySelectorAll: () => [],
      closest: () => null,
    }];
  }));
  const document = {
    activeElement: null,
    getElementById: id => elements.get(id) ?? null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  for (const element of elements.values()) element.ownerDocument = document;
  return document;
}

/** 为加载边界测试安装已挂载的分析窗口，只模拟展开事件与正文可见性，不模拟 CSS 排版。 */
function playbackAnalysisControls(root) {
  const panel = root.getElementById("visualPerformance");
  const events = new Map();
  const toggle = { setAttribute() {}, focus() {} };
  const windows = ["throughput", "bottleneck", "residence"].map(name => ({
    dataset: { analysisWindow: name }, style: { setProperty() {} },
    body: { hidden: true }, header: {},
    querySelector(selector) { return selector === ".analysis-window-body" ? this.body : this.header; },
    querySelectorAll() { return []; }, removeAttribute() {},
  }));
  const retry = { addEventListener(type, callback) { this[type] = callback; } };
  panel.addEventListener = (type, callback) => events.set(type, callback);
  panel.getClientRects = () => [];
  panel.querySelector = selector => selector === "[data-analysis-window]" ? windows[0] : null;
  panel.querySelectorAll = selector => selector === "[data-analysis-window]" ? windows
    : selector === "[data-analysis-toggle]" ? [toggle]
      : selector === "[data-performance-retry]" ? [retry] : [];
  return {
    windows,
    toggle() { events.get("click")({ target: { closest: () => toggle } }); },
    retry() { retry.click(); },
  };
}

/** 构造只有一个加工动作的结果；endTime 明确表示时间轴终点。 */
function processingResult(endTime) {
  return { MoveList: [{
    MoveID: 1, MoveType: 9, ModuleName: "PM1", MatIDList: [1],
    StartTime: 0, EndTime: endTime,
  }] };
}

/** 构造与 fetch.json 契约一致的成功或失败响应。 */
function jsonResponse(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

/** 用显式 resolve/reject 控制服务响应顺序，避免 sleep 和墙钟断言。 */
function deferredResponse() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

module.exports = { playbackDocument, playbackAnalysisControls, processingResult, jsonResponse, deferredResponse };
