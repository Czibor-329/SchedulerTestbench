/** 对象信息契约：稳定实例、明确事件、已知诊断原因与安全文本。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { replayObjectKey, replayObjectIsCurrent, replayObjectEvents, replayActionMatchesObject, replayObjectGanttUrl,
  projectReplayObjectDetails, renderReplayObjectDetails, applyReplayObjectSelection,
  ReplayObjectInspectorController } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

/** 构造单腔完成边界及工艺证据，显式指出槽位，不依赖实际数据集。 */
function input(time = 5) {
  return {device:{Stations:{PM1:{},LP1:{Type:"LoadPort"}},Robots:{VTR:{}}},
    moves:[{MoveID:1,MoveType:1,ModuleName:"VTR",StartTime:0,EndTime:1,MatIDList:[1],DestStationList:["PM1"],DestSlotList:[1],TaskID:["T"],PJobName:["P"]},
      {MoveID:2,MoveType:9,ModuleName:"PM1",StartTime:1,EndTime:3,MatIDList:[1],SlotList:[1],TaskID:["T"],PJobName:["P"]},
      {MoveID:3,MoveType:0,ModuleName:"VTR",StartTime:8,EndTime:9,MatIDList:[1],SrcStationList:["PM1"],SrcSlotList:[1],TaskID:["T"],PJobName:["P"]}],
    snapshot:{time,activeMoves:[],modules:[{name:"PM1",status:"occupied",door:"closed",wafers:["1"],processedWafers:["1"],
      loadPortSlots:[],loadLockSlots:[],processSlots:[{slot:1,wafer:"1",processed:true}],slotCapacity:1}],robots:[],waferOrigins:{1:"LP1.1"}}};
}

test("晶圆归库后对象卡和槽位卡不显示下一站", () => {
  const data = input();
  data.moves = [{MoveType:1,StartTime:3,EndTime:4,MatIDList:[1],DestStationList:["LP1"],TaskID:["T"],PJobName:["P"]}];
  Object.assign(data.snapshot.modules[0],{name:"LP1",type:"LoadPort",loadPortSlots:[{slot:1,wafer:"1"}]});
  for (const selection of [{kind:"wafer",wafer:"1"},{kind:"slot",name:"LP1",slot:1}]) {
    const details = projectReplayObjectDetails(data,selection);
    assert.equal(details.fields.some(field=>field.label === "下一站"),false);
  }
});

test("物料key与当前位置无关，槽位key区分真实模块和槽号", () => {
  assert.equal(replayObjectKey({kind:"wafer",wafer:"1",instanceKey:"T",name:"PM1"}),replayObjectKey({kind:"wafer",wafer:"1",instanceKey:"T",name:"VTR"}));
  assert.notEqual(replayObjectKey({kind:"wafer",wafer:"1",instanceKey:"T"}),replayObjectKey({kind:"wafer",wafer:"1",instanceKey:"U"}));
  assert.notEqual(replayObjectKey({kind:"slot",name:"PM1",slot:1}),replayObjectKey({kind:"slot",name:"PM1",slot:2}));
});

test("对象事件按实际开始结束边界定位，未选对象不受其它资源事件干扰", () => {
  const data = input();
  data.moves.push({MoveID:4,MoveType:9,ModuleName:"PM2",StartTime:4,EndTime:6,MatIDList:[2]});
  const events = replayObjectEvents(data,{kind:"module",name:"PM1"});
  assert.equal(events.previousEvent.time,3);
  assert.equal(events.nextEvent.time,8);
  data.snapshot.time = 3;
  assert.equal(replayObjectEvents(data,{kind:"module",name:"PM1"}).previousEvent.time,1);
  assert.equal(replayObjectEvents(data,{kind:"slot",name:"PM1",slot:2}).events.length,0);
});

