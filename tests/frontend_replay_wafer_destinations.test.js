/** 下一落站契约：只读当前计划，区分候选、未知、实例与 Swap 收发。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { projectReplayWaferDestinations, replayWaferWaitingSeconds } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

/** 用最小完成边界快照明确站点、占片与物料来源，避免真实设备夹具。 */
function input(moves, time = 5, location = "VTR", wafers = ["1"], extra = {}) {
  const module = {name: location, wafers, processedWafers: [], loadPortSlots: [], loadLockSlots: [], slotCapacity: 1};
  return { moves, device: {Robots:{VTR:{},ATR:{}},Stations:{PM1:{},PM2:{},LP1:{Type:"LoadPort"}}},
    snapshot: {time,activeMoves:[],modules:location === "VTR" ? [] : [module],
      robots:location === "VTR" ? [module] : [],waferOrigins:{1:"LP1.1"}}, ...extra };
}

test("Place明确落站，PreTrans不改变目标且输出只有站点名", () => {
  const moves = [
    {MoveType:0,MoveID:1,StartTime:0,EndTime:1,ModuleName:"VTR",MatIDList:[1],SrcStationList:["LP1"],StepIDList:[1]},
    {MoveType:5,MoveID:2,StartTime:6,EndTime:7,ModuleName:"VTR",MatIDList:[1],DestStationList:["PM2"]},
    {MoveType:1,MoveID:3,StartTime:7,EndTime:8,ModuleName:"VTR",MatIDList:[1],DestStationList:["PM1"],StepIDList:[2]},
  ];
  const destination = projectReplayWaferDestinations(input(moves)).get("1");
  assert.equal(destination.station,"PM1");
  assert.equal(destination.label,"PM1");
  assert.equal(destination.status,"confirmed");
});

test("代历史不读取未来重算目标，倒放恢复当时计划", () => {
  const first = [{MoveType:1,MoveID:1,StartTime:20,EndTime:21,MatIDList:[1],DestStationList:["PM1"]}];
  const second = [{MoveType:1,MoveID:2,StartTime:20,EndTime:21,MatIDList:[1],DestStationList:["PM2"]}];
  const data = input(second,5,"VTR",["1"],{generations:[{time:0,moves:first},{time:10,moves:second}]});
  assert.equal(projectReplayWaferDestinations(data).get("1").label,"PM1");
  data.snapshot.time = 11;
  assert.equal(projectReplayWaferDestinations(data).get("1").label,"PM2");
  data.snapshot.time = 5;
  assert.equal(projectReplayWaferDestinations(data).get("1").label,"PM1");
});

test("Route穿过RobotStep列候选，单一候选也不是已确定目标", () => {
  const moves = [{MoveType:9,StartTime:0,EndTime:1,ModuleName:"PM1",MatIDList:[1],StepIDList:[2],PJobName:["job"]}];
  const route = {RouteSteps:[{StepID:2,PostStepID:[3],Visits:[{StationName:"PM1"}]},
    {StepID:3,PostStepID:[4],Visits:[{StationName:"VTR"}]},
    {StepID:4,PostStepID:[],Visits:[{StationName:"PM1"},{StationName:"PM2"}]}]};
  const data = input(moves,5,"PM1",["1"],{plan:{Materials:[{ID:1,Route:route}]}});
  let destination = projectReplayWaferDestinations(data).get("1");
  assert.equal(destination.label,"待定");
  assert.equal(destination.status,"pending");
  assert.deepEqual(destination.candidates,["PM1","PM2"]);
  route.RouteSteps[2].Visits = [{StationName:"PM2"}];
  destination = projectReplayWaferDestinations(data).get("1");
  assert.equal(destination.label,"待定");
  assert.deepEqual(destination.candidates,["PM2"]);
});

test("Route循环和部分计划终止保持未知，不猜目标", () => {
  const data = input([{MoveType:9,StartTime:0,EndTime:1,MatIDList:[1],StepIDList:[2],PJobName:["job"]}],5,"PM1",["1"],{
    resolveRoute:()=>({stages:[{stepId:2,postStepIds:[3],visits:[{stationName:"PM1"}]},
      {stepId:3,postStepIds:[3],visits:[{stationName:"VTR"}]}]})});
  const destination = projectReplayWaferDestinations(data).get("1");
  assert.equal(destination.label,"未知");
  assert.equal(destination.status,"unknown");
});

test("Swap只给送片确定落站，收片继续查后续Place且字段按物料对齐", () => {
  const moves = [
    {MoveType:0,StartTime:0,EndTime:1,MatIDList:[1,2],StepIDList:[1,1],TaskID:["old","new"],PJobName:["P1","P2"]},
    {MoveType:4,StartTime:6,EndTime:9,ModuleName:"VTR",RecvMatList:[1],SendMatList:[2],StationList:["PM1"],
      RecvMatStepIDList:[3],SendMatStepIDList:[4],TaskID:["old","new"],PJobName:["P1","P2"]},
    {MoveType:1,StartTime:10,EndTime:11,ModuleName:"VTR",MatIDList:[1],DestStationList:["PM2"],TaskID:["old"],PJobName:["P1"]},
  ];
  const destinations = projectReplayWaferDestinations(input(moves,5,"VTR",["1","2"]));
  assert.equal(destinations.get("1").label,"PM2");
  assert.equal(destinations.get("2").label,"PM1");
  assert.equal(destinations.get("2").taskId,"new");
});

