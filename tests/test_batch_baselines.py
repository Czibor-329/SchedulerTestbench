"""批量 Baseline 的兼容边界：显式跳过、配置失效及失败隔离。

Heuristic 成功运行及宏策略收益对比已退役；本文件使用最小内存计划，
不读取公司设备，不运行已退役算法，不写入工作区或运行制品。
"""
from copy import deepcopy
from unittest.mock import patch

from app.backend import application
from app.backend.execution import batch_service


def test_skip_baseline_runs_only_selected_plan_before_digest():
    """无基线或已有旧基线均显式跳过；摘要在主算法之后计算，旧记录不被覆盖。"""
    for existing in (None, {"status": "succeeded", "fingerprint": "stable", "makespan": 1}):
        test_case = {} if existing is None else {"baseline": deepcopy(existing)}
        original = deepcopy(test_case)
        plan = {"strategy": "cycle", "rounds": []}
        calls = []

        def execute(received):
            """检查只执行调用方已构造的计划，且摘要未抢占首轮算法。"""
            assert received == plan
            calls.append("selected")
            return {"makespan": 10, "moveCount": 0}

        def fingerprint(*args, **kwargs):
            """用调用顺序约束关键路径，不使用墙钟预算。"""
            calls.append("digest")
            return "stable"

        with (patch.object(application, "execute_plan", side_effect=execute) as run,
              patch.object(batch_service, "_workspace_baseline_fingerprint", side_effect=fingerprint),
              patch.object(batch_service, "build_workspace_batch_plan", side_effect=AssertionError("不得补算基线")),
              patch.object(batch_service, "_persist_workspace_baseline") as persist):
            result, baseline, error = batch_service._execute_workspace_test_with_baseline(
                {}, test_case, "cycle", {}, selected_plan=plan, skip_baseline=True)
        assert error is None and result["makespan"] == 10
        assert calls == ["selected", "digest"] and run.call_count == 1
        assert baseline["status"] == "skipped" and baseline["fingerprint"] == "stable"
        assert test_case == original
        persist.assert_not_called()


def test_changed_configuration_invalidates_cached_metrics():
    """配置指纹不同时清除旧指标，未变化时保留原记录。"""
    test_case = {"baseline": {"status": "succeeded", "fingerprint": "old", "makespan": 10}}
    device = {"tests": [test_case]}
    with patch.object(batch_service, "_workspace_baseline_fingerprint", return_value="old"):
        batch_service._invalidate_stale_device_baselines(device)
    assert test_case["baseline"]["makespan"] == 10
    with patch.object(batch_service, "_workspace_baseline_fingerprint", return_value="new"):
        batch_service._invalidate_stale_device_baselines(device)
    assert test_case["baseline"]["status"] == "invalid"
    assert test_case["baseline"]["fingerprint"] == "new"
    assert "makespan" not in test_case["baseline"]


def test_failed_legacy_baseline_does_not_hide_selected_result():
    """兼容分支遇到不可用旧算法时记录失败，主策略仍能成功，旧指标不能继续使用。"""
    test_case = {"baseline": {"status": "succeeded", "fingerprint": "stale", "makespan": 1}}
    selected_plan = {"strategy": "cycle", "rounds": []}

    def execute(plan, _limiter):
        """只允许保留的主策略成功，旧基线返回明确的不支持错误。"""
        if plan != selected_plan:
            raise ValueError("已退役的算法不可用")
        return {"makespan": 10, "moveCount": 0}

    with (patch.object(batch_service, "_workspace_baseline_fingerprint", return_value="new"),
          patch.object(batch_service, "build_workspace_batch_plan", return_value={"legacy": True}),
          patch.object(batch_service, "_execute_plan_with_validation_limiter", side_effect=execute),
          patch.object(batch_service, "_persist_workspace_baseline")):
        result, baseline, error = batch_service._execute_workspace_test_with_baseline(
            {}, test_case, "cycle", {}, selected_plan=selected_plan)
    assert error is None and result["makespan"] == 10
    assert baseline["status"] == "failed" and "已退役" in baseline["error"]
    assert "makespan" not in baseline and "makespan" not in test_case["baseline"]
