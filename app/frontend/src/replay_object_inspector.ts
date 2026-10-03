/**
 * 回放对象选择、详情与事件定位。
 * 本模块保存稳定业务键，投影只读信息并委托画布交互；DOM 重绘不会丢失选择。
 * 完成边界占位、已发布计划与算法动作诊断各自保留来源，不推断等待原因。
 */
import type { MoveRecord } from "./analysis_contracts";
import type { ReplayActionDiagnostic, DecisionTraceStep } from "./workspace_visualizer";
import {
  projectReplayWaferDestinations, replayMaterialInstanceKey, replayMoveMaterials,
  replayWaferInstance, replayWaferWaitingSeconds, sameReplayMaterialInstance,
  type ReplayDestinationInput, type ReplayMaterialOccurrence, type ReplayWaferDestination,
} from "./replay_wafer_destinations";

export interface ReplayObjectSelection {
  kind: "module" | "robot" | "slot" | "wafer";
  name?: string;
  slot?: number;
  wafer?: string;
  instanceKey?: string;
}

export interface ReplayObjectInspectorInput extends ReplayDestinationInput {
  decision?: DecisionTraceStep | null;
  resultUrl?: string;
  destinations?: Map<string, ReplayWaferDestination>;

}

export interface ReplayObjectEvent {
  time: number;
  moveId: number;
  label: string;
  boundary: "start" | "end";
}

export interface ReplayObjectDetails {
  key: string;
  selection: ReplayObjectSelection;
  title: string;
  time: number;
  summary: string;
  fields: Array<{ label: string; value: string }>;
  relatedActions: ReplayActionDiagnostic[];
  previousEvent: ReplayObjectEvent | null;
  nextEvent: ReplayObjectEvent | null;
  ganttUrl: string;
}

const TIME_TOLERANCE = 1e-6;
const PICK_TYPES = new Set([0, 2]);
const STATUS_LABELS: Record<string, string> = {
  idle: "空闲", occupied: "已载片", door: "门动作", transfer: "传输中", processing: "加工中",
  cleaning: "清洁中", environment: "环境切换", closed: "关闭", opening: "开门中", open: "开启",
  closing: "关门中", doorless: "无门", enabled: "使能", "physical-blocked": "物理拦截", "deadlock-blocked": "死锁规则拦截",
};
const MOVE_NAMES: Record<number, string> = {
  0: "取片", 1: "放片", 2: "多片取片", 3: "多片放片", 4: "换片", 5: "转位", 6: "开门",
  7: "关门", 8: "后置完成", 9: "加工", 10: "环境切换", 11: "对准", 12: "抽气", 13: "充气", 14: "清洁",
};

/** 转义日志文本，防止模块名和诊断原因执行 HTML。 */
function escape(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]!));
}
/** 用一位小数展示回放秒数，保留零且避免负等待。 */
function seconds(value: number): string { return `${Math.max(0, value).toFixed(1)} s`; }

/** 根据业务对象生成固定键；物料实例不会随着当前位置改变。 */
export function replayObjectKey(selection: ReplayObjectSelection): string {
  return JSON.stringify(selection.kind === "wafer"
    ? [selection.kind, selection.wafer ?? "", selection.instanceKey ?? ""]
    : [selection.kind, selection.name ?? "", selection.kind === "slot" ? selection.slot ?? 0 : 0]);
}

/**
 * 判断选择是否仍对应当前物料实例，供对象卡、选中反馈和动作筛选共用。
 * 非晶圆对象始终属于当前观察范围；晶圆按 Task/PJob 稳定键判定，旧选择缺省键时兼容当前实例。
 * 此函数只读取回放输入，不把复用 MatID 的新片动作或清洁状态归给旧实例。
 */
export function replayObjectIsCurrent(input: ReplayDestinationInput, selection: ReplayObjectSelection): boolean {
  if (selection.kind !== "wafer") return true;
  if (!selection.wafer) return false;
  return !selection.instanceKey || selection.instanceKey === replayMaterialInstanceKey(replayWaferInstance(input, selection.wafer));
}

