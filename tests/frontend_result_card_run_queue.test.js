/** 结果卡片双击运行队列的顺序、去重与异常隔离测试。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { createResultCardRunQueue } = require(process.env.CT_RESULT_CARD_RUN_QUEUE_TEST_BUILD);

/** 等待条件成立，避免用固定墙钟时间隐藏队列竞态。 */
async function waitFor(predicate) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    if (predicate()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  assert.fail("队列未在预期的事件循环内完成");
}

test("多个测试按双击入队顺序串行执行", async () => {
  const calls = [];
  let releaseFirst;
  const firstBlocked = new Promise(resolve => { releaseFirst = resolve; });
  const queue = createResultCardRunQueue({
    async runTest(testId) {
      calls.push(`start:${testId}`);
      if (testId === "test-1") await firstBlocked;
      calls.push(`end:${testId}`);
    },
  });

  assert.equal(queue.enqueue("test-1"), true);
  assert.equal(queue.enqueue("test-2"), true);
  await waitFor(() => calls.includes("start:test-1"));
  assert.deepEqual(calls, ["start:test-1"]);
  releaseFirst();
  await waitFor(() => queue.size === 0);
  assert.deepEqual(calls, ["start:test-1", "end:test-1", "start:test-2", "end:test-2"]);
});

test("等待或运行中的同一测试不重复入队", async () => {
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const queue = createResultCardRunQueue({ runTest: () => blocked });

  assert.equal(queue.enqueue("test-1"), true);
  assert.equal(queue.enqueue("test-1"), false);
  assert.equal(queue.size, 1);
  release();
  await waitFor(() => queue.size === 0);
});

test("单项失败后继续执行队列中的后续测试", async () => {
  const calls = [];
  const queue = createResultCardRunQueue({
    async runTest(testId) {
      calls.push(testId);
      if (testId === "test-1") throw new Error("模拟失败");
    },
  });

  queue.enqueue("test-1");
  queue.enqueue("test-2");
  await waitFor(() => queue.size === 0);
  assert.deepEqual(calls, ["test-1", "test-2"]);
});

test("终止时移除未开始项但保留当前运行项", async () => {
  let release;
  let started = false;
  const blocked = new Promise(resolve => { release = resolve; });
  const queue = createResultCardRunQueue({ runTest: () => { started = true; return blocked; } });

  queue.enqueue("test-1");
  queue.enqueue("test-2");
  await waitFor(() => started);
  assert.deepEqual(queue.clearPending(), ["test-2"]);
  assert.equal(queue.size, 1);
  release();
  await waitFor(() => queue.size === 0);
});
