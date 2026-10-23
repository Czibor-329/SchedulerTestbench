"""死锁诊断的状态包、失败反馈与平台判型边界测试。"""

from __future__ import annotations

import json
import unittest

from app.backend import application as config_server
from app.backend.application import LoggedPlanError
from pathlib import Path

from app.backend.artifacts.deadlock_diagnostic import (
    build_deadlock_diagnostic_bundle,
    serialize_deadlock_diagnostic_bundle,
)


def test_bundle_collects_state_routes_recent_moves_and_exact_blocker() -> None:
    """诊断包应同时保留晶圆阶段、当前动作状态和带资源定位的拦截规则。"""
    route = {
        "RouteSteps": [
            {"StepID": 0, "Visits": [{"StationName": "LP1"}]},
            {"StepID": 1, "Visits": [{"StationName": "VTR"}]},
            {"StepID": 2, "Visits": [{"StationName": "PM1"}]},
        ]
    }
    moves = [
        {
            "MoveID": index,
            "MoveType": 0,
            "StartTime": float(index),
            "EndTime": float(index) + 0.5,
            "ModuleName": "VTR",
            "MatIDList": [101],
            "StepIDList": [1],
        }
        for index in range(1, 24)
    ]
    decision = {
        "actionDiagnosticsProvider": "heuristic-machine",
        "actionDiagnosticsSource": "algorithm",
        "actionCounts": {"enabled": 0, "physical-blocked": 1},
        "actionDiagnostics": [{
            "actionId": "place:101:VTR:1:LA:2",
            "kind": "place",
            "status": "physical-blocked",
            "reason": "晶圆下一工序仅允许 PM1/PM2/PM3",
            "robot": "VTR",
            "materialIds": ["101"],
            "source": "VTR",
            "sourceSlot": 1,
            "destination": "LA",
            "destinationSlot": 2,
        }],
    }

    bundle = build_deadlock_diagnostic_bundle(
        replay_time=22.25,
        plan={"strategy": "heuristic", "device": {}, "rounds": []},
        moves=moves,
        update_params={
            "CurrentTime": 20,
            "Materials": [{
                "ID": 101,
                "TaskID": "LP1.1",
                "StepID": 1,
                "CurrentModuleName": "VTR",
                "Route": route,
            }],
        },
        move_states=[{"MoveID": 22, "MoveState": "Running"}],
        decision=decision,
        recompute_round=3,
        playback_snapshot={
            "robots": [{"name": "VTR", "wafers": ["LP1.1"]}],
            "modules": [{"name": "LA", "wafers": ["LP1.2"]}],
        },
        result_id="result-1",
    )

    assert bundle["schemaVersion"] == 1
    assert bundle["replay"]["recomputeRound"] == 3
    assert bundle["replay"]["moveStates"] == [{"MoveID": 22, "MoveState": "Running"}]
    assert len(bundle["recentMoves"]) == 20
    assert bundle["recentMoves"][0]["MoveID"] == 22
    material = bundle["currentState"]["materials"][0]
    assert material["rawStepId"] == 1
    assert material["internalStageIndex"] == 1
    assert material["routeSteps"][2]["stations"] == ["PM1"]
    blocker = bundle["candidateActions"]["actions"][0]["blocker"]
    assert blocker == {
        "category": "physical-blocked",
        "rule": "晶圆下一工序仅允许 PM1/PM2/PM3",
        "materials": ["101"],
        "robot": "VTR",
        "source": "VTR",
        "sourceSlot": 1,
        "destination": "LA",
        "destinationSlot": 2,
    }


def test_bundle_serialization_uses_readable_download_name() -> None:
    """下载文件应为可读 UTF-8 JSON，并在名称中标识结果和回放毫秒。"""
    content, file_name = serialize_deadlock_diagnostic_bundle(
        {"schemaVersion": 1, "message": "死锁"},
        replay_time=43.1,
        result_id="test result/1",
    )

    assert file_name == "deadlock-diagnostic-test-result-1-43100ms.json"
    assert json.loads(content.decode("utf-8"))["message"] == "死锁"


def test_topology_toolbar_exposes_deadlock_diagnostic_download() -> None:
    """真实页面应提供诊断包按钮，前端通过专用分析接口下载文件。"""
    repository_root = Path(__file__).resolve().parents[1]
    html = (
        repository_root / "app/frontend/config_editor.html"
    ).read_text(encoding="utf-8")
    client_source = (
        repository_root / "app/frontend/src/api_client.ts"
    ).read_text(encoding="utf-8")

    assert 'id="visualExportDeadlockDiagnostic"' in html
    assert "/api/analysis/deadlock-diagnostic" in client_source


