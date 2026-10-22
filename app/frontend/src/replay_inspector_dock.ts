/**
 * 回放观察窗口停靠控制。
 *
 * 本模块只管理“晶圆进度”和“合法动作空间”的展开状态与键盘收起行为；
 * 不持有回放数据，也不改变两个观察能力各自的启用开关。
 */

const mountedDocks = new WeakSet<HTMLElement>();

/** 联动整组观察窗口，不改变各自的数据启用开关。 */
export function setReplayInspectorExpanded(dock: HTMLElement, expanded: boolean): void {
  dock.querySelectorAll<HTMLElement>("[data-replay-dock-window]")
    .forEach(window => setReplayDockExpanded(window, expanded));
}

/** 将观察区底边贴在当前可见的分析窗顶边；只调整覆盖层，不参与机器布局。 */
function observeAnalysisBoundary(dock: HTMLElement): void {
  const workspace = dock.closest<HTMLElement>(".topology-playback");
  const panel = workspace?.querySelector<HTMLElement>(".replay-analysis-panel");
  if (!workspace || !panel) return;
  let pending = false;
  const update = (): void => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const bounds = workspace.getBoundingClientRect();
      if (!bounds.height) return;
      const expanded = panel.querySelector<HTMLElement>('.analysis-window[data-expanded="true"]:not([hidden])');
      const boundary = (expanded || panel).getBoundingClientRect().top;
      dock.style.bottom = `${Math.max(0, bounds.bottom - boundary)}px`;
    });
  };
  new MutationObserver(update).observe(panel, { subtree: true, childList: true, attributes: true });
  const resizeObserver = new ResizeObserver(update);
  resizeObserver.observe(workspace);
  resizeObserver.observe(panel);
  window.addEventListener("resize", update);
  update();
}

/** 同步单个停靠窗口的正文可见性、按钮文案和无障碍状态。 */
export function setReplayDockExpanded(window: HTMLElement, expanded: boolean): void {
  const body = window.querySelector<HTMLElement>(".replay-dock-window-body");
  const toggle = window.querySelector<HTMLButtonElement>("[data-replay-dock-toggle]");
  if (!body || !toggle) return;
  window.dataset.expanded = String(expanded);
  body.hidden = !expanded;
  const title = window.querySelector<HTMLElement>("h3")?.textContent || "展开";
  toggle.textContent = expanded ? "最小化" : title;
  toggle.setAttribute("aria-expanded", String(expanded));
}

/**
 * 初始化回放观察停靠区。
 *
 * 两个窗口默认折叠并联动展开；Escape 收起整组窗口。
 */
export function mountReplayInspectorDock(dock: HTMLElement): void {
  dock.querySelectorAll<HTMLElement>("[data-replay-dock-window]")
    .forEach(window => setReplayDockExpanded(window, false));
  if (mountedDocks.has(dock)) return;
  mountedDocks.add(dock);
  observeAnalysisBoundary(dock);
  dock.addEventListener("click", event => {
    const toggle = (event.target as Element).closest<HTMLButtonElement>("[data-replay-dock-toggle]");
    const window = toggle?.closest<HTMLElement>("[data-replay-dock-window]");
    if (!toggle || !window) return;
    setReplayInspectorExpanded(dock, window.dataset.expanded !== "true");
    toggle.focus({ preventScroll: true });
  });
  dock.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    const window = (event.target as Element).closest<HTMLElement>("[data-replay-dock-window]");
    if (!window || window.dataset.expanded !== "true") return;
    setReplayInspectorExpanded(dock, false);
    window.querySelector<HTMLButtonElement>("[data-replay-dock-toggle]")?.focus({ preventScroll: true });
    event.preventDefault();
  });
}
