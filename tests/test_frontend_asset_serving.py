"""静态资源服务契约：白名单内的前端资源（含 assets 子目录里的自托管字体）可直接访问，越界或未知类型拒绝。"""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import Mock

import pytest

from app.backend.api import http


def _serve(asset_path: str) -> tuple[Mock, Mock]:
    """构造未初始化的处理器，只观察资源路由决策。"""
    handler = object.__new__(http.ConfigEditorHandler)
    handler._send_file = Mock()
    handler._send_json = Mock()
    handler._send_frontend_asset(asset_path)
    return handler._send_file, handler._send_json


@pytest.mark.parametrize(
    "asset_path,file_name,content_type",
    [
        ("fonts.css", "fonts.css", "text/css; charset=utf-8"),
        ("config_editor.js", "config_editor.js", "text/javascript; charset=utf-8"),
        ("fonts/PlusJakartaSans-latin.woff2", "PlusJakartaSans-latin.woff2", "font/woff2"),
        ("fonts/KaTeX_Main-Regular.woff2", "KaTeX_Main-Regular.woff2", "font/woff2"),
        ("fonts/KaTeX_Main-Regular.woff", "KaTeX_Main-Regular.woff", "font/woff"),
    ],
)
def test_frontend_asset_whitelist_serves_known_types(
    asset_path: str, file_name: str, content_type: str
) -> None:
    """样式、脚本与字体（含 assets 子目录）都按正确内容类型交给文件发送。"""
    send_file, send_json = _serve(asset_path)

    send_json.assert_not_called()
    send_file.assert_called_once()
    sent_path, sent_type = send_file.call_args.args
    assert sent_path.name == file_name
    assert sent_type == content_type


@pytest.mark.parametrize(
    "asset_path",
    [
        "../app/backend/api/http.py",
        "fonts/../../app/backend/api/http.py",
        "fonts/PlusJakartaSans-latin.woff2.bak",
        "config_editor.json",
    ],
)
def test_frontend_asset_rejects_escapes_and_unknown_types(asset_path: str) -> None:
    """目录穿越与未知扩展名必须以 404 结束，不进入文件发送。"""
    send_file, send_json = _serve(asset_path)

    send_file.assert_not_called()
    send_json.assert_called_once()
    assert send_json.call_args.args[1] == HTTPStatus.NOT_FOUND
