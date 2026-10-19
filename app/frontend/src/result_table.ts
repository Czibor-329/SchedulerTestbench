/**
 * 首页运行结果表格：拥有搜索、状态筛选、分页状态与语义化行展示。
 * 指标与运行选择由页面入口提供；翻页只投影当前选择，不修改测试集合、运行队列或结果。
 */

export const RESULT_TABLE_PAGE_SIZES = [10, 20, 30, 40, 50] as const;
const DEFAULT_PAGE_SIZE = RESULT_TABLE_PAGE_SIZES[0];
const MAX_VISIBLE_PAGES = 5;
const PAGE_EDGE_WINDOW = 3;

/**
 * 行操作按钮的图标：沿用 spark-admin-v1.0.0 模板表格操作按钮的语义
 * （bootstrap-icons 的 eye / eye-slash / play-fill / three-dots），
 * 以内联 SVG 绘制，避免引入模板的图标字体与外部字体依赖。
 */
const RESULT_TABLE_ACTION_ICONS: Record<string, string> = {
  details: '<path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8M1.173 8a13 13 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5s3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5s-3.879-1.168-5.168-2.457A13 13 0 0 1 1.172 8z"/><path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0"/>',
  collapse: '<path d="M13.359 11.238C15.06 9.72 16 8 16 8s-3-5.5-8-5.5a7 7 0 0 0-2.79.588l.77.771A6 6 0 0 1 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755q-.247.248-.517.486z"/><path d="M11.297 9.176a3.5 3.5 0 0 0-4.474-4.474l.823.823a2.5 2.5 0 0 1 2.829 2.829zm-2.943 1.299.822.822a3.5 3.5 0 0 1-4.474-4.474l.823.823a2.5 2.5 0 0 0 2.829 2.829"/><path d="M3.35 5.47q-.27.24-.518.487A13 13 0 0 0 1.172 8l.195.288c.335.48.83 1.12 1.465 1.755C4.121 11.332 5.881 12.5 8 12.5c.716 0 1.39-.133 2.02-.36l.77.772A7 7 0 0 1 8 13.5C3 13.5 0 8 0 8s.939-1.721 2.641-3.238l.708.709zm10.296 8.884-12-12 .708-.708 12 12z"/>',
  run: '<path d="m11.596 8.697-6.363 3.692c-.54.313-1.233-.066-1.233-.697V4.308c0-.63.692-1.01 1.233-.696l6.363 3.692a.802.802 0 0 1 0 1.393"/>',
  more: '<path d="M3 9.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3m5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3"/>',
};

export interface ResultTableRow {
  testId: string;
  testName: string;
  status: string;
  productionText: string;
  recomputeText: string;
  makespanText: string;
  logUrl: string;
  logFileName: string;
  resultUrl: string;
  ganttUrl: string;
  errorText: string;
}

export interface ResultTableState {
  page: number;
  pageSize: number;
  scopeKey: string | null;
  search?: string;
  statuses?: readonly string[];
}

export interface ResultTableContext {
  scope: string;
  selectedTestIds: ReadonlySet<string> | null;
  expandedTestId: string;
}

export interface ResultTablePage {
  rows: ResultTableRow[];
  state: ResultTableState;
  total: number;
  pageCount: number;
  start: number;
  end: number;
}

/** 返回允许的每页条数；异常输入回退到默认值，避免产生空页或无界展示。 */
function normalizePageSize(value: number): number {
  return RESULT_TABLE_PAGE_SIZES.includes(value as typeof RESULT_TABLE_PAGE_SIZES[number])
    ? value : DEFAULT_PAGE_SIZE;
}

/** 创建首屏分页状态；只保存显示偏好，不持有或修改业务运行选择。 */
export function createResultTableState(pageSize: number = DEFAULT_PAGE_SIZE): ResultTableState {
  return { page: 1, pageSize: normalizePageSize(pageSize), scopeKey: null };
}

/** 按“选择测试”的筛选范围取出结果行；null 表示不筛选，输入保持不变。 */
function filterResultRows(
  rows: readonly ResultTableRow[],
  selectedTestIds: ReadonlySet<string> | null,
): readonly ResultTableRow[] {
  return selectedTestIds === null ? rows : rows.filter(row => selectedTestIds.has(row.testId));
}

