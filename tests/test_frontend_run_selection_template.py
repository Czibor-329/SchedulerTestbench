"""首页运行选择区的稳定结构契约测试。"""

from __future__ import annotations

import re
from pathlib import Path


EDITOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "frontend"
    / "config_editor.html"
)


def test_run_selection_keeps_group_after_transfer_actions() -> None:
    """首页不再提供单测选择，并将测试组放在导入导出操作之后。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    run_device_control = re.search(
        r'<div class="run-device-control">(?P<content>.*?)</div>\s*</div>\s*</div>\s*</article>',
        template,
        re.DOTALL,
    )

    assert run_device_control is not None
    content = run_device_control.group("content")
    assert 'id="runTestSelect"' not in template
    assert content.index('id="workspaceImportButton"') < content.index('id="workspaceExportButton"')
    assert content.index('id="workspaceExportButton"') < content.index('id="runGroupSelect"')


def test_run_actions_only_offer_settings_and_selected_tests() -> None:
    """运行区不保留单测入口或标题，设置与所选测试操作同行展示。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")

    assert 'id="runButton"' not in template
    assert ">开始运行<" not in template
    assert 'id="openRunSettingsButton"' in template
    assert 'id="batchRunButton">▶ 运行所选测试<' in template
    assert template.index('id="batchRunButton"') < template.index('id="openRunSettingsButton"')


def test_test_selection_button_lives_in_the_run_strategy_card() -> None:
    """选择测试入口位于右侧运行策略卡；结果区首行不重复提供日志导出或全部甘特图入口。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    strategy_card = template.split('<aside class="side" id="scheduleSide">', 1)[1].split("</aside>", 1)[0]
    preview_area = template.split('id="runPreviewArea"', 1)[1].split('id="batchTestDetails"', 1)[0]

    assert 'id="batchResultFilterButton"' in strategy_card
    assert 'id="batchResultFilterButton"' not in preview_area
    assert 'id="batchLogButton"' not in template
    assert 'id="resultBatchLogButton"' in template
    assert 'id="batchGanttButton"' in template
    assert template.index('id="resultBatchLogButton"') < template.index('id="batchGanttButton"')


def test_selected_tests_run_without_reopening_the_picker() -> None:
    """运行按钮直接使用结果区当前选择，不重复打开选择弹窗。"""
    source = (
        EDITOR_PATH.parent / "src" / "config_editor.ts"
    ).read_text(encoding="utf-8")

    run_function = source.split("async function runCurrentTestGroup", 1)[1].split(
        "/** 请求终止当前批量任务", 1
    )[0]
    assert "return runCurrentTestGroup(selectedIds, runOptions);" in run_function
    assert "openBatchTestSelectionDialog();" not in run_function


def test_starting_a_batch_keeps_result_cards_visible() -> None:
    """等待服务创建批次时立即显示排队卡片，分析报告入口保持禁用。"""
    source = (
        EDITOR_PATH.parent / "src" / "config_editor.ts"
    ).read_text(encoding="utf-8")
    run_function = source.split("async function runCurrentTestGroup", 1)[1].split(
        "/** 请求终止当前批量任务", 1
    )[0]

    assert 'status: "queued"' in run_function
    assert "renderBatchItems(queuedItems);" in run_function
    assert "updateAnalysisReportAvailability();" in run_function


def test_cancelled_card_does_not_repeat_status_below_metrics() -> None:
    """已终止测试仅在状态标签展示，避免与卡片说明重复。"""
    source = (
        EDITOR_PATH.parent / "src" / "config_editor.ts"
    ).read_text(encoding="utf-8")
    render_function = source.split("function renderBatchItems", 1)[1].split(
        "/** 打开批量测试报错信息", 1
    )[0]

    assert 'item.status === "cancelled" ? "调度已终止"' not in render_function


def test_result_rows_toggle_readonly_test_details_below_preview_table() -> None:
    """结果表按需展示只读详情，详情位于结果预览面板下方，不再外包一层面板。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")
    table_source = (EDITOR_PATH.parent / "src" / "result_table.ts").read_text(encoding="utf-8")
    schedule_view = template.split(
        '<div class="tab-view active" data-tab-view="schedule">', 1
    )[1].split('<div class="tab-view" data-tab-view="analysis">', 1)[0]

    assert template.index('id="runPreviewArea"') < template.index('id="runResultsView"')
    assert template.index('id="resultBatchLogButton"') < template.index('id="batchTestDetails"')
    assert 'id="runPreviewArea"' in schedule_view
    assert 'id="batchTestDetails"' in schedule_view
    assert 'id="testGroupAnalysisPanel"' not in schedule_view
    assert 'class="batch-test-details"' in template
    assert 'class="panel batch-test-details"' not in template
    assert 'data-batch-test-card=' in table_source
    assert 'aria-controls="batchTestDetails"' in table_source
    assert "async function toggleBatchTestDetails" in source
    assert "/api/workspaces/${state.workspaceDeviceId}/tests/${encodeURIComponent(testId)}" in source
    assert "batch-test-details-head" not in source


