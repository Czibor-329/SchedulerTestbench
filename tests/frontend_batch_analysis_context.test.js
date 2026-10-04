/** 运行后的分析快照读取、冻结与并发限制；使用当前 TypeScript 临时构建。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createBatchAnalysisContext } = require(process.env.CT_RUN_WORKSPACE_TEST_BUILD);

test("创建上下文不读取测试，提交运行后才显式加载且只加载一次", async () => {
  const reads = [];
  const device = { name: "PM1" };
  const context = createBatchAnalysisContext({
    device, routes: [], selectedTests: [{ id: "selected" }], previousTests: [{ id: "previous" }],
    readTest: async id => { reads.push(id); return { id, rounds: [1] }; },
  });
  device.name = "changed";
  assert.deepEqual(reads, []);
  assert.equal(context.device.name, "PM1");
  const first = context.load();
  assert.equal(context.load(), first);
  await first;
  assert.deepEqual(reads, ["selected"]);
  assert.deepEqual(context.tests.map(test => test.id), ["previous", "selected"]);
});

test("分析读取失败保留已有上下文并显式拒绝，不把缺失测试当作完整数据", async () => {
  const context = createBatchAnalysisContext({
    device: {}, routes: [], selectedTests: [{ id: "missing" }], previousTests: [{ id: "previous" }],
    readTest: async () => { throw new Error("读取失败"); },
  });
  await assert.rejects(context.load(), /读取失败/);
  assert.deepEqual(context.tests, [{ id: "previous" }]);
});
