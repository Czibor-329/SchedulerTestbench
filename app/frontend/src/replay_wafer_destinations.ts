/**
 * 回放晶圆下一落站投影。
 * 从当前代已发布的 Place/Swap 确认落站，缺少显式计划时仅列 Route 候选；
 * 保留 TaskID/PJob 实例，跳过 RobotStep，不把后续重算代的选择泄漏到过去。
 */
import type { DeviceDefinition, MoveRecord, UnknownRecord } from "./analysis_contracts";
import type { ModuleSnapshot, WorkspaceSnapshot } from "./workspace_visualizer";

const PICK_TYPES = new Set([0, 2]);
const PLACE_TYPES = new Set([1, 3]);
const SWAP_TYPE = 4;
const PROCESS_TYPE = 9;
const TIME_TOLERANCE = 1e-6;
interface MaterialTimelineEntry { move: MoveRecord; material: ReplayMaterialOccurrence }
/** MoveList 在一次加载中不可变；物料索引只构建一次，避免播放帧逐片扫描整个计划。 */
const materialTimelines = new WeakMap<MoveRecord[], Map<string, { starts: MaterialTimelineEntry[]; ends: MaterialTimelineEntry[] }>>();

export interface ReplayPlanGeneration {
  time: number;
  moves: MoveRecord[];
  /** 标准 AlgSchedule.Info 或前端运行计划。 */
  plan?: UnknownRecord | null;
}

export interface ReplayDestinationInput {
  snapshot: WorkspaceSnapshot;
  moves: MoveRecord[];
  device: DeviceDefinition | null;
  plan?: UnknownRecord | null;
  generations?: ReplayPlanGeneration[];
  /** 前端计划的 Route 名称解析由计划所有者提供，不在这里复制别名规则。 */
  resolveRoute?: (pjobName: string, time: number) => UnknownRecord | null;
}

export interface ReplayWaferDestination {
  wafer: string;
  instanceKey: string;
  taskId: string;
  pjobName: string;
  stepId: string;
  label: string;
  station: string;
  /** 显式 Place/Swap 的目标物理槽位；缺少槽位时为 0。 */
  stationSlot?: number;
  status: "confirmed" | "pending" | "unknown" | "complete";
  candidates: string[];
}

export interface ReplayMaterialOccurrence {
  wafer: string;
  taskId: string;
  pjobName: string;
  stepId: string;
  /** Swap 公共 TaskID/PJobName 按收片、送片依次拼接。 */
  index: number;
  direction: "receive" | "send" | "material";
  station: string;
  slot: number;
  robotSlot: number;
}

/** 协议列表缺省为空，字段仍按同一物料下标对齐。 */
function list(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
/** 从协议对象读取内嵌记录；不是对象时保持未知。 */
function record(value: unknown): UnknownRecord | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : null;
}
/** 单站 Swap 可以共用一个 StationList 条目，其他标准列表使用对应下标。 */
function stationAt(move: MoveRecord, field: string, index: number): string {
  const entries = list(move[field]);
  return String(entries[index] ?? (entries.length === 1 ? entries[0] : "") ?? "");
}

/** 按物料建立开始与完成索引，排序保留同刻 MoveID 的协议顺序。 */
function materialTimeline(moves: MoveRecord[], wafer: string): { starts: MaterialTimelineEntry[]; ends: MaterialTimelineEntry[] } {
  let timelines = materialTimelines.get(moves);
  if (!timelines) {
    timelines = new Map();
    for (const move of moves) for (const material of replayMoveMaterials(move)) {
      const timeline = timelines.get(material.wafer) ?? { starts: [], ends: [] };
      timeline.starts.push({ move, material }); timeline.ends.push({ move, material });
      timelines.set(material.wafer, timeline);
    }
    for (const timeline of timelines.values()) {
      timeline.starts.sort((a, b) => Number(b.move.StartTime ?? b.move.EndTime ?? 0) - Number(a.move.StartTime ?? a.move.EndTime ?? 0)
        || Number(b.move.MoveID ?? 0) - Number(a.move.MoveID ?? 0));
      timeline.ends.sort((a, b) => Number(b.move.EndTime ?? 0) - Number(a.move.EndTime ?? 0)
        || Number(b.move.MoveID ?? 0) - Number(a.move.MoveID ?? 0));
    }
    materialTimelines.set(moves, timelines);
  }
  return timelines.get(wafer) ?? { starts: [], ends: [] };
}