/** 对物料选择恢复最初实例；倒放或补片复用时不切到另一批晶圆。 */
function selectedMaterial(input: ReplayDestinationInput, selection: ReplayObjectSelection): ReplayMaterialOccurrence | null {
  if (!selection.wafer) return null;
  const material = replayWaferInstance(input, selection.wafer);
  if (!selection.instanceKey || replayMaterialInstanceKey(material) === selection.instanceKey) return material;
  const saved = input.moves.flatMap(replayMoveMaterials).find(row => replayMaterialInstanceKey(row) === selection.instanceKey);
  return saved ?? null;
}

/** 只匹配原始协议明确引用的资源、槽位或晶圆，不通过路径推测关联动作。 */
export function replayMoveMatchesObject(input: ReplayDestinationInput, move: MoveRecord, selection: ReplayObjectSelection): boolean {
  const materials = replayMoveMaterials(move);
  if (selection.kind === "wafer") {
    const material = selectedMaterial(input, selection);
    return Boolean(material && materials.some(row => sameReplayMaterialInstance(material, row)));
  }
  if (selection.kind === "slot") return materials.some(row => (
    row.station === selection.name && row.slot === selection.slot
    || move.ModuleName === selection.name && row.robotSlot === selection.slot));
  return move.ModuleName === selection.name || materials.some(row => row.station === selection.name)
    || ["SrcStationList", "DestStationList", "StationList"].some(field => (
      Array.isArray(move[field]) && (move[field] as unknown[]).map(String).includes(selection.name ?? "")));
}

/** 从相关 Move 的开始/结束边界查找前后事件；相同时刻合并，倒放仍使用纯时间索引。 */
export function replayObjectEvents(input: ReplayDestinationInput, selection: ReplayObjectSelection): {
  events: ReplayObjectEvent[]; previousEvent: ReplayObjectEvent | null; nextEvent: ReplayObjectEvent | null;
} {
  const events = input.moves.filter(move => replayMoveMatchesObject(input, move, selection)).flatMap(move => {
    const label = MOVE_NAMES[Number(move.MoveType)] ?? `动作 ${move.MoveType}`;
    return (["start", "end"] as const).map(boundary => ({ time: Number(move[boundary === "start" ? "StartTime" : "EndTime"]),
      moveId: Number(move.MoveID ?? 0), label: `${label}${boundary === "start" ? "开始" : "结束"}`, boundary }));
  }).filter(event => Number.isFinite(event.time)).sort((a, b) => a.time - b.time || a.moveId - b.moveId);
  const time = input.snapshot.time;
  return { events,
    previousEvent: events.filter(event => event.time < time - TIME_TOLERANCE).at(-1) ?? null,
    nextEvent: events.find(event => event.time > time + TIME_TOLERANCE) ?? null };
}

/** 匹配已有动作诊断的明确端点；未知来源时不声称无拦截。 */
export function replayActionMatchesObject(action: ReplayActionDiagnostic, selection: ReplayObjectSelection): boolean {
  if (selection.kind === "wafer") return action.materialIds.includes(selection.wafer ?? "");
  if (selection.kind === "slot") return action.source === selection.name && action.sourceSlot === selection.slot
    || action.destination === selection.name && action.destinationSlot === selection.slot;
  return [action.robot, action.source, action.destination].includes(selection.name ?? "");
}

/** 生成甘特定位链接；缺少已保存结果时不制造不可用链接。 */
export function replayObjectGanttUrl(resultUrl: string | undefined, selection: ReplayObjectSelection, time: number, moveId?: number): string {
  if (!resultUrl) return "";
  const parameters = new URLSearchParams({ src: resultUrl, time: String(time) });
  if (selection.kind === "wafer" && selection.wafer) parameters.set("wafer", selection.wafer);
  else if (selection.name) parameters.set("resource", selection.name);
  if (moveId !== undefined) parameters.set("moveId", String(moveId));
  return `/movelist_gantt_viewer.html?${parameters}`;
}

