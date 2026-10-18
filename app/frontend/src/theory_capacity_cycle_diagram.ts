/** 理论产能循环约束图：仅布局与解释后端精简核的证据，不计算产能。
 * 上排是真空访问，下排是大气回补；横向为固定轮转顺序而非时间轴。
 * 分类筛选覆盖所有动作约束；节点聚焦仅高亮当前可见关系，不改线的布局；资源负荷由页面的独立卡片展示。
 */
import type {TheoryCapacityCycleDiagram, TheoryCapacityDerivation} from "./analysis_contracts";
import {cycleDiagramLayout, cycleEdgeGeometry, NODE_WIDTH, NODE_HEIGHT, PADDING} from "./theory_capacity_cycle_layout";

const CATEGORIES: Record<string, string> = {overview: "主循环与锁恢复", all: "全部关系",
  vacuum: "VAC 顺序", atmosphere: "ATM 顺序", lock: "锁恢复与交接",
  process: "PM 加工与复用", preparation: "开门准备", handoff: "准时交接", port: "LoadPort 复用"};
const MAX_DEFAULT_GRAPH_HEIGHT = 560;
const RELATION_LEGEND: Record<string, string> = {vacuum:"VAC", atmosphere:"ATM", lock:"锁恢复", process:"PM 复用", preparation:"开门", handoff:"交接", port:"LP 复用"};

