"""Adapter Host 长连接协议的轻量单元测试。"""

from unittest.mock import Mock

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