test("补片复用MatID不借用后续Task目标，回港状态不当未知", () => {
  const moves = [
    {MoveType:0,StartTime:0,EndTime:1,MatIDList:[1],TaskID:["first"],PJobName:["P"]},
    {MoveType:1,StartTime:3,EndTime:4,MatIDList:[1],DestStationList:["LP1"],TaskID:["first"],PJobName:["P"]},
    {MoveType:0,StartTime:8,EndTime:9,MatIDList:[1],TaskID:["second"],PJobName:["P"]},
    {MoveType:1,StartTime:10,EndTime:11,MatIDList:[1],DestStationList:["PM2"],TaskID:["second"],PJobName:["P"]},
  ];
  const destination = projectReplayWaferDestinations(input(moves,5,"LP1")).get("1");
  assert.equal(destination.label,"已回港");
  assert.equal(destination.taskId,"first");
});

test("多片Place按下标对应不同站点", () => {
  const destinations = projectReplayWaferDestinations(input([
    {MoveType:3,StartTime:6,EndTime:7,MatIDList:[1,2],DestStationList:["PM1","PM2"]},
  ],5,"VTR",["1","2"]));
  assert.equal(destinations.get("1").label,"PM1");
  assert.equal(destinations.get("2").label,"PM2");
});

test("等待计时只采用同实例最近完成工艺，倒放和空槽不继承过去", () => {
  const data = input([{MoveType:9,StartTime:1,EndTime:3,ModuleName:"PM1",MatIDList:[1],TaskID:["T"]}],8,"PM1");
  data.snapshot.modules[0].processedWafers = ["1"];
  assert.equal(replayWaferWaitingSeconds(data,"1","PM1"),5);
  data.snapshot.time = 2;
  assert.equal(replayWaferWaitingSeconds(data,"1","PM1"),null);
  data.snapshot.time = 8;
  data.snapshot.modules[0].wafers = [];
  assert.equal(replayWaferWaitingSeconds(data,"1","PM1"),null);
});

test("本站完成等待不依赖整条Route加工完成，重入本站后不继承上次等待", () => {
  const moves = [{MoveType:9,StartTime:1,EndTime:3,ModuleName:"PM1",MatIDList:[1],TaskID:["T"]},
    {MoveType:1,StartTime:8,EndTime:9,MatIDList:[1],DestStationList:["PM1"],TaskID:["T"]}];
  const data = input(moves,5,"PM1");
  assert.deepEqual(data.snapshot.modules[0].processedWafers,[]);
  assert.equal(replayWaferWaitingSeconds(data,"1","PM1"),2);
  data.snapshot.time = 10;
  assert.equal(replayWaferWaitingSeconds(data,"1","PM1"),null);
});

test("下一站标签不提前推进进行中Pick的完成后Step", () => {
  const moves = [{MoveType:9,MoveID:1,StartTime:0,EndTime:1,MatIDList:[1],StepIDList:[2],ModuleName:"PM1"},
    {MoveType:0,MoveID:2,StartTime:4,EndTime:6,MatIDList:[1],StepIDList:[3],ModuleName:"VTR",SrcStationList:["PM1"]},
    {MoveType:1,MoveID:3,StartTime:7,EndTime:8,MatIDList:[1],StepIDList:[4],DestStationList:["PM2"]}];
  const destination = projectReplayWaferDestinations(input(moves,5,"PM1")).get("1");
  assert.equal(destination.stepId,"2");
  assert.equal(destination.label,"PM2");
});

test("新代补片快照在首个Pick前立即切换复用MatID实例", () => {
  const oldMoves = [{MoveType:1,StartTime:0,EndTime:1,MatIDList:[1],DestStationList:["LP1"],TaskID:["old"],PJobName:["P"]}];
  const newMoves = [{MoveType:1,StartTime:20,EndTime:21,MatIDList:[1],DestStationList:["PM2"],TaskID:["new"],PJobName:["P"]}];
  const data = input([...oldMoves,...newMoves],10,"LP1",["1"],{generations:[{time:0,moves:oldMoves},
    {time:10,moves:newMoves,plan:{Materials:[{ID:1,TaskID:"new",PJobName:"P",StepID:0,CurrentModuleName:"LP1",SlotID:1}]}}]});
  const destination = projectReplayWaferDestinations(data).get("1");
  assert.equal(destination.taskId,"new");
  assert.equal(destination.stepId,"0");
  assert.equal(destination.label,"PM2");
});