class DeadlockFeedbackTests(unittest.TestCase):
    """覆盖失败输出保留及平台明确支持的死锁判型边界。"""

    def test_deadlock_feedback_keeps_partial_output(self) -> None:
        """MLP 格式的失败 Feedback 不得进入完整计划校验或丢失部分计划。"""
        output = {
            "MoveList": [{
                "MoveID": 7,
                "MoveType": 9,
                "StartTime": 1.0,
                "EndTime": 2.0,
            }],
            "Feedback": ["调度失败: Machine 无可执行搬运意图"],
        }
        reproduction = config_server.ReproductionLog()

        with self.assertRaises(LoggedPlanError) as context:
            config_server._raise_deadlock_feedback(
                output,
                reproduction,
                context="initial",
            )

        failure = context.exception.failure_output
        self.assertEqual([7], [move["MoveID"] for move in failure["MoveList"]])
        self.assertEqual(
            "DEADLOCK.UNCLASSIFIED",
            failure["FailureContext"]["Code"],
        )
        self.assertEqual(
            "未死锁，但算法认为死锁",
            failure["FailureContext"]["Message"],
        )
        self.assertEqual("AlgOutput", reproduction.entries[-1]["Describe"])


    def test_machine_diagnostic_classifies_cleaning_and_loadlock_deadlocks(self) -> None:
        """平台不再识别清洗冲突、Dummy 自阻塞、LoadLock 及资源等待环。"""
        held_cleaning = {
            "PendingMaterials": [
                {"MaterialID": 1, "Location": "VTR", "NextStations": ["PM4"]},
                {"MaterialID": 100000, "Location": "LB", "IsDummy": True,
                 "RemainingProcessStations": ["PM4"]},
            ],
            "HeldRobots": [{"Robot": "VTR", "Capacity": 2, "Hands": {"2": 1}}],
            "OccupiedStations": [],
            "LoadLocks": [{"Station": "LB"}],
        }
        self.assertIsNone(config_server._classify_deadlock_diagnostic(held_cleaning))

        cleaning_self_blocked = {
            "PendingMaterials": [{
                "MaterialID": 100000, "Location": "PM2", "IsDummy": True,
                "NextStations": ["PM2"],
            }],
        }
        self.assertIsNone(config_server._classify_deadlock_diagnostic(cleaning_self_blocked))

        loadlock_cycle = {
            "PendingMaterials": [
                {"MaterialID": 1, "Location": "LA", "NextStations": ["PM1"]},
                {"MaterialID": 2, "Location": "PM1", "NextStations": ["LA"]},
            ],
            "LoadLocks": [{"Station": "LA"}],
        }
        self.assertIsNone(config_server._classify_deadlock_diagnostic(loadlock_cycle))

        process_cycle = {
            "PendingMaterials": [
                {"MaterialID": 1, "Location": "PM1", "NextStations": ["PM2"]},
                {"MaterialID": 2, "Location": "PM2", "NextStations": ["PM1"]},
            ],
            "OccupiedStations": [
                {"Station": "PM1", "Full": True, "Occupied": {"1": 1}},
                {"Station": "PM2", "Full": True, "Occupied": {"1": 2}},
            ],
        }
        self.assertIsNone(config_server._classify_deadlock_diagnostic(process_cycle))


    def test_machine_diagnostic_only_recognizes_full_hands_and_all_targets(self) -> None:
        """平台仅保留两种满手满腔类型，双臂必须检查两片的全部目标。"""
        diagnostic = {
            "PendingMaterials": [
                {"MaterialID": 1, "NextStations": ["PM1"]},
                {"MaterialID": 2, "NextStations": ["PM2"]},
            ],
            "HeldRobots": [{"Robot": "R", "Capacity": 1, "Hands": {"1": 1}}],
            "OccupiedStations": [{"Station": "PM1", "Full": True}, {"Station": "PM2", "Full": True}],
        }
        self.assertEqual("DEADLOCK.SINGLE_ARM_TARGET_FULL", config_server._classify_deadlock_diagnostic(diagnostic)["Code"])
        diagnostic["HeldRobots"][0]["Capacity"] = 2
        self.assertIsNone(config_server._classify_deadlock_diagnostic(diagnostic))
        diagnostic["HeldRobots"][0]["Hands"]["2"] = 2
        self.assertEqual("DEADLOCK.DUAL_ARM_TARGETS_FULL", config_server._classify_deadlock_diagnostic(diagnostic)["Code"])
        diagnostic["OccupiedStations"][1]["Full"] = False
        self.assertIsNone(config_server._classify_deadlock_diagnostic(diagnostic))
