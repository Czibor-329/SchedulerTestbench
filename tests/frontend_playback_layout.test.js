/** 拓扑回放布局入口契约：固定比例模式不暴露画布缩放控件，播放速度入口仍保留。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("固定比例回放移除画布缩放入口但保留时间轴与播放速度", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "../realtime_scheduler/frontend/config_editor.html"), "utf8",
  );
  for (const id of ["visualCanvasZoomOut", "visualCanvasZoomValue", "visualCanvasZoomIn", "visualCanvasZoomFit"]) {
    assert.doesNotMatch(html, new RegExp(`id="${id}"`));
  }
  for (const id of ["visualDeviceStage", "visualFrontSlotOverview", "visualTimeline", "visualSpeed"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("回放页常驻提供唯一的日志导入入口且不再提供 MoveList 文件导入", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "../realtime_scheduler/frontend/config_editor.html"), "utf8",
  );
  const importButtonIndex = html.indexOf('id="visualImportButton"');
  const playbackIndex = html.indexOf('id="visualTopologyPlayback"');
  assert.ok(importButtonIndex >= 0 && importButtonIndex < playbackIndex);
  assert.equal((html.match(/id="visualFileInput"/g) || []).length, 1);
  assert.match(html, /aria-label="导入复现日志"/);
  assert.doesNotMatch(html, /导入 MoveList/);
});

test("时间轴与四项指标位于顶层紧凑状态条，观察窗口默认折叠并停靠右侧", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "../realtime_scheduler/frontend/config_editor.html"), "utf8",
  );
  const statusBar = html.split('<header class="replay-status-bar"', 2)[1].split("</header>", 1)[0];
  assert.match(statusBar, /id="visualTimeline"/);
  assert.match(statusBar, /id="visualReplayKpis"/);
  assert.doesNotMatch(html, /<aside class="petri-control-panel"/);
  assert.match(html, /<aside class="replay-inspector-dock" aria-label="回放观察窗口">/);
  for (const bodyId of ["visualWaferProgressDockBody", "visualActionsDockBody"]) {
    assert.match(html, new RegExp(`id="${bodyId}" hidden`));
  }
  assert.equal((html.match(/data-replay-dock-window data-expanded="false"/g) || []).length, 2);
  const css = fs.readFileSync(
    path.join(__dirname, "../realtime_scheduler/frontend/assets/config_editor.css"), "utf8",
  );
  assert.match(css, /\.replay-status-bar \{[^}]*position: absolute;/);
  assert.match(css, /\.replay-status-bar \.petri-top-playback-controls \{[^}]*height: 34px;/);
  assert.match(css, /\.replay-inspector-dock \{[^}]*position: absolute;[^}]*right: 0;/);
});

test("回放底层保留大网格，机器内部无重复网格和装饰", () => {
  const css = fs.readFileSync(
    path.join(__dirname, "../realtime_scheduler/frontend/assets/config_editor.css"), "utf8",
  );
  assert.match(css, /\.reference-grid-canvas \{[^}]*background-image:\s*none;/);
  assert.match(css, /\.topology-canvas-area \{[^}]*background-size: 75px 75px;/);
  assert.match(css, /\.topology-machine-frame::before, \.topology-machine-frame > span \{ display: none; \}/);
  assert.match(css, /\.equipment-external-name-port \{ top:\s*108px; \}/);
  assert.match(css, /\.petri-control-panel \.decision-lens-panel:empty \{ display: none; \}/);
});
