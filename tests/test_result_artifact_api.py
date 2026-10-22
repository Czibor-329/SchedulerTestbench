"""运行制品 HTTP 视图契约：甘特轻量输出、回放上下文及摘要分别读取。"""

from __future__ import annotations

import json
from http import HTTPStatus
from io import BytesIO
from unittest.mock import Mock

import pytest

from app.backend.api import http
from tests.support.run_artifact_fixtures import saved_run_fields


@pytest.mark.parametrize("view,with_context", [("", True), ("?view=movelist", False)])
def test_result_artifact_api_output_projection(view: str, with_context: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧结果地址保留完整回放契约，甘特 query 显式避开上下文读取。"""
    read = Mock(return_value={"MoveList": []})
    monkeypatch.setattr(http, "read_result", read)
    handler = object.__new__(http.ConfigEditorHandler)
    handler.path = f"/api/results/{'a' * 32}{view}"
    handler._send_json = Mock()
    handler.do_GET()
    read.assert_called_once_with("a" * 32, include_replay_context=with_context)
    handler._send_json.assert_called_once_with({"MoveList": []})


def test_result_artifact_api_context_does_not_read_moves(monkeypatch: pytest.MonkeyPatch) -> None:
    """独立回放上下文请求不能重新读取或传输 MoveList。"""
    context = {"plan": {}, "updates": []}
    read = Mock(return_value=context)
    monkeypatch.setattr(http, "read_replay_context", read)
    monkeypatch.setattr(http, "read_result", Mock(side_effect=AssertionError("不读取完整结果")))
    handler = object.__new__(http.ConfigEditorHandler)
    handler.path = f"/api/results/{'a' * 32}?view=replay-context"
    handler._send_json = Mock()
    handler.do_GET()
    read.assert_called_once_with("a" * 32)
    handler._send_json.assert_called_once_with(context)


def test_result_artifact_api_summary_is_lightweight(monkeypatch: pytest.MonkeyPatch) -> None:
    """结果摘要入口仅读取身份和指标，不能依赖大输出。"""
    summary = {"testName": "测试一", "moveCount": 10000}
    read = Mock(return_value=summary)
    monkeypatch.setattr(http, "read_result_summary", read)
    monkeypatch.setattr(http, "read_result", Mock(side_effect=AssertionError("不读取完整结果")))
    handler = object.__new__(http.ConfigEditorHandler)
    handler.path = f"/api/artifacts/{'a' * 32}/summary"
    handler._send_json = Mock()
    handler.do_GET()
    read.assert_called_once_with("a" * 32)
    handler._send_json.assert_called_once_with(summary)


def test_result_artifact_api_unknown_view_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """视图参数不受支持时返回稳定 HTTP 错误，避免误载入整份结果。"""
    monkeypatch.setattr(http, "read_result", Mock(side_effect=AssertionError("不能读取未知视图")))
    handler = object.__new__(http.ConfigEditorHandler)
    handler.path = f"/api/results/{'a' * 32}?view=unknown"
    handler._send_json = Mock()
    handler.do_GET()
    assert handler._send_json.call_args.args[1] == HTTPStatus.BAD_REQUEST


def test_successful_run_response_disconnect_does_not_save_another_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功制品已提交时，客户端断开连接不能把同次运行再次归档为失败。"""
    payload = {"strategy": "heuristic", "device": {}, "rounds": []}
    body = json.dumps(payload).encode("utf-8")
    handler = object.__new__(http.ConfigEditorHandler)
    handler.path = "/api/run"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    handler._send_json = Mock(side_effect=BrokenPipeError("客户端已断开"))
    saved = Mock(return_value=saved_run_fields())
    monkeypatch.setattr(http, "save_run_artifacts", saved)
    monkeypatch.setattr(http, "execute_plan", Mock(return_value={
        "output": {"MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 2}]},
        "reproductionLog": [], "logs": ["运行完成"], "updates": [{}],
        "cpuTimeMs": 1, "totalElapsedMs": 2,
    }))

    with pytest.raises(BrokenPipeError, match="客户端已断开"):
        handler.do_POST()

    saved.assert_called_once()
    artifact, reproduction, summary = saved.call_args.args
    assert summary["status"] == "succeeded"
    assert artifact["MoveList"][0]["MoveID"] == 1
    assert artifact["ReplayContext"]["plan"] == payload
    assert reproduction == []
    assert saved.call_args.kwargs["execution_logs"] == ["运行完成"]
