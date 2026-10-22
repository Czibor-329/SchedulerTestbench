// 验证标准 Move 字段在中文解释中的方向、列表对应关系和动作顺序。
// 统一入口必须临时编译当前 TypeScript 源码，不允许回退到仓库构建产物。
const test = require("node:test");
const assert = require("node:assert/strict");

assert.ok(
  process.env.CT_GANTT_SEMANTICS_TEST_BUILD,
  "请通过 scripts/run_test_suite.py --suite frontend 编译并运行当前 Move 语义源码",
);
const { describeMove } = require(process.env.CT_GANTT_SEMANTICS_TEST_BUILD);

/** 找出包含指定物料的独立分句，防止列表被合并后丢失 index 对应关系。 */
function materialClause(description, materialId) {
  const clauses = description.split(/[；。、\n]/).filter((part) => part.includes(String(materialId)));
  assert.equal(clauses.length, 1, `物料 ${materialId} 应只有一个独立说明：${description}`);
  return clauses[0];
}

/** 按句内位置检查关键角色和值的对应顺序，不绑定完整中文句式。 */
function assertOrderedText(description, values) {
  let previous = -1;
  for (const value of values) {
    const position = description.indexOf(value, previous + 1);
    assert.ok(position > previous, `${value} 应按 ${values.join(" → ")} 出现：${description}`);
    previous = position;
  }
}

test("Pick 将单片从源站槽取到对应机械手槽，零号槽位不会被当成缺失", () => {
  const description = describeMove({
    MoveType: 0, ModuleName: "ATR", MatIDList: [101],
    SrcStationList: ["LP1"], SrcSlotList: [4], RobotSlotList: [0],
  });
  assert.match(description, /取/);
  assertOrderedText(materialClause(description, 101), ["LP1", "4 号槽", "0 号手槽"]);
  assert.match(description, /ATR/);
});

test("Place 将单片从对应机械手槽放到目标站槽", () => {
  const description = describeMove({
    MoveType: 1, ModuleName: "VTR", MatIDList: [202],
    RobotSlotList: [3], DestStationList: ["PM2"], DestSlotList: [8],
  });
  assert.match(description, /放/);
  assertOrderedText(materialClause(description, 202), ["3 号手槽", "PM2", "8 号槽"]);
  assert.match(description, /VTR/);
});

test("MultiPick 按下标逐片解释来源和手槽，不把两片并成一个槽", () => {
  const description = describeMove({
    MoveType: 2, ModuleName: "VTR", MatIDList: [101, 202],
    SrcStationList: ["PM1", "PM2"], SrcSlotList: [4, 8], RobotSlotList: [3, 6],
  });
  const first = materialClause(description, 101);
  const second = materialClause(description, 202);
  assertOrderedText(first, ["PM1", "4 号槽", "3 号手槽"]);
  assertOrderedText(second, ["PM2", "8 号槽", "6 号手槽"]);
  assert.doesNotMatch(first, /PM2|202/);
  assert.doesNotMatch(second, /PM1|101/);
});

test("MultiPlace 按下标逐片解释手槽和目标站槽", () => {
  const description = describeMove({
    MoveType: 3, ModuleName: "VTR", MatIDList: [101, 202],
    RobotSlotList: [3, 6], DestStationList: ["PM1", "PM2"], DestSlotList: [4, 8],
  });
  const first = materialClause(description, 101);
  const second = materialClause(description, 202);
  assertOrderedText(first, ["3 号手槽", "PM1", "4 号槽"]);
  assertOrderedText(second, ["6 号手槽", "PM2", "8 号槽"]);
  assert.doesNotMatch(first, /PM2|202/);
  assert.doesNotMatch(second, /PM1|101/);
});

/** 构造同一站点收发不同晶圆的最小 Swap，槽号均互不相同。 */
function swapMove(swapMode) {
  const move = {
    MoveType: 4, ModuleName: "VTR", StationList: ["PM7"],
    RecvMatList: [101], SendMatList: [202],
    StnSendSlotList: [4], RecvSlotList: [3],
    SendSlotList: [6], StnRecvSlotList: [8],
  };
  if (swapMode !== undefined) move.SwapMode = swapMode;
  return move;
}

