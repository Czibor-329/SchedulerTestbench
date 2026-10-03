/**
 * 回放对象到甘特图的定位契约。
 *
 * 解析对象卡传入的资源、晶圆、MoveID 和回放时刻，按标准 Move 字段筛选记录，
 * 并在跨代复用 MoveID 时选择与回放时刻最近的版本。保持纯函数，不修改时间轴或 DOM；
 * movelist_gantt_viewer.html 负责应用筛选和选中状态。
 */

type JsonRecord = Record<string, unknown>;

export interface ReplayGanttFocus {
  resource: string;
  wafer: string;
  moveId: string;
  time: number | null;
}

export interface ReplayGanttRecord {
  raw: JsonRecord;
  start: number;
  end: number;
  rawIndex: number;
  removedByRecompute?: boolean;
}

/** 读取查询参数；空名称和非有限时间不建立筛选或游标，缺省行为保持全量浏览。 */
export function parseReplayGanttFocus(search: string): ReplayGanttFocus {
  const parameters = new URLSearchParams(search);
  const rawTime = parameters.get("time")?.trim() ?? "";
  const time = rawTime ? Number(rawTime) : null;
  return {
    resource: parameters.get("resource")?.trim() ?? "",
    wafer: parameters.get("wafer")?.trim() ?? "",
    moveId: parameters.get("moveId")?.trim() ?? "",
    time: time !== null && Number.isFinite(time) ? time : null,
  };
}

/** 将标准 ID 字段转成精确比较值，兼容多片列表与历史单值字段。 */
function identifierValues(values: unknown[]): string[] {
  return values.flatMap((value) => Array.isArray(value) ? value.flat(Infinity) : [value])
    .flatMap((value) => typeof value === "string" ? value.split(/[,;]+/) : [value])
    .filter((value) => value !== null && value !== undefined)
    .map((value) => String(value).trim().toLowerCase())
    .filter(Boolean);
}

/** 判断 Move 是否涉及所选资源和晶圆；两项同时传入时使用交集，资源包括取放机械手。 */
export function matchesReplayGanttFocus(raw: JsonRecord, focus: ReplayGanttFocus): boolean {
  const resource = focus.resource.toLowerCase();
  const wafer = focus.wafer.toLowerCase();
  const resourceNames = identifierValues([
    raw.ModuleName, raw.Robot, raw.RobotName,
    raw.SrcStationList, raw.DestStationList, raw.StationList,
  ]);
  if (resource && !resourceNames.includes(resource)) return false;
  if (!wafer) return true;
  const waferIds = identifierValues([
    raw.MatIDList, raw.MaterialID, raw.MaterialId, raw.MatID, raw.MatId,
    raw.RecvMatList, raw.SendMatList, raw.RecvMatIDList, raw.SendMatIDList,
  ]);
  return waferIds.includes(wafer);
}

/** 计算时刻到动作区间的距离，执行区间内返回零；无效区间不参与定位。 */
function distanceToInterval(record: ReplayGanttRecord, time: number): number {
  if (!Number.isFinite(record.start) || !Number.isFinite(record.end)) return Infinity;
  return Math.max(record.start - time, time - record.end, 0);
}

/**
 * 在可见记录中定位精确 MoveID，返回原记录引用或 null。
 *
 * time 用于区分跨代同号 Move；同距时优先当前计划和较晚开始的版本。
 * 缺少 time 时优先当前计划中的最早动作，缺少 MoveID 时只筛选而不强行选中。
 */
export function findReplayGanttTarget<T extends ReplayGanttRecord>(
  records: T[],
  focus: ReplayGanttFocus,
): T | null {
  const moveId = focus.moveId.toLowerCase();
  if (!moveId) return null;
  const candidates = records.filter((record) => {
    const identifiers = identifierValues([record.raw.MoveID, record.raw.MoveId, record.raw.Move_id]);
    return identifiers.includes(moveId) && matchesReplayGanttFocus(record.raw, focus)
      && Number.isFinite(record.start) && Number.isFinite(record.end);
  });
  candidates.sort((left, right) => {
    if (focus.time !== null) {
      const distance = distanceToInterval(left, focus.time) - distanceToInterval(right, focus.time);
      if (distance) return distance;
    }
    const removed = Number(left.removedByRecompute === true) - Number(right.removedByRecompute === true);
    if (removed) return removed;
    const startOrder = focus.time !== null ? right.start - left.start : left.start - right.start;
    return startOrder || left.rawIndex - right.rawIndex;
  });
  return candidates[0] ?? null;
}
