/**
 * 甘特图页签统一顶栏：把内嵌查看器（movelist_gantt_viewer.html）的工具条搬到控制台顶栏，
 * 并按顶栏实际高度预留查看器视口，使标题栏与甘特图控件合成同一条工具栏。
 * 只管理工具条的呈现位置；查看器自身负责在嵌入时释放工具条占位，
 * 文件加载、缩放、筛选、跳转和绘制仍由查看器持有。
 */

/** 查看器工具条在控制台顶栏中的挂载点；与回放顶栏各自独占一个槽位。 */
const GANTT_TOOLBAR_SLOT_ID = "consoleGanttControls";
const GANTT_FRAME_ID = "ganttFrame";
/** 顶栏换行后的实际高度，供甘特图页签计算查看器区域高度。 */
const TOOLBAR_HEIGHT_VARIABLE = "--console-gantt-toolbar-height";

/**
 * 安装甘特图顶栏呈现，返回把工具条交还查看器并移除观察器的清理函数。
 * 通过原节点搬移保留控件 ID、事件和查看器状态；查看器每次重新加载都会重新接管新文档的工具条。
 */
export function initializeConsoleGanttToolbar(): () => void {
  const shell = document.querySelector<HTMLElement>(".shell");
  const topbar = document.querySelector<HTMLElement>(".console-topbar");
  const slot = document.getElementById(GANTT_TOOLBAR_SLOT_ID);
  const frame = document.getElementById(GANTT_FRAME_ID) as HTMLIFrameElement | null;
  const view = document.querySelector<HTMLElement>('[data-tab-view="gantt"]');
  if (!shell || !topbar || !slot || !frame || !view) return () => {};

  let measuredHeight = 0;
  let mounted: HTMLElement | null = null;
  let mountedDocument: Document | null = null;

  /** 顶栏换行或工具条内容变化时刷新视口边界，不改查看器内部的绘制尺寸。 */
  function measureToolbar(): void {
    if (!view.classList.contains("active") || !mounted) return;
    const height = Math.ceil(topbar.getBoundingClientRect().height);
    if (height === measuredHeight) return;
    measuredHeight = height;
    shell.style.setProperty(TOOLBAR_HEIGHT_VARIABLE, `${height}px`);
  }

  /** 按页签激活状态显示或隐藏工具条；离开页签时顶栏恢复为普通标题栏。 */
  function synchronizeToolbar(): void {
    const active = view.classList.contains("active") && Boolean(mounted);
    slot.hidden = !active;
    topbar.classList.toggle("console-gantt-toolbar", active);
    if (active) {
      measureToolbar();
    } else {
      measuredHeight = 0;
      shell.style.removeProperty(TOOLBAR_HEIGHT_VARIABLE);
    }
  }

  /**
   * 接管查看器文档中的工具条：iframe 每次重新加载都会替换文档，必须先撤下旧节点，
   * 再从新文档搬移，避免顶栏残留上一份已失效的控件。找不到工具条时撤销查看器的
   * 嵌入标记，让它退回自带的工具条，不至于两边都没有控件。
   */
  function adoptToolbar(): void {
    const viewerDocument = frame?.contentDocument;
    mounted = viewerDocument?.querySelector<HTMLElement>(".toolbar") ?? null;
    mountedDocument = mounted ? viewerDocument : null;
    if (viewerDocument) viewerDocument.documentElement.classList.toggle("toolbar-detached", Boolean(mounted));
    slot.replaceChildren();
    if (mounted) slot.append(mounted);
    synchronizeToolbar();
  }

  const observer = new MutationObserver(synchronizeToolbar);
  observer.observe(view, { attributes: true, attributeFilter: ["class"] });
  const resizeObserver = new ResizeObserver(measureToolbar);
  resizeObserver.observe(topbar);
  frame.addEventListener("load", adoptToolbar);
  if (frame.contentDocument?.readyState === "complete") adoptToolbar();
  synchronizeToolbar();

  return () => {
    observer.disconnect();
    resizeObserver.disconnect();
    frame.removeEventListener("load", adoptToolbar);
    if (mounted && mountedDocument) {
      const container = mountedDocument.querySelector(".controls") ?? mountedDocument.body;
      container.append(mounted);
      mountedDocument.documentElement.classList.remove("toolbar-detached");
    }
    mounted = null;
    mountedDocument = null;
    slot.replaceChildren();
    slot.hidden = true;
    topbar.classList.remove("console-gantt-toolbar");
    shell.style.removeProperty(TOOLBAR_HEIGHT_VARIABLE);
  };
}
