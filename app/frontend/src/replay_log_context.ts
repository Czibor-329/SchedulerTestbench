/**
 * 从标准复现日志保留清洁账本与下一站投影所需的发布上下文。
 * AlgInit 只提供初始设备，AlgSchedule 提供各代 Job/Route；后续计划不能提前进入过去时刻。
 * 本模块不改写 Move 时间、设备数据或持久化格式。
 */
import type { MoveRecord, UnknownRecord } from "./analysis_contracts";
import type { ReplayPlanGeneration } from "./replay_wafer_destinations";

/** 只接受 JSON 对象，防止字符串或数组伪装为协议对象。 */
function record(value: unknown): UnknownRecord | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as UnknownRecord : null;
}

/**
 * 没有 AlgOutput 历史时，仅用各代实际开始的动作构造保守承诺计划。
 * 旧代不采用下一次重算之后才开始的最终落点；最后一代可保留全部未来计划。
 */
export function replayCommittedGenerations(updates: readonly UnknownRecord[], moves: readonly MoveRecord[]): ReplayPlanGeneration[] {
  const byTime = new Map<number, UnknownRecord>();
  for (const update of updates) {
    const time = Number(update.CurrentTime);
    if (Number.isFinite(time) && time >= 0) byTime.set(time, update);
  }
  const published = [...byTime.entries()].sort((left, right) => left[0] - right[0]);
  return published.map(([time, plan], index) => {
    const cutoff = published[index + 1]?.[0] ?? Number.POSITIVE_INFINITY;
    return { time, plan: structuredClone(plan), moves: structuredClone(moves.filter(move => (
      Number(move.StartTime) >= time && Number(move.StartTime) < cutoff))) };
  });
}

/**
 * 保留日志中的标准 update 与各代 MoveList，返回诊断使用的最小计划。
 * 输入只读，返回独立副本；第一代缺失设备时仅从 AlgInit 补缺，不采用后代状态覆盖初值。
 */
export function replayLogContext(entries: readonly UnknownRecord[], topology: UnknownRecord | undefined): {
  plan: Record<string, any> | null;
  updates: Record<string, any>[];
  generations: ReplayPlanGeneration[];
} {
  const updates: Record<string, any>[] = [];
  const generations: ReplayPlanGeneration[] = [];
  let currentUpdate: UnknownRecord | null = null;
  let currentTime = 0;
  for (const entry of entries) {
    const info = record(entry.Info);
    if (!info) continue;
    if (entry.Describe === "AlgSchedule") {
      const value = Number(info.CurrentTime ?? entry.SimTime ?? 0);
      currentTime = Number.isFinite(value) ? value : 0;
      currentUpdate = structuredClone(info);
      currentUpdate.CurrentTime = currentTime;
      if (!updates.length && topology) {
        currentUpdate.Stations ??= structuredClone(topology.Stations);
        currentUpdate.Robots ??= structuredClone(topology.Robots);
      }
      updates.push(currentUpdate);
    } else if (entry.Describe === "AlgOutput" && Array.isArray(info.MoveList)) {
      // 同一时刻重复发布的计划以最后一次为准，避免继续展示已经替换的未来目标。
      const generation = { time: currentTime, moves: structuredClone(info.MoveList) as MoveRecord[], plan: currentUpdate };
      const previous = generations.findIndex(item => item.time === currentTime);
      if (previous >= 0) generations[previous] = generation;
      else generations.push(generation);
    }
  }
  const plan = topology && updates.length ? {
    device: structuredClone(topology), rounds: [], strategy: "",
  } : null;
  return { plan, updates, generations };
}
