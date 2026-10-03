// 验证回放对象的甘特图定位契约；测试入口由统一套件从当前 TypeScript 源码临时编译。
const test = require("node:test");
const assert = require("node:assert/strict");
if (!process.env.CT_GANTT_COMPARE_TEST_BUILD) {
  throw new Error("请通过 scripts/run_test_suite.py --suite frontend 编译并运行本测试。");
}
const {
  parseReplayGanttFocus,
  matchesReplayGanttFocus,
  findReplayGanttTarget,
} = require(process.env.CT_GANTT_COMPARE_TEST_BUILD);

test("缺省与异常查询参数保持全量浏览，不把空时间当作零秒", () => {
  assert.deepEqual(parseReplayGanttFocus(""), { resource: "", wafer: "", moveId: "", time: null });
  assert.equal(parseReplayGanttFocus("?time= ").time, null);
  assert.equal(parseReplayGanttFocus("?time=Infinity").time, null);
  assert.deepEqual(parseReplayGanttFocus("?resource=PM%20A&wafer=7&moveId=3&time=0"), {
    resource: "PM A", wafer: "7", moveId: "3", time: 0,
  });
});

test("资源匹配包含取放机械手，名称和晶圆编号均使用精确匹配", () => {
  const raw = { ModuleName: "PM1", Robot: "VTR", MatIDList: [1, 11] };
  assert.equal(matchesReplayGanttFocus(raw, parseReplayGanttFocus("?resource=VTR&wafer=1")), true);
  assert.equal(matchesReplayGanttFocus(raw, parseReplayGanttFocus("?resource=PM&wafer=1")), false);
  assert.equal(matchesReplayGanttFocus(raw, parseReplayGanttFocus("?resource=PM1&wafer=2")), false);
  assert.equal(matchesReplayGanttFocus(raw, parseReplayGanttFocus("")), true);
  assert.equal(matchesReplayGanttFocus({ ModuleName: "VTR", StationList: ["PM1"], RecvMatList: [2], SendMatList: [3] },
    parseReplayGanttFocus("?wafer=3")), true);
  assert.equal(matchesReplayGanttFocus({ ModuleName: "VTR", SrcStationList: ["PM1"], MatIDList: [1] },
    parseReplayGanttFocus("?resource=PM1&wafer=1")), true);
});

test("跨代复用 MoveID 时按回放时刻选中附近版本，并保留记录身份", () => {
  const earlier = { raw: { MoveID: 7, ModuleName: "PM1", MatIDList: [1] }, rawIndex: 0, start: 1, end: 3 };
  const later = { raw: { MoveID: 7, ModuleName: "PM1", MatIDList: [2] }, rawIndex: 1, start: 10, end: 14 };
  const records = [earlier, later];
  assert.equal(findReplayGanttTarget(records, parseReplayGanttFocus("?moveId=7&time=12")), later);
  assert.equal(findReplayGanttTarget(records, parseReplayGanttFocus("?moveId=7&time=4")), earlier);
  assert.equal(findReplayGanttTarget(records, parseReplayGanttFocus("?moveId=7&wafer=1&time=12")), earlier);
  assert.equal(findReplayGanttTarget(records, parseReplayGanttFocus("?moveId=7")), earlier);
  assert.deepEqual(records, [earlier, later]);
});

test("同距时优先未撤销计划，未知 MoveID 不误选其它动作", () => {
  const removed = { raw: { MoveID: 7, ModuleName: "PM1" }, rawIndex: 0, start: 10, end: 14, removedByRecompute: true };
  const current = { raw: { MoveID: 7, ModuleName: "PM1" }, rawIndex: 1, start: 10, end: 14 };
  assert.equal(findReplayGanttTarget([removed, current], parseReplayGanttFocus("?moveId=7&time=12")), current);
  assert.equal(findReplayGanttTarget([current], parseReplayGanttFocus("?moveId=70")), null);
  assert.equal(findReplayGanttTarget([current], parseReplayGanttFocus("?resource=PM1&time=12")), null);
});