test("卡片给出有证据的加工完成等待，不编造等待原因", () => {
  const details = projectReplayObjectDetails(input(),{kind:"module",name:"PM1"});
  const waiting = details.fields.find(field=>field.label === "待取片");
  assert.equal(waiting.value,"LP1.1 · 已等待 2.0 s");
  assert.equal(details.fields.some(field=>field.label === "已知拦截"),false);
  assert.equal(details.fields.find(field=>field.label === "动作诊断").value,"查询未开启或未加载");
});

test("关联动作复用算法原因和完成边界，槽位按端点匹配", () => {
  const data = input();
  const action = {actionId:"pick",robot:"VTR",source:"PM1",sourceSlot:1,destination:"VTR",destinationSlot:2,
    materialIds:["1"],status:"physical-blocked",reason:"门未开启"};
  data.decision = {time:3,actionDiagnosticsSource:"algorithm",actionDiagnostics:[action]};
  const details = projectReplayObjectDetails(data,{kind:"module",name:"PM1"});
  assert.equal(details.fields.find(field=>field.label === "已知拦截").value,"门未开启");
  assert.match(details.fields.find(field=>field.label === "诊断时刻").value,/3.0 s/);
  assert.equal(replayActionMatchesObject(action,{kind:"slot",name:"PM1",slot:1}),true);
  assert.equal(replayActionMatchesObject(action,{kind:"slot",name:"PM1",slot:2}),false);
});


test("卡片安全转义并提供精确甘特定位参数，缺结果隐藏链接", () => {
  const details = projectReplayObjectDetails(input(),{kind:"module",name:"PM1"});
  details.title = '<img src=x onerror=alert(1)>';
  details.fields.push({label:"原因",value:'<script>bad</script>'});
  assert.doesNotMatch(renderReplayObjectDetails(details),/<script>|<img/);
  const url = replayObjectGanttUrl("/r?a=1&b=2",{kind:"wafer",wafer:"1"},5,3);
  const parameters = new URL(url,"http://localhost").searchParams;
  assert.equal(parameters.get("src"),"/r?a=1&b=2");
  assert.equal(parameters.get("wafer"),"1");
  assert.equal(parameters.get("moveId"),"3");
  assert.equal(replayObjectGanttUrl("",{kind:"module",name:"PM1"},5),"");
});

test("补片复用后旧实例选择保持未在机，不显示新片下一站", () => {
  const data = input(12);
  data.moves.push({MoveID:4,MoveType:0,StartTime:10,EndTime:11,MatIDList:[1],TaskID:["new"],PJobName:["P"]});
  const details = projectReplayObjectDetails(data,{kind:"wafer",wafer:"1",instanceKey:JSON.stringify(["1","T","P"])});
  assert.equal(details.fields.find(field=>field.label === "当前位置").value,"当前实例未在机");
  assert.equal(details.fields.find(field=>field.label === "下一站").value,"未知");
  const events = replayObjectEvents(data,details.selection);
  assert.equal(events.events.some(event=>event.moveId === 4),false);
});

test("当前实例谓词严格区分Task和PJob，资源对象及缺实例键选择兼容当前", () => {
  const data = input();
  const selection = {kind:"wafer",wafer:"1",instanceKey:JSON.stringify(["1","T","P"])};
  assert.equal(replayObjectIsCurrent(data,selection),true);
  assert.equal(replayObjectIsCurrent(data,{...selection,instanceKey:JSON.stringify(["1","T","other"])}),false);
  assert.equal(replayObjectIsCurrent(data,{...selection,instanceKey:JSON.stringify(["1","other","P"])}),false);
  assert.equal(replayObjectIsCurrent(data,{kind:"wafer",wafer:"1"}),true);
  assert.equal(replayObjectIsCurrent(data,{kind:"wafer"}),false);
  for (const kind of ["module","robot","slot"]) assert.equal(replayObjectIsCurrent(data,{kind,name:"PM1",slot:1}),true);
  // 当前代已换片，但新片尚未 Pick：不能借上一片的已开始动作继续认定旧实例当前。
  data.generations = [{time:4,moves:[],plan:{Materials:[{ID:1,TaskID:"next",PJobName:"P",CurrentModuleName:"PM1"}]}}];
  assert.equal(replayObjectIsCurrent(data,selection),false);
});

