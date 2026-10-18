"""设备级 Cycle 理论产能建图器的数学与结构契约。

本文件只验证“把路线事实与设备计时写成固定周期事件图”这一能力：轮转轮数、
聚合服务时长、等待边完整性、资源负荷下界、扫描曲线与拒绝分支。它不重复
``test_theory_capacity_solver.py`` 对通用求解器的覆盖，也不验证生产 Cycle 的动作选择、
Machine 定时或物理校验。

夹具使用 ``tests/support/capacity_problem_builders.py`` 的具名合成设备；时间参数是测试值，
不是真实设备测量值。
"""

from __future__ import annotations

import pytest


from app.backend.theory_capacity.solver import analyze_capacity
from app.backend.theory_capacity.device_graph import (
    SCAN_TOLERANCE_SECONDS,
    CycleCapacityUnsupported,
    analyze_cycle_capacity_model,
    build_cycle_capacity_model,
    describe_route_facts,
    physical_members_per_unit,
    scan_cycle_capacity,
)
from app.backend.theory_capacity.topology import analyze_capacity_topology
from tests.support.capacity_problem_builders import (
    ATMOSPHERE_ROBOT,
    DEFAULT_PROCESS_SECONDS,
    DOOR_SECONDS,
    LOAD_LOCK_A,
    LOAD_LOCK_B,
    LOAD_PORT,
    PUMP_SECONDS,
    ROBOT_MOVE_SECONDS,
    ROBOT_PICK_SECONDS,
    ROBOT_PLACE_SECONDS,
    VACUUM_ROBOT,
    VENT_SECONDS,
    cycle_problem,
    dedicated_cycle_problem,
    mixed_problem,
    parallel_problem,
    serial_problem,
    single_node_problem,
)

# 合成设备的聚合服务时长：取放 = 开门 + 机器人动作 + 关门。
SWAP_SECONDS = 2 * DOOR_SECONDS + ROBOT_PICK_SECONDS + ROBOT_MOVE_SECONDS + ROBOT_PLACE_SECONDS
# 重入旧聚合图仍按独立取放定义锁交换，本轮不迁移该模型。
REENTRANT_AGGREGATE_SWAP_SECONDS = 2 * (DOOR_SECONDS + ROBOT_PICK_SECONDS + DOOR_SECONDS)
PICK_SECONDS = DOOR_SECONDS + ROBOT_PICK_SECONDS + DOOR_SECONDS
PLACE_SECONDS = DOOR_SECONDS + ROBOT_PLACE_SECONDS + DOOR_SECONDS


def _topology(problem):
    """返回识别结果，并断言该夹具确实受支持。"""
    topology = analyze_capacity_topology(problem)
    assert getattr(topology, "supported", False), getattr(topology, "detail", topology)
    return topology


def _analysis(problem, **options):
    """按夹具默认物理成员数求解理论产能。"""
    return analyze_cycle_capacity_model(problem, _topology(problem), **options)


def test_shared_parallel_rounds_follow_least_common_multiple() -> None:
    """共享双锁的完整周期是 2 与各工序候选腔数的最小公倍数。"""
    expectations = (
        (single_node_problem(4, process_seconds=[120.0]), 2),
        (
            cycle_problem(
                process_candidates=[["PM1", "PM2"]],
                wafer_count=4,
                process_seconds=[120.0],
            ),
            2,
        ),
        (parallel_problem(4, process_seconds=[120.0]), 6),
        (serial_problem(4, process_seconds=[120.0, 90.0]), 2),
        (mixed_problem(4, process_seconds=[120.0, 90.0]), 2),
    )
    for problem, rounds in expectations:
        analysis = _analysis(problem)
        assert analysis.facts.rounds_per_cycle == rounds
        assert analysis.facts.lock_mode == "shared-bidirectional"
        assert analysis.facts.units_per_cycle == float(rounds)


def test_aggregate_service_seconds_match_device_timing_parts() -> None:
    """换片、取放与抽充气时长必须等于设备字段的确定性组合。"""
    problem = single_node_problem(4, process_seconds=[120.0])
    built = build_cycle_capacity_model(problem, _topology(problem))
    services = built.service_index()

    swap = services[f"vacuum-swap:{LOAD_LOCK_A}"]
    assert swap.seconds == pytest.approx(SWAP_SECONDS)
    assert sum(part.seconds for part in swap.parts) == pytest.approx(swap.seconds)
    assert [part.label for part in swap.parts][0].endswith("开门准备")

    process_swap = services["process-swap:PM1"]
    assert process_swap.seconds == pytest.approx(SWAP_SECONDS)

    assert services[f"pressure:{LOAD_LOCK_A}:pump"].seconds == pytest.approx(PUMP_SECONDS)
    assert services[f"pressure:{LOAD_LOCK_A}:vent"].seconds == pytest.approx(VENT_SECONDS)
    assert services["atm-receive:LA"].seconds == pytest.approx(PICK_SECONDS)
    assert services["atm-feed:LA"].seconds == pytest.approx(PLACE_SECONDS)
    move = services["vacuum:move:LA->PM1"]
    assert move.seconds == pytest.approx(ROBOT_MOVE_SECONDS)


