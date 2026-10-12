/** 甘特图顶栏契约：查看器工具条搬入控制台顶栏、按页签显隐、重载后重新接管。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { initializeConsoleGanttToolbar } = require(process.env.CT_GANTT_TOOLBAR_TEST_BUILD);

/** 建立只实现顶栏模块所需能力的最小节点替身。 */
function stubNode(extra = {}) {
  const classes = new Set(["active"].filter(() => extra.active));
  const listeners = new Map();
  return Object.assign({
    hidden: false,
    children: [],
    classList: {
      contains: name => classes.has(name),
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      add: name => classes.add(name),
      remove: name => classes.delete(name),
    },
    replaceChildren(...nodes) { this.children = nodes; },
    append(node) { this.children.push(node); },
    addEventListener(name, handler) { listeners.set(name, handler); },
    removeEventListener(name) { listeners.delete(name); },
    emit(name) { listeners.get(name)?.(); },
    getBoundingClientRect: () => ({ height: 96 }),
  }, extra);
}

/**
 * 装配模块所需的全局环境，返回驱动用例的句柄。
 * MutationObserver 与 ResizeObserver 只保留回调，由用例显式触发，避免依赖真实布局。
 */
function installEnvironment() {
  const properties = new Map();
  const slot = stubNode();
  const shell = stubNode({ style: {
    setProperty: (name, value) => properties.set(name, value),
    removeProperty: name => properties.delete(name),
  } });
  const topbar = stubNode();
  const view = stubNode();
  const viewerDocument = { documentElement: stubNode(), querySelector: () => viewerDocument.toolbar };
  const frame = stubNode({ contentDocument: viewerDocument });
  const mutationCallbacks = [];
  const resizeCallbacks = [];

  global.document = {
    querySelector: selector => {
      if (selector === ".shell") return shell;
      if (selector === ".console-topbar") return topbar;
      return selector === '[data-tab-view="gantt"]' ? view : null;
    },
    getElementById: id => (id === "consoleGanttControls" ? slot : id === "ganttFrame" ? frame : null),
  };
  global.MutationObserver = class { constructor(callback) { mutationCallbacks.push(callback); } observe() {} disconnect() {} };
  global.ResizeObserver = class { constructor(callback) { resizeCallbacks.push(callback); } observe() {} disconnect() {} };

  return {
    slot, topbar, view, frame, viewerDocument, properties,
    activate(active) { view.classList.toggle("active", active); mutationCallbacks.forEach(callback => callback()); },
    resize(height) { topbar.getBoundingClientRect = () => ({ height }); resizeCallbacks.forEach(callback => callback()); },
    load(toolbar) { viewerDocument.toolbar = toolbar; frame.emit("load"); },
  };
}

/** 顶栏是否处于甘特图工具栏形态。 */
function isToolbarMode(environment) {
  return environment.topbar.classList.contains("console-gantt-toolbar");
}

test("查看器工具条搬到顶栏，只在甘特图页签激活时显示", () => {
  const environment = installEnvironment();
  const dispose = initializeConsoleGanttToolbar();
  assert.equal(isToolbarMode(environment), false, "iframe 未加载前保持普通标题栏");
  assert.equal(environment.slot.hidden, true);

  const toolbar = stubNode();
  environment.load(toolbar);
  assert.deepEqual(environment.slot.children, [toolbar], "工具条节点被搬入顶栏槽位");
  assert.equal(environment.slot.hidden, true, "非激活页签不显示工具条");
  assert.equal(isToolbarMode(environment), false);

  environment.activate(true);
  assert.equal(environment.slot.hidden, false);
  assert.equal(isToolbarMode(environment), true);
  assert.equal(environment.properties.get("--console-gantt-toolbar-height"), "96px");

  environment.resize(132);
  assert.equal(environment.properties.get("--console-gantt-toolbar-height"), "132px", "顶栏换行后刷新视口高度");

  environment.activate(false);
  assert.equal(environment.slot.hidden, true);
  assert.equal(isToolbarMode(environment), false);
  assert.equal(environment.properties.has("--console-gantt-toolbar-height"), false);
  dispose();
});

test("查看器重新加载后接管新文档的工具条，不残留旧控件", () => {
  const environment = installEnvironment();
  const dispose = initializeConsoleGanttToolbar();
  const first = stubNode();
  environment.load(first);
  environment.activate(true);
  assert.deepEqual(environment.slot.children, [first]);

  const second = stubNode();
  environment.load(second);
  assert.deepEqual(environment.slot.children, [second], "旧工具条被撤下，只保留新文档的控件");
  assert.equal(first.children.length, 0);
  assert.equal(isToolbarMode(environment), true);
  dispose();
});

test("没有工具条的查看器文档不会把顶栏切到工具栏形态", () => {
  const environment = installEnvironment();
  const dispose = initializeConsoleGanttToolbar();
  environment.load(null);
  environment.activate(true);
  assert.equal(environment.slot.hidden, true);
  assert.equal(isToolbarMode(environment), false);
  assert.equal(environment.properties.has("--console-gantt-toolbar-height"), false);
  dispose();
});
