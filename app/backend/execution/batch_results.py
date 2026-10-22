"""批量测试结果项的组装与产物持久化。

本模块拥有批量成功、普通失败、可回放失败和取消项的统一响应格式。算法执行、
并发调度和 Baseline 计算不在这里实现；调用方通过明确回调提供指标与仓储能力。
"""

from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Callable, Dict, Mapping

from app.backend.execution.run_state import ReproductionLog, _alg_output_info


def build_failure_result_artifact(
    error: Any, replay_plan: Mapping[str, Any] | None,
    *, run_metrics: Mapping[str, Any] | None = None,
) -> Dict[str, Any] | None:
    """冻结失败的累计输出与各轮输入；未生成输出时返回 None，不创建回放计划。"""
    if error.failure_output is None:
        return None
    artifact = deepcopy(dict(error.failure_output))
    updates = [
        deepcopy(dict(entry["Info"]))
        for entry in error.reproduction_log
        if isinstance(entry, Mapping)
        and entry.get("Describe") == "AlgSchedule"
        and isinstance(entry.get("Info"), Mapping)
    ]
    if run_metrics is not None and run_metrics.get("cpuTimeMs") is not None:
        artifact["RunMetricsMetadata"] = {
            "cpuTimeMs": max(0.0, float(run_metrics["cpuTimeMs"])),
            "recomputeCount": len(updates),
        }
    if replay_plan is not None:
        artifact["ReplayContext"] = {
            "schema": "machine-replay-context-v1",
            "plan": deepcopy(dict(replay_plan)),
            "updates": updates,
        }
    return artifact


