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

test("回放画布不显示网格或机器框架内部装饰，空动作区不占用分割线", () => {
  const css = fs.readFileSync(
    path.join(__dirname, "../realtime_scheduler/frontend/assets/config_editor.css"), "utf8",
  );
  assert.match(css, /\.reference-grid-canvas \{[^}]*background-image:\s*none;/);
  assert.match(css, /\.topology-machine-frame::before, \.topology-machine-frame > span \{ display: none; \}/);
  assert.match(css, /\.equipment-external-name-port \{ top:\s*108px; \}/);
  assert.match(css, /\.petri-control-panel \.decision-lens-panel:empty \{ display: none; \}/);
});