/** 根据完成边界快照返回晶圆当前位置，保持内部 ID 与用户显示编号分开。 */
function waferLocation(input: ReplayDestinationInput, wafer: string): { name: string; slot: number } | null {
  for (const module of input.snapshot.modules) {
    if (!module.wafers.includes(wafer)) continue;
    const slots = [...module.loadPortSlots, ...module.loadLockSlots, ...module.processSlots ?? []];
    return { name: module.name, slot: slots.find(row => row.wafer === wafer)?.slot ?? 0 };
  }
  for (const robot of input.snapshot.robots) {
    if (robot.wafers.includes(wafer)) return { name: robot.name,
      slot: Number(Object.entries(robot.slotWafers ?? {}).find(([, material]) => material === wafer)?.[0] ?? 0) };
  }
  return null;
}

/** 把选中对象投影为简略字段，等待时长只基于已完成工艺证据，原因只引用算法诊断。 */
export function projectReplayObjectDetails(input: ReplayObjectInspectorInput, selection: ReplayObjectSelection): ReplayObjectDetails {
  const snapshot = input.snapshot;
  const destinations = input.destinations ?? projectReplayWaferDestinations(input);
  const currentObject = replayObjectIsCurrent(input, selection);
  const fields: ReplayObjectDetails["fields"] = [];
  const add = (label: string, value: unknown): void => { if (value !== undefined && value !== null && value !== "") fields.push({ label, value: String(value) }); };
  const module = snapshot.modules.find(item => item.name === selection.name);
  const robot = snapshot.robots.find(item => item.name === selection.name);
  let title = selection.name ?? "对象信息";
  let summary = "";
  let wafers: string[] = [];
  if (selection.kind === "wafer") {
    const wafer = selection.wafer ?? "";
    title = snapshot.waferOrigins[wafer] || wafer;
    const destination = destinations.get(wafer);
    const sameInstance = currentObject;
    const location = sameInstance ? waferLocation(input, wafer) : null;
    add("当前位置", location ? `${location.name}${location.slot ? `.${location.slot}` : ""}` : "当前实例未在机");
    add("MatID", wafer);
    const material = selectedMaterial(input, selection);
    add("Task / PJob", [material?.taskId, material?.pjobName].filter(Boolean).join(" / "));
    add("当前 Step", sameInstance ? destination?.stepId : material?.stepId);
    add("下一站", sameInstance ? destination?.label ?? "未知" : "未知");
    if (sameInstance && destination?.candidates.length) add("候选站点", destination.candidates.join(" / "));
    if (location) wafers = [wafer];
    summary = location ? `晶圆 ${title} · ${location.name}` : `晶圆 ${title} · 当前实例未在机`;
  } else if (selection.kind === "slot") {
    title = `${selection.name}.${selection.slot}`;
    const slot = module ? [...module.loadPortSlots, ...module.loadLockSlots, ...module.processSlots ?? []].find(row => row.slot === selection.slot) : null;
    const wafer = slot?.wafer || (robot?.slotWafers ?? {})[selection.slot ?? 0] || "";
    add("占位", wafer ? snapshot.waferOrigins[wafer] || wafer : "空槽");
    if (wafer) { wafers = [wafer]; add("下一站", destinations.get(wafer)?.label ?? "未知"); }
    const arm = robot?.arms?.find(row => row.slots.includes(selection.slot ?? 0));
    if (arm) add("物理臂", `${arm.name} · ${arm.enabled ? "启用" : "禁用"}`);
    summary = `${title} · ${wafer ? "有片" : "空槽"}`;
  } else if (module) {
    add("状态", STATUS_LABELS[module.status] ?? module.status);
    add("门", module.loadLockDoors ? `上侧 ${STATUS_LABELS[module.loadLockDoors.top]} / 下侧 ${STATUS_LABELS[module.loadLockDoors.bottom]}` : STATUS_LABELS[module.door]);
    add("环境", module.environment);
    add("占位", `${module.wafers.length} / ${module.slotCapacity}`);
    wafers = module.wafers;
    add("晶圆", wafers.map(wafer => snapshot.waferOrigins[wafer] || wafer).join(" / "));
    summary = `${title} · ${STATUS_LABELS[module.status] ?? module.status}`;
  } else if (robot) {
    add("状态", robot.busy ? "执行中" : "待命");
    add("当前动作", robot.activeMoveName);
    add("搬运", [robot.source, robot.target].filter(Boolean).join(" → "));
    add("臂槽", robot.arms?.map(arm => `${arm.name}${arm.enabled ? "" : "（禁用）"}: ${arm.slots.map(slot => `${slot}=${snapshot.waferOrigins[robot.slotWafers?.[slot] ?? ""] || robot.slotWafers?.[slot] || "空"}`).join(", ")}`).join("；"));
    wafers = robot.wafers;
    summary = `${title} · ${robot.busy ? "执行中" : "待命"}`;
  } else {
    summary = `${title} · 当前无对象状态`;
  }

  // 活动 Move 采用真实回放时间；业务槽位保持完成边界语义。
  const active = currentObject ? snapshot.activeMoves.filter(move => replayMoveMatchesObject(input, move, selection)) : [];
  if (active.length) add("进行中", active.map(move => `${MOVE_NAMES[Number(move.MoveType)] ?? move.MoveType} · 剩余 ${seconds(Number(move.EndTime) - snapshot.time)}`).join("；"));
  for (const wafer of wafers) {
    const location = waferLocation(input, wafer);
    const chamber = snapshot.modules.find(item => item.name === location?.name);
    if (!chamber) continue;
    const waiting = replayWaferWaitingSeconds(input, wafer, location!.name);
    if (waiting === null) continue;
    const picking = active.some(move => (PICK_TYPES.has(Number(move.MoveType)) || Number(move.MoveType) === 4)
      && replayMoveMaterials(move).some(row => row.wafer === wafer && row.direction !== "send"));
    if (picking) { add("交接", `${snapshot.waferOrigins[wafer] || wafer} 正在取片`); continue; }
    add("待取片", `${snapshot.waferOrigins[wafer] || wafer} · 已等待 ${seconds(waiting)}`);
  }
  const relatedActions = currentObject
    ? (input.decision?.actionDiagnostics ?? []).filter(action => replayActionMatchesObject(action, selection)) : [];
  if (!currentObject) add("动作诊断", "所选实例未在机，当前动作不属于该实例");
  else if (input.decision?.actionDiagnosticsSource === "algorithm") {
    add("关联动作", `使能 ${relatedActions.filter(action => action.status === "enabled").length} · 拦截 ${relatedActions.filter(action => action.status !== "enabled").length}`);
    const reasons = [...new Set(relatedActions.filter(action => action.status !== "enabled" && action.reason).map(action => action.reason))];
    if (reasons.length) add("已知拦截", reasons.join("；"));
    add("诊断时刻", `${seconds(input.decision.time)}（完成边界）`);
  } else add("动作诊断", input.decision ? "当前算法未提供动作诊断" : "查询未开启或未加载");
  const events = replayObjectEvents(input, selection);
  return { key: replayObjectKey(selection), selection, title, time: snapshot.time, summary, fields, relatedActions,
    previousEvent: events.previousEvent, nextEvent: events.nextEvent,
    ganttUrl: replayObjectGanttUrl(input.resultUrl, selection, snapshot.time,
      active[0]?.MoveID ?? events.previousEvent?.moveId) };
}

