/**
 * 回放设备画布的只读信息注释。
 * 给现有图形增加对象身份、晶圆下一站和等待状态；不修改设备布局与机构。
 */
import type { WorkspaceSnapshot } from "./workspace_visualizer";
import { isReplayInventoryPort, replayWaferWaitingSeconds, type ReplayWaferDestination, type ReplayDestinationInput } from "./replay_wafer_destinations";

/** 为已渲染设备添加信息；只操作文字注释、可访问身份和选中状态所需属性。 */
export function annotateReplayTopology(stage: HTMLElement, snapshot: WorkspaceSnapshot,
  destinations: ReadonlyMap<string, ReplayWaferDestination>,
  replayInput?: ReplayDestinationInput): void {
  const ownerDocument = stage.ownerDocument;
  // 注释层可在原 DOM 上更新；删除旧注释，不触碰原设备、门、晶圆与名称节点。
  stage.querySelectorAll(".wafer-next-station, .cooler-slot-occupancy, .topology-waiting-indicator").forEach(node => node.remove());
  for (const wrapper of Array.from(stage.querySelectorAll<HTMLElement>(".reference-module-position"))) {
    const name = wrapper.querySelector<HTMLElement>(".equipment-external-name");
    const module = snapshot.modules.find(module => module.name === wrapper.dataset.replayName);
    if (!name || !module) continue;
    const body = wrapper.querySelector<HTMLElement>(".equipment-card, .equipment-utility");
    if (body) {
      if (body.dataset.replayWaiting === "true") body.removeAttribute("title");
      body.dataset.replayWaiting = "false";
      const displayedSlot = Number(wrapper.dataset.replaySlot ?? 0);
      const displayedWafers = displayedSlot ? module.processSlots?.filter(slot => slot.slot === displayedSlot && slot.wafer).map(slot => slot.wafer) ?? [] : module.wafers;
      const waits = replayInput && module.status === "occupied" ? displayedWafers.map(wafer => (
        { wafer, seconds: replayWaferWaitingSeconds(replayInput, wafer, module.name) })).filter(row => row.seconds !== null) : [];
      if (waits.length) {
        body.dataset.replayWaiting = "true";
        body.title = `${module.name}：${waits.map(row => `${snapshot.waferOrigins[row.wafer] || row.wafer} 加工完成，等待取片 ${row.seconds!.toFixed(1)} s`).join("；")}`;
        const indicator = stage.ownerDocument.createElement("span");
        indicator.className = "topology-waiting-indicator";
        indicator.title = body.title;
        name.appendChild(indicator);
      }
    }
  }
  for (const wafer of Array.from(stage.querySelectorAll<HTMLElement>(".wafer-token[data-replay-wafer]"))) {
    const destination = destinations.get(wafer.dataset.replayWafer ?? "");
    if (destination?.status === "complete") continue;
    // 画布在放片动画中段已经交接，诊断快照则在 Move 完成时才更新；以可见的端口成品隐藏去向。
    let wrapper = wafer.parentElement;
    while (wrapper && !wrapper.classList.contains("reference-module-position")) wrapper = wrapper.parentElement;
    const module = snapshot.modules.find(item => item.name === wrapper?.dataset.replayName);
    if (module && isReplayInventoryPort(module, replayInput?.device ?? null)
      && wafer.classList.contains("wafer-processed")) continue;
    const surface = wafer.querySelector<HTMLElement>(".wafer-origin-label")?.parentElement;
    if (!surface) continue;
    const next = ownerDocument.createElement("span");
    next.className = "wafer-next-station";
    next.textContent = destination?.label ?? "未知";
    next.title = destination?.station || (destination?.candidates.length ? `候选：${destination.candidates.join("、")}` : "下一站未知");
    surface.appendChild(next);
  }
}