def test_event_graph_edges_reference_known_events_and_services() -> None:
    """每条等待边都必须指向已声明事件与已登记服务，跨期数为非负整数。"""
    fixtures = (
        single_node_problem(4, process_seconds=[120.0]),
        parallel_problem(6, process_seconds=[120.0]),
        serial_problem(4, process_seconds=[120.0, 90.0]),
        mixed_problem(4, process_seconds=[120.0, 90.0]),
        cycle_problem(
            process_candidates=[["PM2"], ["PM3"], ["PM2"], ["PM3"]],
            wafer_count=4,
            process_seconds=[120.0, 90.0, 60.0, 30.0],
        ),
        dedicated_cycle_problem(4, process_seconds=[120.0]),
    )
    for problem in fixtures:
        built = build_cycle_capacity_model(problem, _topology(problem))
        events = set(built.model.events)
        assert len(events) == len(built.model.events)
        known = built.service_index()
        for edge in built.model.constraints:
            assert edge.source in events, edge
            assert edge.target in events, edge
            assert edge.label in known, edge
            assert edge.minimum_delay_seconds >= 0.0
            assert isinstance(edge.cycle_shift, int) and edge.cycle_shift >= 0
        # 真空机器人、大气机器人与两把锁都必须出现在资源负荷里。
        assert any(name.startswith("真空机器人") for name in built.model.resource_work_seconds)
        assert any(name.startswith("大气机器人") for name in built.model.resource_work_seconds)
        assert f"LoadLock {LOAD_LOCK_A}" in built.model.resource_work_seconds
        assert f"LoadLock {LOAD_LOCK_B}" in built.model.resource_work_seconds
        # 资源负荷只含独占工作，不得把晶圆在腔内的加工时间算进机器人。
        vacuum_work = next(
            seconds for name, seconds in built.model.resource_work_seconds.items()
            if name.startswith("真空机器人")
        )
        assert vacuum_work > 0.0


def test_single_node_capacity_is_limited_by_chamber_reprocessing() -> None:
    """单节点路线的片间隔下界是同腔加工加换片，产能等于 3600/(p+h)。"""
    process_seconds = 120.0
    analysis = _analysis(single_node_problem(4, process_seconds=[process_seconds]))
    per_wafer = SWAP_SECONDS + process_seconds
    assert analysis.facts.rounds_per_cycle == 2
    assert analysis.report.period_seconds == pytest.approx(2 * per_wafer, rel=1e-6)
    assert analysis.report.seconds_per_unit == pytest.approx(per_wafer, rel=1e-6)
    assert analysis.report.physical_wafers_per_hour == pytest.approx(
        3600.0 / per_wafer, rel=1e-6,
    )
    assert analysis.limiting_kind == "resource"
    assert "PM1" in analysis.limiting_label


@pytest.mark.parametrize("chamber_count", [1, 2, 3])
def test_parallel_capacity_scales_with_chamber_count(chamber_count: int) -> None:
    """并行路线在锁与机器人不成为瓶颈时，产能与候选腔数成正比。"""
    problem = cycle_problem(
        process_candidates=[[f"PM{index + 1}" for index in range(chamber_count)]],
        wafer_count=6,
        process_seconds=[120.0],
    )
    topology = _topology(problem)
    analysis = analyze_cycle_capacity_model(problem, topology)
    expected_kind = "single-node" if chamber_count == 1 else "parallel"
    assert analysis.facts.route_kind == expected_kind
    assert analysis.facts.chamber_counts == (chamber_count,)
    expected = chamber_count * 3600.0 / (SWAP_SECONDS + 120.0)
    assert analysis.report.physical_wafers_per_hour == pytest.approx(expected, rel=1e-3)


