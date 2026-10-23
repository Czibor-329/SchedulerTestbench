"""前端算法设置边界：使用动态算法目录，禁止退役的 Heuristic 控件与请求配置。"""

from html.parser import HTMLParser
from pathlib import Path


FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "app" / "frontend"


class _TemplateElements(HTMLParser):
    """读取模板的属性集合，避免将架构断言绑定到 HTML 排版。"""

    def __init__(self) -> None:
        """初始化用于收集稳定 ID 与退役属性的容器。"""
        super().__init__()
        self.elements: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """收集每个元素的属性，不执行页面逻辑。"""
        self.elements.append(dict(attrs))


def test_retired_heuristic_controls_are_absent() -> None:
    """旧权重小数输入契约已退役，卡片、弹窗及权重控件均不得重新出现。"""
    parser = _TemplateElements()
    parser.feed((FRONTEND_ROOT / "config_editor.html").read_text(encoding="utf-8"))
    element_ids = {element.get("id") for element in parser.elements}
    assert "otherAlgorithmOptions" in element_ids
    assert "heuristicSettings" not in element_ids
    assert "heuristicSettingsDialog" not in element_ids
    assert all("data-heuristic-weight" not in element for element in parser.elements)


def test_retired_heuristic_configuration_is_not_in_frontend_requests() -> None:
    """页面源码不再持有或构建内置 Heuristic 专属请求配置。"""
    source = (FRONTEND_ROOT / "src/config_editor.ts").read_text(encoding="utf-8")
    assert "heuristicConfig" not in source
