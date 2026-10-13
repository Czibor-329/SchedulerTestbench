/**
 * 理论产能页签的前端契约：筛选参数、路线分类选项、曲线构建、图表几何（坐标映射、刻度、
 * 读数）与控制器取数顺序。只断言导出的纯函数与控制器发出的请求，不复制后端建图或产能
 * 计算口径。曲线是静态图，因此这里不断言任何缩放或平移行为。
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const {
  processSecondsAtPosition,
  renderTeachingDerivation,
  renderBatchDerivation,
  CHART_HEIGHT,
  CHART_WIDTH,
  DEFAULT_SCAN_WINDOW,
  EMPTY_FILTERS,
  buildChartCurves,
  chartPlotBox,
  chartWindowForCurves,
  cleanTypeLabel,
  cleanTypesLabel,
  createTheoryCapacityView,
  curveColor,
  dataToPixel,
  escapeHtml,
  filterQuery,
  formatCapacity,
  formatSeconds,
  groupRouteNames,
  groupTitle,
  limitingKindLabel,
  lockModeLabel,
  nearestChartSample,
  niceTicks,
  pixelToData,
  pointsForRoute,
  renderInteractionChart,
  routeIndexOptions,
  routeKindLabel,
  routeStatusClass,
  routeStatusLabel,
} = require(process.env.CT_THEORY_CAPACITY_TEST_BUILD);

const PROCESS = [0, 60, 120, 180, 240, 300];
const CAPACITY = [89.19, 89.19, 48.87, 34.73, 26.93, 22.0];

const GROUP_PM13 = {
  group_key: "grp-pm13",
  route_kind: "single-node",
  lock_mode: "shared-bidirectional",
  rounds_per_cycle: 2,
  units_per_cycle: 2,
  chamber_counts: [1],
  pressure_domains: ["LA"],
  route_names: ["PM1", "PM3"],
  representative: "PM1",
  start_wafers_per_hour: 89.19,
  end_wafers_per_hour: 26.13,
  best_wafers_per_hour: 89.19,
  matched_tests: 2,
};

const GROUP_PM2 = {
  ...GROUP_PM13,
  group_key: "grp-pm2",
  route_names: ["PM2", "PM4"],
  representative: "PM2",
  end_wafers_per_hour: 26.62,
  matched_tests: 1,
};

const POINTS = [
  {
    id: "t1", name: "test14 (2)", group: "公司单腔示例集-130", clean_types: ["wacclean"],
    route_name: "PM1", group_key: "grp-pm13", status: "supported", reason: "", detail: "",
    process_seconds: [150], key_process_seconds: 150, period_seconds: 335.54,
    seconds_per_unit: 55.92, physical_wafers_per_hour: 64.37,
    limiting_kind: "resource", limiting_label: "加工腔 PM1（335.540 秒/周期）",
  },
  {
    id: "t2", name: "test20 (2)", group: "公司示例集-Pre", clean_types: [],
    route_name: "PM3", group_key: "grp-pm13", status: "supported", reason: "", detail: "",
    process_seconds: [60], key_process_seconds: 60, period_seconds: 242.19,
    seconds_per_unit: 40.37, physical_wafers_per_hour: 89.19,
    limiting_kind: "waiting-chain", limiting_label: "同锁恢复链",
  },
  {
    id: "t3", name: "test31 (2)", group: "公司示例集-Pre", clean_types: ["preclean"],
    route_name: "PM2", group_key: "grp-pm2", status: "unsupported", reason: "residency-not-supported",
    detail: "存在驻留上限，本策略尚未接入精确定时复核链路。",
    process_seconds: [], key_process_seconds: null, period_seconds: null,
    seconds_per_unit: null, physical_wafers_per_hour: null,
    limiting_kind: "", limiting_label: "",
  },
];

/** 造一条路线的扫描列。 */
function route(name, groupKey) {
  return {
    route_name: name, group: "自动示例", status: "supported", reason: "", detail: "",
    group_key: groupKey, facts: null,
    points: { process_seconds: PROCESS, physical_wafers_per_hour: CAPACITY },
  };
}