test("复用MatID的旧实例卡屏蔽当前诊断，保留旧实例历史事件", () => {
  const data = input(12);
  data.moves = [...data.moves,{MoveID:4,MoveType:1,ModuleName:"VTR",StartTime:10,EndTime:11,
    MatIDList:[1],DestStationList:["PM1"],DestSlotList:[1],TaskID:["next"],PJobName:["P"]}];
  data.snapshot.activeMoves = [{MoveID:5,MoveType:9,ModuleName:"PM1",StartTime:11,EndTime:15,
    MatIDList:[1],SlotList:[1],TaskID:["next"],PJobName:["P"]}];
  data.decision = {time:11,actionDiagnosticsSource:"algorithm",actionDiagnostics:[{actionId:"new-pick",robot:"VTR",
    source:"PM1",sourceSlot:1,destination:"VTR",destinationSlot:1,materialIds:["1"],status:"physical-blocked",reason:"新片门未开启"}]};
  data.resultUrl = "/api/results/reuse";
  const oldSelection = {kind:"wafer",wafer:"1",instanceKey:JSON.stringify(["1","T","P"])};
  const oldDetails = projectReplayObjectDetails(data,oldSelection);
  assert.equal(oldDetails.fields.find(field=>field.label==="当前位置").value,"当前实例未在机");
  assert.equal(oldDetails.fields.find(field=>field.label==="下一站").value,"未知");
  assert.equal(oldDetails.fields.find(field=>field.label==="Task / PJob").value,"T / P");
  assert.equal(oldDetails.fields.find(field=>field.label==="动作诊断").value,"所选实例未在机，当前动作不属于该实例");
  assert.deepEqual(oldDetails.relatedActions,[]);
  for (const label of ["进行中","待取片","关联动作","已知拦截","诊断时刻"]) {
    assert.equal(oldDetails.fields.some(field=>field.label===label),false,label);
  }
  assert.equal(oldDetails.previousEvent.moveId,3);
  assert.equal(oldDetails.nextEvent,null);
  assert.equal(new URL(oldDetails.ganttUrl,"http://localhost").searchParams.get("moveId"),"3");
  // 同一帧的新实例必须仍展示自己的动作，不能全局取消诊断。
  const newDetails = projectReplayObjectDetails(data,{...oldSelection,instanceKey:JSON.stringify(["1","next","P"])});
  assert.equal(newDetails.fields.find(field=>field.label==="当前位置").value,"PM1.1");
  assert.equal(newDetails.fields.find(field=>field.label==="已知拦截").value,"新片门未开启");
  assert.equal(newDetails.relatedActions.length,1);
  assert.match(newDetails.fields.find(field=>field.label==="进行中").value,/加工/);
});

test("晶圆选择联动正视槽位，DOM重建仍使用稳定实例", () => {
  const data = input();
  const nodes = [{replayKind:"wafer",replayWafer:"1"},{replayKind:"slot",replayName:"PM1",replaySlot:"1"},
    {replayKind:"slot",replayName:"PM1",replaySlot:"2"}].map(dataset=>({dataset,classList:{contains(){return false;},toggle(name,value){if(name === "is-replay-selected") this.selected=value;}},getAttribute(){return null;}}));
  const root = {querySelectorAll(){return nodes;}};
  const selection = {kind:"wafer",wafer:"1",instanceKey:JSON.stringify(["1","T","P"])};
  applyReplayObjectSelection(root,data,selection);
  assert.deepEqual(nodes.map(node=>node.classList.selected),[true,true,false]);
  data.moves = [...data.moves,{MoveID:4,StartTime:4,EndTime:5,MatIDList:[1],TaskID:["new"],PJobName:["P"]}];
  applyReplayObjectSelection(root,data,selection);
  assert.deepEqual(nodes.map(node=>node.classList.selected),[false,false,false]);
});