/**
 * 按设备、组和运行选择投影一页结果；输入均保持不变，返回校正后的新状态。
 * 相同上下文的轮询保留页码，上下文变化回第一页，结果缩减时钳制到末页。
 */
export function projectResultTablePage(
  rows: readonly ResultTableRow[],
  state: ResultTableState,
  context: ResultTableContext,
): ResultTablePage {
  const selectedIds = context.selectedTestIds;
  const scopeKey = JSON.stringify([
    context.scope,
    selectedIds === null ? null : [...selectedIds].sort(),
    (state.search || "").trim().toLocaleLowerCase(),
    [...(state.statuses || [])].sort(),
  ]);
  const search = (state.search || "").trim().toLocaleLowerCase();
  const selectedRows = filterResultRows(rows, selectedIds).filter(row =>
    row.testName.toLocaleLowerCase().includes(search)
    && (!state.statuses?.length || state.statuses.includes(resultStatusKey(row.status))));
  const pageSize = normalizePageSize(state.pageSize);
  const total = selectedRows.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const requestedPage = state.scopeKey === scopeKey && Number.isFinite(state.page)
    ? Math.trunc(state.page) : 1;
  const page = Math.max(1, Math.min(pageCount, requestedPage));
  const offset = (page - 1) * pageSize;
  return {
    rows: selectedRows.slice(offset, offset + pageSize),
    state: { ...state, page, pageSize, scopeKey },
    total,
    pageCount,
    start: total ? offset + 1 : 0,
    end: Math.min(offset + pageSize, total),
  };
}

