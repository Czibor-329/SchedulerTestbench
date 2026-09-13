/** 验证发片事件、倒放和 Swap 步骤对应关系。 */
const test = require('node:test');
const assert = require('node:assert/strict');
const { waferDispatchProgress } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);
test('离港完成才入列，倒放不保留未来，dummy 重复离港保留全局顺序', () => {
  const moves = [
    {MoveID: 1, MoveType: 0, EndTime: 2, MatIDList: [1,100000], SrcStationList: ['LP1','DummyPort'], StepIDList: [0,0]},
    {MoveID: 2, MoveType: 9, EndTime: 4, MatIDList: [1], StepIDList: [7]},
    {MoveID: 3, MoveType: 0, EndTime: 6, MatIDList: [100000], SrcStationList: ['DummyPort'], StepIDList: [1]},
  ];
  assert.equal(waferDispatchProgress(moves,1,null).departures.length,0);
  const later = waferDispatchProgress(moves,6,null);
  assert.deepEqual(later.departures.map(row=>row.wafer),['1','100000','100000']);
  assert.equal(later.departures[2].cycle,2);
  assert.equal(later.progress.get('1'),'7');
  assert.equal(waferDispatchProgress(moves,2,null).progress.get('1'),'0');
});
test('Swap 收发片使用各自步骤，库存站发片按设备类型识别', () => {
  const data = waferDispatchProgress([{MoveID:1,MoveType:4,EndTime:1,RecvMatList:[8],SendMatList:[9],StationList:['Input'],RecvMatStepIDList:[2],SendMatStepIDList:[5]}],1,{Stations:{Input:{Type:'LoadPort'}}});
  assert.equal(data.departures[0].wafer,'8');
  assert.equal(data.progress.get('8'),'2');
  assert.equal(data.progress.get('9'),'5');
});
test('补片复用 MatID 时按 TaskID 隔离步骤', () => {
  const data = waferDispatchProgress([
    {MoveID:1,MoveType:0,EndTime:1,MatIDList:[1],SrcStationList:['LP1'],StepIDList:[8],TaskID:[10]},
    {MoveID:2,MoveType:0,EndTime:2,MatIDList:[1],SrcStationList:['LP1'],StepIDList:[0],TaskID:[20]},
  ],2,null);
  assert.notEqual(data.departures[0].key,data.departures[1].key);
  assert.equal(data.progress.get(data.departures[0].key),'8');
  assert.equal(data.progress.get(data.departures[1].key),'0');
});
const { renderWaferDispatchProgress } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);
const { updateWaferProgressPanel } = require(process.env.CT_WORKSPACE_VISUALIZER_TEST_BUILD);
test('进度不变保留 DOM，变化时始终定位到最新晶圆', () => {
  let writes = 0;
  let scroller = {scrollTop: 120, scrollHeight: 320};
  const panel = {
    dataset: {},
    querySelector() { return scroller; },
    set innerHTML(value) { writes++; scroller = {scrollTop: 0, scrollHeight: 320}; },
  };
  updateWaferProgressPanel(panel, 'first');
  assert.equal(scroller.scrollTop,320);
  updateWaferProgressPanel(panel, 'first');
  assert.equal(writes,1);
  updateWaferProgressPanel(panel, 'second');
  assert.equal(writes,2);
  assert.equal(scroller.scrollTop,320);
});
test('图形步骤保留回港片和在机片的最后进度', () => {
  const moves = [
    {MoveID:1,MoveType:0,EndTime:1,MatIDList:[1,2],SrcStationList:['LP1','LP1'],StepIDList:[0,0]},
    {MoveID:2,MoveType:9,EndTime:3,MatIDList:[2],StepIDList:[4],CurState:'PM1'},
    {MoveID:3,MoveType:9,EndTime:9,MatIDList:[2],StepIDList:[8],CurState:'PM2'},
  ];
  const snapshot = {time:4,modules:[{name:'LP1',wafers:['1']},{name:'PM1',wafers:['2']}],robots:[],waferOrigins:{1:'LP1.1',2:'LP1.2'}};
  const html = renderWaferDispatchProgress(moves,snapshot,null);
  assert.match(html,/LP1\.1/);
  assert.match(html, /LP1\.2/);
  assert.match(html, /发片顺序 2/);
  assert.doesNotMatch(html, /wafer-dispatch-rank|#2|<svg/);
  assert.match(html, /aria-current="step"[^>]*title="StationStep · PM1 · 当前步骤/);
  assert.match(html, /StationStep · PM2 · 后续步骤/);
  assert.doesNotMatch(html, /wafer-progress-meta|<small>4<\/small>|离港 /);
  snapshot.modules = [{name:'LP1',wafers:['1','2']}];
  const returnedHtml = renderWaferDispatchProgress(moves,snapshot,null);
  assert.match(returnedHtml,/LP1\.1/);
  assert.match(returnedHtml,/LP1\.2/);
  assert.equal((returnedHtml.match(/<article/g)||[]).length,2);
});
test('步骤类型按 Route 实际 Robot 和 Station 区分，不猜 StepID 奇偶', () => {
  const moves = [
    {MoveID:1,MoveType:0,EndTime:1,MatIDList:[1],SrcStationList:['LP1'],StepIDList:[20],PJobName:['job']},
    {MoveID:2,MoveType:9,EndTime:5,MatIDList:[1],StepIDList:[3],PJobName:['job']},
  ];
  const snapshot = {time:2,modules:[],robots:[{name:'ATR',wafers:['1']}],waferOrigins:{1:'LP1.1'}};
  const html = renderWaferDispatchProgress(moves,snapshot,{Robots:{ATR:{}}}, () => ({stages:[
    {stepId:20,visits:[{stationName:'ATR'}]},
    {stepId:3,visits:[{stationName:'PM1'}]},
  ]}));
  assert.match(html, /step-robot is-current/);
  assert.match(html, /step-station is-future/);
  assert.doesNotMatch(html, /wafer-progress-meta/);
});
test('dummy 再离港只保留当前一张卡片并保留发片编号', () => {
  const moves = [1,2].map(id=>({MoveID:id,MoveType:0,EndTime:id,MatIDList:[100000],SrcStationList:['DummyPort'],StepIDList:[0]}));
  const html = renderWaferDispatchProgress(moves,{time:3,modules:[],robots:[{name:'ATR',wafers:['100000']}],waferOrigins:{}},null);
  assert.equal((html.match(/<article/g)||[]).length,1);
  assert.match(html,/发片顺序 2/);
  assert.match(html,/DUMMY/);
});