test("晶圆仅突出明确下一落站和当前位置，候选与清除选择恢复无目标", () => {
  const data=input();
  data.moves=[...data.moves,{MoveType:1,StartTime:10,EndTime:11,MatIDList:[1],DestStationList:["LP1"],TaskID:["T"],PJobName:["P"]}];
  const nodes=["PM1","LP1"].map(name=>({dataset:{replayKind:"module",replayName:name},
    classList:{values:{},toggle(name,value){this.values[name]=value;}},getAttribute(){return null;}}));
  const root={querySelectorAll(){return nodes;}};
  applyReplayObjectSelection(root,data,{kind:"wafer",wafer:"1",instanceKey:JSON.stringify(["1","T","P"])});
  assert.equal(nodes[0].classList.values["is-replay-current-station"],true);
  assert.equal(nodes[1].classList.values["is-replay-next-target"],true);
  applyReplayObjectSelection(root,data,null);
  assert.equal(nodes[0].classList.values["is-replay-current-station"],false);
  assert.equal(nodes[1].classList.values["is-replay-next-target"],false);
});



test("点击正视槽位联动俯视晶圆，槽位换片后跟随当前占片",()=>{
  const data=input();
  const nodes=[{replayKind:"slot",replayName:"PM1",replaySlot:"1"},
    {replayKind:"wafer",replayWafer:"1"},{replayKind:"wafer",replayWafer:"2"}].map(dataset=>({dataset,
    classList:{values:{},contains(){return false;},toggle(name,value){this.values[name]=value;}},getAttribute(){return null;}}));
  const root={querySelectorAll(){return nodes;}};
  const selection={kind:"slot",name:"PM1",slot:1};
  applyReplayObjectSelection(root,data,selection);
  assert.deepEqual(nodes.map(node=>node.classList.values["is-replay-selected"]),[true,true,false]);
  data.snapshot.modules[0].processSlots[0].wafer="2";
  applyReplayObjectSelection(root,data,selection);
  assert.deepEqual(nodes.map(node=>node.classList.values["is-replay-selected"]),[true,false,true]);
});

test("双腔当前位置和下一落站按真实槽位突出，不选中邻腔",()=>{
  const data=input();
  data.moves=[...data.moves,{MoveType:1,StartTime:10,EndTime:11,MatIDList:[1],DestStationList:["PM2"],DestSlotList:[2],TaskID:["T"],PJobName:["P"]}];
  const nodes=[["PM1",1],["PM1",2],["PM2",1],["PM2",2]].map(([name,slot])=>({dataset:{replayKind:"slot",replayName:name,replaySlot:String(slot)},
    classList:{values:{},contains(name){return name==="reference-module-position";},toggle(name,value){this.values[name]=value;}},getAttribute(){return null;}}));
  applyReplayObjectSelection({querySelectorAll(){return nodes;}},data,{kind:"wafer",wafer:"1",instanceKey:JSON.stringify(["1","T","P"])});
  assert.deepEqual(nodes.map(node=>node.classList.values["is-replay-current-station"]),[true,false,false,false]);
  assert.deepEqual(nodes.map(node=>node.classList.values["is-replay-next-target"]),[false,false,false,true]);
});

test("controller每帧保留选择，清除关联筛选与Escape使用同一状态", () => {
  const handlers = {};
  const root = {addEventListener(type,handler){handlers[type]=handler;},querySelectorAll(){return [];}};
  const panel = {hidden:true,dataset:{},ownerDocument:{activeElement:null},contains(){return false;}};
  const filters = [];
  const controller = new ReplayObjectInspectorController({root,panel,onSeek(){},onFilter:value=>filters.push(value)});
  controller.update(input());
  controller.select({kind:"module",name:"PM1"});
  assert.equal(panel.hidden,false);
  controller.update(input(6));
  assert.equal(controller.selection.name,"PM1");
  let stopped = false;
  handlers.keydown({key:"Escape",preventDefault(){},stopPropagation(){stopped=true;}});
  assert.equal(controller.selection,null);
  assert.equal(panel.hidden,true);
  assert.equal(stopped,true);
});
