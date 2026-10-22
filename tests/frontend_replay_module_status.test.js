/** 回放工艺颜色按清洁动作语义判断，不根据普通产品配方的名称猜测。 */
"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const {buildWorkspaceSnapshot}=require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);

/** 最小带片 Process，保留标准 CleanTaskName 空值与可覆写历史字段。 */
function processStatus(fields={}) {
  const move={MoveID:1,MoveType:9,ModuleName:"PM1",MatIDList:[1],CleanTaskName:"",StartTime:10,EndTime:20,...fields};
  return buildWorkspaceSnapshot([move],{Stations:{PM1:{Type:"ProcessChamber",Capacity:1}},Robots:{}},15).modules[0];
}

test("产品配方名含 clean、wac、dummy 仍显示加工，包括旧记录",()=>{
  for (const recipe of ["ProductCleanRecipe","WAC_Process","DummyComparison"]) {
    assert.equal(processStatus({ProcessRecipe:recipe}).status,"processing");
    assert.equal(processStatus({RecipeName:recipe,CleanTaskName:undefined}).status,"processing");
  }
});

test("标准空 CleanTaskName 优先于历史配方字段，不把产品染为清洁",()=>{
  assert.equal(processStatus({CleanRecipe:"WacClean",ProcessRecipe:"ProductCleanRecipe"}).status,"processing");
});

test("带片清洁的正式任务名不要求包含英文 clean 字样",()=>{
  const module=processStatus({CleanTaskName:"前置维护A",ProcessRecipe:"RecipeA"});
  assert.equal(module.status,"cleaning");
  assert.equal(module.activeMoveName,"清洁");
});

test("显式空腔、旧 Clean Move 和旧专用清洁字段继续显示清洁",()=>{
  assert.equal(processStatus({MatIDList:[]}).status,"cleaning");
  assert.equal(processStatus({MoveType:14}).status,"cleaning");
  const move={MoveID:1,MoveType:9,ModuleName:"PM1",MatIDList:["D1"],CleanRecipe:"RecipeA",StartTime:10,EndTime:20};
  assert.equal(buildWorkspaceSnapshot([move],null,15).modules.find(module=>module.name==="PM1").status,"cleaning");
});
