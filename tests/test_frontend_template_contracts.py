"""控制台页面骨架契约：稳定入口、可访问性、本地资源和架构边界。

本文件只解析 HTML 属性，不检查 CSS 像素、局部函数名、源码顺序或实现片段。
动态表格、草稿决策、Route、回放和图表行为由当前 TypeScript 编译的 Node 测试覆盖。
"""
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


FRONTEND = Path(__file__).resolve().parents[1] / "app/frontend"
EXPECTED_FRONTEND_VERSION = "1.6.26"
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class _Element:
    """保存元素属性与所属视图，不依赖标签缩进及属性顺序。"""

    tag: str
    attributes: dict
    views: tuple


class _Page(HTMLParser):
    """读取静态模板，为稳定 ID、视图归属和可访问名称提供查询。"""

    def __init__(self, file_name="config_editor.html"):
        """解析 frontend 下的具名页面，不执行脚本或加载外部资源。"""
        super().__init__()
        self.elements = []
        self.stack = []
        self.feed((FRONTEND / file_name).read_text(encoding="utf-8"))

    def handle_starttag(self, tag, attributes):
        """记录元素及祖先页签；HTML 空元素不进入栈。"""
        values = dict(attributes)
        views = tuple(item[1]["data-tab-view"] for item in self.stack if "data-tab-view" in item[1])
        self.elements.append(_Element(tag, values, views))
        if tag not in VOID_TAGS:
            self.stack.append((tag, values))

    def handle_endtag(self, tag):
        """关闭匹配的祖先，避免嵌套 div 数量或排版影响查询。"""
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def element(self, element_id):
        """取得唯一稳定 ID；缺失或重复均表示页面集成契约被破坏。"""
        matched = [element for element in self.elements if element.attributes.get("id") == element_id]
        assert len(matched) == 1, element_id
        return matched[0]

    def named(self, element_id):
        """确认控件通过 aria-label、aria-labelledby 或显式 label 获得名称。"""
        values = self.element(element_id).attributes
        references = str(values.get("aria-labelledby") or "").split()
        if references:
            for reference in references:
                self.element(reference)
        assert values.get("aria-label") or references or any(
            element.tag == "label" and element.attributes.get("for") == element_id
            for element in self.elements), element_id


def test_navigation_and_view_hosts_are_connected():
    """导航与独立视图配对，运行结果、分析、回放和甘特图拥有各自宿主。"""
    page = _Page()
    views = {element.attributes["data-tab-view"] for element in page.elements if "data-tab-view" in element.attributes}
    targets = {element.attributes["data-tab-target"] for element in page.elements if "data-tab-target" in element.attributes}
    assert targets <= views
    for view, element_ids in {
        "schedule": ("runPreviewArea", "batchResults"),
        "analysis": ("testGroupAnalysisPanel",),
        "playback": ("visualTopologyPlayback", "visualTimeline", "visualDeviceStage"),
        "gantt": ("ganttFrame",),
        "theory-capacity": ("theoryCapacityChart",),
    }.items():
        for element_id in element_ids:
            assert view in page.element(element_id).views, (view, element_id)
    assert page.element("ganttFrame").attributes.get("title")


def test_run_selection_and_transfer_keep_accessible_controls():
    """选择、运行、取消和结果工具仍有稳定入口；后台传输公开进度语义。"""
    page = _Page()
    for element_id in ("runDeviceSelect", "runGroupSelect"):
        page.named(element_id)
    for element_id in ("batchRunButton", "batchResultFilterButton", "batchSelectionList",
                       "batchSelectionRangeStart", "batchSelectionRangeEnd", "batchSelectionRunSelected",
                       "batchGanttButton", "workspaceImportButton", "workspaceExportButton"):
        page.element(element_id)
    progress = page.element("dataTransferProgressBar").attributes
    assert progress.get("role") == "progressbar"
    assert "aria-valuenow" in progress
    assert page.element("resultErrorPanel").attributes.get("role") == "alert"


def test_settings_dialogs_have_accessible_names():
    """运行、批量选择和模型设置弹窗保持具名对话框语义。"""
    page = _Page()
    for element_id in ("runSettingsDialog", "batchTestSelectionDialog", "searchTreeOptionsDialog"):
        assert page.element(element_id).tag == "dialog"
        page.named(element_id)
    for element_id in ("searchTreeCheckpointFile", "skipBaselineInput", "hongYeCheckInput"):
        page.element(element_id)


def test_playback_controls_and_photography_are_accessible():
    """回放与摄影保留可操作入口及状态通知，不固定它们的像素和摆放顺序。"""
    page = _Page()
    for element_id in ("visualPlayButton", "visualTimeline", "visualSpeed", "visualImportButton", "visualPhotographyButton"):
        page.named(element_id)
    assert page.element("visualTimeline").attributes.get("type") == "range"
    assert page.element("visualFileInput").attributes.get("type") == "file"
    trigger = page.element("visualPhotographyButton").attributes
    assert trigger.get("aria-controls") == "visualPhotographyMenu"
    menu = page.element(trigger["aria-controls"]).attributes
    assert menu.get("role") == "dialog"
    page.named("visualPhotographyMenu")
    assert page.element("visualPhotographyStatus").attributes.get("role") == "status"
    for element_id in ("visualPhotographyStartTime", "visualPhotographyCount"):
        page.named(element_id)
    filters = [element.attributes for element in page.elements if "data-action-status-filter" in element.attributes]
    assert {item.get("value") for item in filters} == {"enabled", "physical-blocked", "deadlock-blocked"}
    assert all("checked" in item for item in filters)


