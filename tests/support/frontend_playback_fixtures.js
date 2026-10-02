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
    "visualPerformance", "performanceWindow", "visualReplayKpis",
  ];
  const elements = new Map(identifiers.map(id => {
    const attributes = new Map();
    return [id, {
      id, hidden: false, innerHTML: "", textContent: "", value: "", disabled: false,
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
  return {
    getElementById: id => elements.get(id) ?? null,
    querySelector: () => null,
    querySelectorAll: () => [],
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

module.exports = { playbackDocument, processingResult, jsonResponse, deferredResponse };