/** 造一份最小扫描响应。 */
function scanPayload(window = DEFAULT_SCAN_WINDOW, filters = { kinds: "", locks: "", cleans: "" }) {
  return {
    ok: true,
    device: { id: "dev-1", name: "PSE300", physical_members_per_unit: 1 },
    scan: {
      first_seconds: window.firstSeconds,
      last_seconds: window.lastSeconds,
      step_seconds: window.stepSeconds,
    },
    filters: {
      kinds: [filters.kinds].filter(Boolean),
      locks: [filters.locks].filter(Boolean),
      cleans: [filters.cleans].filter(Boolean),
    },
    route_index: {
      route_kind: [{ value: "single-node", count: 6 }],
      lock_mode: [{ value: "shared-bidirectional", count: 61 }],
      route_total: 80,
      route_supported: 72,
    },
    routes: [
      route("PM1", "grp-pm13"), route("PM3", "grp-pm13"),
      route("PM2", "grp-pm2"), route("PM4", "grp-pm2"),
    ],
    groups: [GROUP_PM13, GROUP_PM2],
    tests: POINTS,
    test_total: POINTS.length,
    test_truncated: false,
  };
}

test("筛选条件转换为接口参数时省略空值", () => {
  assert.deepEqual(filterQuery(EMPTY_FILTERS), { kinds: "", locks: "", cleans: "" });
  assert.deepEqual(
    filterQuery({ kind: "parallel", lock: "shared-bidirectional", clean: "wacclean" }),
    { kinds: "parallel", locks: "shared-bidirectional", cleans: "wacclean" },
  );
});

test("路线分类选项按固定顺序给出并带上数量", () => {
  const options = routeIndexOptions({
    route_kind: [{ value: "single-node", count: 6 }, { value: "parallel", count: 11 }],
    lock_mode: [{ value: "shared-bidirectional", count: 61 }, { value: "single-bidirectional", count: 3 }],
    route_total: 80, route_supported: 72,
  });
  assert.deepEqual(options.kinds.map(row => row.value), [
    "single-node", "parallel", "serial", "mixed", "reentrant",
  ]);
  assert.equal(options.kinds[0].label, "单节点（6）");
  assert.equal(options.kinds[2].label, "串行（0）");
  assert.equal(options.locks[0].label, "共享双通道（61）");
  assert.equal(options.locks[2].label, "单通道（3）");
  assert.deepEqual(options.cleans.map(row => row.value), [
    "preclean", "postclean", "wacclean", "dummy", "dummywac", "none",
  ]);
  assert.equal(options.cleans[5].label, "No Clean");
});

test("曲线按等价分组构建，并与色板一一对应", () => {
  const curves = buildChartCurves(scanPayload());
  assert.equal(curves.length, 2);
  assert.deepEqual(curves.map(curve => curve.groupKey), ["grp-pm13", "grp-pm2"]);
  assert.equal(curves[0].label, "单节点 · 1 腔");
  assert.deepEqual(curves[0].routeNames, ["PM1", "PM3"]);
  assert.equal(curves[0].representative, "PM1");
  assert.equal(curves[0].color, curveColor(0));
  assert.notEqual(curves[1].color, curves[0].color);
  assert.equal(curves[0].visible, true);
  assert.equal(curves[0].processSeconds.length, PROCESS.length);
  // 分组内全部测试点都挂在该曲线上；不可推导的测试仍然列出。
  assert.deepEqual(curves[0].tests.map(point => point.id), ["t1", "t2"]);
  assert.deepEqual(curves[1].tests.map(point => point.id), ["t3"]);
  assert.deepEqual(buildChartCurves(null), []);
});

test("载荷缺少等价分组时按路线回退出曲线", () => {
  const payload = scanPayload();
  const fallback = { ...payload, groups: [] };
  const curves = buildChartCurves(fallback);
  assert.deepEqual(curves.map(curve => curve.groupKey), ["grp-pm13", "grp-pm13", "grp-pm2", "grp-pm2"]);
  assert.deepEqual(curves.map(curve => curve.representative), ["PM1", "PM3", "PM2", "PM4"]);
});

test("按等价分组筛选数据点，缺分组键时退回路线名", () => {
  assert.deepEqual(
    pointsForRoute(POINTS, "PM1", "grp-pm13").map(point => point.id),
    ["t1", "t2"],
  );
  assert.deepEqual(pointsForRoute(POINTS, "PM1").map(point => point.id), ["t1"]);
  assert.deepEqual(pointsForRoute(POINTS, "PM4").map(point => point.id), []);
});

test("等价分组标题区分单条与多条", () => {
  assert.equal(groupTitle(GROUP_PM13), "单节点 · 1 腔");
  assert.equal(groupRouteNames(GROUP_PM13), "PM1 / PM3");
  assert.equal(groupTitle({ ...GROUP_PM13, route_names: ["PM1"], representative: "PM1" }), "PM1");
  assert.equal(
    groupTitle({ ...GROUP_PM13, route_kind: "serial", chamber_counts: [1, 1], route_names: ["A", "B"] }),
    "串行 · 1/1 腔",
  );
});