/** 展开普通与 Swap 物料，统一实例、目标站槽位和机器手槽位字段。 */
export function replayMoveMaterials(move: MoveRecord): ReplayMaterialOccurrence[] {
  const swap = Number(move.MoveType) === SWAP_TYPE;
  const receiveCount = list(move.RecvMatList).length;
  const groups = swap
    ? [{ field: "RecvMatList", steps: "RecvMatStepIDList", direction: "receive" as const, offset: 0 },
      { field: "SendMatList", steps: "SendMatStepIDList", direction: "send" as const, offset: receiveCount }]
    : [{ field: "MatIDList", steps: "StepIDList", direction: "material" as const, offset: 0 }];
  return groups.flatMap(group => list(move[group.field]).map((wafer, index) => {
    const commonIndex = group.offset + index;
    const pick = PICK_TYPES.has(Number(move.MoveType));
    return {
      wafer: String(wafer),
      taskId: String(list(move.TaskID)[commonIndex] ?? ""),
      pjobName: String(list(move.PJobName)[commonIndex] ?? ""),
      stepId: String(list(move[group.steps])[index] ?? ""),
      index: commonIndex,
      direction: group.direction,
      station: swap ? stationAt(move, "StationList", index)
        : stationAt(move, pick ? "SrcStationList" : PLACE_TYPES.has(Number(move.MoveType)) ? "DestStationList" : "StationList", index)
          || (!pick && !PLACE_TYPES.has(Number(move.MoveType)) ? String(move.ModuleName ?? "") : ""),
      slot: Number(list(move[swap ? group.direction === "receive" ? "StnSendSlotList" : "StnRecvSlotList"
        : pick ? "SrcSlotList" : PLACE_TYPES.has(Number(move.MoveType)) ? "DestSlotList" : "SlotList"])[index] ?? 0),
      robotSlot: Number(list(move[swap ? group.direction === "receive" ? "RecvSlotList" : "SendSlotList" : "RobotSlotList"])[index] ?? 0),
    };
  }));
}

/** 用物料及所属 Task/PJob 生成稳定实例键，避免补片与 Dummy 复用串线。 */
export function replayMaterialInstanceKey(material: Pick<ReplayMaterialOccurrence, "wafer" | "taskId" | "pjobName">): string {
  return JSON.stringify([material.wafer, material.taskId, material.pjobName]);
}

/** 有完整实例字段时严格匹配，旧输出缺省字段只与已知字段兼容。 */
export function sameReplayMaterialInstance(left: ReplayMaterialOccurrence, right: ReplayMaterialOccurrence): boolean {
  return left.wafer === right.wafer
    && (!left.taskId || !right.taskId || left.taskId === right.taskId)
    && (!left.pjobName || !right.pjobName || left.pjobName === right.pjobName);
}

/** 返回当前已发布代；等时发布按输入顺序使用最后一代。 */
export function replayGenerationAtTime(input: ReplayDestinationInput): ReplayPlanGeneration | null {
  let selected: ReplayPlanGeneration | null = null;
  for (const generation of input.generations ?? []) {
    if (generation.time <= input.snapshot.time + TIME_TOLERANCE
      && (!selected || generation.time >= selected.time)) selected = generation;
  }
  return selected;
}

/** 找到当前片的最近已开始实例；未发片时使用当前代最早出现的实例。 */
export function replayWaferInstance(input: ReplayDestinationInput, wafer: string): ReplayMaterialOccurrence {
  const time = input.snapshot.time;
  const current = replayGenerationAtTime(input);
  const started = materialTimeline(input.moves, wafer).starts.find(row => Number(row.move.StartTime ?? row.move.EndTime ?? 0) <= time + TIME_TOLERANCE);
  let selected = started?.material;
  // 新一代快照可在首个 Pick 前换盒；不能继续借用复用 MatID 的上一盒实例。
  const publishedMaterial = list(current?.plan?.Materials ?? input.plan?.Materials).map(record).filter(Boolean)
    .find(row => String(row!.ID ?? row!.MatID ?? row!.Name ?? "") === wafer);
  if (publishedMaterial && (!started || current && Number(started.move.StartTime ?? 0) < current.time)) {
    selected = { wafer, taskId: String(publishedMaterial.TaskID ?? selected?.taskId ?? ""),
      pjobName: String(publishedMaterial.PJobName ?? selected?.pjobName ?? ""), stepId: String(publishedMaterial.StepID ?? ""),
      index: 0, direction: "material", station: String(publishedMaterial.CurrentModuleName ?? ""),
      slot: Number(publishedMaterial.SlotID ?? 0), robotSlot: 0 };
  }
  if (!selected) {
    const future = materialTimeline(current?.moves ?? (input.generations?.length ? [] : input.moves), wafer).starts;
    selected = future.at(-1)?.material;
  }
  return selected ?? { wafer, taskId: "", pjobName: "", stepId: "", index: 0, direction: "material", station: "", slot: 0, robotSlot: 0 };
}