/** 渲染唯一信息卡；事件按钮给出明确时刻，内容不重建业务状态。 */
export function renderReplayObjectDetails(details: ReplayObjectDetails, filtered = false): string {
  const eventButton = (event: ReplayObjectEvent | null, direction: string): string => `<button type="button" data-replay-seek="${event?.time ?? ""}"${event ? "" : " disabled"} title="${escape(event ? `${event.label} · ${seconds(event.time)}` : "没有相关事件")}">${direction}</button>`;
  return `<section class="replay-object-details" aria-label="${escape(details.title)} 对象信息"><header class="replay-object-heading"><h3>${escape(details.title)}</h3><time>${seconds(details.time)}</time><button type="button" data-replay-clear aria-label="关闭对象信息">×</button></header>
    <dl>${details.fields.map(field => `<div><dt>${escape(field.label)}</dt><dd>${escape(field.value)}</dd></div>`).join("")}</dl>
    <footer>${eventButton(details.previousEvent, "上一事件")}${eventButton(details.nextEvent, "下一事件")}<button type="button" data-replay-filter aria-pressed="${filtered}">${filtered ? "显示全部动作" : "仅看关联动作"}</button>${details.ganttUrl ? `<a href="${escape(details.ganttUrl)}" target="_blank" rel="noopener">甘特图定位</a>` : ""}</footer></section>`;
}