test("SwapMode 0 先取旧片后放新片，并区分站点收发槽和机械手收发槽", () => {
  const description = describeMove(swapMove(0));
  assertOrderedText(description, ["101", "202"]);
  assertOrderedText(materialClause(description, 101), ["PM7", "4 号槽", "3 号手槽"]);
  assertOrderedText(materialClause(description, 202), ["6 号手槽", "PM7", "8 号槽"]);
});

test("SwapMode 1 先放新片后取旧片，允许同一机械手槽在两个阶段复用", () => {
  const description = describeMove({ ...swapMove(1), RecvSlotList: [6] });
  assertOrderedText(description, ["202", "101"]);
  assertOrderedText(materialClause(description, 202), ["6 号手槽", "PM7", "8 号槽"]);
  assertOrderedText(materialClause(description, 101), ["PM7", "4 号槽", "6 号手槽"]);
});

test("Swap 缺省模式沿用标准的先取后放顺序", () => {
  assertOrderedText(describeMove(swapMove()), ["101", "202"]);
});

test("孪生锁不对称 Swap 仅取出 LA 旧片，分别向 LA 和 LB 放入新片", () => {
  const description = describeMove({
    MoveType: 4, ModuleName: "VTR", SwapMode: 0, StationList: ["LA", "LB"],
    RecvMatList: [101], RecvSlotList: [3], StnSendSlotList: [1],
    SendMatList: [202, 303], SendSlotList: [4, 5], StnRecvSlotList: [2, 2],
  });
  assertOrderedText(materialClause(description, 101), ["LA", "1 号槽", "3 号手槽"]);
  assertOrderedText(materialClause(description, 202), ["4 号手槽", "LA", "2 号槽"]);
  assertOrderedText(materialClause(description, 303), ["5 号手槽", "LB", "2 号槽"]);
  assert.equal((description.match(/取出/g) || []).length, 1);
  assert.doesNotMatch(description, /编号未提供/);
});

test("Swap 显式空收发组不生成不存在的取片或放片", () => {
  const onlySend = describeMove({ ...swapMove(1), RecvMatList: [], RecvSlotList: [], StnSendSlotList: [] });
  assert.match(onlySend, /202/);
  assert.doesNotMatch(onlySend, /取出|编号未提供|101/);
  const onlyReceive = describeMove({ ...swapMove(0), SendMatList: [], SendSlotList: [], StnRecvSlotList: [] });
  assert.match(onlyReceive, /101/);
  assert.doesNotMatch(onlyReceive, /放入|编号未提供|202/);
});

test("未知 SwapMode 保留原模式和值对应关系，不伪造先取或先放顺序", () => {
  const description = describeMove(swapMove(23));
  assert.match(description, /23/);
  assert.doesNotMatch(description, /先|再/);
  assertOrderedText(materialClause(description, 101), ["PM7", "4 号槽", "3 号手槽"]);
  assertOrderedText(materialClause(description, 202), ["6 号手槽", "PM7", "8 号槽"]);
});

test("PreTrans 说明为后续取片转位，不误报物料已经取走或完成 Route Step", () => {
  const description = describeMove({
    MoveType: 5, ModuleName: "ATR", RalatedActionType: 1,
    SrcStationList: ["LP1"], SrcSlotList: [4], RobotSlotList: [3],
    MatIDList: [], StepIDList: [],
  });
  assert.match(description, /转位/);
  assert.match(description, /取片/);
  assert.match(description, /LP1/);
  assert.doesNotMatch(description, /旋转|已取|取出晶圆|Route.*(?:推进|完成)|完成.*Step/);
});

test("带片 PreTrans 为后续放片转位，保留关联信息但不宣称已放入目标", () => {
  const description = describeMove({
    MoveType: 5, ModuleName: "VTR", RalatedActionType: 0,
    MatIDList: [101], RobotSlotList: [3], StepIDList: [19],
    DestStationList: ["PM1"], DestSlotList: [4],
  });
  assert.match(description, /转位/);
  assert.match(description, /放片/);
  assert.match(description, /PM1/);
  assert.doesNotMatch(description, /已放|放入晶圆|Route.*(?:推进|完成)|完成.*Step/);
});