class BatchResultAssembler:
    """使用批量服务提供的仓储与指标能力组装最终测试项。"""

    def __init__(
        self,
        *,
        save_run_artifacts: Callable[..., Dict[str, Any]],
        logged_failure_fields: Callable[..., Dict[str, Any]],
        baseline_comparison: Callable[..., Dict[str, Any]],
        robot_wafer_dwell_time: Callable[[Any], Dict[str, Any]],
        is_external_algorithm: Callable[[str], bool],
        run_context: Mapping[str, Any] | None = None,
    ) -> None:
        """绑定每测试原子保存能力、指标函数及批次身份，避免结果与日志分开提交。"""
        self._save_run_artifacts = save_run_artifacts
        self._logged_failure_fields = logged_failure_fields
        self._baseline_comparison = baseline_comparison
        self._robot_wafer_dwell_time = robot_wafer_dwell_time
        self._is_external_algorithm = is_external_algorithm
        self._run_context = deepcopy(dict(run_context or {}))

    def _persist_terminal_item(
        self, item: Dict[str, Any], test_case: Mapping[str, Any],
        selected_plan: Mapping[str, Any] | None,
    ) -> Dict[str, Any]:
        """保存无算法输出的失败或取消摘要及诊断事件，保留当时的输入快照。"""
        reproduction = ReproductionLog()
        reproduction.add("Input", [deepcopy(dict(selected_plan))] if selected_plan else [])
        reproduction.add("AlgOutput", _alg_output_info(feedback=[{
            "Level": "Error", "Type": item["status"], "Message": item["error"],
        }]))
        summary = {**self._run_context, **item, "testSnapshot": deepcopy(dict(test_case))}
        item.update(self._save_run_artifacts(
            None, reproduction.entries, summary, execution_logs=[item["error"]],
        ))
        return item

    @staticmethod
    def _identity(index: int, test_case: Mapping[str, Any]) -> Dict[str, Any]:
        """生成所有结果状态共用的稳定测试标识。"""
        return {
            "index": index,
            "testId": str(test_case.get("id") or ""),
            "testName": str(test_case.get("name") or f"测试 {index + 1}"),
        }

    def cancelled(
        self, index: int, test_case: Mapping[str, Any],
        selected_plan: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """构造用户终止调度时的取消结果项。"""
        item = {
            **self._identity(index, test_case),
            "ok": False,
            "status": "cancelled",
            "error": "用户终止调度",
        }
        return self._persist_terminal_item(item, test_case, selected_plan)

    def plain_failure(
        self,
        index: int,
        test_case: Mapping[str, Any],
        error: BaseException,
        selected_plan: Mapping[str, Any] | None = None,
        baseline: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """构造算法层未分类异常的结果项。"""
        item = {
            **self._identity(index, test_case),
            "ok": False,
            "status": "failed",
            "error": str(error) or type(error).__name__,
        }
        if baseline is not None:
            item["baseline"] = deepcopy(dict(baseline))
        return self._persist_terminal_item(item, test_case, selected_plan)

    def logged_failure(
        self,
        index: int,
        test_case: Mapping[str, Any],
        error: Any,
        baseline: Mapping[str, Any],
        selected_plan: Mapping[str, Any],
        strategy: str,
        run_started: float,
    ) -> Dict[str, Any]:
        """保存可回放失败产物，并保留外部算法的客观指标。"""
        failure = {
            **self._identity(index, test_case),
            "ok": False,
            "status": "failed",
            "error": str(error) or type(error).__name__,
            "baseline": deepcopy(baseline),
            **self._logged_failure_fields(error, replay_plan=selected_plan, persist=False),
        }
        if self._is_external_algorithm(strategy) and error.validation_issues:
            elapsed_ms = (time.perf_counter() - run_started) * 1000.0
            moves = list((error.failure_output or {}).get("MoveList") or [])
            failure.update({
                "metricsAvailable": True,
                "totalElapsedMs": elapsed_ms,
                "cpuTimeMs": elapsed_ms,
                "robotWaferDwellTime": self._robot_wafer_dwell_time(moves),
            })
            failure.update(self._baseline_comparison(failure, baseline))
        failure.update(self._save_run_artifacts(
            build_failure_result_artifact(error, selected_plan, run_metrics=failure),
            error.reproduction_log,
            {**self._run_context, **failure},
            execution_logs=[str(error)],
        ))
        return failure

    def success(
        self,
        index: int,
        test_case: Mapping[str, Any],
        result: Mapping[str, Any],
        baseline: Mapping[str, Any],
        selected_plan: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """保存通过校验的结果、回放上下文和复现日志。"""
        artifact = deepcopy(dict(result["output"]))
        artifact["RunMetricsMetadata"] = {
            "cpuTimeMs": max(0.0, float(result.get("cpuTimeMs", result.get("totalElapsedMs", 0.0)))),
            "recomputeCount": len(list(result.get("updates") or [])),
        }
        run_metrics = artifact["RunMetricsMetadata"]
        artifact["ReplayContext"] = {
            "schema": "machine-replay-context-v1",
            "plan": deepcopy(selected_plan),
            "updates": deepcopy(list(result.get("updates") or [])),
        }
        item = {
            **self._identity(index, test_case),
            "ok": True,
            "status": "succeeded",
            "totalElapsedMs": result["totalElapsedMs"],
            "cpuTimeMs": result.get("cpuTimeMs", result["totalElapsedMs"]),
            "recomputeCount": run_metrics["recomputeCount"],
            "averageRecomputeTimeMs": (
                run_metrics["cpuTimeMs"] / run_metrics["recomputeCount"]
                if run_metrics["recomputeCount"] > 0
                else None
            ),
            "makespan": result["makespan"],
            "moveCount": result["moveCount"],
            "validation": result["validation"],
            "robotWaferDwellTime": self._robot_wafer_dwell_time(
                list(result["output"].get("MoveList") or []),
            ),
            **self._baseline_comparison(result, baseline),
        }
        item.update(self._save_run_artifacts(
            artifact,
            result["reproductionLog"],
            {**self._run_context, **item},
            execution_logs=result.get("logs") or [],
        ))
        return item
