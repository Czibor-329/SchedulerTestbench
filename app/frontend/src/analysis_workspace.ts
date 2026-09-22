/**
 * 回放分析固定双栏：左侧产能，右侧在瓶颈和驻留时间之间切换。
 * 仅管理现有 DOM 的可见性、标题栏和键盘导航，不持有指标数据或触发分析请求。
 * 右侧选择和全局展开状态在页面会话内保留；刷新默认瓶颈且全部最小化。
 */
export type AnalysisWindowName = "throughput" | "bottleneck" | "residence";
export type RightAnalysisView = Exclude<AnalysisWindowName, "throughput">;
const WINDOW_TITLES = { throughput: "产能分析", bottleneck: "瓶颈分析", residence: "驻留时间分析" };
const controllers = new WeakMap<HTMLElement, AnalysisWorkspaceController>();

/** 根据右侧选择判断分析视图是否可见；产能始终可见。 */
export function isAnalysisViewVisible(name: AnalysisWindowName, selected: RightAnalysisView): boolean {
  return name === "throughput" || name === selected;
}

/** 初始化固定分析双栏；重新渲染时复用委托监听器及右侧选择，尺寸变化仅重绘产能 SVG。 */
export function mountAnalysisWorkspace(panel: HTMLElement, redrawThroughput: (chart: HTMLElement, range: string) => void): void {
  if (!panel.querySelector("[data-analysis-window]")) return;
  let controller = controllers.get(panel);
  if (!controller) {
    controller = new AnalysisWorkspaceController(panel, redrawThroughput);
    controllers.set(panel, controller);
  }
  controller.mount();
}

/** 管理固定位置、全局展开状态和右侧选择，展开仅提升显示层级，不支持拖动或缩放。 */
class AnalysisWorkspaceController {
  private selected: RightAnalysisView = "bottleneck";
  private expanded = false;
  private topLayer = 1000;
  private resizeObserver: ResizeObserver;

