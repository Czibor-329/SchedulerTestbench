"""精简周期核的独立数值契约：九组合对照、手算反例、真实执行证据及输入拒绝。

现有建图器只作为对照，不参与新核的输入准备。非对称样本固定种子，含逐腔配方、
门/转位/切压差异；另用既有合成执行轨迹防止两实现同时偏离已验收的动作语义。
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.backend.theory_capacity.compact_period import calculate_period
from app.backend.theory_capacity.device_graph import build_cycle_capacity_model
from app.backend.theory_capacity.solver import analyze_capacity
from app.backend.theory_capacity.topology import analyze_capacity_topology
from tests.support.compact_period_cases import (
    COMBINATIONS, ROUTES, period_arguments, period_problem, randomize_asymmetric_timing,
)

REFERENCE_TRACES = {row["key"]: row for row in json.loads(
    (Path(__file__).parent / "support/capacity_reference_traces.json").read_text(encoding="utf-8"))["cases"]}
PERIOD_TOLERANCE = 1e-7
RANDOM_SEED_BASE = 20261014


@pytest.mark.parametrize("mode,route", COMBINATIONS)
@pytest.mark.parametrize("process_seconds", [0, 10, 80, 120, 240])
@pytest.mark.parametrize("members", [1, 2])
def test_fixed_combinations_match_existing_model(mode, route, process_seconds, members):
    """对照周期、片间隔、物理产能和负荷下界；成员数不应改变周期。"""
    problem = period_problem(mode, route, process_seconds)
    result = calculate_period(**period_arguments(problem, mode), members_per_unit=members)
    built = build_cycle_capacity_model(problem, analyze_capacity_topology(problem), members_per_unit=members)
    expected = analyze_capacity(built.model)
    assert result["period_seconds"] == pytest.approx(expected.period_seconds, rel=0, abs=PERIOD_TOLERANCE)
    assert result["seconds_per_unit"] == pytest.approx(expected.seconds_per_unit, rel=0, abs=PERIOD_TOLERANCE)
    assert result["units_per_cycle"] == built.facts.units_per_cycle
    assert result["physical_wafers_per_hour"] == pytest.approx(expected.physical_wafers_per_hour, rel=1e-8)
    assert result["resource_period_lower_bound_seconds"] == pytest.approx(expected.resource_period_lower_bound_seconds)


@pytest.mark.parametrize("mode,route", COMBINATIONS)
@pytest.mark.parametrize("seed_offset", range(24))
def test_asymmetric_timing_matches_without_mutating_input(mode, route, seed_offset):
    """216 组非对称输入覆盖不同方向、门动作、逐腔工艺和压力计时。"""
    problem = period_problem(mode, route)
    randomize_asymmetric_timing(problem, RANDOM_SEED_BASE + seed_offset)
    before = deepcopy(problem)
    result = calculate_period(**period_arguments(problem, mode))
    built = build_cycle_capacity_model(problem, analyze_capacity_topology(problem))
    expected = analyze_capacity(built.model)
    assert result["period_seconds"] == pytest.approx(expected.period_seconds, rel=0, abs=PERIOD_TOLERANCE)
    assert result["physical_wafers_per_hour"] == pytest.approx(expected.physical_wafers_per_hour, rel=1e-8)
    assert result["resource_period_lower_bound_seconds"] == pytest.approx(expected.resource_period_lower_bound_seconds)
    assert problem == before


@pytest.mark.parametrize("mode,route", COMBINATIONS)
@pytest.mark.parametrize("process_seconds", [0.0, 10.0, 80.0, 240.0])
def test_matches_recorded_repeated_actions(mode, route, process_seconds):
    """直接对照已通过物理校验的重复动作轨迹，不依赖旧求解结果作为期望。"""
    result = calculate_period(**period_arguments(period_problem(mode, route, process_seconds), mode))
    nodes = [list(node) for node in ROUTES[route]]
    evidence = REFERENCE_TRACES[f"rotation:{mode}:{nodes}:{process_seconds}"]
    assert evidence["physical_validation_passed"]
    arrivals = sorted(row["seconds"] for row in evidence["swap_arrivals"]
                      if set(nodes[0]).intersection(row["stations"]))
    stride = result["units_per_cycle"]
    middle = len(arrivals) // 2
    periods = [arrivals[index + stride] - arrivals[index] for index in range(middle, middle + 5 * stride)]
    assert periods == pytest.approx([result["period_seconds"]] * len(periods), rel=0, abs=0.001)


def test_long_return_transfer_requires_260_seconds():
    """PM 每周期负荷 200 秒、VAC 190 秒；跨资源闭环强制周期 260 秒。"""
    problem = period_problem("shared-bidirectional", "serial", 91)
    problem.robots["VTR"].transfer_time[("PM2", "LA", 1)] = 133
    result = calculate_period(**period_arguments(problem, "shared-bidirectional"))
    assert result["resource_period_lower_bound_seconds"] == 200
    assert result["period_seconds"] == pytest.approx(260, rel=0, abs=PERIOD_TOLERANCE)


def test_dedicated_handoff_exceeds_each_resource():
    """两轮交接项给出 49.5 秒，高于入口/出口锁各自 48 秒的负荷。"""
    problem = period_problem("dedicated-entry-exit", "serial", 10)
    problem.robots["VTR"].transfer_time[("PM1", "PM2", 1)] = 13
    result = calculate_period(**period_arguments(problem, "dedicated-entry-exit"))
    assert result["resource_period_lower_bound_seconds"] == 48
    assert result["period_seconds"] == pytest.approx(49.5, rel=0, abs=PERIOD_TOLERANCE)


def test_calculation_does_not_call_existing_model_or_solver(monkeypatch):
    """旧实现不可用时，新核仍独立展开动作并求得教学案例 258 秒。"""
    from app.backend.theory_capacity import device_graph, solver, timing_graph
    def forbidden(*args, **kwargs):
        """调用旧计算实现意味着独立性契约被破坏。"""
        pytest.fail("精简核不得调用旧建图器或求解器")
    monkeypatch.setattr(device_graph, "build_cycle_capacity_model", forbidden)
    monkeypatch.setattr(timing_graph.CycleActionTimingGraph, "build", forbidden)
    monkeypatch.setattr(solver, "analyze_capacity", forbidden)
    result = calculate_period(**period_arguments(period_problem("shared-bidirectional", "single"), "shared-bidirectional"))
    assert result["period_seconds"] == pytest.approx(258, rel=0, abs=PERIOD_TOLERANCE)


@pytest.mark.parametrize("mode,route", [("dedicated-entry-exit", "mixed"), ("single-bidirectional", "serial"), ("single-bidirectional", "mixed")])
def test_unsupported_combinations_are_rejected(mode, route):
    """矩阵中三个未支持组合不能静默套用近似公式。"""
    with pytest.raises(ValueError, match="不支持"):
        calculate_period(**period_arguments(period_problem(mode, route), mode))


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_invalid_process_seconds_are_rejected(value):
    """不能将非法加工时间转成看似有限的产能。"""
    arguments = period_arguments(period_problem("shared-bidirectional", "single"), "shared-bidirectional")
    arguments["process_nodes"] = ({"PM1": value},)
    with pytest.raises(ValueError, match="有限非负"):
        calculate_period(**arguments)


@pytest.mark.parametrize("field,value", [("tolerance_seconds", 0), ("tolerance_seconds", float("nan")), ("members_per_unit", 0), ("members_per_unit", True)])
def test_invalid_solver_parameters_are_rejected(field, value):
    """容差及产出成员数不能被隐式转换为合法输入。"""
    arguments = period_arguments(period_problem("shared-bidirectional", "single"), "shared-bidirectional")
    with pytest.raises(ValueError):
        calculate_period(**arguments, **{field: value})


def test_missing_exact_transfer_is_rejected():
    """严格计时缺少当前方向时不得借用别的转位时间。"""
    problem = period_problem("shared-bidirectional", "single")
    problem.robots["VTR"].strict_timing = True
    with pytest.raises(ValueError, match="PrepTransTime"):
        calculate_period(**period_arguments(problem, "shared-bidirectional"))


def test_all_zero_times_have_no_finite_capacity():
    """全零时长没有正周期下界，不能除零或报告有限产能。"""
    problem = period_problem("shared-bidirectional", "single", 0)
    for chamber in problem.chambers.values():
        for field in ("pick_prepare_time", "place_prepare_time", "pick_complete_time", "place_complete_time"):
            setattr(chamber, field, dict.fromkeys(getattr(chamber, field), 0.0))
        chamber.pump_time = chamber.vent_time = 0.0
    for robot in problem.robots.values():
        robot.pick_time = robot.place_time = dict.fromkeys(robot.scope, 0.0)
        robot.prep_trans_time = [{"Time": 0.0}]
    with pytest.raises(ValueError, match="没有正周期"):
        calculate_period(**period_arguments(problem, "shared-bidirectional"))