/** 读取标准快照内嵌 Route；生产片可从 ProcessJobs.OriginRoute 获得共享路径。 */
function embeddedRoute(plan: UnknownRecord | null | undefined, material: ReplayMaterialOccurrence): UnknownRecord | null {
  const match = list(plan?.Materials).map(record).find(row => row
    && String(row.ID ?? row.MatID ?? row.Name ?? "") === material.wafer
    && (!material.taskId || !row.TaskID || String(row.TaskID) === material.taskId));
  const materialRoute = record(match?.Route);
  if (materialRoute) return materialRoute;
  const job = list(plan?.ProcessJobs).map(record).find(row => row && (
    String(row.JobName ?? row.PJobName ?? row.Name ?? "") === material.pjobName
    || list(row.MatList).map(String).includes(material.wafer)));
  return record(job?.OriginRoute) ?? record(job?.Route);
}

/** 沿 PostStepID 穿过搬运步骤，返回第一层 Station 候选，环路保持有限。 */
function stationCandidates(route: UnknownRecord | null, stepId: string, device: DeviceDefinition | null): string[] {
  const stages = list(route?.stages ?? route?.RouteSteps).map(record).filter(Boolean) as UnknownRecord[];
  const id = (stage: UnknownRecord): string => String(stage.stepId ?? stage.StepID ?? "");
  const currentIndex = stages.findIndex(stage => id(stage) === stepId);
  if (currentIndex < 0) return [];
  const nextIds = (stage: UnknownRecord): string[] => list(stage.postStepIds ?? stage.PostStepID).map(String);
  const direct = nextIds(stages[currentIndex]);
  const queue = direct.length ? direct : stages[currentIndex + 1] ? [id(stages[currentIndex + 1])] : [];
  const visited = new Set<string>();
  const stations = new Set<string>();
  while (queue.length) {
    const step = queue.shift()!;
    if (visited.has(step)) continue;
    visited.add(step);
    const stage = stages.find(row => id(row) === step);
    if (!stage) continue;
    const resources = list(stage.visits ?? stage.Visits).map(record)
      .map(visit => String(visit?.stationName ?? visit?.ModuleName ?? visit?.StationName ?? "")).filter(Boolean);
    const knownStations = resources.filter(name => !device?.Robots?.[name]);
    if (knownStations.length) knownStations.forEach(name => stations.add(name));
    else queue.push(...nextIds(stage));
  }
  return [...stations];
}

/** 识别回放库存端口，设备类型缺失时使用快照类型、端口槽位或既有端口命名约定。 */
export function isReplayInventoryPort(module: ModuleSnapshot, device: DeviceDefinition | null): boolean {
  const stationType = String(device?.Stations?.[module.name]?.Type || module.type || "").trim().toLowerCase();
  return ["loadport", "dummyport"].includes(stationType)
    || Boolean(module.loadPortSlots.length)
    || !stationType && /^(LP\d*|P\d+|.*PORT)$/i.test(module.name);
}

/**
 * 投影当前在机晶圆的下一落站。显式 Place/Swap 优先，Route 候选只显示“待定”；
 * 返回 Map 以便拓扑与对象卡共享同一语义，输入快照和 MoveList 不会被修改。
 */
