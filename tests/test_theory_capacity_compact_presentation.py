"""精简核页面契约：九组合数值对照、可行相位证据及生产入口独立性。"""
from copy import deepcopy
from dataclasses import replace
import pytest

from app.backend.theory_capacity.compact_presentation import calculate_preset_payload
from app.backend.theory_capacity.device_graph import analyze_cycle_capacity_model, scan_cycle_capacity
from app.backend.theory_capacity.topology import analyze_capacity_topology
from tests.support.compact_period_cases import COMBINATIONS, period_problem, randomize_asymmetric_timing
from tests.support import theory_capacity_fixtures as fixtures


@pytest.mark.parametrize("mode,route", COMBINATIONS)
@pytest.mark.parametrize("members", [1, 2])
def test_presentation_matches_legacy_and_all_edges_are_feasible(mode, route, members):
    """非对称逐腔计时及统一时间扫描均对齐旧核；逐边核对可行相位与余量。"""
    problem = period_problem(mode, route)
    randomize_asymmetric_timing(problem, 31)
    original = deepcopy(problem)
    topology = analyze_capacity_topology(problem)
    points, result = calculate_preset_payload(problem, topology, members, (0, 120, 60))
    reference = analyze_cycle_capacity_model(problem, topology, members_per_unit=members)
    scan = scan_cycle_capacity(problem, topology, first_seconds=0, last_seconds=120,
                               step_seconds=60, members_per_unit=members)
    assert result["period_seconds"] == pytest.approx(reference.report.period_seconds, abs=1e-7)
    for index, point in enumerate(scan.points):
        assert points["period_seconds"][index] == pytest.approx(point.period_seconds, abs=0.002)
        assert points["physical_wafers_per_hour"][index] == pytest.approx(point.physical_wafers_per_hour, abs=0.011)
        assert points["limiting_kind"][index] == point.limiting_kind
    diagram = result["cycle_diagram"]
    events = {event["id"]: event for event in diagram["events"]}
    atmosphere = [event for event in diagram["events"] if event["lane"] == "atmosphere"]
    if mode == "dedicated-entry-exit":
        assert atmosphere[0]["station"] == "LA"
        assert atmosphere[0]["label"].endswith("放片")  # 分向锁 ATM 先补入口，再回收出口。
    for index, edge in enumerate(edge for edge in diagram["edges"] if edge["category"] == "atmosphere"):
        assert edge["source"] == atmosphere[index]["id"]
        assert edge["target"] == atmosphere[(index + 1) % len(atmosphere)]["id"]
    assert set(range(1, result["facts"]["rounds_per_cycle"] + 1)) == {event["round"] for event in events.values()}
    for edge in diagram["edges"]:
        elapsed = events[edge["target"]]["phase_seconds"] - events[edge["source"]]["phase_seconds"] + edge["cycle_shift"] * result["period_seconds"]
        assert elapsed >= edge["minimum_delay_seconds"] - 1e-9
        assert edge["slack_seconds"] == pytest.approx(elapsed - edge["minimum_delay_seconds"], abs=1e-9)
        assert sum(part["seconds"] for part in edge["parts"]) == pytest.approx(edge["minimum_delay_seconds"])
        assert len(edge["parts"]) >= 2
        assert edge["detail"] != f'{events[edge["source"]]["label"]} → {events[edge["target"]]["label"]}'
        if edge["category"] == "handoff":
            assert any("扣除" in part["label"] and part["seconds"] <= 0 for part in edge["parts"])
    assert {edge["category"] for edge in diagram["edges"]} >= {"vacuum", "atmosphere", "lock", "process", "port"}
    assert problem == original


@pytest.mark.parametrize("mode,route", COMBINATIONS)
def test_page_entry_never_calls_legacy_calculators(monkeypatch, mode, route):
    """正式页面扫描和单点均须走精简核；禁用旧求解/建图后仍可返回全图。"""
    from app.backend import theory_capacity_service as capacity
    from app.backend.theory_capacity import device_graph, solver, timing_graph
    from app.backend.theory_capacity_presets import read_theory_capacity_preset
    runtime = capacity._capacity_runtime()  # 先建立真实缓存，再逐属性替换，避免缓存测试替身。
    def forbidden(*args, **kwargs):
        """旧计算入口一旦被调用即使测试失败。"""
        pytest.fail("固定页面不得调用旧计算器")
    monkeypatch.setattr(capacity, "_CAPACITY_RUNTIME", replace(runtime,
                        analyze_cycle_capacity_model=forbidden, scan_cycle_capacity=forbidden,
                        build_cycle_capacity_model=forbidden))
    for module, names in ((device_graph, ("analyze_cycle_capacity_model", "scan_cycle_capacity", "build_cycle_capacity_model")),
                          (solver, ("analyze_capacity",)), (timing_graph, ("build_timing_graph",))):
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(capacity, "get_workspace_device_overview", lambda _: fixtures.device_overview())
    payload = read_theory_capacity_preset(fixtures.DEVICE_ID, kind="single-node" if route == "single" else route,
                                        lock=mode, first_seconds=0, last_seconds=120, step_seconds=60, process_seconds=120)
    assert payload["derivation"]["model_name"] == "compact-period"
    assert payload["derivation"]["cycle_diagram"]["edges"]
    assert payload["points"]["physical_wafers_per_hour"][-1] == pytest.approx(
        payload["derivation"]["physical_wafers_per_hour"], abs=0.011)