/** 读取节点业务属性，晶圆选择即时保存当前实例，不依赖 DOM 生命周期。 */
function selectionFromElement(element: HTMLElement, input: ReplayDestinationInput): ReplayObjectSelection | null {
  const kind = element.dataset.replayKind as ReplayObjectSelection["kind"];
  if (!["module", "robot", "slot", "wafer"].includes(kind)) return null;
  const selection: ReplayObjectSelection = { kind, name: element.dataset.replayName,
    slot: Number(element.dataset.replaySlot ?? 0), wafer: element.dataset.replayWafer };
  if (kind === "wafer" && selection.wafer) selection.instanceKey = replayMaterialInstanceKey(replayWaferInstance(input, selection.wafer));
  return selection;
}

/** 让俯视晶圆、正视槽位和真实模块共享选中反馈，实例变化后不突出复用的新片。 */
export function applyReplayObjectSelection(root: HTMLElement, input: ReplayDestinationInput, selection: ReplayObjectSelection | null): void {
  const location = selection?.kind === "wafer" && selection.wafer ? waferLocation(input, selection.wafer) : null;
  const sameInstance = selection?.kind === "wafer" && replayObjectIsCurrent(input, selection);
  const destination = sameInstance && selection?.wafer ? projectReplayWaferDestinations(input).get(selection.wafer) : null;
  const nextStation = destination?.station;
  const selectedSlotModule = selection?.kind === "slot" ? input.snapshot.modules.find(module => module.name === selection.name) : null;
  const selectedSlotRobot = selection?.kind === "slot" ? input.snapshot.robots.find(robot => robot.name === selection.name) : null;
  const selectedSlotWafer = selection?.kind === "slot" ? [...selectedSlotModule?.loadPortSlots ?? [],
    ...selectedSlotModule?.loadLockSlots ?? [], ...selectedSlotModule?.processSlots ?? []].find(slot => slot.slot === selection.slot)?.wafer
    || selectedSlotRobot?.slotWafers?.[selection.slot ?? 0] || "" : "";
  for (const element of Array.from(root.querySelectorAll<HTMLElement>("[data-replay-kind]"))) {
    const kind = element.dataset.replayKind;
    const name = element.dataset.replayName;
    const slot = Number(element.dataset.replaySlot ?? 0);
    const wafer = element.dataset.replayWafer;
    let selected = false;
    if (selection) {
      if (selection.kind === "wafer") selected = sameInstance
        && (wafer === selection.wafer || kind === "slot" && name === location?.name && slot === location?.slot);
      else if (selection.kind === "slot") selected = Boolean(name === selection.name && (slot === selection.slot
        || kind === "module" || kind === "robot") || selectedSlotWafer && kind === "wafer" && wafer === selectedSlotWafer);
      else selected = name === selection.name && kind !== "wafer";
    }
    element.classList.toggle("is-replay-selected", selected);
    const stationWrapper = kind === "module" || kind === "slot" && element.classList.contains("reference-module-position");
    element.classList.toggle("is-replay-current-station", Boolean(sameInstance && stationWrapper && name === location?.name
      && (kind !== "slot" || !location?.slot || slot === location.slot)));
    element.classList.toggle("is-replay-next-target", Boolean(nextStation && stationWrapper && name === nextStation
      && (kind !== "slot" || !destination?.stationSlot || slot === destination.stationSlot)));
    element.dataset.replaySelected = String(selected);
    if (element.getAttribute("role") === "button") element.setAttribute("aria-pressed", String(selected));
  }
}

export interface ReplayObjectInspectorOptions {
  root: HTMLElement;
  panel: HTMLElement;
  onSeek: (time: number) => void;
  onFilter?: (selection: ReplayObjectSelection | null) => void;
}

