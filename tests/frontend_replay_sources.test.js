/** 回放来源所有权契约：迟到的文件、结果和日志不得覆盖已经选定的新来源。 */
"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const {createVisualizationWorkspace}=require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);
const {replayWorkspaceDocument}=require("./support/replay_workspace_dom");

/** 外部响应由案例显式释放，避免用 sleep 推测异步加载顺序。 */
function deferred() {let resolve; const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
/** 来源测试只需一个可回放动作，设备与排程上下文在案例中独立冻结。 */
function result() {return {MoveList:[{MoveID:1,MoveType:9,ModuleName:"PM1",MatIDList:[1],StartTime:0,EndTime:10}],
  ReplayContext:{plan:{device:{Stations:{PM1:{Type:"ProcessChamber",Capacity:1}},Robots:{}},rounds:[],strategy:""},updates:[{CurrentTime:0,Stations:{},Robots:{},Materials:[],ProcessJobs:[]}]}};}
/** 模拟无数据的分析能力，不把性能分析耦合进来源测试。 */
function response(value) {return {ok:true,json:async()=>value};}

test("迟到的旧结果日志不覆盖后来已加载的结果来源",async()=>{
  const original=global.fetch;const log=deferred();const requested=deferred();
  global.fetch=async url=>{if(url==="/slow-log"){requested.resolve();return log.promise;}
    if(String(url).startsWith("/api/results/")) return response(result());
    return response({decision:{cleaning:{time:0,modules:[]}}});};
  try {const root=replayWorkspaceDocument();const workspace=createVisualizationWorkspace(root);
    const old=workspace.loadResult("old","旧结果","/slow-log");await requested.promise;
    await workspace.loadResult("new","新结果");log.resolve(response([]));await old;
    assert.equal(root.getElementById("visualSource").textContent,"新结果");
    assert.equal(root.getElementById("visualOpenGantt").href,"/movelist_gantt_viewer.html?src=%2Fapi%2Fresults%2Fnew");
    assert.equal(workspace.hasReplaySource,true);workspace.destroy();
  }finally{global.fetch=original;}
});

test("清空工作台会撤销尚未完成的文件加载与来源所有权",async()=>{
  const original=global.fetch;global.fetch=async()=>response({});
  try {const root=replayWorkspaceDocument();const workspace=createVisualizationWorkspace(root);const file=deferred();
    const loading=workspace.loadFile({name:"旧文件",text:()=>file.promise});
    assert.equal(workspace.hasReplaySource,true);workspace.clear();file.resolve(JSON.stringify(result()));await loading;
    assert.equal(workspace.hasReplaySource,false);assert.equal(root.getElementById("visualTopologyPlayback").hidden,true);
    workspace.destroy();
  }finally{global.fetch=original;}
});

test("新文件来源优先于迟到的旧结果响应",async()=>{
  const original=global.fetch;const server=deferred();global.fetch=async url=>String(url).startsWith("/api/results/")?server.promise:response({});
  try {const root=replayWorkspaceDocument();const workspace=createVisualizationWorkspace(root);
    const loading=workspace.loadResult("old","旧结果");await workspace.loadFile({name:"新文件",text:async()=>JSON.stringify(result())});
    server.resolve(response(result()));await loading;
    assert.equal(root.getElementById("visualSource").textContent,"新文件");workspace.destroy();
  }finally{global.fetch=original;}
});
