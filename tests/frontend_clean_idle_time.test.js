/** 前置清洁的阈值默认、历史值保留、校验和弹窗适用范围。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { cleanIdleTimeValues, supportsCleanIdleTime, updateCleanIdleTimeFields } = require(process.env.CT_CLEAN_IDLE_TIME_TEST_BUILD);

test("新增默认值和历史配置在编辑往返时保留", () => {
  assert.deepEqual(cleanIdleTimeValues({}), {lower: 0, upper: 99999});
  for (const values of [{lower: 12.5, upper: 999999}, {lower: 0, upper: 0}, {lower: 5, upper: 5}]) {
    assert.deepEqual(cleanIdleTimeValues(JSON.parse(JSON.stringify(values))), values);
  }
  assert.deepEqual(cleanIdleTimeValues({lower: "0.25", upper: "100"}), {lower: 0.25, upper: 100});
});

test("拒绝空白、负数、非有限值和倒置区间", () => {
  for (const value of ["", " ", -1, Infinity, NaN, "oops", true]) {
    assert.throws(() => cleanIdleTimeValues({lower: value}));
    assert.throws(() => cleanIdleTimeValues({upper: value}));
  }
  assert.throws(() => cleanIdleTimeValues({lower: 2, upper: 1}), /最大值/);
});

test("仅三类前置清洁显示并启用输入，切换类别不清空数值", () => {
  const nodes = Object.fromEntries(["Min", "Max"].flatMap(suffix => [
    [`cleanIdleTime${suffix}`, {value: "42", disabled: false}],
    [`cleanIdleTime${suffix}Field`, {hidden: false}],
  ]));
  nodes.cleanIdleTimeFields = {hidden: false};
  const root = {getElementById: id => nodes[id]};
  for (const type of ["preclean", "postclean", "wacclean", "dummy", "dummywac"]) {
    const expected = ["preclean", "dummy", "dummywac"].includes(type);
    assert.equal(supportsCleanIdleTime(type), expected);
    updateCleanIdleTimeFields(root, type);
    assert.equal(nodes.cleanIdleTimeFields.hidden, !expected);
    for (const suffix of ["Min", "Max"]) {
      assert.equal(nodes[`cleanIdleTime${suffix}`].disabled, !expected);
      assert.equal(nodes[`cleanIdleTime${suffix}Field`].hidden, !expected);
      assert.equal(nodes[`cleanIdleTime${suffix}`].value, "42");
    }
  }
});
