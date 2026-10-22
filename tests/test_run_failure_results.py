"""单次运行失败的正式 HTTP 响应与诊断制品契约测试。

从旧批量综合测试迁移失败耗时、Baseline 和部分计划风险；通过真实 do_POST 分支
验证结构化字段与保存内容，不依赖已废弃的前端函数名或源码排列。
"""

from __future__ import annotations

from io import BytesIO
import json
from unittest.mock import Mock, patch

import app.backend.application as config_server
from app.backend.application import LoggedPlanError
from tests.support.run_artifact_fixtures import saved_run_fields


def _run_handler(payload: dict) -> config_server.ConfigEditorHandler:
    """创建只处理内存请求的单次运行 HTTP handler，省略 socket 和真实服务生命周期。"""
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler = object.__new__(config_server.ConfigEditorHandler)
    handler.path = "/api/run"
    handler.headers = {"Content-Length": str(len(encoded))}
    handler.rfile = BytesIO(encoded)
    handler._send_json = Mock()
    return handler


def test_single_external_failure_keeps_elapsed_time_and_baseline_visible() -> None:
    """外部算法失败必须通过真实 HTTP payload 保留耗时、Baseline 与原始指标。"""
    handler = _run_handler({
        "workspaceDeviceId": "device-id",
        "workspaceTestId": "test-id",
        "strategy": "other_alg:demo",
    })
    error = LoggedPlanError(
        "状态推进失败|MVL-STATE-UNKNOWN|无效动作",
        [],
        failure_output={"MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 80}]},
        validation_issues=["MoveID=1 无效动作"],
    )
    baseline = {"status": "succeeded", "makespan": 100.0, "cpuTimeMs": 10.0}
    with (
        patch.object(config_server, "get_workspace_run_context", return_value=(
            {"id": "device-id", "device": {"Stations": {}, "Robots": {}}},
            {"id": "test-id", "name": "失败案例"},
        )),
        patch.object(config_server, "_execute_workspace_test_with_baseline", return_value=(None, baseline, error)),
        patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()) as store,
    ):
        handler.do_POST()

    response, status = handler._send_json.call_args.args
    assert int(status) == 400
    assert response["ok"] is False
    assert response["metricsAvailable"] is True
    assert response["totalElapsedMs"] >= 0
    assert response["cpuTimeMs"] == response["totalElapsedMs"]
    assert response["validation"] == "failed"
    assert response["baseline"] == baseline
    assert response["makespan"] == 80.0
    assert response["improvementPercent"] == 20.0
    assert response["resultUrl"] == "/api/results/result-id"
    store.assert_called_once()
    assert store.call_args.args[2]["error"] == str(error)


def test_single_failure_result_keeps_machine_replay_context() -> None:
    """单次算法失败的部分 MoveList 和当时的计划/update 应在一次保存中保持关联。"""
    payload = {"strategy": "heuristic", "rounds": [{"currentTime": 0}]}
    handler = _run_handler(payload)
    error = LoggedPlanError(
        "Machine 死锁",
        [{"Describe": "AlgSchedule", "Info": {"CurrentTime": 0, "Materials": [{"ID": 101}]}}],
        failure_output={
            "MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 8}],
            "Feedback": ["调度失败: Machine 无可执行搬运意图"],
            "FailureContext": {
                "Stage": "algorithm-deadlock",
                "Code": "DEADLOCK.NO_EXECUTABLE_ACTION",
                "Category": "no-executable-action",
                "Message": "Machine 无可执行搬运意图",
            },
        },
    )
    with (
        patch.object(config_server, "execute_plan", side_effect=error),
        patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields("deadlock-result")) as store,
    ):
        handler.do_POST()

    response, status = handler._send_json.call_args.args
    assert int(status) == 400
    assert response["resultUrl"] == "/api/results/deadlock-result"
    assert response["moveCount"] == 1
    assert response["deadlock"]["Code"] == "DEADLOCK.NO_EXECUTABLE_ACTION"
    store.assert_called_once()
    artifact, reproduction, summary = store.call_args.args
    assert artifact["MoveList"] == error.failure_output["MoveList"]
    assert artifact["ReplayContext"]["plan"] == payload
    assert artifact["ReplayContext"]["updates"][0]["Materials"][0]["ID"] == 101
    assert reproduction == error.reproduction_log
    assert summary["status"] == "failed"
    assert summary["deadlock"]["Code"] == "DEADLOCK.NO_EXECUTABLE_ACTION"
