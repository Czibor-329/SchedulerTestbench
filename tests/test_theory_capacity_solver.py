"""验证周期产能数学契约；使用手算环、耦合反例及可行相位作为独立依据。"""

import math
import pytest

from app.backend.theory_capacity.solver import CapacityModel, WaitingConstraint, analyze_capacity, shared_parallel_model


def test_shared_single_node_process_limit():
    """120 秒加工加 8 秒服务，每片最短 128 秒。"""
    report = analyze_capacity(shared_parallel_model([120]))
    assert report.period_seconds == pytest.approx(256, abs=1e-6)
    assert report.physical_wafers_per_hour == pytest.approx(28.125)


def test_pressure_limit_can_overlap_other_lock():
    """双锁各 144 秒恢复，不把两锁压力时长相加为串行整机停机。"""
    report = analyze_capacity(shared_parallel_model([8], pump_seconds=60, vent_seconds=60))
    assert report.period_seconds == pytest.approx(144, abs=1e-6)
    assert report.seconds_per_unit == pytest.approx(72)


def test_unequal_parallel_preserves_round_robin():
    """两腔各访问一次，慢腔 128 秒约束整个两片周期。"""
    report = analyze_capacity(shared_parallel_model([40, 120]))
    assert report.period_seconds == pytest.approx(128, abs=1e-6)
    assert report.physical_wafers_per_hour == pytest.approx(56.25)


def test_three_chambers_need_six_rounds_and_dual_counts_physical_members():
    """A/B 相位和三腔相位最小公倍数为六，双腔成员只改产出计数。"""
    single = analyze_capacity(shared_parallel_model([120] * 3))
    dual = analyze_capacity(shared_parallel_model([120] * 3, physical_members_per_unit=2))
    assert single.period_seconds == pytest.approx(256, abs=1e-6)
    assert single.seconds_per_unit == pytest.approx(128 / 3)
    assert dual.period_seconds == pytest.approx(single.period_seconds)
    assert dual.physical_wafers_per_hour == pytest.approx(single.physical_wafers_per_hour * 2)


def test_coupled_waiting_chain_is_stronger_than_each_resource():
    """两个资源各忙 10 秒，跨资源环总计 30 秒，不能只取 max(10,10)。"""
    edges = (WaitingConstraint("a", "b", 10, 0, "资源一服务"),
             WaitingConstraint("b", "a", 20, 1, "交接返回"))
    model = CapacityModel("耦合反例", ("a", "b"), edges, 1, {"一": 10, "二": 10})
    report = analyze_capacity(model)
    assert report.period_seconds == pytest.approx(30, abs=1e-6)
    assert report.resource_period_lower_bound_seconds == 10
    assert sum(edge.minimum_delay_seconds for edge in report.critical_chain) == 30
    assert sum(edge.cycle_shift for edge in report.critical_chain) == 1
    for edge in edges:
        assert report.event_offsets_seconds[edge.target] + edge.cycle_shift * report.period_seconds >= report.event_offsets_seconds[edge.source] + edge.minimum_delay_seconds - 1e-8


def test_two_period_lag_uses_total_period_count():
    """100 秒等待跨两个周期，对周期下界贡献 50 秒。"""
    model = CapacityModel("跨两期", ("a",), (WaitingConstraint("a", "a", 100, 2),), 1, {})
    assert analyze_capacity(model).period_seconds == pytest.approx(50, abs=1e-6)


def test_same_period_positive_cycle_rejected():
    """互相要求同一期先结束，没有任何放慢周期可解。"""
    model = CapacityModel("不可行", ("a", "b"),
                          (WaitingConstraint("a", "b", 1), WaitingConstraint("b", "a", 1)), 1, {})
    with pytest.raises(ValueError, match="同期正环"):
        analyze_capacity(model)


@pytest.mark.parametrize("invalid", [-1, math.nan, math.inf, True])
def test_invalid_duration_rejected(invalid):
    """未知或非法时长必须拒绝，不能静默算作零。"""
    with pytest.raises(ValueError):
        analyze_capacity(CapacityModel("坏输入", ("a",), (WaitingConstraint("a", "a", invalid, 1),), 1, {}))


def test_zero_period_model_rejected():
    """缺少重复等待环不能被包装成无限产能。"""
    with pytest.raises(ValueError, match="正周期下界"):
        analyze_capacity(CapacityModel("无周期", ("a",), (), 1, {}))