test("显示窗口取扫描区间，远端测试点不压扁主曲线", () => {
  const payload = scanPayload();
  // 造一个加工时间远超扫描区间的测试点：它只出现在测试表里，不进显示窗口。
  payload.tests = [
    ...payload.tests,
    {
      ...POINTS[0], id: "t9", name: "test90 (2)", key_process_seconds: 780,
      physical_wafers_per_hour: 6.2, group_key: "grp-pm13",
    },
  ];
  const curves = buildChartCurves(payload);
  const window = chartWindowForCurves(curves);
  assert.equal(window.x0, 0);
  assert.equal(window.x1, 300, "横轴为扫描区间");
  assert.equal(window.y0, 0);
  assert.ok(window.y1 > 89.19 && window.y1 < 100, "纵轴只按区间内的点留余量");
  const plot = chartPlotBox();
  const far = dataToPixel(window, plot, { x: 780, y: 6.2 });
  assert.ok(far.x > plot.right, "远端数据点落在绘图区外，由裁剪隐藏");
  const empty = chartWindowForCurves([]);
  assert.ok(empty.x1 > empty.x0);
  assert.ok(empty.y1 > 0);
});

test("像素与数据坐标互为逆映射，且角点对齐窗口", () => {
  const plot = chartPlotBox();
  const window = { x0: 0, x1: 300, y0: 0, y1: 100 };
  assert.deepEqual(dataToPixel(window, plot, { x: 0, y: 0 }), { x: plot.left, y: plot.bottom });
  const topRight = dataToPixel(window, plot, { x: 300, y: 100 });
  assert.ok(Math.abs(topRight.x - plot.right) < 1e-9);
  assert.ok(Math.abs(topRight.y - plot.top) < 1e-9);
  for (const point of [{ x: 60, y: 50 }, { x: 240, y: 12.5 }]) {
    const pixel = dataToPixel(window, plot, point);
    const back = pixelToData(window, plot, pixel.x, pixel.y);
    assert.ok(Math.abs(back.x - point.x) < 1e-9);
    assert.ok(Math.abs(back.y - point.y) < 1e-9);
  }
});

test("刻度按 1/2/5 步长覆盖区间", () => {
  assert.deepEqual(niceTicks(0, 300, 6), [0, 50, 100, 150, 200, 250, 300]);
  assert.deepEqual(niceTicks(0, 100, 5), [0, 20, 40, 60, 80, 100]);
  assert.deepEqual(niceTicks(0, 1, 2), [0, 0.5, 1]);
  assert.deepEqual(niceTicks(5, 5, 4), [5]);
  const ticks = niceTicks(37.2, 91.4, 5);
  assert.ok(ticks.length >= 3);
  assert.ok(ticks[0] >= 37.2 && ticks[ticks.length - 1] <= 91.4 + 1e-9);
});

test("读数取可见曲线上最近的采样点", () => {
  const curves = buildChartCurves(scanPayload());
  const plot = chartPlotBox();
  const window = chartWindowForCurves(curves);
  const target = dataToPixel(window, plot, { x: 120, y: 48.87 });
  const nearest = nearestChartSample(curves, plot, window, target.x);
  assert.ok(nearest);
  assert.equal(nearest.processSeconds, 120);
  assert.equal(nearest.curve.groupKey, "grp-pm13");
  curves[0].visible = false;
  const hidden = nearestChartSample(curves, plot, window, target.x);
  assert.equal(hidden.curve.groupKey, "grp-pm2", "隐藏的曲线不参与读数");
});

test("SVG 渲染包含坐标轴、可见曲线、数据点与读数", () => {
  const curves = buildChartCurves(scanPayload());
  const window = chartWindowForCurves(curves);
  for (const curve of curves) curve.visible = curve.groupKey === "grp-pm13";
  const svg = renderInteractionChart({ curves, window, readout: null });
  assert.match(svg, /^<svg /);
  assert.match(svg, /data-theory-plot="1"/);
  assert.match(svg, /clipPath/);
  assert.match(svg, /加工时间（秒）/);
  assert.match(svg, /理论产能（片\/h）/);
  assert.equal((svg.match(/class="theory-chart-line"/g) ?? []).length, 1, "只画可见曲线");
  assert.equal((svg.match(/theory-chart-marker/g) ?? []).length, 2, "该分组的两个测试点");
  assert.match(svg, /data-theory-point="t1"/);
  assert.doesNotMatch(svg, /theory-chart-crosshair/);
  assert.doesNotMatch(svg, /theory-chart-zoom-box/, "曲线不提供缩放框");
  const withReadout = renderInteractionChart({
    curves,
    window,
    readout: { x: 120, y: 48.87, label: "PM1/PM3：120.0 秒 → 48.87 片/h" },
  });
  assert.match(withReadout, /theory-chart-crosshair/);
  assert.match(withReadout, /PM1\/PM3：120\.0 秒 → 48\.87 片\/h/);
  assert.ok(CHART_WIDTH > 0 && CHART_HEIGHT > 0);
});

