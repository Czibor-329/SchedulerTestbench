/** 回放观察停靠窗口契约：两个窗口联动展开，刷新默认折叠。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { setReplayDockExpanded, setReplayInspectorExpanded } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

test("观察窗口整组开合且不修改数据开关", () => {
  const windows = ["晶圆进度", "合法动作空间"].map(title => {
    const body = { hidden: true };
    const toggle = { setAttribute() {} };
    return {
      dataset: { expanded: "false" }, body, toggle,
      querySelector(selector) {
        if (selector === "h3") return { textContent: title };
        return selector === ".replay-dock-window-body" ? body : toggle;
      },
    };
  });
  const dock = { querySelectorAll() { return windows; } };
  for (const expanded of [true, false, true]) {
    setReplayInspectorExpanded(dock, expanded);
    for (const window of windows) {
      assert.equal(window.dataset.expanded, String(expanded));
      assert.equal(window.body.hidden, !expanded);
    }
  }
});

test("停靠窗口同步正文、按钮文案和 aria-expanded", () => {
  const attributes = {};
  const body = { hidden: true };
  const toggle = {
    textContent: "展开",
    setAttribute(name, value) { attributes[name] = value; },
  };
  const window = {
    dataset: { expanded: "false" },
    querySelector(selector) {
      if (selector === "h3") return { textContent: "晶圆进度" };
      return selector === ".replay-dock-window-body" ? body : toggle;
    },
  };

  setReplayDockExpanded(window, true);
  assert.equal(window.dataset.expanded, "true");
  assert.equal(body.hidden, false);
  assert.equal(toggle.textContent, "最小化");
  assert.equal(attributes["aria-expanded"], "true");

  setReplayDockExpanded(window, false);
  assert.equal(window.dataset.expanded, "false");
  assert.equal(body.hidden, true);
  assert.equal(toggle.textContent, "晶圆进度");
  assert.equal(attributes["aria-expanded"], "false");
});
