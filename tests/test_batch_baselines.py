"""批量运行的 Heuristic Baseline 跳过、创建、刷新与失效契约测试。

从原批量综合测试机械迁移，原有输入与断言保持；运行制品保存桩隔离 exports，
设备夹具由 tests/support 独立读取。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import app.backend.application as config_server
from app.backend.application import LoggedPlanError, extract_init_data
from tests.support.plan_fixtures import device_recording, job as _job, route as _route
from tests.support.run_artifact_fixtures import saved_run_fields


class BatchBaselineTests(unittest.TestCase):
    """只覆盖 Baseline 对主策略结果的影响和工作区更新语义。"""

    def setUp(self) -> None:
        """为每项测试构造独立设备副本，避免可变 Baseline 泄漏。"""
        self.device = extract_init_data(device_recording())

    def test_non_heuristic_batch_creates_baseline_and_reports_improvement(self) -> None:
        """其他策略首次运行时应先补算 Heuristic，并返回相对改善。"""
        test_case = {
            "id": "test-baseline", "name": "Baseline 案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-baseline", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }

        def fake_execute(plan):
            makespan = 100.0 if plan["strategy"] == "heuristic" else 80.0
            return {
                "ok": True, "totalElapsedMs": 12.0, "cpuTimeMs": 7.0,
                "makespan": makespan, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server.run_workspace_test_batch(
                "device-baseline", "回归", "loadlock-macro", {}, maximum_workers=1, hongye_check=False,
            )

        item = result["items"][0]
        self.assertEqual("succeeded", item["baseline"]["status"])
        self.assertEqual(100.0, item["baseline"]["makespan"])
        self.assertEqual(80.0, item["makespan"])
        self.assertEqual(-20.0, item["makespanDelta"])
        self.assertEqual(20.0, item["improvementPercent"])
        self.assertEqual(0, item["robotWaferDwellTime"]["sampleCount"])

    def test_batch_skip_baseline_skips_heuristic(self) -> None:
        """勾选“跳过Baseline”后批量运行不再连带执行本地 heuristic。"""
        test_case = {
            "id": "test-skip-baseline", "name": "跳过基线案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-skip-baseline", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        executed_strategies: list = []

        def fake_execute(plan):
            executed_strategies.append(str(plan["strategy"]))
            return {
                "ok": True, "totalElapsedMs": 12.0, "cpuTimeMs": 7.0,
                "makespan": 80.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server.run_workspace_test_batch(
                "device-skip-baseline", "回归", "other_alg:demo", {},
                skip_baseline=True, maximum_workers=1, hongye_check=False,
            )

        # 只执行主策略，不再补算 heuristic baseline。
        self.assertEqual(["other_alg:demo"], executed_strategies)
        item = result["items"][0]
        self.assertEqual("succeeded", item["status"])
        self.assertEqual("skipped", item["baseline"]["status"])
        self.assertNotIn("improvementPercent", item)
        self.assertNotIn("baseline", test_case)

    def test_skip_baseline_ignores_existing_baseline(self) -> None:
        """跳过 Baseline 时不读取已有基线记录，统一返回 skipped 占位。"""
        test_case = {
            "id": "test-skip-baseline-existing", "name": "已有基线跳过案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-skip-baseline-existing", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        # 放入指纹匹配的有效旧基线，验证跳过时不读取它。
        matching_fingerprint = config_server._workspace_baseline_fingerprint(device, test_case)
        test_case["baseline"] = {
            "status": "succeeded", "fingerprint": matching_fingerprint, "makespan": 1.0,
        }
        executed_strategies: list = []

        def fake_execute(plan):
            executed_strategies.append(str(plan["strategy"]))
            return {
                "ok": True, "totalElapsedMs": 12.0, "cpuTimeMs": 7.0,
                "makespan": 80.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
        ):
            result, baseline, error = config_server._execute_workspace_test_with_baseline(
                device, test_case, "other_alg:demo", {}, skip_baseline=True,
            )

        self.assertEqual(["other_alg:demo"], executed_strategies)
        self.assertIsNotNone(result)
        self.assertIsNone(error)
        self.assertEqual("skipped", baseline["status"])
        self.assertEqual(matching_fingerprint, test_case["baseline"]["fingerprint"])

    def test_skip_baseline_heuristic_keeps_result_without_persisting(self) -> None:
        """heuristic 主策略 + 跳过 Baseline：照常执行，但不回写基线记录。"""
        test_case = {
            "id": "test-skip-baseline-heuristic", "name": "启发式跳过基线案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-skip-baseline-heuristic", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        executed_strategies: list = []

        def fake_execute(plan):
            executed_strategies.append(str(plan["strategy"]))
            return {
                "ok": True, "totalElapsedMs": 12.0, "cpuTimeMs": 7.0,
                "makespan": 80.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True) as persist_mock,
        ):
            result, baseline, error = config_server._execute_workspace_test_with_baseline(
                device, test_case, "heuristic", {}, skip_baseline=True,
            )

        self.assertIsNotNone(result)
        self.assertIsNone(error)
        # 只执行一次 heuristic 主策略；结果不落库、不写入工作区基线。
        self.assertEqual(["heuristic"], executed_strategies)
        self.assertEqual("skipped", baseline["status"])
        persist_mock.assert_not_called()
        self.assertNotIn("baseline", test_case)

    def test_heuristic_refreshes_changed_baseline_result(self) -> None:
        """再次运行 Heuristic 时，应以本次 makespan 和 CPU Time 覆盖旧值。"""
        test_case = {
            "id": "test-refresh", "name": "刷新案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-refresh", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        fingerprint = config_server._workspace_baseline_fingerprint(device, test_case)
        test_case["baseline"] = {
            "status": "succeeded", "fingerprint": fingerprint,
            "makespan": 90.0, "cpuTimeMs": 4.0,
        }
        refreshed = {
            "ok": True, "totalElapsedMs": 13.0, "cpuTimeMs": 8.0,
            "makespan": 100.0, "moveCount": 3, "validation": "passed",
            "output": {"MoveList": []}, "reproductionLog": [],
        }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", return_value=refreshed),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server.run_workspace_test_batch(
                "device-refresh", "回归", "heuristic", {}, maximum_workers=1, hongye_check=False,
            )

        baseline = result["items"][0]["baseline"]
        self.assertEqual(100.0, baseline["makespan"])
        self.assertEqual(8.0, baseline["cpuTimeMs"])

    def test_failed_baseline_replaces_old_data_and_reports_reason(self) -> None:
        """Baseline 重算失败时不能继续返回旧数据，但其他策略结果仍可展示。"""
        test_case = {
            "id": "test-failed-base", "name": "失败案例", "group": "回归",
            "roundCount": 1, "options": {"seed": 2},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
            "baseline": {
                "status": "succeeded", "fingerprint": "stale",
                "makespan": 50.0, "cpuTimeMs": 2.0,
            },
        }
        device = {
            "id": "device-failed-base", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }

        def fake_execute(plan):
            if plan["strategy"] == "heuristic":
                raise LoggedPlanError("Baseline 无可行解", [])
            return {
                "ok": True, "totalElapsedMs": 12.0, "cpuTimeMs": 7.0,
                "makespan": 80.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server.run_workspace_test_batch(
                "device-failed-base", "回归", "loadlock-macro", {}, maximum_workers=1, hongye_check=False,
            )

        item = result["items"][0]
        self.assertTrue(item["ok"])
        self.assertEqual("failed", item["baseline"]["status"])
        self.assertIn("Baseline 无可行解", item["baseline"]["error"])
        self.assertNotIn("improvementPercent", item)
        self.assertNotIn("makespan", item["baseline"])

    def test_configuration_change_invalidates_baseline_fingerprint(self) -> None:
        """测试配置变化后，旧 Baseline 应立即变为 invalid。"""
        test_case = {
            "id": "test-invalid", "name": "失效案例", "group": "回归",
            "roundCount": 1, "options": {"seed": 1},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-invalid", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        test_case["baseline"] = {
            "status": "succeeded",
            "fingerprint": config_server._workspace_baseline_fingerprint(device, test_case),
            "makespan": 100.0,
            "cpuTimeMs": 5.0,
        }
        test_case["options"]["seed"] = 9
        config_server._invalidate_stale_device_baselines(device)

        self.assertEqual("invalid", test_case["baseline"]["status"])
        self.assertNotIn("makespan", test_case["baseline"])
        self.assertIn("配置已修改", test_case["baseline"]["error"])