test("标签与数值格式化对缺失值保留占位符", () => {
  assert.equal(lockModeLabel("shared-bidirectional"), "共享双通道");
  assert.equal(lockModeLabel("dedicated-entry-exit"), "单进单出");
  assert.equal(lockModeLabel("single-bidirectional"), "单通道");
  assert.equal(routeKindLabel("reentrant"), "重入");
  assert.equal(cleanTypeLabel("dummywac"), "Dummy Wac");
  assert.equal(cleanTypeLabel("none"), "No Clean");
  assert.equal(cleanTypesLabel([]), "No Clean");
  assert.equal(cleanTypesLabel(["preclean", "wacclean"]), "Pre + Wac");
  assert.equal(limitingKindLabel("resource"), "资源负荷");
  assert.equal(routeStatusLabel("unsupported"), "模板不适用");
  assert.equal(routeStatusClass("error"), "error");
  assert.equal(formatCapacity(89.18617), "89.19");
  assert.equal(formatCapacity(null), "—");
  assert.equal(formatSeconds(undefined), "—");
  assert.equal(formatSeconds(155.73, 3), "155.730");
  assert.equal(escapeHtml('<a href="x">&\'</a>'), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;");
  assert.equal(curveColor(0), curveColor(8), "色板循环使用");
});

/** 固定组合页签的最小 DOM 与模板图表替身，覆盖请求、竞态与错误状态。 */
function createFakeDom() {
  const elements = new Map();
  class Element {
    constructor(id) { this.id=id; this.value=""; this.innerHTML=""; this.listeners=new Map(); }
    addEventListener(type, handler) { this.listeners.set(type, handler); }
    replaceChildren(...children) { this.value=children[0]?.value ?? ""; }
    async fire(type, event={}) { this.listeners.get(type)?.(event); await new Promise(resolve=>setImmediate(resolve)); }
  }
  globalThis.HTMLSelectElement=Element;
  globalThis.Option=class {constructor(text,value) {this.text=text;this.value=value;}};
  globalThis.MutationObserver=class {observe(){} disconnect(){}};
  const charts=[];
  globalThis.window={ApexCharts:class {
    constructor(host, options) { this.options=options; charts.push(this); }
    async render() {} async updateOptions(options) {this.options=options;} destroy() {this.destroyed=true;}
  }};
  const attributes=new Map();
  const root={classList:{contains:()=>true},
    setAttribute(name,value){attributes.set(name,value);},getAttribute(name){return attributes.get(name);},
    querySelector(selector) {if(!elements.has(selector)) elements.set(selector,new Element(selector));return elements.get(selector);},
    querySelectorAll() {return [this.querySelector("#theoryCapacityKind")];}
  };
  for(const [key,value] of Object.entries({Kind:"single-node",Lock:"shared-bidirectional",FirstSeconds:"0",LastSeconds:"300",StepSeconds:"10",Slider:"20"})) root.querySelector(`#theoryCapacity${key}`).value=value;
  return {root,charts};
}
/** 返回扫描与单点使用同一参数的最小协议响应。 */
function presetPayload(clean = false) {
  return {name:"PM1",points:{process_seconds:[0,20,40],physical_wafers_per_hour:[100,90,80]},
    baseline_points:{process_seconds:[0,20,40],physical_wafers_per_hour:[110,100,90]},
    derivation:{model_kind:clean ? "cycle-periodic" : undefined,
      period_timing:clean ? {basis:"job",output_wafers:13,phase_count:2,start_seconds:100,end_seconds:260,full_period_seconds:160,divisor:2,verified_repeats:3,reference:"首工序同相位产品开始加工",resource:"PM1",segments:[{label:"产品加工",seconds:60},{label:"PreClean",seconds:20}]} : undefined,
      baseline_wafers_per_hour:100,baseline_seconds:900,capacity_change_percent:-10,
      facts:{units_per_cycle:2,rounds_per_cycle:2,physical_members_per_unit:1},period_seconds:80,seconds_per_unit:40,
      physical_wafers_per_hour:90,process_seconds:[20],resource_work_seconds:{VAC:60},resource_period_lower_bound_seconds:60}};
}
/** 清理测试专用全局对象。 */
function cleanupDom() {for(const key of ["HTMLSelectElement","Option","MutationObserver","window"]) delete globalThis[key];}

test("控制器直接求解固定组合并用模板图表同步公式，切换参数刷新选择文案",async()=>{
 const dom=createFakeDom(); const calls=[];const sync=[];
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"dev-1",name:"设备"}],
   requestPreset:async(id,values)=>{calls.push({id,values});return presetPayload();},syncSelect:select=>sync.push(select.id)});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.length,1);assert.equal(calls[0].values.kind,"single-node");
  assert.equal(calls[0].values.first,"0");assert.equal(dom.charts[0].options.chart.zoom.enabled,false);
  assert.equal(dom.charts[0].options.tooltip.theme,"dark");
  assert.match(dom.root.querySelector("#theoryCapacityDerivation").innerHTML,/90.00/);
  assert.equal(dom.root.querySelector("#theoryCapacityResults").hidden,false);
  dom.root.querySelector("#theoryCapacityKind").value="mixed";
  await dom.root.querySelector("#theoryCapacityKind").fire("change");
  assert.equal(calls.length,1,"参数编辑完成后统一应用，避免每改一个清洁参数都触发整条扫描");
  await dom.root.querySelector("#theoryCapacityApply").fire("click");
  assert.equal(calls[1].values.kind,"mixed");assert.ok(sync.includes("#theoryCapacityKind"));
  assert.equal(dom.charts.length,1);view.destroy();
 } finally {cleanupDom();}
});

