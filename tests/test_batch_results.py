"""覆盖批量结果组装的保存契约、轻量指标及各终态的诊断内容。

仓储能力通过具名保存边界注入，测试不读写 exports 或设备主数据。真实文件布局、
迁移和 24 小时清理由运行制品仓储测试负责。
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.backend.execution.batch_results import BatchResultAssembler
from app.backend.execution.run_state import LoggedPlanError
from tests.support.run_artifact_fixtures import saved_run_fields


def _assembler(save_run_artifacts: Mock, logged_failure_fields: Mock | None = None) -> BatchResultAssembler:
    """创建只使用最小指标桩的结果组装器，供保存契约测试注入持久化记录器。"""
    return BatchResultAssembler(
        save_run_artifacts=save_run_artifacts,
        logged_failure_fields=logged_failure_fields or Mock(return_value={}),
        baseline_comparison=lambda *_args: {},
        robot_wafer_dwell_time=lambda _moves: {},
        is_external_algorithm=lambda _strategy: False,
        run_context={"deviceId": "device-id", "batchId": "batch-id", "strategy": "heuristic"},
    )


def test_success_exposes_average_recompute_time_for_result_card() -> None:
    """成功项应直接携带重算次数和平均重算时间，避免卡片等待完整分析。"""
    store = Mock(return_value=saved_run_fields())
    assembler = _assembler(store)
    output = {"MoveList": [{"MoveID": 1}], "Feedback": ["完成"]}
    updates = [{"CurrentTime": 0}, {"CurrentTime": 2}, {"CurrentTime": 4}]
    reproduction = [{"Describe": "AlgOutput", "Info": output}]
    plan = {"device": {}, "rounds": []}

    item = assembler.success(
        0,
        {"id": "test-id", "name": "测试一"},
        {
            "output": output,
            "cpuTimeMs": 12.0,
            "totalElapsedMs": 15.0,
            "updates": updates,
            "reproductionLog": reproduction,
            "logs": ["算法完成", "输出校验通过"],
            "makespan": 100.0,
            "moveCount": 1,
            "validation": "passed",
        },
        {"status": "skipped"},
        plan,
    )

    assert item["recomputeCount"] == 3
    assert item["averageRecomputeTimeMs"] == 4.0
    store.assert_called_once()
    artifact, events, summary = store.call_args.args
    assert artifact["MoveList"] == output["MoveList"]
    assert artifact["Feedback"] == ["完成"]
    assert artifact["ReplayContext"]["plan"] == plan
    assert artifact["ReplayContext"]["updates"] == updates
    assert artifact["RunMetricsMetadata"] == {"cpuTimeMs": 12.0, "recomputeCount": 3}
    assert events == reproduction
    assert summary["status"] == "succeeded"
    assert summary["testId"] == "test-id"
    assert summary["deviceId"] == "device-id"
    assert summary["batchId"] == "batch-id"
    assert store.call_args.kwargs["execution_logs"] == ["算法完成", "输出校验通过"]
    assert item["resultId"] == item["artifactId"]
    assert item["logUrl"] == "/api/logs/result-id"
    assert "ReplayContext" not in output
    output["MoveList"][0]["MoveID"] = 9
    updates[0]["CurrentTime"] = 99
    plan["rounds"].append({"currentTime": 99})
    assert artifact["MoveList"][0]["MoveID"] == 1
    assert artifact["ReplayContext"]["updates"][0]["CurrentTime"] == 0
    assert artifact["ReplayContext"]["plan"]["rounds"] == []


def test_logged_failure_saves_partial_output_context_and_error_once() -> None:
    """部分失败计划与原始复现事件必须进入同一次保存，诊断字段提取不能再独立存盘。"""
    store = Mock(return_value=saved_run_fields())
    fields = Mock(return_value={"moveCount": 1, "validation": "failed", "makespan": 8.0})
    assembler = _assembler(store, fields)
    plan = {"strategy": "heuristic", "rounds": [{"currentTime": 0}]}
    error = LoggedPlanError(
        "Machine 死锁",
        [{"Describe": "AlgSchedule", "Info": {"CurrentTime": 0, "Materials": [{"ID": 101}]}}],
        failure_output={"MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 8}]},
    )

    item = assembler.logged_failure(0, {"id": "test-id", "name": "测试一"}, error, {"status": "skipped"}, plan, "heuristic", 0)

    fields.assert_called_once_with(error, replay_plan=plan, persist=False)
    store.assert_called_once()
    artifact, events, summary = store.call_args.args
    assert artifact["MoveList"] == error.failure_output["MoveList"]
    assert artifact["ReplayContext"]["plan"] == plan
    assert artifact["ReplayContext"]["updates"][0]["Materials"] == [{"ID": 101}]
    assert events == error.reproduction_log
    assert summary["status"] == "failed"
    assert summary["error"] == "Machine 死锁"
    assert summary["moveCount"] == 1
    assert store.call_args.kwargs["execution_logs"] == ["Machine 死锁"]
    assert item["resultUrl"] == "/api/results/result-id"


def test_logged_failure_without_partial_output_keeps_log_and_summary() -> None:
    """尚未产出 MoveList 的日志失败也保存运行归属，且不能生成虚假的回放入口。"""
    store = Mock(return_value=saved_run_fields(has_output=False))
    assembler = _assembler(store)
    error = LoggedPlanError("算法初始化失败", [{"Describe": "AlgInit", "Info": {}}])

    item = assembler.logged_failure(0, {"id": "test-id"}, error, {"status": "skipped"}, {}, "heuristic", 0)

    store.assert_called_once()
    artifact, events, summary = store.call_args.args
    assert artifact is None
    assert events == error.reproduction_log
    assert summary["error"] == "算法初始化失败"
    assert item["logUrl"] == "/api/logs/result-id"
    assert "resultUrl" not in item
    assert "ganttUrl" not in item


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_terminal_item_without_output_preserves_input_snapshot_and_log(status: str) -> None:
    """普通异常和取消项均保存冻结输入及诊断事件，不能静默丢弃该次运行。"""
    store = Mock(return_value=saved_run_fields(has_output=False))
    assembler = _assembler(store)
    test_case = {"id": "test-id", "name": "测试一", "rounds": [{"currentTime": 0}]}
    snapshot = deepcopy(test_case)
    plan = {"device": {"Stations": []}, "rounds": [{"currentTime": 0}]}
    if status == "failed":
        item = assembler.plain_failure(0, test_case, RuntimeError("文件解析失败"), plan, {"status": "skipped"})
    else:
        item = assembler.cancelled(0, test_case, plan)

    store.assert_called_once()
    artifact, events, summary = store.call_args.args
    assert artifact is None
    assert summary["status"] == status
    assert summary["testSnapshot"] == snapshot
    assert summary["testId"] == "test-id"
    assert events[0]["Describe"] == "Input"
    assert events[0]["Info"] == [plan]
    assert any(entry["Describe"] == "AlgOutput" for entry in events)
    assert item["artifactId"] == "result-id"
    assert item["summaryUrl"] == "/api/artifacts/result-id/summary"
    assert item["logUrl"] == "/api/logs/result-id"
    assert "resultUrl" not in item
    assert "ganttUrl" not in item
    test_case["rounds"][0]["currentTime"] = 99
    plan["rounds"][0]["currentTime"] = 99
    assert summary["testSnapshot"] == snapshot
    assert events[0]["Info"][0]["rounds"][0]["currentTime"] == 0
