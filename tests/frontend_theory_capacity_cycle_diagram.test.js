/** 循环图语义：跨期数、完整轮转、筛选及不可信设备名称的转义。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {cycleDiagramEdges, cycleDiagramLayout, cycleEdgeGeometry, renderCycleDiagram, renderCompactDerivation} = require(process.env.CT_THEORY_CAPACITY_TEST_BUILD);

function diagram(rounds = 2) {
  // 最小共享锁例子；事件 ID 顺序不能被误当作相位顺序。
  const events = Array.from({length: rounds}, (_, index) => [
    {id:`v${index}`, lane:"vacuum", round:index+1, label:`PM${index%3+1} 交换`, phase_seconds:100-index, body_seconds:8},
    {id:`a${index}`, lane:"atmosphere", round:index+1, label:"LA 取片", phase_seconds:index, body_seconds:2},
  ]).flat();
  const edges = [
    {id:"0", source:"v0", target:"v1", category:"vacuum", cycle_shift:0},
    {id:"1", source:"v1", target:"v0", category:"process", cycle_shift:1},
    {id:"2", source:"v0", target:"a0", category:"lock", cycle_shift:0},
    {id:"3", source:"a0", target:"a1", category:"atmosphere", cycle_shift:0},
  ].map(edge => ({...edge, label:edge.category, minimum_delay_seconds:8, slack_seconds:0, parts:[]}));
  return {events, edges};
}

test("总览、全部关系和选中动作覆盖精确的后端约束", () => {
  const data = diagram();
  assert.deepEqual(cycleDiagramEdges(data).map(edge => edge.id), ["0", "2", "3"]);
  assert.deepEqual(cycleDiagramEdges(data,"all").map(edge => edge.id), ["0", "1", "2", "3"]);
  assert.deepEqual(cycleDiagramEdges(data,"all","v0").map(edge => edge.id), ["0", "1", "2"]);
  assert.deepEqual(cycleDiagramEdges(data,"handoff"), []);
});

test("并行完整六轮不截断，VAC 与 ATM 分排而非按相位排序", () => {
  const data = diagram(6), layout = cycleDiagramLayout(data);
  assert.equal(layout.rounds, 6);
  assert.equal(layout.nodes.length, 12);
  assert.ok(layout.nodes[0].x < layout.nodes[2].x);
  assert.ok(layout.nodes[0].y < layout.nodes[1].y);
  assert.ok(layout.nodes.every(node => node.x + 144 < layout.width));
});

test("跨周期说明与设备名称安全输出，动作可键盘聚焦", () => {
  const data = diagram(); data.events[0].label = '<img src=x onerror="alert(1)">';
  const html = renderCycleDiagram(data,"all");
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
  assert.match(html, /下一周期/);
  assert.match(html, /tabindex="0"/);
});

test("精简核公式展示可行性求解，不伪造空关键链比值", () => {
  const html = renderCompactDerivation({route_name:"PM1", period_seconds:258,
    resource_period_lower_bound_seconds:258, physical_wafers_per_hour:27.907, seconds_per_unit:129,
    process_seconds:[120], facts:{rounds_per_cycle:2, units_per_cycle:2}, cycle_diagram:diagram()});
  assert.match(html, /二分/);
  assert.match(html, /3600 × 2 ÷ 258/);
  assert.doesNotMatch(html, /Σd ÷ Σλ|0 ÷ 0/);
});


test("选边详情只解释指定约束，并保留跨期与松弛量的口径", () => {
  const data = diagram();
  data.edges[1].parts = [{label:"加工", seconds:6}, {label:"开门", seconds:2}];
  data.edges[1].slack_seconds = 1.25;
  const html = renderCycleDiagram(data, "all", "", "1");
  assert.match(html, /data-cycle-edge="1" role="button" tabindex="0" aria-pressed="true"/);
  assert.match(html, /跨 1 周期/);
  assert.match(html, /d = 6 \+ 2 = <strong>8 秒/);
  assert.match(html, /加工<\/span>/);
  assert.match(html, /开门<\/span>/);
  assert.match(html, /可行解余量<\/dt><dd>1.25/);
  assert.equal((html.match(/class="cycle-node vacuum  connected"/g) || []).length, 2);
});

test("分类不包含所选边时不展示过期约束详情", () => {
  const html = renderCycleDiagram(diagram(), "atmosphere", "", "1");
  assert.doesNotMatch(html, /aria-label="约束验算"/);
  assert.match(html, /选择连线查看计算依据/);
  assert.doesNotMatch(html, /class="cycle-inspector" hidden/);
});

test("边的名称、ID 和分项始终作为文本转义", () => {
  const data = diagram();
  data.edges[1].id = 'edge" onclick="bad';
  data.edges[1].parts = [{label:'<script>bad</script>', seconds:8}];
  const html = renderCycleDiagram(data, "all", "", data.edges[1].id);
  assert.doesNotMatch(html, /<script>| onclick="bad/);
  assert.match(html, /&lt;script&gt;bad/);
});


test("节点聚焦不增加可见边，也不改变原有边的路径", () => {
  const data = diagram();
  const before = renderCycleDiagram(data, "overview");
  const after = renderCycleDiagram(data, "overview", "v0");
  const paths = html => [...html.matchAll(/class="cycle-edge-hit" d="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(paths(after), paths(before));
  assert.doesNotMatch(after, /data-cycle-edge="1"/);
});


test("箭头尺寸不随高亮描边改变，跨期约束展示相位代入", () => {
  const html = renderCycleDiagram(diagram(), "all", "", "1", 10);
  assert.match(html, /<polygon class="cycle-arrow/);
  assert.doesNotMatch(html, /marker-end/);
  assert.match(html, /100 − 99 \+ 1 × 10 = 11 ≥ 8 秒/);
});

test("准时交接的扣除项保留负号并正确显示公式", () => {
  const data = diagram();
  data.edges[1].parts = [{label:"加工", seconds:12}, {label:"带片转位（扣除）", seconds:-4}];
  const html = renderCycleDiagram(data, "all", "", "1");
  assert.match(html, /d = 12 − 4 = <strong>8 秒/);
  assert.match(html, /带片转位（扣除）/);
});


test("锁恢复的反向边使用独立端口，跨期标识不受选中影响", () => {
  const data = diagram();
  data.edges.push({...data.edges[2], id:"4", source:"a0", target:"v0", cycle_shift:1});
  const html = renderCycleDiagram(data,"all","","4");
  const pathFor = id => html.match(new RegExp(`data-cycle-edge="${id}"[^>]*><path class="cycle-edge-hit" d="([^"]+)"`))[1];
  const vent = pathFor("2"), pump = pathFor("4");
  const startX = path => Number(path.match(/^M([\d.]+)/)[1]);
  assert.notEqual(startX(vent), startX(pump));
  assert.match(html, /cycle-edge-lock cycle-edge-next/);
  assert.match(html, /cycle-edge-lock cycle-edge-current/);
  assert.doesNotMatch(html, /stroke-dasharray/);
});


test("边无文字标注，详情卡明确机器人与站点动作", () => {
  const data = diagram();
  data.events[0].label = "LA 取片";
  const html = renderCycleDiagram(data, "all", "", "2");
  const relationships = [...html.matchAll(/<g class="cycle-relationship[\s\S]*?<\/g>/g)];
  assert.equal(relationships.length, data.edges.length);
  relationships.forEach(match => assert.doesNotMatch(match[0], /<text/));
  assert.match(html, /VAC 在 LA 取片/);
  assert.match(html, /ATM 在 LA 取片/);
});

test("反向与平行边全量布线不共享竖直段，箭头线身止于底边", () => {
  const data = diagram();
  data.edges.push({...data.edges[2], id:"4", source:"a0", target:"v0", cycle_shift:1});
  data.edges.push({...data.edges[2], id:"5"});
  const layout = cycleDiagramLayout(data);
  assert.equal(layout.routes.length, data.edges.length);
  const vertical = [];
  for (const route of layout.routes) {
    const geometry = cycleEdgeGeometry(route, layout.routes);
    assert.doesNotMatch(geometry.path, /NaN|Infinity/);
    const triangle = geometry.arrow.split(" ").map(pair => pair.split(",").map(Number));
    const endpoint = geometry.path.match(/L([^ LQ]+)$/)[1].split(",").map(Number);
    assert.deepEqual(endpoint, [(triangle[1][0]+triangle[2][0])/2, (triangle[1][1]+triangle[2][1])/2]);
    route.points.slice(1).forEach((b,index) => {
      const a = route.points[index];
      if (a.x === b.x && a.y !== b.y) vertical.push({x:a.x, min:Math.min(a.y,b.y), max:Math.max(a.y,b.y)});
    });
  }
  vertical.forEach((a,index) => vertical.slice(index+1).forEach(b => {
    assert.ok(a.x !== b.x || a.max <= b.min || b.max <= a.min, "不同边不共享竖直线段");
  }));
});


test("约束图使用统一关系选择器且不再渲染等待关系列表", () => {
  const html = renderCycleDiagram(diagram());
  assert.match(html, /循环约束图/);
  assert.match(html, /data-compact-label="关系类型"/);
  assert.doesNotMatch(html, /查看关系|逐条查看|cycle-edge-list/);
});


test("节点直接说明 VAC 和 ATM 执行动作，且只提供统一重置入口", () => {
  const data = diagram(); data.events[0].label = "LA 取片";
  const html = renderCycleDiagram(data);
  const nodes = [...html.matchAll(/<g role="button"[^>]*data-cycle-node[\s\S]*?<\/g>/g)].map(match => match[0]).join("");
  assert.match(nodes, />VAC 在 LA 取片<\/text>/);
  assert.match(nodes, />ATM 在 LA 取片<\/text>/);
  assert.equal((html.match(/data-cycle-reset/g) || []).length, 1);
  assert.match(html, />重置视图<\/button>/);
  assert.doesNotMatch(html, /显示整轮|适应画布/);
});
