/**
 * 调度工作台的导航外壳：同步面包屑、折叠桌面侧栏并控制移动端导航。
 * 仅观察业务控制器已确认的页面切换，不参与草稿保存、运行状态或设备数据管理。
 */

const MOBILE_NAVIGATION_QUERY = "(max-width: 760px)";

/**
 * 为现有页面安装导航呈现，返回清理函数。
 * 通过已激活的页签和子菜单读取标题；只修改导航样式、ARIA 与面包屑文本。
 */
export function initializeConsoleShell(): () => void {
  const shell = document.querySelector<HTMLElement>(".shell");
  const sidebar = document.getElementById("primarySidebar");
  const toggle = document.getElementById("sidebarToggle");
  const backdrop = document.getElementById("sidebarBackdrop");
  const closeButton = document.getElementById("sidebarClose");
  const breadcrumb = document.getElementById("consoleBreadcrumb");
  const layout = document.getElementById("pageLayout");
  if (!shell || !sidebar || !toggle || !backdrop || !breadcrumb || !layout) {
    return () => {};
  }

  const mobile = window.matchMedia(MOBILE_NAVIGATION_QUERY);
  let desktopCollapsed = false;
  let mobileOpen = false;
  let currentPage = "";

  // 折叠时仍保留完整的可访问名称，并为图标导航提供悬浮提示。
  sidebar.querySelectorAll<HTMLElement>(".tab, .sidebar-subtab").forEach(link => {
    link.title = link.textContent?.trim() || "";
  });

  /** 同步断点下的导航展开状态，遮挡内容不可获得键盘焦点。 */
  function renderNavigation(): void {
    const expanded = mobile.matches ? mobileOpen : !desktopCollapsed;
    shell.classList.toggle("sidebar-collapsed", !mobile.matches && desktopCollapsed);
    shell.classList.toggle("sidebar-open", mobile.matches && mobileOpen);
    toggle.setAttribute("aria-expanded", String(expanded));
    const label = mobile.matches ? (expanded ? "关闭导航" : "打开导航") : (expanded ? "折叠导航" : "展开导航");
    toggle.setAttribute("aria-label", label);
    toggle.setAttribute("title", label);
    backdrop.hidden = !mobile.matches || !mobileOpen;
    sidebar.inert = mobile.matches && !mobileOpen;
    layout.inert = mobile.matches && mobileOpen;
    const replayControls = document.getElementById("consoleReplayControls");
    if (replayControls) replayControls.inert = mobile.matches && mobileOpen;
  }

  /** 关闭移动导航；键盘关闭时将焦点交还触发按钮。 */
  function closeNavigation(restoreFocus: boolean): void {
    mobileOpen = false;
    renderNavigation();
    if (restoreFocus) toggle.focus();
  }

  /** 仅从成功激活的业务页签同步标题，取消草稿离开时不会提前切换。 */
  function synchronizePage(): void {
    const activeView = document.querySelector<HTMLElement>("[data-tab-view].active");
    const view = activeView?.dataset.tabView || "schedule";
    const section = view === "test-management"
      ? sidebar.querySelector<HTMLElement>("[data-management-target].active")
      : null;
    const activeLink = section || sidebar.querySelector<HTMLElement>(`.tab[data-tab-target="${view}"]`);
    const page = section?.dataset.managementTarget || view;
    const title = activeLink?.textContent?.trim() || "运行分析";
    breadcrumb.textContent = section ? `测试管理 / ${title}` : title;
    document.title = `${title} · 调度平台`;
    if (currentPage && currentPage !== page && mobileOpen) closeNavigation(true);
    currentPage = page;
  }

  /** 点击触发按钮时保留桌面折叠偏好，移动端独立展开。 */
  function toggleNavigation(): void {
    if (mobile.matches) mobileOpen = !mobileOpen;
    else desktopCollapsed = !desktopCollapsed;
    renderNavigation();
    if (mobile.matches && mobileOpen) sidebar.querySelector<HTMLElement>(".tab.active, .sidebar-subtab.active")?.focus();
  }

  /** 断点改变后关闭浮层，避免移动端状态影响桌面工作区。 */
  function resizeNavigation(): void {
    closeNavigation(sidebar.contains(document.activeElement));
  }

  /** 移动导航展开时保持焦点在可见导航内，Escape 关闭后回到触发按钮。 */
  function handleNavigationKey(event: KeyboardEvent): void {
    if (!mobile.matches || !mobileOpen) return;
    // 原生业务对话框处于顶层时，让其自行管理按键和焦点。
    if (document.querySelector("dialog[open]")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closeNavigation(true);
    } else if (event.key === "Tab") {
      const targets = Array.from(sidebar.querySelectorAll<HTMLElement>("a[href], button:not(:disabled)"))
        .filter(element => element.getClientRects().length > 0 && element.tabIndex >= 0);
      const first = targets[0];
      const last = targets.at(-1);
      if (!first || !last) return;
      const focused = document.activeElement;
      if (!sidebar.contains(focused) || (event.shiftKey ? focused === first : focused === last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    }
  }

  /** 点击遮罩可关闭导航，不触发底层业务控件。 */
  function handleBackdrop(): void { closeNavigation(true); }

  /** 再次选择当前页也可收起移动导航，其他切换等待业务控制器确认。 */
  function handleCurrentNavigation(event: MouseEvent): void {
    const target = (event.target as Element).closest<HTMLElement>(".tab.active, .sidebar-subtab.active");
    if (mobile.matches && mobileOpen && target) closeNavigation(true);
  }

  const observer = new MutationObserver(synchronizePage);
  document.querySelectorAll("[data-tab-view], .sidebar-subtab").forEach(element => {
    observer.observe(element, { attributes: true, attributeFilter: ["class"] });
  });
  toggle.addEventListener("click", toggleNavigation);
  backdrop.addEventListener("click", handleBackdrop);
  closeButton?.addEventListener("click", handleBackdrop);
  sidebar.addEventListener("click", handleCurrentNavigation);
  document.addEventListener("keydown", handleNavigationKey);
  mobile.addEventListener("change", resizeNavigation);
  renderNavigation();
  synchronizePage();

  return () => {
    observer.disconnect();
    toggle.removeEventListener("click", toggleNavigation);
    backdrop.removeEventListener("click", handleBackdrop);
    closeButton?.removeEventListener("click", handleBackdrop);
    sidebar.removeEventListener("click", handleCurrentNavigation);
    document.removeEventListener("keydown", handleNavigationKey);
    mobile.removeEventListener("change", resizeNavigation);
    sidebar.inert = layout.inert = false;
    const replayControls = document.getElementById("consoleReplayControls");
    if (replayControls) replayControls.inert = false;
  };
}