test("计算失败不残留旧曲线，恢复后可以重新计算",async()=>{
 const dom=createFakeDom();let fail=false;
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"dev-1",name:"设备"}],
   requestPreset:async()=>{if(fail)throw new Error("设备缺少指定加工腔 PM3");return presetPayload();}});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));fail=true;
  await dom.root.querySelector("#theoryCapacityApply").fire("click");
  assert.equal(dom.root.querySelector("#theoryCapacityResults").hidden,false,"失败后保留调节控件供修正");
  assert.equal(dom.root.querySelector("#theoryCapacityChartSection").hidden,true,"失败时隐藏旧曲线");
  assert.equal(dom.root.querySelector("#theoryCapacityCurrentRate").textContent,"—");
  assert.match(dom.root.querySelector("#theoryCapacityNotice").textContent,/PM3/);
  assert.equal(dom.charts[0].destroyed,true);fail=false;
  await dom.root.querySelector("#theoryCapacityApply").fire("click");
  assert.equal(dom.root.querySelector("#theoryCapacityResults").hidden,false);view.destroy();
 } finally {cleanupDom();}
});

test("设备目录为空时不求解",async()=>{
 const dom=createFakeDom();let calls=0;
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[],requestPreset:async()=>{calls++;}});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,0);assert.equal(dom.root.querySelector("#theoryCapacityResults").hidden,true);view.destroy();
 } finally {cleanupDom();}
});


test("拖动加工时间按绘图区换算并钳制边界，支持零与小数",()=>{
 assert.equal(processSecondsAtPosition(250,100,300,0,300),150);
 assert.equal(processSecondsAtPosition(50,100,300,0,300),0);
 assert.equal(processSecondsAtPosition(600,100,300,0,300),300);
 assert.equal(processSecondsAtPosition(250.25,100,300,10,40),25);
 assert.equal(processSecondsAtPosition(250,100,0,10,40),10);
});

test("教学公式说明每个量及等待链来源，不让字母承担解释",()=>{
 const payload=presetPayload();
 delete payload.derivation.model_kind; // 保留旧稳态协议的独立渲染契约覆盖。
 payload.derivation.critical_chain=[{minimum_delay_seconds:160,cycle_shift:2}];
 const text=renderTeachingDerivation(payload.derivation,"PM1");
 for(const word of ["加工时间", "一次大气手交替产出", "周期时间", "平均出片间隔", "每小时产能", "160.00 ÷ 2 = 80.00", "1 小时有 3600 秒"]) assert.ok(text.includes(word),word);
});


