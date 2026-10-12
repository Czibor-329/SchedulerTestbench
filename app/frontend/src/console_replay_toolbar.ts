/**
 * 回放页统一顶栏：搬移既有播放、指标和日志工具节点，并按顶栏实际高度预留画布视口。
 * 只管理控件的呈现位置；事件、回放状态、指标计算及画布绘制仍由 workspace_visualizer 持有。
 */

const COMPACT_METRIC_LABELS = ["产能", "平均重算", "瓶颈利用率", "LoadLock 效率"];

/**
 * 安装回放顶栏呈现，返回恢复原始 DOM 位置并移除观察器的清理函数。
 * 通过原节点搬移保留 ID、事件和播放状态；退出回放时恢复节点，避免影响其他页面。
 */
export function initializeConsoleReplayToolbar(): () => void {
  const shell = document.querySelector<HTMLElement>(".shell");
  const topbar = document.querySelector<HTMLElement>(".console-topbar");
  const slot = document.getElementById("consoleReplayControls");
  const playback = document.querySelector<HTMLElement>('[data-tab-view="playback"]');
  const topology = document.getElementById("visualTopologyPlayback");
  const status = playback?.querySelector<HTMLElement>(".replay-status-bar");
  const tools = playback?.querySelector<HTMLElement>(".playback-log-import");
  const metrics = document.getElementById("visualReplayKpis");
  if (!shell || !topbar || !slot || !playback || !topology || !status || !tools) return () => {};

  // 注释锚点保留原位置，不复制任何业务节点或控件 ID。
  const statusAnchor = document.createComment("回放状态条原始位置");
  const toolsAnchor = document.createComment("回放日志工具原始位置");
  status.before(statusAnchor);
  tools.before(toolsAnchor);
  const originalStatusHidden = status.hidden;
  let mounted = false;
  let measuredHeight = 0;

  /** 顶栏使用完整指标名的简写，原统计口径保存在提示中，详细说明仍由指标帮助承载。 */
  function compactMetricLabels(): void {
    metrics?.querySelectorAll<HTMLElement>(".performance-kpi-label > span:first-child").forEach((label, index) => {
      const compact = COMPACT_METRIC_LABELS[index];
      if (!compact || label.textContent === compact) return;
      label.title = label.textContent || compact;
      label.textContent = compact;
    });
  }

  /** 顶栏换行或指标异步载入时刷新视口边界，不改画布内部坐标和设备尺寸。 */
  function measureToolbar(): void {
    if (!mounted) return;
    const height = Math.ceil(topbar.getBoundingClientRect().height);
    if (height === measuredHeight) return;
    measuredHeight = height;
    shell.style.setProperty("--console-replay-toolbar-height", `${height}px`);
  }

  /** 按已确认的业务页签和数据可用状态挂载工具，空状态仍保留日志导入入口。 */
  function synchronizeToolbar(): void {
    const active = playback.classList.contains("active");
    if (active !== mounted) {
      mounted = active;
      if (active) slot.append(status, tools);
      else {
        statusAnchor.after(status);
        toolsAnchor.after(tools);
        status.hidden = originalStatusHidden;
        measuredHeight = 0;
        shell.style.removeProperty("--console-replay-toolbar-height");
      }
    }
    slot.hidden = !active;
    topbar.classList.toggle("console-replay-toolbar", active);
    topbar.classList.toggle("console-replay-empty", active && topology.hidden);
    if (active) {
      status.hidden = topology.hidden;
      measureToolbar();
    }
  }

  const observer = new MutationObserver(synchronizeToolbar);
  observer.observe(playback, { attributes: true, attributeFilter: ["class"] });
  observer.observe(topology, { attributes: true, attributeFilter: ["hidden"] });
  const resizeObserver = new ResizeObserver(measureToolbar);
  resizeObserver.observe(topbar);
  const metricObserver = new MutationObserver(compactMetricLabels);
  if (metrics) metricObserver.observe(metrics, { childList: true, subtree: true });
  compactMetricLabels();
  synchronizeToolbar();

  return () => {
    observer.disconnect();
    resizeObserver.disconnect();
    metricObserver.disconnect();
    statusAnchor.replaceWith(status);
    toolsAnchor.replaceWith(tools);
    status.hidden = originalStatusHidden;
    slot.hidden = true;
    topbar.classList.remove("console-replay-toolbar", "console-replay-empty");
    shell.style.removeProperty("--console-replay-toolbar-height");
  };
}