/** 保存单一对象选择并通过事件委托处理重绘后的节点；播放更新字段但不自动暂停。 */
export class ReplayObjectInspectorController {
  private input: ReplayObjectInspectorInput | null = null;
  private selected: ReplayObjectSelection | null = null;
  private filtered = false;

  /** 构造只读观察控制器；回放时间与动作筛选由调用方拥有。 */
  constructor(private options: ReplayObjectInspectorOptions) {
    options.root.addEventListener("click", event => this.handleClick(event));
    options.root.addEventListener("keydown", event => {
      if (event.key === "Escape") { if (this.selected) { this.clear(); event.preventDefault(); event.stopPropagation(); } return; }
      if (!["Enter", " "].includes(event.key) || !this.input) return;
      const object = (event.target as Element).closest<HTMLElement>("[data-replay-kind]");
      if (!object) return;
      event.preventDefault();
      this.select(selectionFromElement(object, this.input));
    });
  }

  /** 返回稳定选择副本，避免调用方修改控制器状态。 */
  get selection(): ReplayObjectSelection | null { return this.selected ? { ...this.selected } : null; }

  /** 每帧重新投影选中反馈与卡片；对象键和筛选状态保持不变。 */
  update(input: ReplayObjectInspectorInput): void { this.input = input; this.render(); }

  /** 清除对象与关联筛选，空白点击和 Escape 共用相同语义。 */
  clear(): void {
    const wasFiltered = this.filtered;
    this.selected = null; this.filtered = false;
    if (wasFiltered) this.options.onFilter?.(null);
    this.render();
  }

  /** 设置新对象；再次点击同一业务键会清除选择。 */
  select(selection: ReplayObjectSelection | null): void {
    if (selection && this.selected && replayObjectKey(selection) === replayObjectKey(this.selected)) { this.clear(); return; }
    this.selected = selection;
    if (this.filtered) this.options.onFilter?.(selection);
    this.render();
  }

  /** 委托卡片控制与画布点击；工具栏和观察窗口空白不会取消选择。 */
  private handleClick(event: MouseEvent): void {
    if (!this.input) return;
    const target = event.target as Element;
    if (target.closest("[data-replay-clear]")) { this.clear(); return; }
    const seek = target.closest<HTMLElement>("[data-replay-seek]");
    if (seek && seek.dataset.replaySeek !== "" && !seek.hasAttribute("disabled")) {
      this.options.onSeek(Number(seek.dataset.replaySeek)); return;
    }
    if (target.closest("[data-replay-filter]")) {
      this.filtered = !this.filtered;
      this.options.onFilter?.(this.filtered ? this.selected : null); this.render(); return;
    }
    const object = target.closest<HTMLElement>("[data-replay-kind]");
    if (object) { this.select(selectionFromElement(object, this.input)); return; }
    if (!this.options.panel.contains(target) && target.closest(".topology-unified-canvas")) this.clear();
  }

  /** 只在文本或选中时刻变化时替换卡片，避免无变化帧打断键盘焦点。 */
  private render(): void {
    if (!this.input) return;
    applyReplayObjectSelection(this.options.root, this.input, this.selected);
    this.options.panel.hidden = !this.selected;
    const markup = this.selected ? renderReplayObjectDetails(projectReplayObjectDetails(this.input, this.selected), this.filtered) : "";
    if (this.options.panel.dataset.replayObjectMarkup === markup) return;
    const focus = this.options.panel.ownerDocument.activeElement as HTMLElement | null;
    const attribute = focus && this.options.panel.contains(focus)
      ? ["data-replay-clear", "data-replay-seek", "data-replay-filter"].find(name => focus.hasAttribute(name)) : null;
    const focusValue = attribute ? focus!.getAttribute(attribute) : null;
    this.options.panel.innerHTML = markup;
    this.options.panel.dataset.replayObjectMarkup = markup;
    if (attribute) {
      const candidates = Array.from(this.options.panel.querySelectorAll<HTMLElement>(`[${attribute}]`));
      (candidates.find(element => element.getAttribute(attribute) === focusValue) ?? candidates[0])?.focus({ preventScroll: true });
    }
  }
}