/** 将设备名称转义为安全文本，同时适用于 HTML 与 SVG 属性。 */
function escape(value: unknown): string {
  return String(value).replace(/[&<>"']/g, character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[character]!));
}

/** 秒数显示到毫秒；保留数值核提供的精度用于计算，不在此反推周期。 */
function seconds(value = 0): string { return Number(value.toFixed(3)).toString(); }

/** 根据分类和选中动作筛选真实约束；总览显示两机器人主线与锁恢复。 */
export function cycleDiagramEdges(diagram: TheoryCapacityCycleDiagram, category = "overview", selected = "") {
  return diagram.edges.filter(edge => (category === "all" || (category === "overview"
    ? ["vacuum", "atmosphere", "lock"].includes(edge.category) : edge.category === category))
    && (!selected || edge.source === selected || edge.target === selected));
}

/** 将有符号耗时渲染为求和式；负项表示倒推交接时扣除的前置耗时。 */
function delayFormula(parts: TheoryCapacityCycleDiagram["edges"][number]["parts"]): string {
  return parts.map((part, index) => `${part.seconds < 0 ? " − " : index ? " + " : ""}${seconds(Math.abs(part.seconds))}`).join("");
}

/** 展示选中边的设备来源、等待原因及相位代入；period 来自同次后端推导。 */
function renderEdgeInspector(diagram: TheoryCapacityCycleDiagram, edge: TheoryCapacityCycleDiagram["edges"][number], period?: number): string {
  const source = diagram.events.find(event => event.id === edge.source)!;
  const target = diagram.events.find(event => event.id === edge.target)!;
  const elapsed = period === undefined ? undefined : target.phase_seconds - source.phase_seconds + edge.cycle_shift * period;
  return `<div class="cycle-inspector-heading"><div class="cycle-inspector-route"><span><em>第 ${source.round} 轮</em><strong>${escape(`${source.lane === "vacuum" ? "VAC" : "ATM"} 在 ${source.label}`)}</strong></span><span class="cycle-inspector-arrow" aria-hidden="true">→</span><span><em>第 ${target.round} 轮${edge.cycle_shift ? ` · 跨 ${edge.cycle_shift} 周期` : ""}</em><strong>${escape(`${target.lane === "vacuum" ? "VAC" : "ATM"} 在 ${target.label}`)}</strong></span></div><span class="cycle-inspector-badge">${escape(edge.label)}</span></div>
    <p class="cycle-inspector-reason">${escape(edge.detail || edge.label)}</p>
    <div class="cycle-inspector-content"><section class="cycle-delay-evidence" aria-label="等待时长来源"><h3>等待时长来源</h3><div class="cycle-delay-parts">${edge.parts.map(part => `<div class="cycle-delay-part ${part.seconds < 0 ? "deduction" : ""}"><span>${escape(part.label)}</span><b>${part.seconds < 0 ? "−" : ""}${seconds(Math.abs(part.seconds))}<span> 秒</span></b></div>`).join("") || "未提供分项"}</div><div class="cycle-delay-equation">d = ${edge.parts.length ? `${delayFormula(edge.parts)} = ` : ""}<strong>${seconds(edge.minimum_delay_seconds)} 秒</strong></div></section>
    <section class="cycle-phase-evidence" aria-label="约束验算"><h3>约束验算</h3><dl class="cycle-inspector-values"><div><dt>最小开始间隔 d</dt><dd>${seconds(edge.minimum_delay_seconds)}<span> 秒</span></dd></div><div><dt>可行解余量</dt><dd>${seconds(edge.slack_seconds)}<span> 秒</span></dd></div></dl><details class="cycle-phase-details"><summary>查看相位代入验算</summary><div class="cycle-phase-formula">x目标 − x来源 + λT ≥ d${elapsed === undefined ? "" : `<br>${seconds(target.phase_seconds)} − ${seconds(source.phase_seconds)} + ${edge.cycle_shift} × ${seconds(period)} = ${seconds(elapsed)} ≥ ${seconds(edge.minimum_delay_seconds)} 秒<br>余量 = ${seconds(elapsed)} − ${seconds(edge.minimum_delay_seconds)} = ${seconds(edge.slack_seconds)} 秒`}</div></details></section></div>`;
}

/** 渲染可滚动的完整轮转图与逐边解释，所有标注取自当前后端结果。 */
export function renderCycleDiagram(diagram: TheoryCapacityCycleDiagram, category = "all", selected = "", selectedEdge = "", period?: number): string {
  const layout = cycleDiagramLayout(diagram);
  const nodes = new Map(layout.nodes.map(node => [node.id, node]));
  const edges = cycleDiagramEdges(diagram, category);
  const activeEdge = edges.find(edge => edge.id === selectedEdge);
  const paths = edges.map(edge => {
    const related = selected && (edge.source === selected || edge.target === selected);
    const source = nodes.get(edge.source)!, target = nodes.get(edge.target)!;
    const route = layout.routes.find(route => route.edge.id === edge.id)!;
    const geometry = cycleEdgeGeometry(route, layout.routes);
    return `<g class="cycle-relationship ${edge.id === selectedEdge || related ? "selected" : ""} ${(activeEdge && edge.id !== selectedEdge) || (selected && !related) ? "muted" : ""}" data-cycle-edge="${escape(edge.id)}" role="button" tabindex="0" aria-pressed="${edge.id === selectedEdge}" aria-label="${escape(source.label)} → ${escape(target.label)}，${escape(edge.label)}，至少 ${seconds(edge.minimum_delay_seconds)} 秒"><path class="cycle-edge-hit" d="${geometry.hit}"/><path class="cycle-edge-clearance" d="${geometry.path}"/><path class="cycle-edge cycle-edge-${escape(edge.category)} ${edge.cycle_shift ? "cycle-edge-next" : "cycle-edge-current"}" d="${geometry.path}"><title>${escape(edge.detail)}；${escape(edge.label)} ≥ ${seconds(edge.minimum_delay_seconds)} 秒；跨 ${edge.cycle_shift} 周期</title></path><polygon class="cycle-arrow cycle-edge-${escape(edge.category)}" points="${geometry.arrow}"/></g>`;
  }).join("");
  const boxes = layout.nodes.map(node => `<g role="button" tabindex="0" data-cycle-node="${escape(node.id)}" aria-label="第 ${node.round} 轮 ${escape(`${node.lane === "vacuum" ? "VAC" : "ATM"} 在 ${node.label}`)}，查看关联约束" aria-pressed="${node.id === selected}" class="cycle-node ${node.lane} ${node.id === selected ? "selected" : ""} ${activeEdge ? ([activeEdge.source, activeEdge.target].includes(node.id) ? "connected" : "muted") : ""}">
    <rect x="${node.x}" y="${node.y}" width="${NODE_WIDTH}" height="${NODE_HEIGHT}" rx="7"/>
    <text x="${node.x + NODE_WIDTH / 2}" y="${node.y + 22}" text-anchor="middle">${escape(`${node.lane === "vacuum" ? "VAC" : "ATM"} 在 ${node.label}`)}</text>
    <text class="cycle-node-time" x="${node.x + NODE_WIDTH / 2}" y="${node.y + 40}" text-anchor="middle">动作 ${seconds(node.body_seconds)} s</text></g>`).join("");
  const roundLabels = Array.from({length: layout.rounds}, (_, index) => `<text class="cycle-round" x="${PADDING + index * layout.roundWidth}" y="24">第 ${index + 1} 轮</text>`).join("");
  return `<div class="cycle-toolbar"><h2 class="card-title">循环约束图</h2><select data-cycle-category data-compact-label="关系类型" aria-label="循环图关系类型">${Object.entries(CATEGORIES).map(([value, label]) => `<option value="${value}" ${value === category ? "selected" : ""}>${label}</option>`).join("")}</select><div class="cycle-zoom-controls" role="group" aria-label="画布缩放"><button type="button" data-cycle-zoom="out" aria-label="缩小循环图">−</button><output data-cycle-scale>100%</output><button type="button" data-cycle-zoom="in" aria-label="放大循环图">+</button><button type="button" data-cycle-reset>重置视图</button></div><span>${layout.rounds} 轮 · ${edges.length} 条关系</span></div>

    <div class="cycle-legend" aria-label="关系颜色与跨期线宽图例">${Object.entries(RELATION_LEGEND).map(([key, label]) => `<span><i class="cycle-key-${key}" aria-hidden="true"></i>${label}</span>`).join("")}<span class="cycle-period-key"><i aria-hidden="true"></i>本周期</span><span class="cycle-period-key next"><i aria-hidden="true"></i>下一周期</span></div>
    <div class="cycle-workspace"><div class="cycle-scroll" tabindex="0" role="region" aria-label="循环约束图"><svg viewBox="0 0 ${layout.width} ${layout.height}" style="width:100%;height:auto;max-width:${Math.min(layout.width, layout.width * MAX_DEFAULT_GRAPH_HEIGHT / layout.height)}px" role="group" aria-label="完整相位周期的动作交互图">${roundLabels}${paths}${boxes}</svg></div>
    <div class="cycle-inspector" aria-live="polite">${activeEdge ? renderEdgeInspector(diagram, activeEdge, period) : '<div class="cycle-inspector-empty">选择连线查看计算依据</div>'}</div></div>
    `;
}

/** 安装图的筛选及键盘交互；重复计算时替换本容器的处理器，不积累监听。 */
export function mountCycleDiagram(container: HTMLElement, diagram?: TheoryCapacityCycleDiagram, period?: number, syncSelect?: (select: HTMLSelectElement) => void): void {
  const simulation = container.parentElement?.querySelector<HTMLElement>(".theory-simulation-control");
  container.hidden = !diagram;
  if (!diagram) {
    // 错误清空图时保留独立滑块及其监听，下次计算仍能恢复。
    if (simulation) container.before(simulation);
    container.innerHTML = ""; return;
  }
  let category = "all", selected = "", selectedEdge = "", zoom = 1;
  const MIN_ZOOM = 0.25, MAX_ZOOM = 2, ZOOM_STEP = 1.25;
  const layout = cycleDiagramLayout(diagram);
  let panX = 0, panY = 0;
  /** 缩放只改变呈现尺寸，保留节点顺序与数值证据。 */
  const applyZoom = () => {
    const svg = container.querySelector<SVGElement>(".cycle-scroll svg");
    if (svg) svg.setAttribute("viewBox", `${panX + layout.width * (1 - 1 / zoom) / 2} ${panY + layout.height * (1 - 1 / zoom) / 2} ${layout.width / zoom} ${layout.height / zoom}`);
    const output = container.querySelector("[data-cycle-scale]");
    if (output) output.textContent = `${Math.round(zoom * 100)}%`;
  };
  /** 重绘时保留视口与键盘焦点，避免选边后跳回第一轮。 */
  const render = () => {
    const previous = container.querySelector(".cycle-scroll");
    const left = previous?.scrollLeft ?? 0, top = previous?.scrollTop ?? 0;
    container.innerHTML = renderCycleDiagram(diagram, category, selected, selectedEdge, period);
    if (simulation) container.querySelector(".cycle-toolbar")?.append(simulation);
    const select = container.querySelector<HTMLSelectElement>("[data-cycle-category]");
    if (select) syncSelect?.(select);
    applyZoom();
    const viewport = container.querySelector<HTMLElement>(".cycle-scroll")!;
    viewport.scrollLeft = left; viewport.scrollTop = top;
  };
  container.onchange = event => {
    const target = event.target as HTMLSelectElement;
    if (target.matches("[data-cycle-category]")) {
      category = target.value; selected = ""; selectedEdge = ""; render();
      container.querySelector<HTMLElement>(".compact-select-trigger, select")?.focus();
    }
  };
  container.onclick = event => {
    const target = (event.target as Element).closest<HTMLElement>("[data-cycle-node], [data-cycle-edge], [data-cycle-reset], [data-cycle-zoom]");
    if (!target) return;
    const zoomAction = target.dataset.cycleZoom;
    if (zoomAction) {
      zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * (zoomAction === "in" ? ZOOM_STEP : 1 / ZOOM_STEP)));
      applyZoom(); return;
    }
    const node = target.dataset.cycleNode, edge = target.dataset.cycleEdge;
    if (node !== undefined) { selected = selected === node ? "" : node; selectedEdge = ""; }
    else if (edge !== undefined) { selectedEdge = selectedEdge === edge ? "" : edge; selected = ""; }
    else { selected = ""; selectedEdge = ""; category = "all"; zoom = 1; panX = 0; panY = 0; }
    const attribute = node !== undefined ? "data-cycle-node" : edge !== undefined ? "data-cycle-edge" : "data-cycle-reset";
    const value = target.getAttribute(attribute);
    render();
    Array.from(container.querySelectorAll<HTMLElement>(`[${attribute}]`))
      .find(element => element.getAttribute(attribute) === value)?.focus({preventScroll: true});
  };
  container.onkeydown = event => {
    if (event.key === "Escape") { selected = ""; selectedEdge = ""; render(); container.querySelector<HTMLElement>("[data-cycle-reset]")?.focus({preventScroll:true}); }
    if ((event.key === "Enter" || event.key === " ") && (event.target as Element).matches("g[data-cycle-node], g[data-cycle-edge]")) {
      event.preventDefault(); (event.target as Element).dispatchEvent(new MouseEvent("click", {bubbles: true}));
    }
  };
  let drag: {x: number; y: number; left: number; top: number; viewport: HTMLElement} | undefined;
  container.onpointerdown = event => {
    const target = event.target as Element;
    const viewport = target.closest<HTMLElement>(".cycle-scroll");
    if (event.button !== 0 || !viewport || target.closest("[data-cycle-node], [data-cycle-edge]")) return;
    drag = {x:event.clientX, y:event.clientY, left:panX, top:panY, viewport};
    viewport.setPointerCapture(event.pointerId);
  };
  container.onpointermove = event => {
    if (!drag) return;
    const ratio = layout.width / drag.viewport.querySelector("svg")!.getBoundingClientRect().width / zoom;
    panX = drag.left + (drag.x - event.clientX) * ratio;
    panY = drag.top + (drag.y - event.clientY) * ratio;
    applyZoom();
  };
  container.onpointerup = container.onpointercancel = () => { drag = undefined; };
  render();
}

