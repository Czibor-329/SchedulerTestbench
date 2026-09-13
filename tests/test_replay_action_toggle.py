"""诊断导出关闭动作查询时，仍保留回放上下文且不调用算法。"""
from unittest.mock import Mock

from realtime_scheduler.backend.api import http


def test_diagnostic_context_skips_algorithm_when_actions_disabled(monkeypatch):
    """关闭时禁止进入算法会话和动作评估，仍返回物料现场。"""
    machine = Mock()
    machine.replay_update_at.return_value = {"Materials": []}
    factory = Mock(return_value=machine)
    algorithm_session = Mock(side_effect=AssertionError("不应启动算法会话"))
    monkeypatch.setattr(http, "BUILTIN_ALGORITHM_AVAILABLE", True)
    monkeypatch.setattr(http, "ReplayMachine", factory)
    monkeypatch.setattr(http, "algorithm_session", algorithm_session)
    monkeypatch.setattr(http, "normalize_move_payload", lambda _: [])
    context = http._evaluate_replay_action_context({
        "plan": {"device": {}, "strategy": http.OTHER_ALGORITHM_STRATEGY_PREFIX + "example"},
        "moves": [], "time": 2, "includeActions": False,
    })
    assert context["updateParams"] == {"Materials": []}
    assert context["decision"] == {}
    algorithm_session.assert_not_called()
    machine.evaluate_actions.assert_not_called()
