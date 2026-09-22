/** 晶圆发片观察：从完成的 Move 重建每次离港顺序与最近 StepID，不推断超片违规。 */
import type { MoveRecord, DeviceDefinition } from "./analysis_contracts";
import type { WorkspaceSnapshot } from "./workspace_visualizer";
const PICK_TYPES = new Set([0, 2]);
const SWAP_TYPE = 4;
/** 平台计划构建约定的 Dummy 物料编号起点。 */
const DUMMY_MATERIAL_ID_START = 100000;
/** 每份不可变 MoveList 只建立一次排序、步骤和离港索引；加载新计划自动使用新缓存。 */
const timelineCache = new WeakMap<MoveRecord[], {
  departures: { wafer: string; key: string; time: number; moveId: number; source: string; cycle: number }[];
  steps: Map<string, { time: number; step: string }[]>;
  sequences: Map<string, string[]>;
  device: DeviceDefinition | null;
  processJobs: Map<string, string>;
  targets: Map<string, Map<string, string>>;
}>();
/** 协议数组缺省为空，保持原有物料与步骤索引对应。 */
function values(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
/** 转义协议文本，避免物料标识作为 HTML 执行。 */
function escape(value: unknown): string { return String(value ?? "—").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]!)); }
/** 按设备类型识别离港边界；缺少设备时兼容标准端口名称。 */
function isPort(name: string, device: DeviceDefinition | null): boolean {
  const type = String(device?.Stations?.[name]?.Type ?? "").toLowerCase();
  return ["loadport", "dummyport"].includes(type) || /^(LP\d*|P\d+|.*PORT)$/i.test(name);
}
/** TaskID 优先区分补片批次；旧输出退回 PJobName，再退回 MatID。 */
function instanceKey(move: MoveRecord, wafer: string, index: number): string {
  const tasks = values(move.TaskID); const jobs = values(move.PJobName);
  const batch = tasks[index] ?? tasks[0] ?? jobs[index] ?? jobs[0];
  return batch == null ? wafer : `${wafer}\u0000${batch}`;
}
/** 二分查询预计算时序，返回当前离港事件和最近完成 StepID；倒放不沿用未来状态。 */
export function waferDispatchProgress(moves: MoveRecord[], time: number, device: DeviceDefinition | null) {
  const cached = timelineCache.get(moves);
  if (cached && cached.device === device) {
    const progress = new Map<string, string>();
    for (const [key, events] of cached.steps) {
      let lower = 0;
      let upper = events.length;
      while (lower < upper) {
        const middle = Math.floor((lower + upper) / 2);
        if (events[middle].time <= time) lower = middle + 1;
        else upper = middle;
      }
      if (lower > 0) progress.set(key, events[lower - 1].step);
    }
    return { departures: cached.departures.filter(event => event.time <= time), progress };
  }
  const stepEvents = new Map<string, { time: number; step: string }[]>();
  const processJobs = new Map<string, string>();
  const targets = new Map<string, Map<string, string>>();
  const departures: { wafer: string; key: string; time: number; moveId: number; source: string; cycle: number }[] = [];
  const cycles = new Map<string, number>();
  const completed = [...moves].sort((a,b) => Number(a.EndTime)-Number(b.EndTime) || Number(a.MoveID)-Number(b.MoveID));
  for (const move of completed) {
    const swap = Number(move.MoveType) === SWAP_TYPE;
    const groups = swap ? [["RecvMatList", "RecvMatStepIDList"], ["SendMatList", "SendMatStepIDList"]] : [["MatIDList", "StepIDList"]];
    for (const [groupIndex, [materials, steps]] of groups.entries()) values(move[materials]).forEach((id, index) => {
      const step = values(move[steps])[index];
      if (step !== undefined && step !== null) {
        const key = instanceKey(move, String(id), index + (swap && groupIndex === 1 ? values(move.RecvMatList).length : 0));
        const events = stepEvents.get(key) ?? [];
        events.push({ time: Number(move.EndTime), step: String(step) });
        stepEvents.set(key, events);
        const jobIndex = index + (swap && groupIndex === 1 ? values(move.RecvMatList).length : 0);
        const job = values(move.PJobName)[jobIndex] ?? values(move.PJobName)[0];
        if (job != null) processJobs.set(key, String(job));
        const stepTargets = targets.get(key) ?? new Map<string, string>();
        if (move.CurState) stepTargets.set(String(step), String(move.CurState));
        targets.set(key, stepTargets);
      }
    });
    if (!PICK_TYPES.has(Number(move.MoveType)) && !swap) continue;
    values(move[swap ? "RecvMatList" : "MatIDList"]).forEach((id,index) => {
      const source = String(values(move[swap ? "StationList" : "SrcStationList"])[index] ?? "");
      if (!isPort(source, device)) return;
      const wafer = String(id); const key = instanceKey(move, wafer, index);
      const cycle = (cycles.get(key) ?? 0) + 1; cycles.set(key, cycle);
      departures.push({wafer, key, source, cycle, time: Number(move.EndTime), moveId: Number(move.MoveID)});
    });
  }
  timelineCache.set(moves, { departures, steps: stepEvents, sequences: plannedSteps(moves), device, processJobs, targets });
  return waferDispatchProgress(moves, time, device);
}
/** 从完整 MoveList 收集各批晶圆实际出现的步骤顺序；不把 StepID 当成连续整数或百分比。 */
function plannedSteps(moves: MoveRecord[]): Map<string, string[]> {
  const result = new Map<string, string[]>();
  for (const move of [...moves].sort((a,b) => Number(a.EndTime)-Number(b.EndTime) || Number(a.MoveID)-Number(b.MoveID))) {
    const groups = Number(move.MoveType) === SWAP_TYPE
      ? [["RecvMatList", "RecvMatStepIDList"], ["SendMatList", "SendMatStepIDList"]]
      : [["MatIDList", "StepIDList"]];
    groups.forEach(([materials, steps], groupIndex) => values(move[materials]).forEach((id,index) => {
      const step = values(move[steps])[index];
      if (step == null) return;
      const offset = groupIndex === 1 ? values(move.RecvMatList).length : 0;
      const key = instanceKey(move, String(id), index + offset);
      const sequence = result.get(key) ?? [];
      if (!sequence.includes(String(step))) sequence.push(String(step));
      result.set(key, sequence);
    }));
  }
  return result;
}
/** 渲染已进入流程的晶圆步骤节点图。每片只显示最近离港事件，回港后仍保留最后进度。 */
export function renderWaferDispatchProgress(moves: MoveRecord[], snapshot: WorkspaceSnapshot, device: DeviceDefinition | null,
  resolveRoute?: (processJob: string) => Record<string, unknown> | null): string {
  const {departures, progress} = waferDispatchProgress(moves, snapshot.time, device);
  const locations = new Map<string,string>();
  for (const item of [...snapshot.modules, ...snapshot.robots]) for (const wafer of item.wafers) locations.set(String(wafer), item.name);
  const latest = new Map(departures.map((event,index) => [event.wafer, index]));
  const active = departures.map((event,index) => ({event,index})).filter(({event,index}) => {
    return latest.get(event.wafer) === index;
  });
  if (!active.length) return '<p class="wafer-progress-note">当前没有已进入流程的晶圆。</p>';
  const sequences = timelineCache.get(moves)!.sequences;
  const rows = active.map(({event,index}) => {
    const dummy = /dummy/i.test(event.source) || Number(event.wafer) >= DUMMY_MATERIAL_ID_START;
    const step = progress.get(event.key);
    const sequence = sequences.get(event.key) ?? [];
    const current = sequence.indexOf(step ?? "");
    const label = dummy ? event.wafer : snapshot.waferOrigins[event.wafer] || event.wafer;
    const cached = timelineCache.get(moves)!;
    const route = resolveRoute?.(cached.processJobs.get(event.key) ?? "");
    const stages = values(route?.stages) as Record<string, unknown>[];
    const nodes = sequence.map((id,position) => {
      const stage = stages.find(stage => String(stage.stepId) === id);
      const resources = stage ? values(stage.visits).map(visit => String((visit as Record<string,unknown>).stationName ?? "")) : [cached.targets.get(event.key)?.get(id) ?? ""];
      const known = resources.filter(Boolean);
      const robot = known.length > 0 && known.every(name => Boolean(device?.Robots?.[name]));
      const kind = known.length ? robot ? 'robot' : 'station' : 'unknown';
      const description = `${kind === 'robot' ? 'RobotStep' : kind === 'station' ? 'StationStep' : '类型未知'} · ${known.join('/') || '未知模块'} · ${position < current ? '已越过' : position === current ? '当前步骤' : '后续步骤'}`;
      return `<li class="wafer-step step-${kind} ${position < current ? 'is-past' : position === current ? 'is-current' : 'is-future'}" ${position === current ? 'aria-current="step"' : ''} title="${escape(description)}" aria-label="${escape(description)}"><span aria-hidden="true">${kind === 'unknown' ? '?' : ''}</span></li>`;
    }).join('');
    return `<article class="wafer-progress-item${dummy ? ' is-dummy' : ''}" aria-label="${escape(label)}，发片顺序 ${index+1}，当前 Step ${escape(step)}">
      <div class="wafer-progress-heading"><strong title="MatID ${escape(event.wafer)}">${escape(label)}</strong>${dummy ? '<small class="dummy-badge">DUMMY</small>' : ''}<span class="wafer-location" title="${escape(locations.get(event.wafer) ?? event.source)}">${escape(locations.get(event.wafer) ?? event.source)}</span></div>
      ${nodes ? `<ol class="wafer-step-track" aria-label="MoveList 中的步骤顺序">${nodes}</ol>` : '<p class="wafer-progress-note">步骤未知</p>'}
    </article>`;
  }).join('');
  return `<div class="wafer-progress-scroll">${rows}</div>`;
}

/** 仅在可见进度变化时替换内容，并定位到最新进入晶圆；动画帧之间保持原 DOM。 */
export function updateWaferProgressPanel(panel: HTMLElement, html: string): void {
  if (panel.dataset.progressMarkup === html) return;
  panel.innerHTML = html;
  panel.dataset.progressMarkup = html;
  const scroller = panel.querySelector<HTMLElement>(".wafer-progress-scroll");
  if (scroller) scroller.scrollTop = scroller.scrollHeight;
}
