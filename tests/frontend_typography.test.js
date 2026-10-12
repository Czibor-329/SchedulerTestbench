/** 统一字体契约：字体族只从 --font-ui / --font-mono 取，自托管字体文件与两个页面引用一致。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const frontendRoot = path.join(__dirname, "../app/frontend");
const assetsRoot = path.join(frontendRoot, "assets");
const readAsset = name => fs.readFileSync(path.join(assetsRoot, name), "utf8");

/** 我们自己的样式表（vendor 产物 tabler/katex 不参与统一）。 */
const OWN_STYLESHEETS = [
  "config_editor.css",
  "run_workspace.css",
  "console_shell.css",
  "console_components.css",
  "spark_table.css",
  "result_table.css",
  "analysis_report.css",
  "console_replay_toolbar.css",
  "console_gantt_toolbar.css",
];

/** 内联字体栈的特征词；变量定义与 @font-face 声明允许出现字体名。 */
const INLINE_STACK_PATTERN =
  /font(?:-family)?\s*:[^;{}]*?(?:Consolas|monospace|Inter|"Fira|"Cascadia|SFMono|ui-monospace|Arial|sans-serif)[^;{}]*/g;

test("字体族只在基础样式里定义一次，其余声明一律走变量", () => {
  const base = readAsset("config_editor.css");
  assert.match(
    base,
    /--font-ui:\s*"Plus Jakarta Sans", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", system-ui, sans-serif;/,
  );
  assert.match(base, /--font-mono:\s*"Consolas", "Cascadia Mono", ui-monospace, monospace;/);
  assert.match(base, /font: \d+px\/[\d.]+ var\(--font-ui\)/, "正文字体应引用 --font-ui");

  for (const name of OWN_STYLESHEETS) {
    const css = readAsset(name);
    const inline = (css.match(INLINE_STACK_PATTERN) || []).filter(declaration => {
      const value = declaration.replace(/\s+/g, " ");
      return !value.includes("var(--font-ui)")
        && !value.includes("var(--font-mono)")
        && !/--font-(ui|mono):/.test(value);
    });
    assert.deepEqual(inline, [], `${name} 仍内联字体栈：${inline.join(" | ")}`);
  }

  const shell = readAsset("console_shell.css");
  assert.match(shell, /font: 16px\/1\.55 var\(--font-ui\)/);
  assert.doesNotMatch(shell, /Inter,/, "旧皮肤字体栈应已收敛到变量");
});

test("自托管字体随仓库提供，并保留许可与拉丁子集范围", () => {
  const fontPath = path.join(assetsRoot, "fonts", "PlusJakartaSans-latin.woff2");
  assert.ok(fs.statSync(fontPath).size > 10 * 1024, "字体文件应包含完整 latin 子集");

  const fontsCss = readAsset("fonts.css");
  assert.match(fontsCss, /@font-face\s*\{[^}]*font-family:\s*"Plus Jakarta Sans"/);
  assert.match(fontsCss, /url\("\/assets\/fonts\/PlusJakartaSans-latin\.woff2"\) format\("woff2"\)/);
  assert.match(fontsCss, /font-weight:\s*400 800/);
  assert.match(fontsCss, /font-display:\s*swap/);
  assert.match(fontsCss, /unicode-range:\s*U\+0000-00FF/, "中文应继续由系统字体渲染");
  assert.match(fontsCss, /SIL Open Font License/);

  const license = fs.readFileSync(path.join(assetsRoot, "fonts", "PlusJakartaSans-OFL.txt"), "utf8");
  assert.match(license, /SIL Open Font License/);
});

test("控制台与甘特图查看器加载同一份字体声明", () => {
  const shell = fs.readFileSync(path.join(frontendRoot, "config_editor.html"), "utf8");
  const viewer = fs.readFileSync(path.join(frontendRoot, "movelist_gantt_viewer.html"), "utf8");

  assert.match(shell, /<link rel="stylesheet" href="\/assets\/fonts\.css\?v=\d+\.\d+\.\d+">/);
  assert.match(viewer, /<link rel="stylesheet" href="\/assets\/fonts\.css">/);
  assert.match(viewer, /--font-ui: "Plus Jakarta Sans"/);
  assert.match(viewer, /font-family: var\(--font-ui\);/);
  assert.doesNotMatch(viewer, /"Inter", Arial/, "查看器不应再保留旧字体栈");
});