def test_fixed_rotation_uses_slowest_chamber_not_sum_of_rates() -> None:
    """固定轮转周期由最慢腔决定，而不是各腔服务率之和。"""
    problem = cycle_problem(
        process_candidates=[["PM1", "PM2"]],
        wafer_count=6,
        process_seconds=[60.0],
    )
    for wafer in problem.wafers:
        for stage in wafer.stages:
            if stage.stage_type == "process":
                stage.process_time_by_chamber = {"PM1": 60.0, "PM2": 240.0}
    analysis = analyze_cycle_capacity_model(problem, _topology(problem))
    slowest = SWAP_SECONDS + 240.0
    fastest = SWAP_SECONDS + 60.0
    assert analysis.report.period_seconds == pytest.approx(slowest, rel=1e-6)
    optimistic_sum = 3600.0 / fastest + 3600.0 / slowest
    assert analysis.report.physical_wafers_per_hour < optimistic_sum
    assert analysis.report.physical_wafers_per_hour == pytest.approx(
        2 * 3600.0 / slowest, rel=1e-6,
    )


def test_serial_capacity_uses_slowest_stage_interval() -> None:
    """纯串行固定轮转的片间隔是各道 (p_j + h_j) 的最大值。"""
    problem = serial_problem(4, process_seconds=[120.0, 90.0])
    analysis = analyze_cycle_capacity_model(problem, _topology(problem))
    slowest = SWAP_SECONDS + 120.0
    assert analysis.facts.route_kind == "serial"
    assert analysis.report.seconds_per_unit == pytest.approx(slowest, rel=1e-6)
    assert analysis.report.period_seconds == pytest.approx(2 * slowest, rel=1e-6)
    assert analysis.report.physical_wafers_per_hour == pytest.approx(
        3600.0 / slowest, rel=1e-6,
    )


def test_dual_chamber_members_scale_physical_output() -> None:
    """双腔逻辑对片的物理产出是逻辑单元数的两倍，片间隔按物理片折算。"""
    problem = single_node_problem(4, process_seconds=[120.0])
    topology = _topology(problem)
    single = analyze_cycle_capacity_model(problem, topology, members_per_unit=1)
    dual = analyze_cycle_capacity_model(problem, topology, members_per_unit=2)
    assert dual.facts.units_per_cycle == 2 * single.facts.units_per_cycle
    assert dual.report.period_seconds == pytest.approx(single.report.period_seconds, rel=1e-6)
    assert dual.report.physical_wafers_per_hour == pytest.approx(
        2 * single.report.physical_wafers_per_hour, rel=1e-6,
    )
    assert physical_members_per_unit({"PM9": {"Type": "MultiProcessChamber"}}) == 2
    assert physical_members_per_unit({"PM9": {"Type": "ProcessChamber"}}) == 1


def test_critical_chain_ratio_equals_solved_period() -> None:
    """关键闭合等待链的“总等待 ÷ 总跨期”必须等于求解出的周期。"""
    problem = parallel_problem(6, process_seconds=[120.0, 120.0])
    analysis = analyze_cycle_capacity_model(problem, _topology(problem))
    chain = analysis.report.critical_chain
    assert chain
    total_delay = sum(edge.minimum_delay_seconds for edge in chain)
    total_shift = sum(edge.cycle_shift for edge in chain)
    assert total_shift > 0
    assert total_delay / total_shift == pytest.approx(analysis.report.period_seconds, rel=1e-3)


def test_dedicated_entry_exit_uses_two_distinct_locks() -> None:
    """固定单进单出路线使用入口与出口两把不同锁，并各自承担恢复环。"""
    problem = dedicated_cycle_problem(4, process_seconds=[120.0])
    analysis = analyze_cycle_capacity_model(problem, _topology(problem))
    assert analysis.facts.lock_mode == "dedicated-entry-exit"
    assert analysis.facts.pressure_domains == (LOAD_LOCK_A, LOAD_LOCK_B)
    services = analysis.model.service_index()
    assert f"vacuum-pick:{LOAD_LOCK_A}" in services
    assert f"vacuum-place:{LOAD_LOCK_B}" in services
    assert f"vacuum-swap:{LOAD_LOCK_A}" not in services
    loads = analysis.model.model.resource_work_seconds
    assert f"LoadLock {LOAD_LOCK_A}" in loads and f"LoadLock {LOAD_LOCK_B}" in loads
    # 单槽、单节点分向锁：片间隔由入口锁恢复或加工腔决定，两者都大于锁内单片服务。
    assert analysis.report.period_seconds > SWAP_SECONDS


