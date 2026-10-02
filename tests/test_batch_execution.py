"""批量编排、校验配额、进度和取消状态的集成边界测试。

Baseline 与单次失败制品分别由专用测试文件覆盖；本文件只验证批量调度边界，
保存边界使用确定夹具，不写入真实 exports。
"""

from __future__ import annotations

from concurrent.futures import Future
import inspect
import json
import threading
import time
import unittest
import zipfile
from io import BytesIO
from unittest.mock import patch

import app.backend.application as config_server
from app.backend.application import LoggedPlanError, execute_plan, extract_init_data
from tests.support.plan_fixtures import PSE300_DEVICE_PATH, device_recording, job as _job, route as _route
from tests.support.run_artifact_fixtures import saved_run_fields


class BatchExecutionTests(unittest.TestCase):
    """批量运行、校验配额、Baseline 与取消状态。"""

    def setUp(self) -> None:
        """为每个案例提取同一份设备拓扑。"""
        self.recording = device_recording()
        self.device = extract_init_data(self.recording)

    def test_batch_run_uses_selected_strategy_for_every_test_in_current_group(self) -> None:
        """批量运行应筛选当前组，并把同一策略应用到组内全部测试。"""
        routes = [_route("BatchRoute", "PM1,PM2", "BatchRecipe")]
        grouped_tests = [
            {
                "id": "test-a", "name": "案例 A", "group": "回归",
                "roundCount": 1, "options": {"seed": 1},
                "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
            },
            {
                "id": "test-b", "name": "案例 B", "group": "回归",
                "roundCount": 1, "options": {"seed": 2},
                "rounds": [{"currentTime": 0, "jobs": [_job("B", "BatchRoute", "LP2")]}],
            },
            {
                "id": "test-c", "name": "其他组", "group": "性能",
                "roundCount": 1, "options": {},
                "rounds": [{"currentTime": 0, "jobs": [_job("C", "BatchRoute", "LP1")]}],
            },
        ]
        device = {
            "id": "device-batch",
            "name": "fixture.json",
            "device": self.device,
            "routes": routes,
            "cleans": [],
            "tests": grouped_tests,
        }
        submitted = []

        def fake_execute(plan):
            submitted.append(plan)
            return {
                "ok": True,
                "totalElapsedMs": 10.0,
                "makespan": 20.0,
                "moveCount": 3,
                "validation": "passed",
                "output": {"MoveList": []},
                "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server.run_workspace_test_batch(
                "device-batch", "回归", "loadlock-macro", {"seed": 9}, maximum_workers=2, hongye_check=False,
            )

        self.assertEqual(2, result["testCount"])
        self.assertEqual(2, result["succeeded"])
        self.assertEqual({"案例 A", "案例 B"}, {item["testName"] for item in result["items"]})
        self.assertEqual(4, len(submitted))
        self.assertEqual(2, sum(plan["strategy"] == "loadlock-macro" for plan in submitted))
        self.assertEqual(2, sum(plan["strategy"] == "heuristic" for plan in submitted))
        self.assertTrue(all(plan["options"]["seed"] == 9 for plan in submitted))
        self.assertTrue(all([route["name"] for route in plan["routes"]] == ["BatchRoute"] for plan in submitted))
        self.assertTrue(all(item["baseline"]["status"] == "succeeded" for item in result["items"]))
        self.assertTrue(all(item["improvementPercent"] == 0 for item in result["items"]))

    def test_batch_test_selection_filters_but_preserves_workspace_order(self) -> None:
        """调用方即使倒序提交测试 ID，执行队列也必须保持名称自然顺序。"""
        tests = [
            {"id": "test-a", "name": "案例 A", "group": "回归"},
            {"id": "test-b", "name": "案例 B", "group": "回归"},
            {"id": "test-c", "name": "案例 C", "group": "回归"},
            {"id": "test-x", "name": "其他组", "group": "性能"},
        ]
        group, selected = config_server._workspace_group_tests(
            {"tests": tests},
            "回归",
            ["test-c", "test-a"],
        )

        self.assertEqual("回归", group)
        self.assertEqual(["test-a", "test-c"], [test["id"] for test in selected])
        _, naturally_ordered = config_server._workspace_group_tests(
            {"tests": [
                {"id": "id-10", "name": "test10", "group": "回归"},
                {"id": "id-2", "name": "test2", "group": "回归"},
                {"id": "id-1", "name": "test1", "group": "回归"},
            ]},
            "回归",
        )
        self.assertEqual(["test1", "test2", "test10"], [test["name"] for test in naturally_ordered])
        with self.assertRaisesRegex(ValueError, "不属于当前测试组"):
            config_server._workspace_group_tests(
                {"tests": tests},
                "回归",
                ["test-x"],
            )

    def test_background_batch_exposes_queued_running_and_completed_item_status(self) -> None:
        """后台批量任务应在运行期间暴露逐项状态，并在结束后返回全部结果 URL。"""
        device = {
            "id": "device-progress",
            "name": "fixture.json",
            "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [],
            "tests": [
                {
                    "id": f"test-{index}", "name": f"案例 {index}", "group": "回归",
                    "roundCount": 1, "options": {},
                    "rounds": [{"currentTime": 0, "jobs": [_job(f"J{index}", "BatchRoute", "LP1")]}],
                }
                for index in (1, 2)
            ],
        }
        first_started = threading.Event()
        release = threading.Event()

        def fake_execute(_plan):
            first_started.set()
            self.assertTrue(release.wait(2))
            return {
                "ok": True, "totalElapsedMs": 10.0, "makespan": 20.0,
                "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
            # Baseline 持久化会全量解析工作区数据集目录，本测试只验证批量
            # 编排状态机，不验证文件写入，mock 掉以避免慢 I/O 拖垮断言时限。
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
        ):
            initial = config_server.start_workspace_test_batch(
                "device-progress", "回归", "heuristic", {}, maximum_workers=1, hongye_check=False,
            )
            self.assertTrue(first_started.wait(2))
            running = config_server.read_workspace_batch_run(initial["batchId"])
            self.assertEqual(["running", "queued"], [item["status"] for item in running["items"]])
            release.set()
            deadline = time.time() + 3
            while time.time() < deadline:
                completed = config_server.read_workspace_batch_run(initial["batchId"])
                if completed["status"] == "completed":
                    break
                time.sleep(0.01)
            else:
                self.fail("后台批量任务未在时限内完成")

        self.assertEqual(2, completed["completed"])
        self.assertEqual(["succeeded", "succeeded"], [item["status"] for item in completed["items"]])
        self.assertTrue(all(item["resultUrl"] == "/api/results/result-id" for item in completed["items"]))

    def test_large_batch_uses_configured_isolated_algorithm_processes(self) -> None:
        """8 项批量任务应使用配置的隔离进程数，不能被算法会话锁串行化。"""
        device = {
            "id": "device-process-batch", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [],
            "tests": [
                {
                    "id": f"test-{index}", "name": f"案例 {index}", "group": "回归",
                    "roundCount": 1, "options": {},
                    "rounds": [{"currentTime": 0, "jobs": [_job(f"J{index}", "BatchRoute", "LP1")]}],
                }
                for index in range(8)
            ],
        }
        executor_options = []
        submitted_process_devices = []

        class ImmediateProcessExecutor:
            """在当前进程执行任务，以确定性验证生产并发路由。"""

            def __init__(self, **options):
                executor_options.append(options)

            def submit(self, function, *args):
                submitted_process_devices.append(args[0])
                future = Future()
                try:
                    future.set_result(function(*args))
                except Exception as error:  # noqa: BLE001
                    future.set_exception(error)
                return future

            def shutdown(self, **_options):
                return None

        def fake_execute(_plan):
            return {
                "ok": True, "totalElapsedMs": 10.0, "cpuTimeMs": 8.0,
                "makespan": 20.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server._batch_service, "ProcessPoolExecutor", ImmediateProcessExecutor),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server._execute_workspace_test_batch(
                device,
                device["tests"],
                "回归",
                "heuristic",
                {},
                hongye_check=False,
                skip_baseline=True,
                maximum_workers=8,
                use_process_isolation=True,
            )

        self.assertTrue(result["processIsolation"])
        self.assertEqual(8, result["workerCount"])
        self.assertEqual(8, result["succeeded"])
        self.assertEqual(8, executor_options[0]["max_workers"])
        self.assertTrue(all("tests" not in device for device in submitted_process_devices))

    def test_small_external_algorithm_batch_uses_process_isolation(self) -> None:
        """小批次外部算法也必须绕开进程内全局会话锁并真正并行。"""
        should_isolate = config_server._batch_service._should_use_process_isolation

        self.assertTrue(should_isolate(
            "other_alg:fra-09151735",
            worker_count=6,
            test_count=6,
            use_process_isolation=True,
        ))
        self.assertFalse(should_isolate(
            "heuristic",
            worker_count=6,
            test_count=6,
            use_process_isolation=True,
        ))
        self.assertFalse(should_isolate(
            "other_alg:fra-09151735",
            worker_count=1,
            test_count=6,
            use_process_isolation=True,
        ))
        self.assertFalse(should_isolate(
            "other_alg:fra-09151735",
            worker_count=6,
            test_count=6,
            use_process_isolation=False,
        ))

    def _parallel_worker_device(self, device_id: str, count: int = 3) -> dict:
        """构造并发配置测试共用的批量设备与测试组。"""
        return {
            "id": device_id, "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [],
            "tests": [
                {
                    "id": f"test-{index}", "name": f"案例 {index}", "group": "回归",
                    "roundCount": 1, "options": {},
                    "rounds": [{"currentTime": 0, "jobs": [_job(f"J{index}", "BatchRoute", "LP1")]}],
                }
                for index in range(1, count + 1)
            ],
        }

    def test_validation_limiter_is_shared_by_all_algorithm_workers(self) -> None:
        """同一批算法 worker 必须收到同一个 HongYe 校验闸门。"""
        device = self._parallel_worker_device("device-validation-limit")
        received_limiters = []

        def fake_execute(plan, **kwargs):
            received_limiters.append(kwargs.get("hongye_validation_limiter"))
            return {
                "ok": True, "totalElapsedMs": 10.0, "cpuTimeMs": 8.0,
                "makespan": 20.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
        ):
            result = config_server.run_workspace_test_batch(
                "device-validation-limit", "回归", "heuristic", {},
                maximum_workers=3, validation_workers=2, hongye_check=True,
            )

        self.assertEqual(3, result["workerCount"])
        self.assertEqual(2, result["validationWorkers"])
        self.assertEqual(3, result["succeeded"])
        self.assertIsNotNone(received_limiters[0])
        self.assertTrue(all(item is received_limiters[0] for item in received_limiters))

    def test_automatic_baseline_uses_same_validation_limiter(self) -> None:
        """外部策略补算 Baseline 时不得绕过配置的 HongYe 校验配额。"""
        device = self._parallel_worker_device("device-baseline-limit", count=1)
        received = []

        def fake_execute(plan, **kwargs):
            received.append((plan["strategy"], kwargs.get("hongye_validation_limiter")))
            return {
                "ok": True, "totalElapsedMs": 10.0, "cpuTimeMs": 8.0,
                "makespan": 20.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
        ):
            result = config_server.run_workspace_test_batch(
                "device-baseline-limit", "回归", "other_alg:demo", {},
                maximum_workers=1, validation_workers=1, hongye_check=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(["heuristic", "other_alg:demo"], [row[0] for row in received])
        self.assertIs(received[0][1], received[1][1])

    def test_worker_configuration_is_clamped_to_server_limits(self) -> None:
        """后端必须独立限制算法与校验并行数，不能信任 HTTP 输入。"""
        device = self._parallel_worker_device("device-worker-clamp", count=31)
        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", return_value={
                "ok": True, "totalElapsedMs": 10.0, "cpuTimeMs": 8.0,
                "makespan": 20.0, "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
        ):
            result = config_server.run_workspace_test_batch(
                "device-worker-clamp", "回归", "heuristic", {},
                maximum_workers=99, validation_workers=99, hongye_check=True,
            )
        self.assertEqual(30, result["workerCount"])
        self.assertEqual(15, result["validationWorkers"])

    def test_skip_baseline_failure_does_not_persist_from_parallel_worker(self) -> None:
        """跳过 Baseline 后算法失败也不得抢占工作区写锁保存失败基线。"""
        test_case = {
            "id": "test-failed-skip", "name": "跳过失败基线", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-failed-skip", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        with (
            patch.object(config_server, "execute_plan", side_effect=RuntimeError("算法失败")),
            patch.object(config_server, "_persist_workspace_baseline") as persist,
        ):
            result, baseline, error = config_server._execute_workspace_test_with_baseline(
                device,
                test_case,
                "heuristic",
                {},
                skip_baseline=True,
                hongye_check=False,
            )

        self.assertIsNone(result)
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual("skipped", baseline["status"])
        persist.assert_not_called()

    def test_batch_execution_has_no_compatibility_setting(self) -> None:
        """批量执行不再传递独立兼容开关，默认保持算法时间。"""
        test_case = {
            "id": "test-execution-settings", "name": "执行设置链路", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")]}],
        }
        device = {
            "id": "device-execution-settings", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }
        captured_plans = []

        def fake_execute(plan):
            captured_plans.append(plan)
            return {
                "ok": True, "totalElapsedMs": 1.0, "makespan": 2.0,
                "moveCount": 0, "validation": "skipped",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with patch.object(config_server, "execute_plan", side_effect=fake_execute):
            result, _baseline, error = config_server._execute_workspace_test_with_baseline(
                device, test_case, "heuristic", {},
                skip_baseline=True, hongye_check=False,
            )

        self.assertIsNone(error)
        self.assertIsNotNone(result)
        self.assertEqual(1, len(captured_plans))
        self.assertNotIn("compatibilityMode", captured_plans[0])
        self.assertFalse(captured_plans[0]["executionTimingEnabled"])

    def test_batch_log_archive_contains_each_available_test_log_and_manifest(self) -> None:
        """批量日志下载应将各测试日志及其测试集映射一次性打包。"""
        batch_id = "a" * 32
        config_server._BATCH_RUNS[batch_id] = {
            "batchId": batch_id,
            "deviceName": "fixture.json",
            "group": "回归",
            "strategy": "heuristic",
            "items": [
                {"index": 0, "testId": "test-a", "testName": "案例/A", "status": "succeeded", "logUrl": "/api/logs/log-a"},
                {"index": 1, "testId": "test-b", "testName": "案例 B", "status": "cancelled"},
                {"index": 2, "testId": "test-c", "testName": "案例 C", "status": "failed", "logUrl": "/api/logs/log-c"},
            ],
        }
        try:
            with patch.object(config_server, "read_reproduction_log", side_effect=lambda log_id: [{"Type": log_id}]):
                content, filename = config_server.build_workspace_batch_log_archive(batch_id)
        finally:
            config_server._BATCH_RUNS.pop(batch_id, None)

        self.assertRegex(filename, r"^批量复现日志-fixture-回归-\d{8}-\d{6}\.zip$")
        with zipfile.ZipFile(BytesIO(content)) as archive:
            self.assertEqual(["t01_案例_A.json", "t03_案例 C.json", "manifest.json"], archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(2, manifest["exportedLogCount"])
        self.assertEqual("t01_案例_A.json", manifest["items"][0]["logFile"])
        self.assertEqual("", manifest["items"][1]["logFile"])


    def test_external_validation_failure_keeps_metrics_and_baseline_comparison(self) -> None:
        """外部算法校验失败后仍应保留原始指标和 Baseline 对比。"""
        test_case = {
            "id": "test-external-invalid", "name": "外部校验失败案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "jobs": [_job("A", "BatchRoute", "LP1")] }],
        }
        device = {
            "id": "device-external-invalid", "name": "fixture.json", "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [], "tests": [test_case],
        }

        def fake_execute(plan):
            if plan["strategy"] == "heuristic":
                return {
                    "ok": True, "totalElapsedMs": 12.0, "cpuTimeMs": 7.0,
                    "makespan": 100.0, "moveCount": 3, "validation": "passed",
                    "output": {"MoveList": []}, "reproductionLog": [],
                }
            raise LoggedPlanError(
                "状态推进失败|MVL-STATE-UNKNOWN|无效动作",
                [],
                failure_output={"MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 80}]},
                validation_issues=["MoveID=1 无效动作"],
            )

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "_persist_workspace_baseline", return_value=True),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields()),
        ):
            result = config_server.run_workspace_test_batch(
                "device-external-invalid", "回归", "other_alg:demo", {}, maximum_workers=1, hongye_check=False,
            )

        item = result["items"][0]
        self.assertEqual("failed", item["status"])
        self.assertTrue(item["metricsAvailable"])
        self.assertEqual("failed", item["validation"])
        self.assertEqual(80.0, item["makespan"])
        self.assertEqual(20.0, item["improvementPercent"])
        self.assertEqual("/api/results/result-id", item["resultUrl"])

    def test_legacy_skip_validation_flag_cannot_bypass_move_list_checks(self) -> None:
        """遗留请求字段不能绕过平台状态推进校验。"""
        pse300 = json.loads(PSE300_DEVICE_PATH.read_text(encoding="utf-8"))
        plan = {
            "deviceName": "PSE300",
            "device": pse300,
            "strategy": "heuristic",
            "hongYeCheck": False,
            "roundCount": 1,
            "options": {},
            "recipes": [{"name": "R1", "time": 20, "modules": "PM1,PM2", "weight": {}}],
            "cleans": [],
            "routes": [_route("R1", "PM1,PM2", "R1")],
            "rounds": [{"currentTime": 0, "cjobs": [{"taskId": "1", "jobType": "NormalLot", "priority": 1, "taskMode": "Smart", "pjobs": [
                {"jobName": "P1", "routeRef": "R1", "loadPort": "LP1", "waferCount": 2, "priority": 1},
            ]}]}],
        }
        with patch.object(config_server, "validate_move_list", return_value=["[MVL-TEST] 无效动作"]):
            with self.assertRaisesRegex(LoggedPlanError, "状态推进失败\\|MVL-TEST\\|无效动作"):
                execute_plan({**plan, "skipValidation": True})

    def test_batch_plan_does_not_emit_legacy_skip_validation_flag(self) -> None:
        """批量计划不再生成跳过平台状态推进校验的配置。"""
        pse300 = json.loads(PSE300_DEVICE_PATH.read_text(encoding="utf-8"))
        test_case = {
            "id": "test-skip-batch", "name": "跳过校验批量案例", "group": "回归",
            "roundCount": 1, "options": {},
            "rounds": [{"currentTime": 0, "cjobs": [{"taskId": "1", "jobType": "NormalLot", "priority": 1, "taskMode": "Smart", "pjobs": [
                {"jobName": "P1", "routeRef": "R1", "loadPort": "LP1", "waferCount": 2, "priority": 1},
            ]}]}],
        }
        device = {
            "id": "device-skip-batch", "name": "fixture.json", "device": pse300,
            "routes": [_route("R1", "PM1,PM2", "R1")],
            "cleans": [], "tests": [test_case],
        }
        # 默认不写 skipValidation 键，保证 Baseline 指纹与旧版本一致。
        default_plan = config_server.build_workspace_batch_plan(device, test_case, "heuristic", {})
        self.assertNotIn("skipValidation", default_plan)
        self.assertNotIn("compatibilityMode", default_plan)
        fluctuation_plan = config_server.build_workspace_batch_plan(
            device, test_case, "heuristic", {}, execution_timing_enabled=True,
        )
        self.assertTrue(fluctuation_plan["executionTimingEnabled"])
        self.assertNotIn("compatibilityMode", fluctuation_plan)




    def test_robot_wafer_dwell_time_tracks_pick_place_and_swap_waits(self) -> None:
        """机器人持片驻留应统计 Pick/Place 间隙，并正确衔接 Swap 的收发晶圆。"""
        moves = [
            {"MoveID": 1, "MoveType": 0, "Robot": "VTR", "MatIDList": [1], "StartTime": 0, "EndTime": 2},
            {"MoveID": 8, "MoveType": 5, "Robot": "VTR", "StartTime": 3, "EndTime": 5},
            {"MoveID": 2, "MoveType": 1, "Robot": "VTR", "MatIDList": [1], "StartTime": 7, "EndTime": 9},
            {"MoveID": 3, "MoveType": 2, "ModuleName": "ATR", "MatIDList": [2], "StartTime": 8, "EndTime": 10},
            {"MoveID": 4, "MoveType": 3, "ModuleName": "ATR", "MatIDList": [2], "StartTime": 13, "EndTime": 15},
            {"MoveID": 5, "MoveType": 0, "Robot": "VTR", "MatIDList": [3], "StartTime": 18, "EndTime": 20},
            {"MoveID": 6, "MoveType": 4, "Robot": "VTR", "RecvMatList": [4], "SendMatList": [3], "StartTime": 22, "EndTime": 24},
            {"MoveID": 7, "MoveType": 1, "Robot": "VTR", "MatIDList": [4], "StartTime": 28, "EndTime": 30},
        ]

        metrics = config_server._robot_wafer_dwell_time(moves)

        self.assertEqual(4, metrics["sampleCount"])
        self.assertAlmostEqual(12.0, metrics["totalSeconds"])
        self.assertAlmostEqual(3.0, metrics["medianSeconds"])
        self.assertAlmostEqual(4.0, metrics["maxSeconds"])




    def test_background_batch_can_be_cancelled_without_overwriting_status(self) -> None:
        """取消后排队和运行项都应立即终止，迟到的算法结果不能覆盖状态。"""
        device = {
            "id": "device-cancel",
            "name": "fixture.json",
            "device": self.device,
            "routes": [_route("BatchRoute", "PM1,PM2", "BatchRecipe")],
            "cleans": [],
            "tests": [
                {
                    "id": f"test-{index}", "name": f"案例 {index}", "group": "回归",
                    "roundCount": 1, "options": {},
                    "rounds": [{"currentTime": 0, "jobs": [_job(f"J{index}", "BatchRoute", "LP1")]}],
                }
                for index in (1, 2)
            ],
        }
        started = threading.Event()
        release = threading.Event()

        def fake_execute(_plan):
            started.set()
            self.assertTrue(release.wait(2))
            return {
                "ok": True, "totalElapsedMs": 10.0, "makespan": 20.0,
                "moveCount": 3, "validation": "passed",
                "output": {"MoveList": []}, "reproductionLog": [],
            }

        with (
            patch.object(config_server, "get_workspace_batch_run_context", return_value=device),
            patch.object(config_server, "execute_plan", side_effect=fake_execute),
            patch.object(config_server, "save_run_artifacts", return_value=saved_run_fields(has_output=False)),
        ):
            initial = config_server.start_workspace_test_batch(
                "device-cancel", "回归", "heuristic", {}, maximum_workers=1, hongye_check=False,
            )
            self.assertTrue(started.wait(2))
            cancelled = config_server.cancel_workspace_batch_run(initial["batchId"])
            self.assertEqual("cancelled", cancelled["status"])
            self.assertEqual(2, cancelled["cancelled"])
            self.assertEqual(["cancelled", "cancelled"], [item["status"] for item in cancelled["items"]])
            release.set()
            time.sleep(0.2)

        final = config_server.read_workspace_batch_run(initial["batchId"])
        self.assertEqual("cancelled", final["status"])
        self.assertEqual(["cancelled", "cancelled"], [item["status"] for item in final["items"]])

    def test_batch_status_route_is_served_by_get_and_cancelled_by_delete(self) -> None:
        """轮询必须走 GET；DELETE 用于终止同一批量任务。"""
        get_source = inspect.getsource(config_server.ConfigEditorHandler.do_GET)
        post_source = inspect.getsource(config_server.ConfigEditorHandler.do_POST)
        delete_source = inspect.getsource(config_server.ConfigEditorHandler.do_DELETE)
        self.assertIn('path.startswith("/api/run-batches/")', get_source)
        self.assertNotIn('path.startswith("/api/run-batches/")', post_source)
        self.assertIn("cancel_workspace_batch_run", delete_source)
        self.assertIn('parts[2] == "devices"', delete_source)
        self.assertIn("delete_workspace_device", delete_source)

    def test_batch_run_api_forwards_algorithm_and_validation_workers(self) -> None:
        """批量 HTTP 入口必须把两个独立并发配置传给后台任务。"""
        post_source = inspect.getsource(config_server.ConfigEditorHandler.do_POST)
        self.assertIn('payload.get("maximumWorkers", DEFAULT_BATCH_WORKERS)', post_source)
        self.assertIn('payload.get("validationWorkers", DEFAULT_VALIDATION_WORKERS)', post_source)

    def test_run_settings_preferences_have_get_and_put_routes(self) -> None:
        """运行与分析习惯必须通过各自偏好 API 读写同一本地数据。"""
        get_source = inspect.getsource(config_server.ConfigEditorHandler.do_GET)
        put_source = inspect.getsource(config_server.ConfigEditorHandler.do_PUT)
        self.assertIn('path == "/api/preferences/run-settings"', get_source)
        self.assertIn("read_run_preferences()", get_source)
        self.assertIn('path == "/api/preferences/run-settings"', put_source)
        self.assertIn("update_run_preferences", put_source)
        self.assertIn('path == "/api/preferences/analysis-settings"', get_source)
        self.assertIn("read_analysis_preferences()", get_source)
        self.assertIn('path == "/api/preferences/analysis-settings"', put_source)
        self.assertIn("update_analysis_preferences", put_source)