test("曲线标记拖动联动加工输入并在松手时立即求解",async()=>{
 const dom=createFakeDom();const calls=[];
 try {
  const host=dom.root.querySelector("#theoryCapacityChart");
  host.setPointerCapture=()=>{};host.releasePointerCapture=()=>{};
  dom.root.querySelector("#theoryCapacityChart .apexcharts-grid").getBoundingClientRect=()=>({left:100,width:300});
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"d",name:"设备"}],requestPreset:async(id,values)=>{
    calls.push(values);const payload=presetPayload();payload.derivation.process_seconds=[Number(values.process)];
    payload.derivation.period_seconds=Number(values.process);return payload;
  }});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  await host.fire("pointerdown",{pointerId:1,clientX:120,target:{closest:()=>true},preventDefault(){}});
  await host.fire("pointermove",{pointerId:1,clientX:250});
  assert.equal(dom.root.querySelector("#theoryCapacitySlider").value,"150");
  assert.equal(calls.length,1,"拖动中不发起求解");
  assert.match(dom.root.querySelector("#theoryCapacityDerivation").innerHTML,/20.00/,"松手前保留已计算公式");
  await host.fire("pointerup",{pointerId:1,clientX:280});
  assert.equal(calls.at(-1).process,"180");
  assert.match(dom.root.querySelector("#theoryCapacityDerivation").innerHTML,/180.00/);
  view.destroy();
 } finally {cleanupDom();}
});


test("加工时间滑块松手后求解并同步公式",async()=>{
 const dom=createFakeDom();const calls=[];
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"d",name:"设备"}],requestPreset:async(id,values)=>{
   calls.push(values);const payload=presetPayload();payload.derivation.process_seconds=[Number(values.process)];return payload;
  }});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  const input=dom.root.querySelector("#theoryCapacitySlider"); input.value="80"; input.checkValidity=()=>true;
  await input.fire("input",{target:input});
  assert.equal(calls.length,1);
  await input.fire("change");
  assert.equal(calls.at(-1).process,"80");assert.match(dom.root.querySelector("#theoryCapacityDerivation").innerHTML,/80.00/);
  view.destroy();
 } finally {cleanupDom();}
});

test("批次推导明确产品数、同批次基线和尾部清洁，不冒充周期图",()=>{
 const entry={model_kind:"cycle-batch",job_count:2,wafers_per_job:25,batch_wafers:50,
  alternation:"drain",period_seconds:2400,baseline_seconds:2000,physical_wafers_per_hour:75,
  seconds_per_unit:48,baseline_wafers_per_hour:90,capacity_change_percent:-16.67};
 const html=renderBatchDerivation(entry);
 assert.match(html,/2 个 Job × 25 片 = 50 片产品/);
 assert.match(html,/全部动作/);assert.match(html,/3600 × 50 ÷ 2400.00 = 75.00/);
 assert.doesNotMatch(html,/损失|产能变化/);
 assert.doesNotMatch(html,/每周期产出/);
 assert.equal(renderTeachingDerivation(entry,"PM1"),html);

});

test("单选清洁选择周期口径，仅提交相关参数并保持论文基线",async()=>{
 const dom=createFakeDom();const calls=[];
 try {
  for(const [id,value] of Object.entries({theoryCleaningType:"dummywac",
   theoryJobWafers:"25",theoryJobCount:"2",theoryJobAlternation:"continuous",theoryDummyInventory:"3",
   theoryDummyVisits:"2",theoryWacInterval:"5",theoryWacSeconds:"30"})) dom.root.querySelector(`#${id}`).value=value;
  dom.root.querySelector("#theoryPostEnabled").checked=true;
  dom.root.querySelector("#theoryWacEnabled").checked=true;
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"d",name:"设备"}],requestPreset:async(id,values)=>{
   calls.push(values);const payload=presetPayload(Boolean(values.clean));
   payload.baseline_points=payload.points;payload.theory_points=payload.points;return payload;
  }});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls[0].clean,"dummywac");assert.equal(calls[0].mode,"periodic");assert.equal(calls[0].wafers,undefined);
  assert.equal(calls[0].jobs,undefined);assert.equal(calls[0].dummy_inventory,"3");
  assert.deepEqual(dom.charts[0].options.series.map(row=>row.name),["带清洁","不带清洁"]);
  assert.match(dom.root.querySelector("#theoryCapacityComparison").textContent,/13 片 \/ Job/);
  for(const id of ["theoryPreSeconds","theoryPostSeconds","theoryWacSeconds","theoryDummySeconds"]) {
   assert.equal(dom.root.querySelector(`#${id}`).max,"200");
   assert.equal(dom.root.querySelector(`#${id}Slider`).max,"200");
  }
  for(const id of ["theoryWacInterval","theoryDummyVisits","theoryDummyInventory"]) {
   assert.equal(dom.root.querySelector(`#${id}`).max,"3");
   assert.equal(dom.root.querySelector(`#${id}Slider`).max,"3");
  }
  const expected = {
   "": [],
   preclean: ["pre_seconds"], postclean: ["post_seconds"], wacclean: ["wac_seconds", "wac_interval"],
   dummy: ["dummy_seconds", "dummy_visits", "dummy_inventory"],
   dummywac: ["dummy_seconds", "dummy_visits", "dummy_inventory", "wac_seconds"]
  };
  const allKeys = [...new Set(Object.values(expected).flat())];
  for (const [type, keys] of Object.entries(expected)) {
   dom.root.querySelector("#theoryCleaningType").value = type;
   await dom.root.querySelector("#theoryCapacityKind").fire("change");
   await dom.root.querySelector("#theoryCapacityApply").fire("click");
   const sent = calls.at(-1);
   assert.equal(sent.clean, type);
   assert.equal(sent.mode, type ? "periodic" : "steady");
   assert.deepEqual(allKeys.filter(key => key in sent).sort(), [...keys].sort());
   assert.equal(dom.root.querySelector("#theoryPreSettings").hidden, type !== "preclean");
   assert.deepEqual(dom.charts[0].options.series.map(row => row.name), type ? ["带清洁", "不带清洁"] : ["不带清洁"]);
   assert.equal(dom.root.querySelector("#theoryPostSettings").hidden, type !== "postclean");
   assert.equal(dom.root.querySelector("#theoryDummySettings").hidden, !type.startsWith("dummy"));
   assert.equal(dom.root.querySelector("#theoryWacIntervalLabel").hidden, type !== "wacclean");
  }
  view.destroy();
 } finally {cleanupDom();}
});