test("PreTrans 为后续换片转位，并保留未知关联动作的数值", () => {
  assert.match(describeMove({ MoveType: 5, ModuleName: "VTR", RalatedActionType: 2 }), /换片/);
  const unknown = describeMove({ MoveType: 5, ModuleName: "VTR", RalatedActionType: 23 });
  assert.match(unknown, /23/);
  assert.doesNotMatch(unknown, /(?:取片|放片|换片)/);
});

for (const [moveType, actionWord] of [[6, "开门"], [7, "关门"], [8, "收尾"]]) {
  test(`门动作 ${moveType} 仅按关联机械手类型解释，1 真空、0 和 2 大气`, () => {
    for (const [robotType, label] of [[1, "真空"], [0, "大气"], [2, "大气"]]) {
      const description = describeMove({
        MoveType: moveType, ModuleName: "LL1", RelatedRobotType: robotType,
        RelatedActionType: 1, MatIDList: [101], SlotList: [4],
      });
      assert.match(description, new RegExp(actionWord));
      assert.match(description, new RegExp(label));
      assert.match(description, /LL1/);
      assert.doesNotMatch(description, /上侧|下侧|内侧|外侧/);
    }
  });
}

test("未知门动作机械手类型保留原值，不猜真空或大气方向", () => {
  const description = describeMove({ MoveType: 6, ModuleName: "LL1", RelatedRobotType: 23 });
  assert.match(description, /23/);
  assert.doesNotMatch(description, /真空|大气/);
});

test("开门缺失关联动作时保持已知机械手，未知动作保留类型且不出现 undefined", () => {
  for (const actionType of [undefined, 23]) {
    const description = describeMove({
      MoveType: 6, ModuleName: "LL1", RelatedRobotType: 1, RelatedActionType: actionType,
    });
    assert.match(description, /真空机械手/);
    assert.match(description, /开门/);
    assert.doesNotMatch(description, /undefined|null|取片|放片|换片/);
    if (actionType !== undefined) assert.match(description, /23/);
  }
});

test("Process 显示物料槽位和配方，普通工艺不误称为清洁", () => {
  const description = describeMove({
    MoveType: 9, ModuleName: "PM1", MatIDList: [101, 202],
    SlotList: [4, 8], ProcessRecipe: "ETCH_A", CleanTaskName: "",
  });
  assert.match(description, /加工|工艺/);
  assert.match(description, /ETCH_A/);
  assertOrderedText(materialClause(description, 101), ["4 号槽", "101"]);
  assertOrderedText(materialClause(description, 202), ["8 号槽", "202"]);
  assert.doesNotMatch(description, /清洁/);
});

test("有片和无片清洁均保留 CleanTask、配方及最后清洁动作标记", () => {
  for (const materials of [[101], []]) {
    const description = describeMove({
      MoveType: 9, ModuleName: "PM1", MatIDList: materials,
      SlotList: materials.length ? [4] : [],
      ProcessRecipe: "CLEAN_A", CleanTaskName: "WAC_7", IsLastCleanTaskMove: true,
    });
    assert.match(description, /清洁/);
    assert.match(description, /CLEAN_A/);
    assert.match(description, /WAC_7/);
    assert.match(description, /最后|结束/);
  }
});

test("CleanTaskName 无需带 clean 或 WAC 字样，也能明确标识清洁", () => {
  const description = describeMove({
    MoveType: 9, ModuleName: "PM1", MatIDList: [101], SlotList: [4],
    ProcessRecipe: "R7", CleanTaskName: "task-7",
  });
  assert.match(description, /清洁/);
  assert.match(description, /task-7/);
  assert.match(description, /R7/);
});