/** 转义来自设备、测试名称及服务返回字段的 HTML 文本和属性。 */
function escapeResultText(value: string): string {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

/** 保留同源结果 API 和查看器链接；拒绝可执行或跨源 URL 注入。 */
function resultLink(value: string): string {
  return /^\/(?!\/)/.test(value) ? escapeResultText(value) : "";
}

/** 显示可见页附近、首页与末页，长区间折叠，避免大量页码占满底栏。 */
function pageNumbers(page: number, pageCount: number): (number | null)[] {
  if (pageCount <= MAX_VISIBLE_PAGES) return Array.from({ length: pageCount }, (_, index) => index + 1);
  if (page <= PAGE_EDGE_WINDOW) return [1, 2, 3, 4, null, pageCount];
  if (page >= pageCount - PAGE_EDGE_WINDOW + 1) return [1, null, pageCount - 3, pageCount - 2, pageCount - 1, pageCount];
  return [1, null, page - 1, page, page + 1, null, pageCount];
}

/** 行状态的中文标签；未知状态按等待中处理，避免出现空状态格。 */
const RESULT_STATUS_LABELS: Record<string, string> = {
  "not-run": "未运行", queued: "等待中", running: "运行中", succeeded: "成功", failed: "失败", cancelled: "已终止",
};

/** 取规范化后的行状态键。 */
function resultStatusKey(status: string): string {
  return Object.hasOwn(RESULT_STATUS_LABELS, status) ? status : "queued";
}

/**
 * 指标 CSV 的列定义：保留产能、平均重算及 Makespan 导出契约，表头带单位便于表格软件直接阅读。
 * 无值沿用表格中的占位文本，不把缺失当成 0。
 */
const METRICS_CSV_COLUMNS: { header: string; value: (row: ResultTableRow) => string }[] = [
  { header: "测试名称", value: row => row.testName },
  { header: "状态", value: row => RESULT_STATUS_LABELS[resultStatusKey(row.status)] },
  { header: "产能（片/h）", value: row => row.productionText },
  { header: "平均重算（s）", value: row => row.recomputeText },
  { header: "Makespan（s）", value: row => row.makespanText },
];

/** 转义 CSV 单元格：含分隔符、引号或换行时整体加引号并转义内部引号。 */
function csvField(value: string): string {
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** 把结果行的指标序列化为 CSV 文本；只导出三项指标，不改变选择或分页。 */
export function resultTableMetricsCsv(rows: readonly ResultTableRow[]): string {
  const headers = METRICS_CSV_COLUMNS.map(column => csvField(column.header));
  const body = rows.map(row => METRICS_CSV_COLUMNS.map(column => csvField(column.value(row))).join(","));
  return [headers.join(","), ...body].join("\r\n");
}

/** 生成行操作按钮内的 16×16 图标；图标对辅助技术隐藏，按钮名称由 aria-label 提供。 */
function actionIcon(name: string): string {
  return `<svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true" focusable="false">${RESULT_TABLE_ACTION_ICONS[name]}</svg>`;
}

/** 生成指标单元格：缺失指标及仅有基线的产能留白，真实数值（包括零）正常展示。 */
function metricCell(text: string): string {
  const empty = !text.trim() || /^(?:—|-)(?:\/.*)?$/.test(text.trim());
  return `<td class="result-table-number${empty ? " is-empty" : ""}">${empty ? "" : escapeResultText(text)}</td>`;
}

/** 生成独立可访问的行操作；按钮承担键盘入口，表格行保持原生语义。 */
function renderResultRow(row: ResultTableRow, expandedTestId: string): string {
  const status = resultStatusKey(row.status);
  const badgeClass = { succeeded: "success", failed: "failed", queued: "pending", running: "running" }[status] || "neutral";
  const testId = escapeResultText(row.testId);
  const name = escapeResultText(row.testName);
  const expanded = row.testId === expandedTestId;
  const logUrl = resultLink(row.logUrl);
  const resultUrl = resultLink(row.resultUrl);
  const ganttUrl = resultLink(row.ganttUrl);
  const menuId = `result-table-menu-${encodeURIComponent(row.testId)}`;
  return `<tr class="result-table-row ${status}${expanded ? " details-open" : ""}" data-batch-test-card="${testId}">
    <th scope="row"><span class="table-user-name result-table-name" title="${name}">${name}</span></th>
    <td><span class="badge-table ${badgeClass}">${RESULT_STATUS_LABELS[status]}</span></td>
    ${metricCell(row.productionText)}
    ${metricCell(row.recomputeText)}
    <td><div class="result-table-actions">
      <button class="table-btn-action" type="button" title="${expanded ? "收起" : "查看"}详情" data-result-table-details="${testId}" aria-expanded="${expanded}" aria-controls="batchTestDetails" aria-label="${expanded ? "收起" : "查看"} ${name} 的详情">${actionIcon(expanded ? "collapse" : "details")}</button>
      <button class="table-btn-action" type="button" title="运行" data-result-table-run="${testId}" aria-label="运行 ${name}"${["queued", "running"].includes(status) ? " disabled" : ""}>${actionIcon("run")}</button>
      <button class="table-btn-action" type="button" title="更多操作" data-result-table-more="${testId}" aria-haspopup="menu" aria-expanded="false" aria-controls="${menuId}" aria-label="${name} 的更多操作">${actionIcon("more")}</button>
      <div class="result-table-menu" id="${menuId}" popover="auto" role="menu" aria-label="${name} 的结果操作" data-result-table-menu="${testId}">
        ${logUrl ? `<a role="menuitem" data-result-table-menu-action="log" href="${logUrl}" download="${escapeResultText(row.logFileName)}">下载日志</a>` : '<button role="menuitem" type="button" disabled>下载日志</button>'}
        ${resultUrl ? `<button role="menuitem" data-result-table-menu-action="playback" type="button" data-playback-result="${resultUrl}" data-playback-name="${name}" data-playback-log="${logUrl}">回放诊断</button>` : '<button role="menuitem" type="button" disabled>回放诊断</button>'}
        ${ganttUrl ? `<a role="menuitem" data-result-table-menu-action="gantt" href="${ganttUrl}" target="_blank" rel="noopener">查看甘特图</a>` : '<button role="menuitem" type="button" disabled>查看甘特图</button>'}
        ${row.errorText ? `<button role="menuitem" data-result-table-menu-action="error" class="danger" type="button" data-batch-error="${testId}">查看报错</button>` : ""}
      </div>
    </div></td>
  </tr>`;
}

/** 模板分页的单/双箭头按钮；禁用边界页并保留中文可访问名称。 */
function paginationArrow(page: number, label: string, direction: string, disabled: boolean): string {
  const paths: Record<string, string> = {
    first: "m11 5-7 7 7 7m9-14-7 7 7 7", previous: "m15 5-7 7 7 7",
    next: "m9 5 7 7-7 7", last: "m4 5 7 7-7 7m9-14 7 7-7 7",
  };
  return `<li><button class="result-page-button result-page-arrow" type="button" data-result-table-page="${page}" aria-label="${label}"${disabled ? " disabled" : ""}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[direction]}"/></svg></button></li>`;
}

/** 生成当前分页的原生表格与页码栏；空选择也保留表头、范围与禁用导航。 */
export function renderResultTable(page: ResultTablePage, expandedTestId = ""): string {
  const { state, pageCount, total, start, end } = page;
  return `<div class="table-responsive result-table-scroll" tabindex="0" role="region" aria-label="测试运行结果表格，可滚动">
    <table class="table-custom result-table">
      <caption class="sr-only">所选测试的运行结果；单击测试行查看详情，双击加入运行队列。</caption>
      <thead><tr><th scope="col">测试名称</th><th scope="col" class="result-table-status-column">状态</th><th scope="col" class="result-table-number result-table-metric-column" title="单位：片/h">产能</th><th scope="col" class="result-table-number result-table-metric-column" title="单位：s">平均重算</th><th scope="col" class="result-table-actions-column" aria-label="操作"></th></tr></thead>
      <tbody>${page.rows.length ? page.rows.map(row => renderResultRow(row, expandedTestId)).join("") : '<tr><td colspan="5" class="result-table-empty">没有匹配的测试，请调整搜索、状态筛选或测试选择。</td></tr>'}</tbody>
    </table>
  </div>
  <footer class="table-footer-control result-table-footer">
    <label class="result-table-page-size"><span class="result-page-select"><select data-result-table-size data-compact-label="每页条数" aria-label="每页条数">${RESULT_TABLE_PAGE_SIZES.map(size => `<option value="${size}"${size === state.pageSize ? " selected" : ""}>${size}</option>`).join("")}</select></span><span>条 / 页</span></label>
    <span class="sr-only" role="status" aria-live="polite">第 ${start}–${end} 条，共 ${total} 条</span>
    <div class="result-pagination-controls">
      <span class="result-page-position">第 ${state.page} 页，共 ${pageCount} 页</span>
      <nav aria-label="运行结果分页"><ul class="result-table-pagination">
        ${paginationArrow(1, "首页", "first", state.page === 1)}
        ${paginationArrow(state.page - 1, "上一页", "previous", state.page === 1)}
        ${pageNumbers(state.page, pageCount).map(number => number === null ? '<li class="result-table-page-gap" aria-hidden="true">...</li>' : `<li><button class="result-page-button" type="button" data-result-table-page="${number}" aria-label="第 ${number} 页"${number === state.page ? ' aria-current="page"' : ""}>${number}</button></li>`).join("")}
        ${paginationArrow(state.page + 1, "下一页", "next", state.page === pageCount)}
        ${paginationArrow(pageCount, "末页", "last", state.page === pageCount)}
      </ul></nav>
    </div>
  </footer>`;
}

export interface ResultTableController {
  update(rows: readonly ResultTableRow[], context: ResultTableContext): void;
  setExpandedTest(testId: string): void;
  /** 更新显示筛选；不改变运行选择和导出范围。 */
  setFilters(search: string, statuses: readonly string[]): void;
  /** 导出当前筛选范围内的指标 CSV；分页只影响屏幕，不影响导出内容。 */
  metricsCsv(): string;
  dispose(): void;
}

/**
 * 在唯一结果容器上绑定分页与详情/运行按钮；业务操作通过回调交还页面入口。
 * update 保留分页并只重建变化的可见表格，dispose 移除监听；均不修改传入的运行选择。
 */
export function createResultTable(
  container: HTMLElement,
  actions: { showDetails(testId: string): void; runTest(testId: string): void; beforeRender?(): void; afterRender?(): void },
  toolbar?: HTMLElement,
): ResultTableController {
  let state = createResultTableState();
  let rows: readonly ResultTableRow[] = [];
  let context: ResultTableContext = { scope: "", selectedTestIds: null, expandedTestId: "" };
  let lastMarkup = "";
  let activeMenu: HTMLElement | null = null;
  let menuOpener: HTMLButtonElement | null = null;

  /** 关闭顶层菜单；键盘关闭恢复触发按钮，点击其它控件时不抢占焦点。 */
  function closeMenu(restoreFocus = false): void {
    const previousOpener = menuOpener;
    if (activeMenu?.matches(":popover-open")) activeMenu.hidePopover();
    previousOpener?.setAttribute("aria-expanded", "false");
    activeMenu = null;
    menuOpener = null;
    if (restoreFocus) previousOpener?.focus();
  }

  /** 使用浏览器顶层浮层定位结果菜单，避免表格滚动区域裁剪操作。 */
  function openMenu(button: HTMLButtonElement, focusFirst = true): void {
    const menu = container.ownerDocument.getElementById(button.getAttribute("aria-controls") || "");
    if (!menu) return;
    closeMenu();
    activeMenu = menu;
    menuOpener = button;
    menu.showPopover();
    button.setAttribute("aria-expanded", "true");
    const rect = button.getBoundingClientRect();
    const view = container.ownerDocument.defaultView;
    const gap = 6;
    const viewportPadding = 8;
    const viewportWidth = view?.innerWidth || container.ownerDocument.documentElement.clientWidth;
    const viewportHeight = view?.innerHeight || container.ownerDocument.documentElement.clientHeight;
    menu.style.left = `${Math.max(viewportPadding, Math.min(rect.right - menu.offsetWidth, viewportWidth - menu.offsetWidth - viewportPadding))}px`;
    menu.style.top = `${rect.bottom + gap + menu.offsetHeight > viewportHeight - viewportPadding ? Math.max(viewportPadding, rect.top - menu.offsetHeight - gap) : rect.bottom + gap}px`;
    if (focusFirst) menu.querySelector<HTMLElement>('a[href], button:not(:disabled)')?.focus();
  }

  /** 重新投影可见页，异步轮询不重置用户当前页或重复替换相同内容。 */
  function refresh(): void {
    const page = projectResultTablePage(rows, state, context);
    state = page.state;
    const markup = renderResultTable(page, context.expandedTestId);
    if (lastMarkup !== markup || !container.innerHTML) {
      const openTestId = menuOpener?.dataset.resultTableMore;
      const focusedMenuAction = activeMenu?.contains(container.ownerDocument.activeElement)
        ? (container.ownerDocument.activeElement as HTMLElement)?.dataset.resultTableMenuAction : undefined;
      closeMenu();
      actions.beforeRender?.();
      container.innerHTML = markup;
      actions.afterRender?.();
      lastMarkup = markup;
      if (openTestId) {
        const opener = Array.from(container.querySelectorAll<HTMLButtonElement>("[data-result-table-more]"))
          .find(button => button.dataset.resultTableMore === openTestId);
        if (opener) {
          openMenu(opener, false);
          if (focusedMenuAction) activeMenu?.querySelector<HTMLElement>(`[data-result-table-menu-action="${focusedMenuAction}"]`)?.focus();
        }
      }
    }
  }

  /** 分页与明确行操作在容器内处理，不冒泡为测试行的单击详情事件。 */
  function handleClick(event: MouseEvent): void {
    const target = event.target as Element | null;
    if (target?.closest("[data-result-table-menu] a, [data-result-table-menu] button:not(:disabled)")) {
      closeMenu();
      return;
    }
    const button = target?.closest<HTMLButtonElement>("[data-result-table-page], [data-result-table-details], [data-result-table-run], [data-result-table-more]");
    if (!button || !container.contains(button)) {
      closeMenu();
      return;
    }
    if (button.disabled) return;
    event.stopPropagation();
    if (button.dataset.resultTableMore !== undefined) {
      if (menuOpener === button) closeMenu(true);
      else openMenu(button);
    } else if (button.dataset.resultTablePage !== undefined) {
      closeMenu();
      state = { ...state, page: Number(button.dataset.resultTablePage) };
      refresh();
      container.querySelector<HTMLButtonElement>('[aria-current="page"]')?.focus();
    } else if (button.dataset.resultTableDetails !== undefined) {
      closeMenu();
      actions.showDetails(button.dataset.resultTableDetails);
      Array.from(container.querySelectorAll<HTMLButtonElement>("[data-result-table-details]"))
        .find(candidate => candidate.dataset.resultTableDetails === button.dataset.resultTableDetails)?.focus();
    } else if (button.dataset.resultTableRun !== undefined) {
      closeMenu();
      actions.runTest(button.dataset.resultTableRun);
    }
  }

  /** 模板每页条数切换保留当前首行所在区间，不更改运行选择或导出范围。 */
  function handlePageSize(event: Event): void {
    const select = (event.target as Element)?.closest<HTMLSelectElement>("[data-result-table-size]");
    if (!select) return;
    const pageSize = normalizePageSize(Number(select.value));
    state = { ...state, page: Math.floor((state.page - 1) * state.pageSize / pageSize) + 1, pageSize };
    refresh();
    (container.querySelector<HTMLElement>(".result-page-select .compact-select-trigger")
      || container.querySelector<HTMLSelectElement>("[data-result-table-size]"))?.focus();
  }

  /** 结果菜单按常规菜单键盘方式导航，Escape 关闭并返回“更多”。 */
  function handleKeydown(event: KeyboardEvent): void {
    const target = event.target as Element | null;
    const more = target?.closest<HTMLButtonElement>("[data-result-table-more]");
    if (more && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      openMenu(more);
      return;
    }
    if (!activeMenu || !target || !activeMenu.contains(target)) return;
    if (event.key === "Tab") {
      closeMenu(true);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
      return;
    }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    event.stopPropagation();
    const items = Array.from(activeMenu.querySelectorAll<HTMLElement>('a[href], button:not(:disabled)'));
    if (!items.length) return;
    const current = items.indexOf(target as HTMLElement);
    const index = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[index].focus();
  }

  /** 浏览器自动关闭浮层时同步触发按钮，无需抢占外部点击的焦点。 */
  function handleToggle(event: Event): void {
    if (event.target === activeMenu && !activeMenu.matches(":popover-open")) closeMenu();
  }

  /** 更新显示条件并重新分页，运行选择和 CSV 导出范围保持独立。 */
  function setFilters(search: string, statuses: readonly string[]): void {
    state = { ...state, search, statuses: [...statuses] };
    refresh();
  }

  /** 静态工具栏不随轮询重建，因此输入焦点和弹出菜单保持稳定。 */
  function handleFilters(): void {
    const search = toolbar?.querySelector<HTMLInputElement>("#resultTableSearch")?.value || "";
    const statuses = Array.from(toolbar?.querySelectorAll<HTMLInputElement>("[data-result-status]:checked") || []).map(input => input.value);
    const count = toolbar?.querySelector<HTMLElement>("#resultStatusCount");
    if (count) { count.textContent = String(statuses.length); count.hidden = statuses.length === 0; }
    setFilters(search, statuses);
  }

  /** 清除状态条件，保留名称搜索；浮层中的按钮仍可继续通过键盘访问。 */
  function handleFilterClick(event: Event): void {
    if (!(event.target as Element)?.closest("[data-result-status-clear]")) return;
    toolbar?.querySelectorAll<HTMLInputElement>("[data-result-status]").forEach(input => { input.checked = false; });
    handleFilters();
  }

  toolbar?.addEventListener("input", handleFilters);
  toolbar?.addEventListener("click", handleFilterClick);
  container.addEventListener("change", handlePageSize);
  container.addEventListener("click", handleClick);
  container.addEventListener("keydown", handleKeydown);
  container.addEventListener("toggle", handleToggle, true);
  return {
    setFilters,
    update(nextRows, nextContext) {
      rows = nextRows;
      context = nextContext;
      refresh();
    },
    setExpandedTest(testId) {
      context = { ...context, expandedTestId: testId };
      refresh();
    },
    metricsCsv() {
      return resultTableMetricsCsv(filterResultRows(rows, context.selectedTestIds));
    },
    dispose() {
      toolbar?.removeEventListener("input", handleFilters);
      toolbar?.removeEventListener("click", handleFilterClick);
      closeMenu();
      container.removeEventListener("change", handlePageSize);
      container.removeEventListener("click", handleClick);
      container.removeEventListener("keydown", handleKeydown);
      container.removeEventListener("toggle", handleToggle, true);
    },
  };
}
