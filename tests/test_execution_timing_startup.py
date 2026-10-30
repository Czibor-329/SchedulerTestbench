"""执行时间开关在服务入口的隔离边界，不依赖可选算法或真实数据集。"""

import copy

import pytest

import app.backend.application  # 使用与 HTTP 入口一致的执行模块绑定。
from app.backend.execution import service
from app.backend.execution.run_state import ReproductionLog


def _plan():
    """构造设备已删字段、元数据仍保留旧覆盖表的启动现场。"""
    return {
        "device": {
            "Stations": {"heater": {"Type": "Heater", "Name": "heater"}},
            "Robots": {},
            "ExecutionTiming": {"stations": {"heater": {"PrePrepareTime": [2.0]}}},
        },
        "strategy": "other_alg:fixture",
        "roundCount": 1,
        "rounds": [{"currentTime": 0, "cjobs": []}],
        "routes": [],
    }


def _capture_startup(monkeypatch):
    """只隔离算法执行，将真实服务入口准备好的请求返回供契约断言。"""
    monkeypatch.setattr(service, "discover_other_algorithms", lambda: [{"id": "fixture"}])
    monkeypatch.setattr(service, "discover_builtin_algorithms", lambda: [])
    monkeypatch.setattr(service, "build_round_update", lambda *args: {"CurrentTime": 0})

    def capture(plan, *args, **kwargs):
        """记录服务准备结果，不加载外部算法。"""
        return plan

    monkeypatch.setattr(service, "_execute_standard_algorithm", capture)


@pytest.mark.parametrize("enabled", [False, None])
def test_disabled_execution_timing_does_not_validate_stale_overrides(monkeypatch, enabled):
    """关闭或未提供开关时，不允许无效旧覆盖表阻断正常调度或泄露进 AlgInit。"""
    _capture_startup(monkeypatch)
    plan = _plan()
    if enabled is not None:
        plan["executionTimingEnabled"] = enabled
    original = copy.deepcopy(plan)
    log = ReproductionLog()
    result = service._execute_plan(plan, log)
    assert result["executionTiming"] is None
    assert "ExecutionTiming" not in result["device"]
    assert "ExecutionTiming" not in log.entries[0]["Info"]
    assert plan == original


def test_enabled_execution_timing_keeps_invalid_override_error(monkeypatch):
    """真正启用模拟时仍拒绝旧拓扑字段，不能用放宽校验消除错误。"""
    _capture_startup(monkeypatch)
    plan = _plan()
    plan["executionTimingEnabled"] = True
    with pytest.raises(ValueError, match="stations.heater.*PrePrepareTime"):
        service._execute_plan(plan, ReproductionLog())


def test_enabled_execution_timing_preserves_operator_override(monkeypatch):
    """启用时保留合法执行值，并与下发算法的理论设备时间隔离。"""
    _capture_startup(monkeypatch)
    plan = _plan()
    plan["executionTimingEnabled"] = True
    plan["device"]["Stations"]["heater"]["PrePrepareTime"] = [{"Time": 3.0}]
    result = service._execute_plan(plan, ReproductionLog())
    assert result["executionTiming"]["stations"]["heater"]["PrePrepareTime"] == [2.0]
    assert result["device"]["Stations"]["heater"]["PrePrepareTime"] == [{"Time": 3.0}]