test("PrePrepare 根据 Pump 和 Vent 类型解释抽气与充气，并显示前后状态", () => {
  const pump = describeMove({
    MoveType: 10, ModuleName: "LL1", PrePrepareType: "Pump", LastState: "ATM", CurState: "VAC",
  });
  assert.match(pump, /抽气/);
  assertOrderedText(pump, ["ATM", "VAC"]);
  const vent = describeMove({
    MoveType: 10, ModuleName: "LL1", PrePrepareType: "Vent", LastState: "VAC", CurState: "ATM",
  });
  assert.match(vent, /充气|放气/);
  assertOrderedText(vent, ["VAC", "ATM"]);
});

test("PrePrepare 未知转换保留原类型和状态，不从未知状态猜测 Pump 或 Vent", () => {
  const description = describeMove({
    MoveType: 10, ModuleName: "LL1", PrePrepareType: "CustomTransition", LastState: "LEFT", CurState: "RIGHT",
  });
  assert.match(description, /CustomTransition/);
  assertOrderedText(description, ["LEFT", "RIGHT"]);
  assert.doesNotMatch(description, /抽气|充气|放气/);
});

test("历史 Pump 和 Vent MoveType 仍提供抽气与充气说明", () => {
  assert.match(describeMove({ MoveType: 12, ModuleName: "LL1" }), /抽气/);
  assert.match(describeMove({ MoveType: 13, ModuleName: "LL1" }), /充气/);
});

test("Align 说明对应模块和物料的对准动作，不编造槽位", () => {
  const description = describeMove({ MoveType: 11, ModuleName: "AL1", MatIDList: [101] });
  assert.match(description, /对准|校准/);
  assert.match(description, /AL1/);
  assert.match(description, /101/);
  assert.doesNotMatch(description, /undefined|null|NaN|槽.*0/);
});

test("缺失或非数组字段给出可读说明，不复制第一项填补第二片的位置", () => {
  for (const raw of [null, undefined, {}, { MoveType: 0 }, { MoveType: 9, MatIDList: "bad" }]) {
    const description = describeMove(raw);
    assert.equal(typeof description, "string");
    assert.ok(description.length > 0);
    assert.doesNotMatch(description, /undefined|null|NaN|\[object Object\]/);
  }
  const missingLocation = describeMove({
    MoveType: 2, ModuleName: "VTR", MatIDList: [101, 202],
    SrcStationList: ["PM1"], SrcSlotList: [4], RobotSlotList: [3, 6],
  });
  assert.doesNotMatch(materialClause(missingLocation, 202), /PM1/);
});

test("Pick 的站点 SlotList 不冒充缺失的 RobotSlotList", () => {
  const description = describeMove({
    MoveType: 0, ModuleName: "ATR", MatIDList: [101],
    SrcStationList: ["LP1"], SrcSlotList: [4], SlotList: [9],
  });
  assert.match(description, /4 号槽/);
  assert.match(description, /手槽未提供/);
  assert.doesNotMatch(description, /9 号手槽/);
});

test("多片字段空项保持下标，后续晶圆不会被借用来填补未知位置", () => {
  const description = describeMove({
    MoveType: 2, ModuleName: "VTR", MatIDList: [101, 202, 303],
    SrcStationList: ["PM1", "", "PM3"], SrcSlotList: [4, 8, 9], RobotSlotList: [3, "", 7],
  });
  const missing = materialClause(description, 202);
  assert.match(missing, /8 号槽/);
  assert.match(missing, /手槽未提供/);
  assert.doesNotMatch(missing, /PM1|PM3|3 号手槽|7 号手槽/);
  assertOrderedText(materialClause(description, 303), ["PM3", "9 号槽", "7 号手槽"]);
});

test("解释只读取 Move，不修改输入对象或嵌套列表", () => {
  const move = swapMove(1);
  const original = JSON.stringify(move);
  for (const value of Object.values(move)) {
    if (Array.isArray(value)) Object.freeze(value);
  }
  Object.freeze(move);
  assert.doesNotThrow(() => describeMove(move));
  assert.equal(JSON.stringify(move), original);
});

test("未知 MoveType 保留动作类型数值，不套用已知取放语义", () => {
  const description = describeMove({ MoveType: 23, ModuleName: "CUSTOM" });
  assert.match(description, /23/);
  assert.match(description, /CUSTOM/);
  assert.doesNotMatch(description, /取片|放片|换片|加工|清洁|对准/);
});