def test_theory_capacity_exposes_named_inputs_and_result_regions():
    """理论产能的静态宿主与输入可用，数值与请求竞态由 TypeScript 行为测试验证。"""
    page = _Page()
    for element_id in ("theoryCapacityDevice", "theoryCapacityKind", "theoryCapacityLock",
                       "theoryCapacityFirstSeconds", "theoryCapacityLastSeconds", "theoryCapacityStepSeconds", "theoryCapacitySlider"):
        page.named(element_id)
    for element_id in ("theoryCapacityView", "theoryCapacityApply", "theoryCapacityResults",
                       "theoryCapacityChart", "theoryCapacityDerivation", "theoryCapacityResources"):
        page.element(element_id)
    assert page.element("theoryCapacityNotice").attributes.get("role") == "alert"
    assert page.element("theoryCapacityResultState").attributes.get("role") == "status"


def test_clean_editor_keeps_configurable_domain_inputs():
    """清洁编辑仍提供阈值和 Dummy 数量控件，启用切换由具名行为测试覆盖。"""
    page = _Page()
    for element_id in ("cleanIdleTimeMin", "cleanIdleTimeMax", "cleanDummyWaferCount"):
        page.named(element_id)
        assert page.element(element_id).attributes.get("type") == "number"


def test_frontend_versions_and_local_resources_are_consistent():
    """包、显示与缓存版本一致，页面引用的本地资源及字体许可存在。"""
    package = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((FRONTEND / "package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == EXPECTED_FRONTEND_VERSION
    html = (FRONTEND / "config_editor.html").read_text(encoding="utf-8")
    assert f"V{EXPECTED_FRONTEND_VERSION}</span>" in html
    for file_name in ("config_editor.html", "movelist_gantt_viewer.html"):
        resources = [element.attributes.get("src") or element.attributes.get("href", "")
                     for element in _Page(file_name).elements if element.tag in {"script", "link"}]
        paths = {urlsplit(resource).path for resource in resources}
        assert "/assets/fonts.css" in paths
        if file_name == "config_editor.html":
            assert {"/assets/config_editor.css", "/assets/config_editor.js"} <= paths
        for resource in resources:
            url = urlsplit(resource)
            if url.path.startswith("/assets/"):
                assert (FRONTEND / url.path.lstrip("/")).is_file(), resource
                version = parse_qs(url.query).get("v")
                if file_name == "config_editor.html" and url.path in {"/assets/config_editor.css", "/assets/config_editor.js"}:
                    assert version == [EXPECTED_FRONTEND_VERSION]
    assert (FRONTEND / "assets/fonts/PlusJakartaSans-latin.woff2").read_bytes().startswith(b"wOF2")
    assert "SIL Open Font License" in (FRONTEND / "assets/fonts/PlusJakartaSans-OFL.txt").read_text(encoding="utf-8")


def test_documentation_redirect_stays_outside_console_views():
    """文档入口指向独立阅读器，正文不重新嵌入控制台。"""
    page = _Page()
    links = [element.attributes for element in page.elements if element.tag == "a"]
    assert any(item.get("href") == "/documentation.html" and item.get("target") == "_blank" for item in links)
    assert not any(element.attributes.get("data-tab-view") == "documentation" for element in page.elements)
    reader = _Page("documentation.html").element("documentationLink")
    assert urlsplit(reader.attributes["href"]).port == 8766


def test_view_modules_respect_network_and_application_boundaries():
    """保留明确架构禁令，不锁定模块的局部函数、文案或代码顺序。"""
    view = (FRONTEND / "src/theory_capacity_view.ts").read_text(encoding="utf-8")
    for banned in ("fetch(", "./workspace_visualizer", "./config_editor", "./group_analysis_view", "./result_table", "document.getElementById("):
        assert banned not in view
    contracts = (FRONTEND / "src/analysis_contracts.ts").read_text(encoding="utf-8")
    assert "fetch(" not in contracts and "function " not in contracts
    workspace = (FRONTEND / "src/workspace_visualizer.ts").read_text(encoding="utf-8")
    assert "test_support" not in workspace


def test_retired_heuristic_controls_and_request_configuration_are_absent():
    """退役的派工卡片、弹窗、权重与专属请求配置不能重新出现。"""
    page = _Page()
    page.element("otherAlgorithmOptions")
    ids = {element.attributes.get("id") for element in page.elements}
    assert not ids.intersection({"heuristicSettings", "heuristicSettingsDialog"})
    assert all("data-heuristic-weight" not in element.attributes for element in page.elements)
    assert "heuristicConfig" not in (FRONTEND / "src/config_editor.ts").read_text(encoding="utf-8")


def test_gantt_keeps_shared_resources_and_accessible_detail_controls():
    """甘特图加载共享语义与执行对比资源，保留时间差及取消动作的交互入口。"""
    page = _Page("movelist_gantt_viewer.html")
    scripts = {element.attributes.get("src") for element in page.elements if element.tag == "script"}
    assert {"/assets/gantt_execution_compare.js", "/assets/gantt_move_semantics.js"} <= scripts
    for element_id in ("durationOnlyToggle", "prevDiffBtn", "nextDiffBtn", "tooltip", "showRemovedMovesToggle"):
        page.element(element_id)