test("清洁滑块松手求解，只移动清洁曲线并固定基线和纵轴",async()=>{
 const dom=createFakeDom(); const calls=[];
 dom.root.querySelector("#theoryPreSeconds").value="30";
 dom.root.querySelector("#theoryCleaningType").value="preclean";
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"d",name:"设备"}],requestPreset:async(id,values)=>{
   calls.push(values); const cost=Number(values.pre_seconds)/10;
   const base=values.kind==="mixed"?[240,220,200]:[120,110,100];
   const payload=presetPayload(true);
   payload.points={process_seconds:[0,20,40],physical_wafers_per_hour:base.map(rate=>rate-cost)};
   payload.baseline_points={process_seconds:[0,20,40],physical_wafers_per_hour:base};
   payload.derivation={...payload.derivation,
    physical_wafers_per_hour:base[1]-cost,baseline_wafers_per_hour:base[1],baseline_seconds:800,
    capacity_change_percent:-100*cost/base[1]};
   return payload;
  }});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  const chart=dom.charts[0], baseline=chart.options.series[1].data, ceiling=chart.options.yaxis.max;
  const originalClean=structuredClone(chart.options.series[0].data);
  const slider=dom.root.querySelector("#theoryPreSecondsSlider");slider.value="120";
  await slider.fire("input");
  assert.equal(calls.length,1,"拖动时不生成排程风暴");
  assert.equal(dom.root.querySelector("#theoryPreSeconds").value,"120");
  assert.deepEqual(chart.options.series[0].data,originalClean,"求解前保留已计算曲线，避免把插值冒充求解");
  assert.equal(dom.root.getAttribute("data-result-stale"),"true");
  await slider.fire("change");
  assert.equal(calls.at(-1).pre_seconds,"120");
  assert.strictEqual(chart.options.series[1].data,baseline,"基线数据保持原引用");
  assert.equal(chart.options.yaxis.max,ceiling,"纵轴不随清洁变化缩放");
  assert.notDeepEqual(chart.options.series[0].data,originalClean);
  assert.doesNotMatch(dom.root.querySelector("#theoryCapacityComparison").textContent,/损失/);
  assert.equal(dom.root.getAttribute("data-result-stale"),"false");
  const input=dom.root.querySelector("#theoryPreSeconds");input.value="150.5";
  await input.fire("input");
  assert.equal(slider.max,"200","清洁时长范围固定为 0～200 秒");
  assert.equal(calls.length,2);
  await input.fire("change");
  assert.equal(calls.at(-1).pre_seconds,"150.5");
  assert.strictEqual(chart.options.series[1].data,baseline);
  const countAfterChange=calls.length;
  await input.fire("blur");
  assert.equal(calls.length,countAfterChange,"change 后离焦不重复扫描");
  input.value="60";await input.fire("input");await input.fire("blur");
  assert.equal(calls.at(-1).pre_seconds,"60","没有 change 的输入在离焦时也提交");
  input.value="80";await input.fire("input");await input.fire("keydown",{key:"Enter",preventDefault(){}});
  assert.equal(calls.at(-1).pre_seconds,"80");
  dom.root.querySelector("#theoryCapacityKind").value="mixed";
  await dom.root.querySelector("#theoryCapacityKind").fire("change");
  await dom.root.querySelector("#theoryCapacityApply").fire("click");
  assert.notDeepEqual(chart.options.series[1].data,baseline,"更换模板时建立新基线");
  assert.ok(chart.options.yaxis.max>ceiling);
  view.destroy();
 } finally {cleanupDom();}
});