def test_reentrant_route_runs_full_chain_per_wafer() -> None:
    """独立重入模板按片走完整加工链，片间隔等于链条各段之和。"""
    process_seconds = (120.0, 90.0, 60.0, 30.0)
    problem = cycle_problem(
        process_candidates=[["PM2"], ["PM3"], ["PM2"], ["PM3"]],
        wafer_count=4,
        process_seconds=list(process_seconds),
    )
    analysis = analyze_cycle_capacity_model(problem, _topology(problem))
    assert analysis.facts.route_kind == "reentrant"
    hold_total = sum(PLACE_SECONDS + seconds for seconds in process_seconds)
    hop_total = len(process_seconds) * (PICK_SECONDS + ROBOT_MOVE_SECONDS)
    start_total = REENTRANT_AGGREGATE_SWAP_SECONDS + ROBOT_MOVE_SECONDS
    per_wafer = hold_total + hop_total + start_total
    rounds = analysis.facts.rounds_per_cycle
    assert rounds == 2
    assert analysis.report.seconds_per_unit == pytest.approx(per_wafer, rel=1e-6)
    assert analysis.report.period_seconds == pytest.approx(rounds * per_wafer, rel=1e-6)
    # 重入路线的真空侧只有一片在制；资源负荷只含放片、取片与转位，不含加工。
    vacuum_work = next(
        seconds for name, seconds in analysis.model.model.resource_work_seconds.items()
        if name.startswith("真空机器人")
    )
    expected_vacuum = rounds * (
        start_total
        + len(process_seconds) * (PLACE_SECONDS + PICK_SECONDS)
        + len(process_seconds) * ROBOT_MOVE_SECONDS
    )
    assert vacuum_work == pytest.approx(expected_vacuum, rel=1e-6)
    assert vacuum_work < sum(process_seconds)


def test_resource_load_lower_bound_appears_in_analysis() -> None:
    """资源负荷下界必须等于最大单项工作量，并作为产能上限的分子。"""
    problem = parallel_problem(4, process_seconds=[120.0, 120.0])
    analysis = analyze_cycle_capacity_model(problem, _topology(problem))
    loads = analysis.model.model.resource_work_seconds
    peak = max(loads.values())
    assert analysis.report.resource_period_lower_bound_seconds == pytest.approx(peak)
    if peak >= analysis.report.period_seconds:
        assert analysis.report.period_seconds == pytest.approx(peak, rel=1e-6)
    units = analysis.facts.units_per_cycle
    assert analysis.report.resource_upper_bound_wafers_per_hour == pytest.approx(
        3600.0 * units / peak, rel=1e-6,
    )


def test_scan_is_monotonic_and_uses_requested_window() -> None:
    """扫描按统一加工时间给出非增产能曲线，点数与区间一致。"""
    problem = single_node_problem(4, process_seconds=[120.0])
    scan = scan_cycle_capacity(
        problem,
        _topology(problem),
        first_seconds=0.0,
        last_seconds=240.0,
        step_seconds=40.0,
    )
    assert [point.process_seconds for point in scan.points] == [0.0, 40.0, 80.0, 120.0, 160.0, 200.0, 240.0]
    capacities = [point.physical_wafers_per_hour for point in scan.points]
    assert all(
        later <= earlier + 1e-6 for earlier, later in zip(capacities, capacities[1:])
    )
    # 加工时间足够长时，加工腔成为唯一限制，曲线收敛到 3600/(p+h)。
    assert capacities[-1] == pytest.approx(3600.0 / (SWAP_SECONDS + 240.0), rel=1e-3)
    assert scan.points[0].limiting_kind in {"resource", "waiting-chain"}


def test_scan_default_tolerance_is_looser_than_solver_default() -> None:
    """扫描使用毫秒级误差，不影响推导所用的严格误差。"""
    assert SCAN_TOLERANCE_SECONDS == pytest.approx(1e-3)
    process_seconds = 120.0
    problem = single_node_problem(4, process_seconds=[process_seconds])
    scan = scan_cycle_capacity(
        problem,
        _topology(problem),
        first_seconds=process_seconds,
        last_seconds=process_seconds,
        step_seconds=10.0,
    )
    strict = analyze_average_period(problem)
    assert len(scan.points) == 1
    assert scan.points[0].period_seconds == pytest.approx(strict, abs=1e-2)


def analyze_average_period(problem) -> float:
    """用求解器默认误差重算一次周期，作为扫描精度的参照。"""
    built = build_cycle_capacity_model(problem, _topology(problem))
    return analyze_capacity(built.model).period_seconds