  /** 委托标签点击和方向键导航；尺寸观察仅重绘现有曲线。 */
  constructor(private panel: HTMLElement, redrawThroughput: (chart: HTMLElement, range: string) => void) {
    panel.ownerDocument.defaultView?.addEventListener("resize", () => this.updateExpandedLayout());
    panel.ownerDocument.defaultView?.addEventListener("scroll", () => this.updateExpandedLayout(), true);
    this.resizeObserver = new ResizeObserver(() => {
      const range = panel.querySelector<HTMLSelectElement>("#throughputRangeSelect")?.value ?? "wafer:30";
      panel.querySelectorAll<HTMLElement>("[data-throughput-points]").forEach(chart => {
        if (!chart.hidden) redrawThroughput(chart, range);
      });
      this.updateExpandedLayout();
    });
    panel.addEventListener("click", event => {
      const toggle = (event.target as Element).closest<HTMLElement>("[data-analysis-toggle]");
      if (toggle) {
        this.expanded = !this.expanded;
        this.select(this.selected, false);
        toggle.focus({ preventScroll: true });
        return;
      }
      const tab = (event.target as Element).closest<HTMLElement>("[data-analysis-tab]");
      if (!tab) return;
      this.select(tab.dataset.analysisTab as RightAnalysisView, true);
    });
    panel.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        const window = (event.target as Element).closest<HTMLElement>("[data-analysis-window]");
        if (window?.dataset.expanded === "true") {
          this.expanded = false;
          this.select(this.selected, false);
          this.panel.querySelector<HTMLElement>(`[data-analysis-window="${this.selected}"] [data-analysis-toggle]`)?.focus();
          event.preventDefault();
        }
        return;
      }
      if (!(event.target as Element).matches?.("[data-analysis-tab]")) return;
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "bottleneck" : event.key === "End" ? "residence" : this.selected === "bottleneck" ? "residence" : "bottleneck";
      this.select(next, true);
    });
  }

  /** 创建紧凑标题栏，保留筛选控件节点及其事件，右侧标签与控件同栏。 */
  mount(): void {
    this.resizeObserver.disconnect();
    this.panel.classList.add("analysis-fixed-workspace");
    this.panel.querySelectorAll<HTMLElement>("[data-analysis-window]").forEach(window => {
      const name = window.dataset.analysisWindow as AnalysisWindowName;
      if (window.querySelector(".analysis-window-titlebar")) {
        this.resizeObserver.observe(window);
        return;
      }
      const body = this.panel.ownerDocument.createElement("div");
      body.className = "analysis-window-body";
      while (window.firstChild) body.append(window.firstChild);
      window.append(body);
      const tabs = name === "throughput" ? `<h3>${WINDOW_TITLES[name]}</h3>` : `<div class="analysis-view-tabs" role="tablist" aria-label="右侧分析视图">${(["bottleneck", "residence"] as const).map(view => `<button type="button" role="tab" id="analysis-tab-${name}-${view}" data-analysis-tab="${view}" aria-controls="analysis-view-${view}">${WINDOW_TITLES[view]}</button>`).join("")}</div>`;
      window.insertAdjacentHTML("afterbegin", `<header class="analysis-window-titlebar">${tabs}</header>`);
      const titlebar = window.querySelector<HTMLElement>(".analysis-window-titlebar")!;
      const controls = body.querySelector<HTMLElement>(".analysis-section-head");
      if (controls) {
        Array.from(controls.children).forEach(control => {
          if (!control.classList.contains("analysis-section-title")) titlebar.append(control);
        });
        controls.remove();
      }
      window.id = `analysis-view-${name}`;
      body.id = `analysis-body-${name}`;
      if (name !== "throughput") titlebar.insertAdjacentHTML("beforeend", `<button type="button" class="analysis-window-toggle" data-analysis-toggle aria-controls="analysis-body-throughput analysis-body-bottleneck analysis-body-residence">展开全部</button>`);
      window.setAttribute("role", name === "throughput" ? "region" : "tabpanel");
      if (name === "throughput") window.setAttribute("aria-label", WINDOW_TITLES[name]);
      else window.setAttribute("aria-labelledby", `analysis-tab-${name}-${name}`);
      this.resizeObserver.observe(window);
    });
    this.select(this.selected, false);
    // 页面隐藏/重新显示也会改变工作区尺寸，不能只观察固定定位的子窗口。
    this.resizeObserver.observe(this.panel);
  }

  /** 切换右侧可见视图并同步可访问状态；可选将焦点移到新视图的当前标签。 */
  private select(selected: RightAnalysisView, focus: boolean): void {
    this.selected = selected;
    this.panel.querySelectorAll<HTMLElement>("[data-analysis-window]").forEach(window => {
      const name = window.dataset.analysisWindow as AnalysisWindowName;
      window.hidden = !isAnalysisViewVisible(window.dataset.analysisWindow as AnalysisWindowName, selected);
      const expanded = this.expanded;
      window.dataset.expanded = String(expanded);
      window.querySelector<HTMLElement>(".analysis-window-body")!.hidden = !expanded;
      this.panel.querySelectorAll<HTMLElement>("[data-analysis-toggle]").forEach(toggle => {
        toggle.textContent = expanded ? "最小化" : "展开全部";
        toggle.setAttribute("aria-expanded", String(expanded));
      });
      if (expanded && !window.hidden) {
        window.style.zIndex = String(++this.topLayer);
      } else window.removeAttribute("style");
      window.querySelectorAll<HTMLElement>("[data-analysis-tab]").forEach(tab => {
        const active = tab.dataset.analysisTab === selected;
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
      });
    });
    this.updateExpandedLayout();
    if (focus) this.panel.querySelector<HTMLElement>(`[data-analysis-window="${selected}"] [data-analysis-tab="${selected}"]`)?.focus({ preventScroll: true });
  }

  /** 依据可见工作区更新展开窗口位置和高度；隐藏时保留有效位置，重新显示后重新测量。 */
  private updateExpandedLayout(): void {
    if (!this.expanded || !this.panel.getClientRects().length) return;
    const bounds = this.panel.getBoundingClientRect();
    if (bounds.width <= 0) return;
    const narrow = this.panel.ownerDocument.defaultView!.innerWidth <= 1100;
    this.panel.querySelectorAll<HTMLElement>("[data-analysis-window]").forEach(window => {
      if (window.hidden) return;
      const name = window.dataset.analysisWindow as AnalysisWindowName;
      const width = narrow ? bounds.width : bounds.width * (name === "throughput" ? 1.15 / 2.15 : 1 / 2.15);
      window.style.setProperty("--analysis-overlay-left", `${name === "throughput" || narrow ? bounds.left : bounds.right - width}px`);
      window.style.setProperty("--analysis-overlay-width", `${width}px`);
    });
    this.updateExpandedHeight();
  }

  /** 用右侧实际内容末端确定两窗共享高度，避免正文伸展产生的空白计入高度。 */
  private updateExpandedHeight(): void {
    if (!this.expanded) return;
    const window = this.panel.querySelector<HTMLElement>(`[data-analysis-window="${this.selected}"]`);
    if (!window || window.hidden) return;
    const body = window.querySelector<HTMLElement>(".analysis-window-body")!;
    const header = window.querySelector<HTMLElement>(".analysis-window-titlebar")!;
    const contentBottom = Math.max(body.getBoundingClientRect().top, ...Array.from(body.children)
      .filter(child => child.getClientRects().length > 0)
      .map(child => child.getBoundingClientRect().bottom));
    const windowBorderHeight = 2;
    const height = Math.ceil(header.getBoundingClientRect().height + contentBottom - body.getBoundingClientRect().top + body.scrollTop + windowBorderHeight);
    this.panel.style.setProperty("--analysis-overlay-height", `${height}px`);
  }
}
