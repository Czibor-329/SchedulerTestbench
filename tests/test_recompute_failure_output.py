"""重算失败诊断输出：保留提交前缀、最新计划及取消标记，不包含前端源码扫描。"""

import unittest
from unittest.mock import patch

from app.backend import application as config_server
from app.backend.application import LoggedPlanError, execute_plan


class RecomputeFailureOutputTests(unittest.TestCase):
    """覆盖算法执行失败与现场推进失败两个真实边界。"""

    def test_recompute_algorithm_error_keeps_only_moves_not_in_remove_list(self) -> None:
        """重算调用报错时，诊断甘特图不得继续显示已取消的旧 Move。"""
        class FakeRuntime:
            """提供失败快照构造所需的最小运行时计划。"""

            current_plan = [
                {"MoveID": 11, "StartTime": 0, "EndTime": 5},
                {"MoveID": 12, "StartTime": 5, "EndTime": 10},
                {"MoveID": 13, "StartTime": 10, "EndTime": 15},
            ]

            @staticmethod
            def combined_output() -> dict:
                """模拟上一轮已拼接的甘特图数据。"""
                return {
                    "MoveList": [
                        {"MoveID": 9, "StartTime": -5, "EndTime": 0},
                        *FakeRuntime.current_plan,
                    ],
                    "Feedback": [{"Level": "Info", "Message": "首排完成"}],
                    "RecomputePoints": [],
                }

        output = config_server._build_recompute_failure_output(
            FakeRuntime(),
            {"RemoveList": [12, 13]},
            5.0,
            "第 2 轮新增 Job",
            RuntimeError("第二轮算法异常"),
        )

        self.assertEqual([9, 11, 12, 13], [move["MoveID"] for move in output["MoveList"]])
        self.assertFalse(output["MoveList"][1].get("RemovedByRecompute", False))
        self.assertTrue(output["MoveList"][2]["RemovedByRecompute"])
        self.assertTrue(output["MoveList"][3]["RemovedByRecompute"])
        self.assertEqual("algorithm-error", output["RecomputePoints"][-1]["Status"])
        self.assertEqual("第二轮算法异常", output["Feedback"][-1]["Message"])


    def test_recompute_preparation_error_keeps_last_successful_movelist(self) -> None:
        """算法调用前的旧计划回放异常也应返回上一代诊断甘特图。"""
        previous_moves = [
            {"MoveID": 1, "StartTime": 0.0, "EndTime": 5.0},
            {"MoveID": 2, "StartTime": 5.0, "EndTime": 9.0},
        ]

        def fail_after_initial_output(_plan, reproduction):
            """模拟首排已保存、CJobCycle 旧计划回放随后失败。"""
            reproduction.add("AlgOutput", {"MoveList": previous_moves, "Feedback": []})
            raise ValueError("旧计划回放失败")

        with patch.object(config_server, "_execute_plan", side_effect=fail_after_initial_output):
            with self.assertRaises(LoggedPlanError) as context:
                execute_plan({"rounds": [], "hongYeCheck": False})

        failure_output = context.exception.failure_output
        self.assertIsNotNone(failure_output)
        self.assertEqual([1, 2], [move["MoveID"] for move in failure_output["MoveList"]])
        self.assertEqual("after-algorithm-output", failure_output["FailureContext"]["Stage"])
        self.assertEqual("旧计划回放失败", failure_output["Feedback"][-1]["Message"])


    def test_recompute_preparation_error_combines_committed_prefix_and_latest_plan(self) -> None:
        """跨代现场回放失败时，甘特图必须拼接旧代已启动前缀与最新计划。"""
        entries = [
            {
                "Describe": "AlgSchedule",
                "SimTime": 0.0,
                "Info": {},
            },
            {
                "Describe": "AlgOutput",
                "SimTime": 0.0,
                "Info": {
                    "MoveList": [
                        {"MoveID": 1, "StartTime": 0.0, "EndTime": 5.0},
                        {"MoveID": 2, "StartTime": 5.0, "EndTime": 12.0},
                        {"MoveID": 3, "StartTime": 12.0, "EndTime": 20.0},
                    ],
                    "Feedback": [],
                },
            },
            {
                "Describe": "RecomputeControl",
                "SimTime": 10.0,
                "Info": {
                    "RecomputeInfo": {
                        "CurrentTime": 10.0,
                        "EffectiveTime": 10.0,
                        "Reason": "CJobCycle 补片（LP1 2/4）",
                    }
                },
            },
            {
                "Describe": "AlgSchedule",
                "SimTime": 10.0,
                "Info": {},
            },
            {
                "Describe": "AlgOutput",
                "SimTime": 10.0,
                "Info": {
                    "MoveList": [
                        {"MoveID": 4, "StartTime": 10.0, "EndTime": 15.0},
                        {"MoveID": 5, "StartTime": 15.0, "EndTime": 25.0},
                    ],
                    "Feedback": [],
                },
            },
        ]

        output = config_server._build_prior_plan_failure_output(
            entries,
            RuntimeError("第二代现场回放失败"),
        )

        self.assertIsNotNone(output)
        self.assertEqual([1, 2, 4, 5], [move["MoveID"] for move in output["MoveList"]])
        self.assertEqual(0.0, output["MoveList"][0]["StartTime"])
        self.assertEqual(25.0, output["MoveList"][-1]["EndTime"])
        self.assertEqual(1, len(output["RecomputePoints"]))
        self.assertEqual(10.0, output["RecomputePoints"][0]["Time"])
        self.assertIn("CJobCycle 补片", output["RecomputePoints"][0]["Reason"])
