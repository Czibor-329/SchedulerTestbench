"""Adapter Host 长连接协议的轻量单元测试。"""

import threading
from collections import deque
from unittest.mock import Mock

import pytest

from app.backend.algorithms.dotnet_adapter_runtime import DotNetAdapterRuntime


def test_update_move_states_uses_one_host_request() -> None:
    """批量 MoveState 应只产生一次跨进程请求，并保持原通知顺序。"""
    runtime = object.__new__(DotNetAdapterRuntime)
    runtime._request = Mock(return_value={"ok": True})
    notifications = [
        {"MoveID": 1, "MoveState": "Running"},
        {"MoveID": 1, "MoveState": "Done"},
        {"MoveID": 2, "MoveState": "Running"},
    ]

    runtime.update_move_states(notifications)

    runtime._request.assert_called_once_with(
        "updateMoveStates",
        payload=notifications,
    )


def test_update_move_states_skips_empty_batch() -> None:
    """空通知列表无需唤醒 Adapter Host。"""
    runtime = object.__new__(DotNetAdapterRuntime)
    runtime._request = Mock(return_value={"ok": True})

    runtime.update_move_states([])

    runtime._request.assert_not_called()


def test_request_reports_host_diagnostics_when_input_pipe_is_closed() -> None:
    """Host 启动失败时应暴露原始诊断，不能只返回 Windows Errno。"""
    runtime = object.__new__(DotNetAdapterRuntime)
    runtime._closed = False
    runtime._request_lock = threading.Lock()
    runtime._diagnostic_lines = deque(
        ["Access to the path 'PowerShell/logs' is denied"],
        maxlen=50,
    )
    runtime._reader = Mock()
    runtime._process = Mock()
    runtime._process.poll.return_value = None
    runtime._process.stdin.write.side_effect = OSError(22, "Invalid argument")

    with pytest.raises(RuntimeError) as error_info:
        runtime._request("init", payload={})

    assert "Adapter Host 输入管道已关闭" in str(error_info.value)
    assert "PowerShell/logs" in str(error_info.value)
    runtime._reader.join.assert_called_once_with(timeout=1)