/** 精简核的教学公式采用可行性证据，不能用空关键链伪造闭环比值。 */
export function renderCompactDerivation(entry: TheoryCapacityDerivation): string {
  const facts = entry.facts!, period = entry.period_seconds!, floor = entry.resource_period_lower_bound_seconds!;
  return `<p class="theory-calculation-inputs">${escape(entry.route_name)} · 加工 ${seconds(entry.process_seconds?.[0])} 秒 · 精简周期核</p>
    <div class="theory-formula-step"><span class="theory-step-number">1</span><div><h3>资源负荷下界</h3><p>${facts.rounds_per_cycle} 轮组成完整周期，产出 q = ${facts.units_per_cycle} 片。</p><div class="theory-formula-equation">T资源 = max(Wᵣ) = ${seconds(floor)} 秒</div></div></div>
    <div class="theory-formula-step"><span class="theory-step-number">2</span><div><h3>加入交接约束，求最短可行周期</h3><p>对试探周期 T 检查全部 ${entry.cycle_diagram!.edges.length} 条动作约束及资源下界，二分收敛到最小可行值。图中可查看每条约束和一组可行相位。</p><div class="theory-formula-equation">x目标 − x来源 + λT ≥ d<br>T相位 = ${seconds(period)} 秒</div><p>周期比资源下界多 ${seconds(Math.max(0, period - floor))} 秒；这个差不是各动作等待时间之和。</p></div></div>
    <div class="theory-formula-step"><span class="theory-step-number">3</span><div><h3>换算产能</h3><div class="theory-formula-equation">一次交替 = ${seconds(period)} ÷ ${facts.rounds_per_cycle} = ${seconds(period / facts.rounds_per_cycle)} 秒<br>平均片间隔 = T相位 ÷ q = ${seconds(entry.seconds_per_unit)} 秒 / 片<br>产能 = 3600 × ${facts.units_per_cycle} ÷ ${seconds(period)} = ${seconds(entry.physical_wafers_per_hour)} 片 / 小时</div></div></div>`;
}