@pytest.mark.parametrize(
    "window, message",
    [
        ((100.0, 10.0, 10.0), "扫描终点不能小于起点"),
        ((10.0, 100.0, 0.0), "扫描步长必须为正数"),
    ],
)
def test_scan_rejects_invalid_windows(window, message) -> None:
    """非法扫描区间必须报错，不能静默返回空曲线。"""
    problem = single_node_problem(4, process_seconds=[120.0])
    with pytest.raises(ValueError) as context:
        scan_cycle_capacity(
            problem,
            _topology(problem),
            first_seconds=window[0],
            last_seconds=window[1],
            step_seconds=window[2],
        )
    assert message in str(context.value)


def test_unsupported_topology_raises_stable_reason_before_building() -> None:
    """路线不受支持时，扫描与推导都必须先报稳定错误码而不是属性错误。"""
    problem = cycle_problem(
        process_candidates=[["PM1"], ["PM2"], ["PM3", "PM4"]],
        wafer_count=4,
        process_seconds=[120.0, 90.0, 60.0],
    )
    topology = analyze_capacity_topology(problem)
    assert not topology.supported
    assert topology.reason == "unsupported-route-shape"
    for call in (
        lambda: analyze_cycle_capacity_model(problem, topology),
        lambda: scan_cycle_capacity(
            problem, topology, first_seconds=0.0, last_seconds=60.0, step_seconds=30.0,
        ),
        lambda: describe_route_facts(problem, topology),
        lambda: build_cycle_capacity_model(problem, topology),
    ):
        with pytest.raises(CycleCapacityUnsupported) as context:
            call()
        assert context.value.reason == topology.reason


def test_residency_and_cleaning_inputs_are_rejected_and_not_silently_relaxed() -> None:
    """驻留上限与清洁义务不在本建图器口径内，必须由拓扑识别明确拒绝。"""
    problem = serial_problem(4, process_seconds=[120.0, 90.0])
    for stage in problem.wafers[0].stages:
        if stage.stage_type == "process":
            stage.residency = 30.0
            break
    topology = analyze_capacity_topology(problem)
    assert not topology.supported
    assert topology.reason == "residency-not-supported"
    with pytest.raises(CycleCapacityUnsupported) as context:
        build_cycle_capacity_model(problem, topology)
    assert context.value.reason == "residency-not-supported"


def test_loadlock_batch_and_member_counts_are_validated() -> None:
    """两片压力批与非法物理成员数必须明确拒绝，不得近似。"""
    problem = single_node_problem(4, process_seconds=[120.0])
    topology = _topology(problem)
    with pytest.raises(CycleCapacityUnsupported) as batch_context:
        describe_route_facts(problem, topology, loadlock_batch_size=2)
    assert batch_context.value.reason == "loadlock-batch-not-modeled"
    with pytest.raises(CycleCapacityUnsupported) as member_context:
        describe_route_facts(problem, topology, members_per_unit=3)
    assert member_context.value.reason == "invalid-physical-members"


def test_route_facts_expose_diagnosable_fields() -> None:
    """路线事实必须能直接支撑页面展示：工序、候选腔、机器人与假设清单。"""
    problem = mixed_problem(4, process_seconds=[120.0, 90.0])
    facts = describe_route_facts(problem, _topology(problem))
    payload = facts.to_dict()
    assert payload["route_kind"] == "mixed"
    assert payload["chamber_counts"] == [1, 2]
    assert payload["vacuum_robot"] == VACUUM_ROBOT
    assert payload["atmosphere_robot"] == ATMOSPHERE_ROBOT
    assert payload["source_station"] == LOAD_PORT
    assert payload["sink_station"] == LOAD_PORT
    assert len(payload["process_nodes"]) == 2
    assert payload["process_nodes"][1]["chambers"]
    assert payload["assumptions"]
    assert payload["units_per_cycle"] == float(facts.rounds_per_cycle)


def test_analysis_payload_contains_full_derivation_evidence() -> None:
    """推导载荷必须同时给出事件、等待边、服务组成、资源负荷与关键链。"""
    problem = serial_problem(4, process_seconds=[DEFAULT_PROCESS_SECONDS, 90.0])
    analysis = _analysis(problem)
    payload = analysis.to_dict()
    assert payload["events"] and payload["edges"] and payload["services"]
    assert payload["resource_work_seconds"]
    assert payload["critical_chain"]
    assert payload["period_seconds"] > 0
    edge = payload["edges"][0]
    assert edge["label"] and edge["detail"]
    assert isinstance(edge["cycle_shift"], int)
    service = next(item for item in payload["services"] if item["key"] == edge["label"])
    assert service["parts"]
    assert sum(part["seconds"] for part in service["parts"]) == pytest.approx(
        service["seconds"],
    )
    assert payload["facts"]["process_nodes"][0]["alias"] == "PM1"