test("连续拖动清洁滑块时旧请求不覆盖最新参数的结果",async()=>{
 const dom=createFakeDom();const pending=[];
 dom.root.querySelector("#theoryPreSeconds").value="30";
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"d",name:"设备"}],requestPreset:async()=>{
   if(!dom.charts.length)return presetPayload();
   return new Promise(resolve=>pending.push(resolve));
  }});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  const slider=dom.root.querySelector("#theoryPreSecondsSlider");
  slider.value="50";await slider.fire("input");await slider.fire("change");
  slider.value="90";await slider.fire("input");await slider.fire("change");
  const latest=presetPayload();latest.derivation.physical_wafers_per_hour=70;
  pending[1](latest);await new Promise(resolve=>setImmediate(resolve));
  pending[0](presetPayload());await new Promise(resolve=>setImmediate(resolve));
  assert.equal(dom.root.querySelector("#theoryCapacityCurrentRate").textContent,"70.00");
  assert.equal(dom.root.querySelector("#theoryPreSeconds").value,"90");
  view.destroy();
 } finally {cleanupDom();}
});

test("旧后端忽略清洁参数时拒绝显示无清洁结果，保留参数供重试",async()=>{
 const dom=createFakeDom();
 dom.root.querySelector("#theoryCleaningType").value="preclean";
 try {
  const view=createTheoryCapacityView({root:dom.root,listDevices:async()=>[{id:"d",name:"设备"}],requestPreset:async()=>{
   const payload=presetPayload();delete payload.derivation.model_kind;delete payload.baseline_points;return payload;
  }});
  view.initialize();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(dom.charts.length,0);
  assert.equal(dom.root.querySelector("#theoryCapacityCurrentRate").textContent,"—");
  assert.match(dom.root.querySelector("#theoryCapacityNotice").textContent,/重启调度平台服务/);
  assert.equal(dom.root.querySelector("#theoryCapacityChartSection").hidden,true);
  assert.equal(dom.root.querySelector("#theoryCapacityResults").hidden,false);
  view.destroy();
 } finally {cleanupDom();}
});


test("连续 Job 推导展示实际周期分项，不再展示损失或空机批次",()=>{
 const entry=presetPayload(true).derivation;
 const text=renderTeachingDerivation(entry,"PM1");
 assert.match(text,/一个 Job · 13 片/);
 assert.match(text,/260.00 − 100.00/);
 assert.match(text,/60.00 \+ 20.00 =/);
 assert.match(text,/连续 3 个窗口/);
 assert.doesNotMatch(text,/产能损失|批次验证|首片回盒/);
});

test("WAC 推导显示计数与轮转共同复位，不强制使用 13 片",()=>{
 const entry=presetPayload(true).derivation;
 Object.assign(entry.period_timing,{basis:"wac",output_wafers:6,phase_count:1,divisor:1,
  phase_factors:[2,3],candidate_rounds:6,physical_members_per_unit:1});
 const text=renderTeachingDerivation(entry,"PM1");
 assert.match(text,/WAC 重复周期 · 6 片/);
 assert.match(text,/lcm\(2, 3\) = 6/);
 assert.doesNotMatch(text,/13 片/);
});


test("ATM 周期工作量包括搬运主线且不重复计入压力等待",()=>{
 const entry=presetPayload().derivation;
 entry.edges=[
  {source:"ATM_receive0",target:"ATM_home0",label:"atm-receive:LA",minimum_delay_seconds:12},
  {source:"ATM_home0",target:"ATM_raw0",label:"atm-home:LP1",minimum_delay_seconds:8},
  {source:"ATM_feed0",target:"LOCK0",label:"atm-feed:LA",minimum_delay_seconds:100}
 ];
 const text=renderTeachingDerivation(entry,"PM1");
 assert.match(text,/收成品并回程 12.00 \+ 回盒 8.00/);
 assert.match(text,/补片并转位 0.00 = 20.00 秒/);
});
