/** 已发布日志与保存结果的保守代历史，不把后代最终目标投射到过去。 */
"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const {replayLogContext,replayCommittedGenerations,projectReplayWaferDestinations}=require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

test("标准日志保留各代Route与MoveList，只从Init补第一代设备且输入独立",()=>{
  const topology={Stations:{PM1:{Type:"ProcessChamber"}},Robots:{VTR:{}}};
  const entries=[{Describe:"AlgSchedule",SimTime:0,Info:{Materials:[{ID:1,TaskID:"T"}],CurrentTime:0}},
    {Describe:"AlgOutput",Info:{MoveList:[{MoveType:1,StartTime:20,EndTime:21,MatIDList:[1],DestStationList:["PM1"]}]}},
    {Describe:"AlgSchedule",SimTime:10,Info:{CurrentTime:10,Stations:{PM1:{State:"new"}}}},
    {Describe:"AlgOutput",Info:{MoveList:[{MoveType:1,StartTime:20,EndTime:21,MatIDList:[1],DestStationList:["PM2"]}]}}];
  const result=replayLogContext(entries,topology);
  assert.deepEqual(result.generations.map(item=>item.time),[0,10]);
  assert.equal(result.updates[0].Stations.PM1.Type,"ProcessChamber");
  assert.equal(result.updates[1].Stations.PM1.State,"new");
  assert.equal(result.plan.device.Stations.PM1.Type,"ProcessChamber");
  entries[0].Info.Materials[0].TaskID="changed";
  assert.equal(result.generations[0].plan.Materials[0].TaskID,"T");
  const snapshot={time:5,activeMoves:[],modules:[],robots:[{name:"VTR",wafers:["1"]}],waferOrigins:{}};
  const input={snapshot,moves:result.generations[1].moves,device:{Robots:{VTR:{}}},generations:result.generations};
  assert.equal(projectReplayWaferDestinations(input).get("1").label,"PM1");
  snapshot.time=15;
  assert.equal(projectReplayWaferDestinations(input).get("1").label,"PM2");
});

test("同刻重新发布替换原代，非法协议Info不作为计划",()=>{
  const output=target=>({Describe:"AlgOutput",Info:{MoveList:[{DestStationList:[target]}]}});
  const result=replayLogContext([{Describe:"AlgSchedule",SimTime:5,Info:{CurrentTime:5}},output("PM1"),output("PM2"),
    {Describe:"AlgSchedule",Info:"invalid"}],{Stations:{},Robots:{}});
  assert.equal(result.generations.length,1);
  assert.equal(result.generations[0].moves[0].DestStationList[0],"PM2");
  assert.equal(result.updates.length,1);
});

test("保存结果无AlgOutput时旧代只承诺重算前开始的动作，末代保留未来",()=>{
  const updates=[{CurrentTime:10,Materials:[]},{CurrentTime:0,Materials:[]},{CurrentTime:10,Materials:[{ID:1,TaskID:"new"}]}];
  const moves=[{MoveID:1,StartTime:2,EndTime:3,MatIDList:[1],MoveType:0},
    {MoveID:2,StartTime:12,EndTime:13,MatIDList:[1],MoveType:1,DestStationList:["PM2"]},
    {MoveID:3,StartTime:20,EndTime:21,MatIDList:[2],MoveType:1,DestStationList:["PM1"]}];
  const generations=replayCommittedGenerations(updates,moves);
  assert.deepEqual(generations.map(item=>item.time),[0,10]);
  assert.deepEqual(generations[0].moves.map(move=>move.MoveID),[1]);
  assert.deepEqual(generations[1].moves.map(move=>move.MoveID),[2,3]);
  assert.equal(generations[1].plan.Materials[0].TaskID,"new");
  const snapshot={time:5,activeMoves:[],modules:[],robots:[{name:"VTR",wafers:["1"]}],waferOrigins:{}};
  const input={snapshot,moves,device:{Robots:{VTR:{}}},generations};
  assert.equal(projectReplayWaferDestinations(input).get("1").label,"未知");
  snapshot.time=11;
  assert.equal(projectReplayWaferDestinations(input).get("1").label,"PM2");
  generations[1].moves[0].DestStationList[0]="changed";
  assert.equal(moves[1].DestStationList[0],"PM2");
});

test("前一代已经开始的搬运动作仍从活动快照确认，未来最终计划不能代替它",()=>{
  const active={MoveType:1,MoveID:1,StartTime:8,EndTime:12,MatIDList:[1],DestStationList:["PM1"]};
  const future={MoveType:1,MoveID:2,StartTime:20,EndTime:21,MatIDList:[1],DestStationList:["PM2"]};
  const snapshot={time:10,activeMoves:[active],modules:[],robots:[{name:"VTR",wafers:["1"]}],waferOrigins:{}};
  const generations=replayCommittedGenerations([{CurrentTime:0},{CurrentTime:10}],[active,future]);
  assert.equal(projectReplayWaferDestinations({snapshot,moves:[active,future],device:{Robots:{VTR:{}}},generations}).get("1").label,"PM1");
});