export function projectReplayWaferDestinations(input: ReplayDestinationInput): Map<string, ReplayWaferDestination> {
  const result = new Map<string, ReplayWaferDestination>();
  const time = input.snapshot.time;
  const generation = replayGenerationAtTime(input);
  const scheduled = [...input.snapshot.activeMoves, ...(generation?.moves ?? (input.generations?.length ? [] : input.moves))]
    .filter(move => Number(move.EndTime ?? 0) > time + TIME_TOLERANCE)
    .sort((a, b) => Number(a.StartTime ?? 0) - Number(b.StartTime ?? 0) || Number(a.MoveID ?? 0) - Number(b.MoveID ?? 0));
  // 一帧只展开一次未来落站，避免每张在机晶圆重复解析整份 MoveList。
  const placements = new Map<string, ReplayMaterialOccurrence[]>();
  for (const move of scheduled) {
    const type = Number(move.MoveType);
    if (!PLACE_TYPES.has(type) && type !== SWAP_TYPE) continue;
    for (const material of replayMoveMaterials(move)) {
      if (type === SWAP_TYPE && material.direction !== "send" || !material.station || input.device?.Robots?.[material.station]) continue;
      const rows = placements.get(material.wafer) ?? [];
      rows.push(material);
      placements.set(material.wafer, rows);
    }
  }
  const locations = new Map<string, string>();
  for (const item of [...input.snapshot.modules, ...input.snapshot.robots]) item.wafers.forEach(wafer => locations.set(wafer, item.name));
  for (const [wafer, location] of locations) {
    const material = replayWaferInstance(input, wafer);
    const completed = materialTimeline(input.moves, wafer).ends.filter(row => Number(row.move.EndTime ?? 0) <= time + TIME_TOLERANCE
      && sameReplayMaterialInstance(material, row.material));
    // 转位等辅助动作不能覆盖最后一次取放的归库证据。
    const last = completed.find(row => PICK_TYPES.has(Number(row.move.MoveType))
      || PLACE_TYPES.has(Number(row.move.MoveType)) || Number(row.move.MoveType) === SWAP_TYPE);
    const confirmedStep = completed.find(row => row.material.stepId)?.material.stepId ?? (publishedStep(input, material));
    const base = { wafer, taskId: material.taskId, pjobName: material.pjobName, stepId: confirmedStep,
      instanceKey: replayMaterialInstanceKey(material), station: "", candidates: [] as string[] };
    const placement = placements.get(wafer)?.find(row => sameReplayMaterialInstance(material, row));
    const station = placement?.station ?? "";
    const stationSlot = placement?.slot ?? 0;
    if (station) {
      result.set(wafer, { ...base, label: station, station, stationSlot, status: "confirmed" });
      continue;
    }
    const module = input.snapshot.modules.find(item => item.name === location);
    if (module && isReplayInventoryPort(module, input.device)
      && (module.processedWafers.includes(wafer)
        || last && (PLACE_TYPES.has(Number(last.move.MoveType)) || last.material.direction === "send")
          && last.material.station === location)) {
      result.set(wafer, { ...base, label: "", status: "complete" });
      continue;
    }
    const route = embeddedRoute(generation?.plan ?? input.plan, material)
      ?? input.resolveRoute?.(material.pjobName, time) ?? null;
    const candidates = stationCandidates(route, confirmedStep, input.device);
    result.set(wafer, { ...base, label: candidates.length ? "待定" : "未知",
      candidates, status: candidates.length ? "pending" : "unknown" });
  }
  return result;
}

/** 标准代快照的 StepID 是发布时的事实，用于尚未产生完成 Move 的新物料。 */
function publishedStep(input: ReplayDestinationInput, material: ReplayMaterialOccurrence): string {
  const plan = replayGenerationAtTime(input)?.plan ?? input.plan;
  const state = list(plan?.Materials).map(record).find(row => row && String(row.ID ?? row.MatID ?? row.Name ?? "") === material.wafer
    && (!material.taskId || !row.TaskID || String(row.TaskID) === material.taskId));
  return state?.StepID === undefined ? "" : String(state.StepID);
}

/** 仅对当前驻片且本次进腔后已完成工艺返回等待时长；缺少完成 Move 时保留 null。 */
export function replayWaferWaitingSeconds(input: ReplayDestinationInput, wafer: string, station: string): number | null {
  const module = input.snapshot.modules.find(item => item.name === station);
  if (!module?.wafers.includes(wafer)) return null;
  const material = replayWaferInstance(input, wafer);
  const completed = materialTimeline(input.moves, wafer).ends.filter(({ move, material: occurrence }) => (
    Number(move.EndTime) <= input.snapshot.time + TIME_TOLERANCE && sameReplayMaterialInstance(material, occurrence)));
  const arrival = completed.find(({ move, material: occurrence }) => occurrence.station === station
    && (PLACE_TYPES.has(Number(move.MoveType)) || occurrence.direction === "send"));
  const arrivalTime = Number(arrival?.move.EndTime ?? Number.NEGATIVE_INFINITY);
  const finishes = completed.filter(({ move }) => Number(move.MoveType) === PROCESS_TYPE
    && move.ModuleName === station && Number(move.EndTime) >= arrivalTime).map(({ move }) => Number(move.EndTime));
  if (input.snapshot.activeMoves.some(move => Number(move.MoveType) === PROCESS_TYPE && move.ModuleName === station
    && replayMoveMaterials(move).some(row => sameReplayMaterialInstance(material, row)))) return null;
  return finishes.length ? Math.max(0, input.snapshot.time - Math.max(...finishes)) : null;
}