def test_analysis_wizard_selects_tests_without_reference_comparison() -> None:
    """分析向导第二步选择要分析的测试，不再提供参考测试对比。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")

    assert "选择分析的测试" in template
    assert "参与对比的测试" not in template
    assert 'id="analysisReferenceTest"' not in template
    assert "参考测试" not in template
    assert "referenceCaseId" not in source
    assert "groupAnalysisComparisonKey" not in source
    assert 'id="analysisWindowMode"' in template
    assert 'id="analysisTimeBudget"' in template


def test_result_rows_keep_accessible_run_and_detail_controls() -> None:
    """结果行保留稳定交互入口；事件隔离与排队顺序由 TypeScript 行为测试验证。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    table_source = (EDITOR_PATH.parent / "src" / "result_table.ts").read_text(encoding="utf-8")

    assert 'id="batchResults"' in template
    assert 'data-batch-test-card=' in table_source
    assert 'data-result-table-run=' in table_source
    assert 'data-result-table-details=' in table_source
    assert 'aria-controls="batchTestDetails"' in table_source


def test_result_actions_export_metrics_csv_next_to_gantt() -> None:
    """结果区底部在“全部甘特图”右侧提供指标 CSV 导出，内容为表格中的三项指标。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    table_source = (EDITOR_PATH.parent / "src" / "result_table.ts").read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")

    assert template.index('id="batchGanttButton"') < template.index('id="exportMetricsCsvButton"')
    assert "exportResultMetricsCsv" in source
    assert "export function resultTableMetricsCsv" in table_source
    assert "metricsCsv()" in table_source
    for header in ("产能（片/h）", "平均重算（s）", "Makespan（s）"):
        assert header in table_source


def test_analysis_report_is_a_workspace_tab_without_batch_progress() -> None:
    """分析报告是工作空间下的独立页签；结果标题栏不再内嵌预览/分析切换。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")

    assert 'data-tab-target="analysis"' in template
    assert '<div class="tab-view" data-tab-view="analysis">' in template
    assert 'id="generateAnalysisReportButton"' in template
    assert 'id="resultPreviewViewButton"' not in template
    assert 'id="analysisReportViewButton"' not in template
    assert 'id="testGroupAnalysisButton"' not in template
    assert 'id="batchProgress"' not in template
    assert 'function canOpenAnalysisReport()' in source
    assert 'button.disabled = !enabled;' in source
    assert 'switchTab("analysis")' in source


def test_gantt_viewer_is_an_in_app_tab_under_result_diagnostics() -> None:
    """甘特图是“结果诊断”分组下的页签，工具条并入控制台顶栏，初始只展示开头时间窗。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")
    toolbar_source = (EDITOR_PATH.parent / "src" / "gantt_toolbar.ts").read_text(encoding="utf-8")
    viewer = (EDITOR_PATH.parent / "movelist_gantt_viewer.html").read_text(encoding="utf-8")
    diagnostic_group = template.split('aria-labelledby="diagnosticNavigationTitle"', 1)[1]
    diagnostic_group = diagnostic_group.split("</nav>", 1)[0]

    assert 'data-tab-target="gantt"' in diagnostic_group
    assert '<div class="tab-view" data-tab-view="gantt">' in template
    assert 'id="ganttFrame"' in template
    assert 'id="batchGanttButton"' in template
    assert 'id="consoleGanttControls"' in template
    assert "console_gantt_toolbar.css" in template
    assert "initializeConsoleGanttToolbar()" in source
    assert ".toolbar" in toolbar_source
    assert "function openGanttViewer(url)" in source
    assert 'switchTab("gantt")' in source
    assert 'a[href*="movelist_gantt_viewer.html"]' in source
    assert "result-card-run-hint" not in template
    assert "双击测试行可运行" not in template
    # 查看器侧：工具条可被顶栏接管，且初始时间窗固定为 0~1500 秒。
    assert 'id="ganttToolbar"' in viewer
    assert "toolbar-detached" in viewer
    assert "const INITIAL_VIEW_SECONDS = 1500;" in viewer
    assert "model.domainMin + INITIAL_VIEW_SECONDS" in viewer
