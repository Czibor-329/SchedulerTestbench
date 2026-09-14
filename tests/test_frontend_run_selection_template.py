"""首页运行选择区的稳定结构契约测试。"""

from __future__ import annotations

import re
from pathlib import Path


EDITOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "realtime_scheduler"
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


def test_result_header_keeps_only_test_selection_actions() -> None:
    """结果预览首行不重复提供日志导出或全部甘特图入口。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")

    assert 'id="batchResultFilterButton"' in template
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


def test_result_cards_toggle_readonly_test_details_above_results_area() -> None:
    """结果卡片按需展示只读详情，详情容器位于结果面板之前。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")

    assert template.index('id="batchTestDetails"') < template.index('id="runResultsView"')
    assert 'data-batch-test-card=' in source
    assert "async function toggleBatchTestDetails" in source
    assert "/api/workspaces/${state.workspaceDeviceId}/tests/${encodeURIComponent(testId)}" in source
    assert "点击测试卡片可展开详情<br>再次点击即可收起。" in template
    assert "batch-test-details-head" not in source


def test_result_cards_double_click_into_a_serial_run_queue() -> None:
    """双击卡片使用独立串行队列，并且在服务接收前先显示等待状态。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")

    run_hint = "双击测试卡片可运行<br>多个测试将按顺序排队。"
    detail_hint = "点击测试卡片可展开详情<br>再次点击即可收起。"
    assert run_hint in template
    assert template.index(run_hint) < template.index(detail_hint)
    assert 'document.addEventListener("dblclick"' in source
    assert "createResultCardRunQueue" in source
    assert "markResultCardTestQueued" in source
    assert "maximumWorkers: fromResultCardQueue ? 1 : batchParallelism()" in source


def test_result_header_uses_preview_and_analysis_tabs_without_batch_progress() -> None:
    """结果标题栏内切换预览和分析；没有可分析结果时分析页不可点击。"""
    template = EDITOR_PATH.read_text(encoding="utf-8")
    source = (EDITOR_PATH.parent / "src" / "config_editor.ts").read_text(encoding="utf-8")

    assert 'id="resultPreviewViewButton"' in template
    assert 'id="analysisReportViewButton"' in template
    assert 'id="testGroupAnalysisButton"' not in template
    assert 'id="batchProgress"' not in template
    assert 'function canOpenAnalysisReport()' in source
    assert 'button.disabled = !enabled;' in source
