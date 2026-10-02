/** 运行结果读取边界：轻量甘特来源、指标后台加载及异步结果归属。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { createVisualizationWorkspace, movelistResultSourceUrl } = require(
  process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD,
);
const {
  playbackDocument, processingResult, jsonResponse, deferredResponse,
} = require("./support/frontend_playback_fixtures");

const pageUrl = "http://127.0.0.1:8765/movelist_gantt_viewer.html";

test("甘特只为同源单结果选择 movelist 视图并保留查询参数", () => {
  assert.equal(movelistResultSourceUrl("/api/results/result-1", pageUrl),
    "/api/results/result-1?view=movelist");
  assert.equal(movelistResultSourceUrl("/api/results/result-1?name=%E6%B5%8B%E8%AF%95&view=replay-context#move-2", pageUrl),
    "/api/results/result-1?name=%E6%B5%8B%E8%AF%95&view=movelist#move-2");
  assert.equal(movelistResultSourceUrl("http://127.0.0.1:8765/api/results/result-1", pageUrl),
    "http://127.0.0.1:8765/api/results/result-1?view=movelist");
});

test("日志、本地文件及其他服务地址保留来源语义", () => {
  for (const source of [
    "/api/logs/log-1", "test-result.json", "blob:http://127.0.0.1:8765/local-file",
    "https://example.com/api/results/result-1", "/api/results/result-1/other-view",
  ]) assert.equal(movelistResultSourceUrl(source, pageUrl), source);
});

test("指标仍在等待时回放结果已就绪，指标失败保留可用拓扑", async t => {
  const root = playbackDocument();
  const workspace = createVisualizationWorkspace(root);
  const analysis = deferredResponse();
  let analysisInput;
  t.mock.method(global, "fetch", async (url, options) => {
    if (url === "/api/results/result-1") return jsonResponse(processingResult(20));
    assert.equal(url, "/api/analysis/schedule");
    analysisInput = JSON.parse(options.body);
    return analysis.promise;
  });

  let loaded = false;
  const loading = workspace.loadResult("result-1", "第一项").then(() => { loaded = true; });
  await new Promise(setImmediate);
  assert.equal(loaded, true, "拓扑加载不能等待分析响应");
  assert.equal(root.getElementById("visualTopologyPlayback").hidden, false);
  assert.equal(root.getElementById("visualTimeline").max, "20");
  assert.equal(root.getElementById("visualSource").textContent, "第一项");
  assert.equal(analysisInput.resultId, "result-1");
  assert.match(root.getElementById("visualReplayKpis").textContent, /正在计算指标/);
  analysis.resolve(jsonResponse({ ok: false, error: "分析暂不可用" }, 500));
  await loading;
  await new Promise(setImmediate);
  assert.match(root.getElementById("visualPerformance").innerHTML, /分析暂不可用/);
  assert.equal(root.getElementById("visualTopologyPlayback").hidden, false);
});

test("快速切换结果时迟到的旧结果不会覆盖当前时间轴", async t => {
  const root = playbackDocument();
  const workspace = createVisualizationWorkspace(root);
  const first = deferredResponse();
  const analysis = deferredResponse();
  t.mock.method(global, "fetch", async url => {
    if (url === "/api/results/first") return first.promise;
    if (url === "/api/results/second") return jsonResponse(processingResult(30));
    return analysis.promise;
  });
  const previous = workspace.loadResult("first", "旧测试");
  await workspace.loadResult("second", "新测试");
  first.resolve(jsonResponse(processingResult(10)));
  await previous;
  assert.equal(root.getElementById("visualSource").textContent, "新测试");
  assert.equal(root.getElementById("visualTimeline").max, "30");
  workspace.clear();
  analysis.resolve(jsonResponse({ ok: false, error: "清空后的分析" }, 500));
  await new Promise(setImmediate);
});

test("切换结果后旧分析失败不会污染新测试的指标", async t => {
  const root = playbackDocument();
  const workspace = createVisualizationWorkspace(root);
  const analyses = [];
  t.mock.method(global, "fetch", async url => {
    if (url.startsWith("/api/results/")) return jsonResponse(processingResult(20));
    const analysis = deferredResponse();
    analyses.push(analysis);
    return analysis.promise;
  });
  await workspace.loadResult("first");
  await workspace.loadResult("second");
  analyses[1].resolve(jsonResponse({ ok: false, error: "新分析失败" }, 500));
  await new Promise(setImmediate);
  analyses[0].resolve(jsonResponse({ ok: false, error: "旧分析失败" }, 500));
  await new Promise(setImmediate);
  assert.match(root.getElementById("visualPerformance").innerHTML, /新分析失败/);
  assert.doesNotMatch(root.getElementById("visualPerformance").innerHTML, /旧分析失败/);
});

test("清空后迟到的结果或错误不会重新显示回放", async t => {
  for (const fail of [false, true]) {
    const root = playbackDocument();
    const workspace = createVisualizationWorkspace(root);
    const result = deferredResponse();
    t.mock.method(global, "fetch", () => result.promise);
    const loading = workspace.loadResult("first");
    workspace.clear();
    if (fail) result.reject(new Error("迟到的读取错误"));
    else result.resolve(jsonResponse(processingResult(10)));
    await loading;
    assert.equal(root.getElementById("visualTopologyPlayback").hidden, true);
    assert.match(root.getElementById("visualPlaybackEmpty").innerHTML, /等待回放数据/);
    assert.doesNotMatch(root.getElementById("visualPlaybackEmpty").innerHTML, /迟到的读取错误/);
    t.mock.restoreAll();
  }
});
