"""单结果分析的输入解析与按需缓存边界。

HTTP 提交结果 ID 或一次性 MoveList；本模块校验设备和工序上下文，恢复运行指标，
调用 analysis 的统一统计实现，并将可重用的派生结果交给运行制品缓存持久化。
"""

from __future__ import annotations

from typing import Any, Mapping

from app.backend.analysis import (
    analyze_schedule_performance,
    build_schedule_analysis_context,
    normalize_move_payload,
    summarize_bottleneck_utilization,
)
from app.backend.artifacts.analysis_cache import read_cached_analysis, save_cached_analysis
from app.backend.artifacts.repository import read_result
from app.backend.execution.plan_builder import _finite_number


def analyze_schedule_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """校验分析请求并返回指标与瓶颈；同一持久化结果与统计口径命中时跳过 MoveList 读取。"""
    result_id = str(payload.get("resultId") or "").strip()
    device = payload.get("device")
    if device is not None and not isinstance(device, Mapping):
        raise ValueError("device 必须是 JSON 对象或 null")
    context = payload.get("context")
    if context is None:
        routes = payload.get("routes")
        rounds = payload.get("rounds")
        if routes is not None or rounds is not None:
            if routes is not None and not isinstance(routes, list):
                raise ValueError("routes 必须是数组")
            if rounds is not None and not isinstance(rounds, list):
                raise ValueError("rounds 必须是数组")
            context = build_schedule_analysis_context(routes, rounds)
    if context is not None and not isinstance(context, Mapping):
        raise ValueError("context 必须是 JSON 对象或 null")
    parameters = {
        "device": device, "context": context,
        "windowMode": str(payload.get("windowMode") or "steady"),
        "metricGroups": payload.get("metricGroups"),
    }
    if result_id:
        cached = read_cached_analysis(result_id, parameters)
        if cached is not None:
            return cached
        saved_result = read_result(result_id, include_replay_context=False)
        if saved_result is None:
            raise ValueError("结果不存在或已过期")
        moves = normalize_move_payload(saved_result)
        run_metrics = saved_result.get("RunMetricsMetadata")
        if not isinstance(run_metrics, Mapping):
            legacy_metadata = saved_result.get("ProductionMetricsMetadata")
            run_metrics = {
                "cpuTimeMs": (
                    _finite_number(legacy_metadata.get("calculationSeconds")) * 1000.0
                    if isinstance(legacy_metadata, Mapping) else None
                ),
                # RecomputePoints 不含首排，但首排也有一次算法 update。
                "recomputeCount": (
                    len(list(saved_result.get("RecomputePoints") or [])) + 1
                    if isinstance(legacy_metadata, Mapping) else 0
                ),
            }
    else:
        moves = normalize_move_payload(payload.get("moves", payload.get("result")))
        run_metrics = {
            "cpuTimeMs": payload.get("cpuTimeMs"),
            "recomputeCount": payload.get("recomputeCount"),
        }
    analysis = analyze_schedule_performance(
        moves, device, parameters["windowMode"], context, run_metrics,
        metric_groups=parameters["metricGroups"],
    )
    response = {
        "ok": True, "analysis": analysis,
        "bottleneck": summarize_bottleneck_utilization(analysis),
    }
    if result_id:
        save_cached_analysis(result_id, parameters, response)
    return response
