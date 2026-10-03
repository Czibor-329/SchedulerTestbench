/** 注释层契约：本站等待与下一站文本；重复刷新不污染硬件节点。 */
"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const {annotateReplayTopology}=require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);
const {buildReplayAnnotationStage}=require("./support/replay_annotation_dom");

/** 工艺完成事实与整Route完成色分开，当前仍驻片且没有加工中的Move。 */
function replayInput(time=5,moduleName="PM1") {
  const module={name:moduleName,status:"occupied",wafers:["1"],processedWafers:[],slotCapacity:6,loadPortSlots:[],loadLockSlots:[]};
  return {moves:[{MoveType:9,StartTime:1,EndTime:3,ModuleName:moduleName,MatIDList:[1]}],device:null,
    snapshot:{time,activeMoves:[],modules:[module],robots:[],waferOrigins:{1:"LP1.1"}}};
}


test("第二行只有下一station名称，反复刷新不重复、源标签保持",()=>{
  const fixture=buildReplayAnnotationStage();
  const input=replayInput();
  const destinations=new Map([["1",{label:"LongStationName",station:"LongStationName",candidates:[]}] ]);
  annotateReplayTopology(fixture.stage,input.snapshot,destinations,input);
  annotateReplayTopology(fixture.stage,input.snapshot,destinations,input);
  const labels=fixture.stage.querySelectorAll(".wafer-next-station");
  assert.equal(labels.length,1); assert.equal(labels[0].textContent,"LongStationName");
  assert.equal(fixture.origin.textContent,"LP1.1");
  assert.equal(fixture.body.dataset.replayWaiting,"true");
  assert.match(fixture.body.title,/等待取片 2.0 s/);
  assert.equal(fixture.stage.querySelectorAll(".topology-waiting-indicator").length,1);
  assert.deepEqual(fixture.body.style.values,{});
});

test("倒放到工艺结束前清除等待标记，取片中和缺证据不装作已完成",()=>{
  const fixture=buildReplayAnnotationStage();
  const input=replayInput();
  annotateReplayTopology(fixture.stage,input.snapshot,new Map(),input);
  assert.equal(fixture.body.dataset.replayWaiting,"true");
  input.snapshot.time=2;
  annotateReplayTopology(fixture.stage,input.snapshot,new Map(),input);
  assert.equal(fixture.body.dataset.replayWaiting,"false"); assert.equal(fixture.body.title,"");
  assert.equal(fixture.stage.querySelectorAll(".topology-waiting-indicator").length,0);
  input.snapshot.time=5; input.snapshot.modules[0].status="transfer";
  annotateReplayTopology(fixture.stage,input.snapshot,new Map(),input);
  assert.equal(fixture.body.dataset.replayWaiting,"false");
  annotateReplayTopology(fixture.stage,input.snapshot,new Map());
  assert.equal(fixture.body.dataset.replayWaiting,"false");
});

test("重复刷新不添加Cooler槽位数字",()=>{
  const fixture=buildReplayAnnotationStage("Cooler",true);
  const input=replayInput(5,"Cooler");
  annotateReplayTopology(fixture.stage,input.snapshot,new Map(),input);
  annotateReplayTopology(fixture.stage,input.snapshot,new Map(),input);
  assert.equal(fixture.stage.querySelectorAll(".cooler-slot-occupancy").length,0);
  annotateReplayTopology(fixture.stage,input.snapshot,new Map(),input);
});
