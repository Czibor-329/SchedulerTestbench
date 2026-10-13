"""「理论产能」页签的结构、可访问性与模块边界契约。

只断言稳定 ID、可访问性语义、资源版本一致性、模板来源注释和明确的架构禁令，
不断言局部函数名、代码排版或整段实现字符串。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "app" / "frontend"
EDITOR_HTML = FRONTEND / "config_editor.html"
EDITOR_SOURCE = FRONTEND / "src" / "config_editor.ts"
VIEW_SOURCE = FRONTEND / "src" / "theory_capacity_view.ts"
VIEW_STYLE = FRONTEND / "assets" / "theory_capacity.css"
CLIENT_SOURCE = FRONTEND / "src" / "api_client.ts"
CONTRACT_SOURCE = FRONTEND / "src" / "analysis_contracts.ts"
PACKAGE_JSON = FRONTEND / "package.json"

# 页面控制器必须依赖的稳定元素 ID；改动它们等于改动页面契约。
STABLE_ELEMENT_IDS = (
    "theoryCapacityView",
    "theoryCapacityDevice",
    "theoryCapacityKind",
    "theoryCapacityLock",
    "theoryCapacityFirstSeconds",
    "theoryCapacityLastSeconds",
    "theoryCapacityStepSeconds",
    "theoryCapacityApply",
    "theoryCapacityNotice",
    "theoryCapacityChart",
    "theoryCapacityDerivation",
    "theoryCapacityResults",
    "theoryCapacityResources",
    "theoryCapacitySlider",
)

# 筛选控件必须带可访问名称与标签；下拉框使用首页紧凑下拉，按 data-compact-label 命名。
FILTER_SELECT_IDS = (
    "theoryCapacityDevice",
    "theoryCapacityKind",
    "theoryCapacityLock",
)
FILTER_NUMBER_IDS = (
    "theoryCapacityFirstSeconds",
    "theoryCapacityLastSeconds",
    "theoryCapacityStepSeconds",
)


class TheoryCapacityPageTests(unittest.TestCase):
    """验证页签骨架、可访问性和模块边界。"""

    def setUp(self) -> None:
        self.html = EDITOR_HTML.read_text(encoding="utf-8")
        self.view = VIEW_SOURCE.read_text(encoding="utf-8")
        self.style = VIEW_STYLE.read_text(encoding="utf-8")
        self.editor = EDITOR_SOURCE.read_text(encoding="utf-8")

    def _tab_view(self) -> str:
        """返回“理论产能”页签的标签片段（按 div 深度取到该页签自己的收尾）。"""
        marker = self.html.index('data-tab-view="theory-capacity"')
        start = self.html.rindex("<div", 0, marker)
        depth = 0
        index = start
        while index < len(self.html):
            next_open = self.html.find("<div", index)
            next_close = self.html.find("</div>", index)
            if next_close < 0:
                break
            if 0 <= next_open < next_close:
                depth += 1
                index = next_open + len("<div")
                continue
            depth -= 1
            index = next_close + len("</div>")
            if depth == 0:
                return self.html[start:index]
        return self.html[start:]

    def test_navigation_places_tab_inside_diagnostic_group(self) -> None:
        """页签属于“结果诊断”分组，并出现在分组内的最后一个入口之后。"""
        navigation = self.html.split('id="diagnosticNavigationTitle"', 1)[1]
        navigation = navigation.split("</nav>", 1)[0]
        self.assertIn('data-tab-target="theory-capacity"', navigation)
        self.assertIn("<span>理论产能</span>", navigation)
        self.assertLess(
            navigation.index('data-tab-target="gantt"'),
            navigation.index('data-tab-target="theory-capacity"'),
        )

    def test_tab_view_exposes_stable_elements(self) -> None:
        """页签视图必须提供控制器使用的全部稳定 ID。"""
        view = self._tab_view()
        for element_id in STABLE_ELEMENT_IDS:
            self.assertIn(f'id="{element_id}"', view)

    def test_filters_and_regions_are_accessible(self) -> None:
        """筛选控件必须有可访问名称，图表与各区块必须有语义标签。"""
        view = self._tab_view()
        self.assertIn('id="theoryCapacityNotice" role="alert" hidden', view)
        self.assertIn('aria-label="产能曲线"', view)
        self.assertIn('aria-label="资源负荷下界"', view)
        self.assertIn('aria-label="资源负荷下界"', view)
        self.assertIn('aria-label="计算过程"', view)
        self.assertIn('aria-label="计算过程"', view)
        for element_id in FILTER_SELECT_IDS:
            self.assertRegex(
                view,
                rf'id="{element_id}"[^>]*data-compact-label="[^"]+"',
                f"{element_id} 必须使用首页紧凑下拉命名",
            )
        for element_id in (*FILTER_SELECT_IDS, *FILTER_NUMBER_IDS):
            self.assertIn(f'id="{element_id}"', view)
            self.assertRegex(
                view,
                rf'id="{element_id}"[^>]*aria-label=|for="{element_id}"',
                f"{element_id} 缺少可访问名称",
            )

    def test_top_panel_keeps_only_filters(self) -> None:
        """顶部面板只保留筛选控件：没有页面标题、说明文字与指标卡片。"""
        view = self._tab_view()
        for removed in (
            "theory-capacity-toolbar-title",
            "theoryCapacityKpis",
            "theoryCapacityStatus",
            "theory-route-grid",
            'class="field"',
        ):
            self.assertNotIn(removed, view)
        for label in ("设备", "路径", "LoadLock", "起点（秒）", "终点（秒）", "步长（秒）"):
            self.assertIn(label, view)
        # 不再提供测试选择、组别、状态与关键字筛选。
        for removed in (
            'id="theoryCapacityTest"',
            'id="theoryCapacityGroupFilter"',
            'id="theoryCapacityStatusFilter"',
            'id="theoryCapacityKeyword"',
        ):
            self.assertNotIn(removed, self.html)
        self.assertNotIn("测试推导", view)

    def test_styles_are_loaded_with_current_asset_version(self) -> None:
        """样式表按当前前端版本加载，并与其它页面样式版本一致。"""
        version = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))["version"]
        # katex.min.css 使用第三方库自身版本，不参与前端资源版本一致性。
        versions = set(re.findall(
            r"/assets/(?!katex\.min\.css)[\w.]+\.css\?v=([\d.]+)", self.html,
        ))
        self.assertEqual(versions, {version}, f"资源版本不一致：{sorted(versions)}")
        self.assertIn(
            f'<link rel="stylesheet" href="/assets/theory_capacity.css?v={version}">',
            self.html,
        )

    def test_style_documents_spark_template_source(self) -> None:
        """样式文件必须注明取值来源，保持与既有模板移植同一约定。"""
        self.assertIn("spark-admin", self.style)
        self.assertIn("assets/css/main.css", self.style)

    def test_view_module_keeps_network_and_state_boundaries(self) -> None:
        """控制器不直接发起 fetch，也不依赖其它页签的控制器。"""
        self.assertNotIn("fetch(", self.view)
        for banned in (
            "./workspace_visualizer",
            "./config_editor",
            "./group_analysis_view",
            "./result_table",
            "document.getElementById(",
        ):
            self.assertNotIn(banned, self.view, f"理论产能视图不应依赖 {banned}")
        self.assertIn("TheoryCapacityViewOptions", self.view)
        self.assertIn("renderInteractionChart", self.view)
        self.assertIn("chartWindowForCurves", self.view)
        self.assertIn("nearestChartSample", self.view)
        self.assertIn("routeIndexOptions", self.view)

    def test_fixed_preset_regions_replace_matching(self) -> None:
        """新语义保留图表、公式与下界，明确禁止恢复路线/测试匹配控件。"""
        view = self._tab_view()
        for removed in ("theoryCapacityRoutes", "theoryCapacityTests", "theoryCapacityClean"):
            self.assertNotIn(removed, view)
        for name in ("single-node", "serial", "parallel", "mixed"):
            self.assertIn(f'value="{name}"', view)

    def test_chart_uses_bundled_template_asset(self) -> None:
        """图表依赖模板自带的本地资源，不依赖 CDN 或自画 SVG 页面。"""
        self.assertIn('/assets/vendor/apexcharts.min.js', self.html)
        self.assertTrue((FRONTEND / "assets/vendor/apexcharts.min.js").is_file())

    def test_client_owns_the_three_endpoints(self) -> None:
        """三个接口路径只出现在 API 客户端里，页面通过注入调用。"""
        client = CLIENT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("/api/theory-capacity/route-index", client)
        self.assertIn("/api/theory-capacity/scan", client)
        self.assertIn("/api/theory-capacity/derivation", client)
        self.assertNotIn("/api/theory-capacity", self.view)

    def test_contracts_declare_response_shape_without_logic(self) -> None:
        """响应契约只声明形状，不包含计算或网络代码。"""
        contracts = CONTRACT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("TheoryCapacityScanResponse", contracts)
        self.assertIn("TheoryCapacityDerivationResponse", contracts)
        self.assertNotIn("fetch(", contracts)
        self.assertNotIn("function ", contracts)

    def test_editor_wires_view_and_allows_tab_during_running_batch(self) -> None:
        """入口必须在运行期间也允许进入页签，并完成控制器装配。"""
        self.assertIn("initializeTheoryCapacityView()", self.editor)
        self.assertIn('"theory-capacity"]', self.editor)
        self.assertIn("createTheoryCapacityView", self.editor)


if __name__ == "__main__":
    unittest.main()
