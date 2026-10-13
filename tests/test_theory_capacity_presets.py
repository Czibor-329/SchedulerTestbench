"""固定 PM 组合的隔离契约测试：不依赖工作空间路线和测试，检查实际计时参与求解。"""
from copy import deepcopy
import pytest
from app.backend import theory_capacity_service as capacity
from app.backend.theory_capacity_presets import PRESETS, build_preset_device, read_theory_capacity_preset
from tests.support import theory_capacity_fixtures as fixtures


@pytest.mark.parametrize("kind", PRESETS)
def test_fixed_combinations_ignore_workspace_routes(monkeypatch, kind):
    """空路线目录依然可计算指定组合，逐节点 PM 必须精确对应预设。"""
    device = fixtures.device_overview()
    device.update(routes=[], tests=[], cleans=[])
    monkeypatch.setattr(capacity, "get_workspace_device_overview", lambda _: deepcopy(device))
    monkeypatch.setattr(capacity, "get_workspace_test", lambda *args: pytest.fail("不得读取测试"))
    payload = read_theory_capacity_preset(fixtures.DEVICE_ID, kind=kind,
        first_seconds=10, last_seconds=30, step_seconds=10, process_seconds=20)
    nodes = payload["derivation"]["facts"]["process_nodes"]
    assert [tuple(node["chambers"]) for node in nodes] == list(PRESETS[kind])
    assert payload["points"]["process_seconds"] == [10, 20, 30]
    assert payload["derivation"]["physical_wafers_per_hour"] == pytest.approx(
        payload["points"]["physical_wafers_per_hour"][1], abs=0.01)
    assert device["routes"] == []


def test_missing_required_chamber_is_not_substituted():
    """缺 PM3 时不使用其它腔冒充用户指定的并行组合。"""
    device = fixtures.device_overview()
    del device["device"]["Stations"]["PM3"]
    with pytest.raises(ValueError, match="PM3"):
        build_preset_device(device, "parallel", "shared-bidirectional")


def test_dual_loadlocks_select_two_logical_channels(monkeypatch):
    """双腔先合并配对锁，避免将 LA/LB 误当作共享双通道。"""
    device = fixtures.device_overview()
    stations = device["device"]["Stations"]
    stations["PM1"]["Type"] = "MultiProcessChamber"
    for name in ("LC", "LD"):
        stations[name] = deepcopy(stations["LA"])
        stations[name]["Name"] = name
    from src.compiler import dual_chamber
    monkeypatch.setattr(dual_chamber, "_merged_ll_groups", lambda *args: {
        "LALB": ["LA", "LB"], "LCLD": ["LC", "LD"]})
    _, route, _ = build_preset_device(device, "single-node", "shared-bidirectional")
    assert [visit["stationName"] for visit in route["stages"][2]["visits"]] == ["LA", "LB", "LC", "LD"]
