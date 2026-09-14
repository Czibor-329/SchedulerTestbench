/** 运行首页测试草稿决策契约；输入为最小领域数据，不访问主数据。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { resolveTestDraft } = require(process.env.CT_RUN_WORKSPACE_TEST_BUILD);

test("无草稿直接导航且不保存", async () => {
  assert.equal(await resolveTestDraft(false, () => assert.fail(), () => assert.fail(), () => assert.fail()), true);
});

test("取消离开保留草稿，不产生读写副作用", async () => {
  assert.equal(await resolveTestDraft(true, async () => "cancel", () => assert.fail(), () => assert.fail()), false);
});

test("保存成功后才允许导航", async () => {
  const calls = [];
  assert.equal(await resolveTestDraft(true, async () => "save", async () => { calls.push("saved"); }, () => assert.fail()), true);
  assert.deepEqual(calls, ["saved"]);
});

test("放弃仅还原已保存测试，不写回草稿", async () => {
  let restored = false;
  assert.equal(await resolveTestDraft(true, async () => "discard", () => assert.fail(), async () => { restored = true; }), true);
  assert.equal(restored, true);
});

test("保存失败阻断导航，不静默放弃草稿", async () => {
  await assert.rejects(resolveTestDraft(true, async () => "save", async () => { throw new Error("保存失败"); }, () => assert.fail()), /保存失败/);
});
