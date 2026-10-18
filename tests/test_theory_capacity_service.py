"""理论产能页面服务的隔离契约测试。

覆盖：路线分类索引、按筛选懒计算、测试数据点换算、无清洁口径、逐路线错误隔离、
参数校验与 HTTP 边界。夹具是 ``tests/support/theory_capacity_fixtures.py`` 的最小合成
设备，时间参数可与手算结果逐项核对；本文件不读取工作区主数据。
"""

from __future__ import annotations

import json
from copy import deepcopy
from http import HTTPStatus
from typing import Any, Mapping
from unittest.mock import Mock

import pytest

from app.backend import theory_capacity_service
from app.backend.api import http
from tests.support import theory_capacity_fixtures as fixtures


@pytest.fixture()
def isolated_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """把服务的数据读取替换为隔离夹具，并清空各级缓存。"""
    fixtures.install(monkeypatch)
    theory_capacity_service._SCAN_CACHE.clear()
    theory_capacity_service._ROUTE_INDEX_CACHE.clear()
    theory_capacity_service._TEST_INDEX_CACHE.clear()


def _route(payload: dict, name: str) -> dict:
    """按路线名取扫描结果。"""
    return next(row for row in payload["routes"] if row["route_name"] == name)


def _point(payload: dict, name: str) -> dict:
    """按测试名取数据点。"""
    return next(row for row in payload["tests"] if row["name"] == name)


def test_route_index_classifies_without_solving(isolated_workspace: None) -> None:
    """路线分类接口只编译并识别形态，给出筛选器需要的数量。"""
    payload = theory_capacity_service.read_theory_capacity_route_index(fixtures.DEVICE_ID)
    index = payload["route_index"]
    assert index["route_total"] == 5
    assert index["route_supported"] == 3
    counts = {row["value"]: row["count"] for row in index["route_kind"]}
    assert counts["single-node"] == 1
    assert counts["parallel"] == 1
    assert counts["reentrant"] == 1
    assert counts["serial"] == 0
    locks = {row["value"]: row["count"] for row in index["lock_mode"]}
    assert locks["shared-bidirectional"] == 3
    assert locks["dedicated-entry-exit"] == 0
    # 分类结果同样逐条列出，不可建图的路线带稳定原因。
    broken = next(row for row in payload["routes"] if row["route_name"] == "PM1(坏入口)")
    assert broken["status"] == "error"
    assert broken["reason"] == "compile-failed"
    unsupported = next(row for row in payload["routes"] if row["route_name"] == "PM1 → PM2/PM3 → PM4")
    assert unsupported["reason"] == "unsupported-route-shape"
    # 分类不产生任何曲线。
    assert "points" not in payload


def test_scan_only_solves_selected_route_kind(isolated_workspace: None) -> None:
    """只对选中的路径类型求解：其它类型不出现在结果里。"""
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="parallel",
    )
    assert [row["route_name"] for row in payload["routes"]] == ["PM1/PM2"]
    assert payload["filters"]["kinds"] == ["parallel"]
    assert payload["routes"][0]["facts"]["route_kind"] == "parallel"
    reentrant = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="reentrant",
    )
    assert [row["route_name"] for row in reentrant["routes"]] == ["PM1 → PM2 → PM1 → PM2"]
    assert reentrant["routes"][0]["facts"]["route_kind"] == "reentrant"
    empty = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="serial",
    )
    assert empty["routes"] == []
    assert empty["tests"] == []
    assert empty["ok"] is True


def test_scan_filters_by_lock_mode(isolated_workspace: None) -> None:
    """LoadLock 类型筛选同样只保留匹配的路线。"""
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, locks="single-bidirectional",
    )
    assert payload["routes"] == []
    shared = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, locks="shared-bidirectional", kinds="single-node",
    )
    assert [row["route_name"] for row in shared["routes"]] == ["PM1"]
    assert shared["routes"][0]["facts"]["lock_mode"] == "shared-bidirectional"


def test_equivalent_routes_share_one_group(isolated_workspace: None) -> None:
    """形态与设备计时相同的路线合并为一个等价分组，测试点跟随分组。"""
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node",
    )
    groups = payload["groups"]
    assert len(groups) == len({
        row["group_key"] for row in payload["routes"] if row["status"] == "supported"
    })
    # 夹具里只有一条单节点路线，因此只有一个分组，键与路线键一致。
    group = groups[0]
    assert group["route_names"] == ["PM1"]
    assert group["representative"] == "PM1"
    assert group["route_kind"] == "single-node"
    assert group["lock_mode"] == "shared-bidirectional"
    assert group["chamber_counts"] == [1]
    assert group["units_per_cycle"] == 2.0
    # 数据点带分组键，页面据此把等价路线的测试挂到同一张卡片。
    point = _point(payload, "合成测试（WAC）")
    assert point["group_key"] == group["group_key"]
    assert group["matched_tests"] == len(payload["tests"])


def test_group_key_ignores_chamber_identity(isolated_workspace: None, monkeypatch) -> None:
    """只有站点名不同的路线必须归入同一组，计时不同的路线必须分开。"""
    overview = fixtures.device_overview()

    def rename_chamber(route: dict, source: str, target: str) -> dict:
        """把一条路线里的加工腔站点改名，用于构造只差腔室名的路线。"""
        renamed = deepcopy(route)
        renamed["name"] = target
        for stage in renamed["stages"]:
            for visit in stage.get("visits") or []:
                if visit.get("stationName") == source:
                    visit["stationName"] = target
        return renamed

    single = overview["routes"][0]
    same_timing = rename_chamber(single, "PM1", "PM2")
    different_timing = rename_chamber(single, "PM1", "PM4")
    # 让 PM4 的取片准备时间与 PM1 不同，模拟真实设备上同类腔室的计时差异。
    overview["device"]["Stations"]["PM4"]["PickPrepareTime"] = {
        fixtures.VACUUM_ROBOT: fixtures.DOOR_SECONDS + 2.5,
    }
    overview["routes"] = [single, same_timing, different_timing]

    def read_device(device_id: str, path: Any = None) -> Mapping[str, Any]:
        return deepcopy(overview)

    monkeypatch.setattr(theory_capacity_service, "get_workspace_device_overview", read_device)
    theory_capacity_service._SCAN_CACHE.clear()
    theory_capacity_service._ROUTE_INDEX_CACHE.clear()
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node",
    )
    by_name = {row["route_name"]: row["group_key"] for row in payload["routes"]}
    assert by_name["PM1"] == by_name["PM2"], "计时相同的腔室应合并为同一分组"
    assert by_name["PM4"] != by_name["PM1"], "计时不同的腔室必须单独成组"
    names = {group["group_key"]: group["route_names"] for group in payload["groups"]}
    assert sorted(names[by_name["PM1"]]) == ["PM1", "PM2"]


def test_group_key_is_independent_of_scan_window(isolated_workspace: None) -> None:
    """分组只由形态与设备计时决定，不受扫描区间与步长影响。"""
    coarse = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", first_seconds=0, last_seconds=60, step_seconds=60,
    )
    fine = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", first_seconds=0, last_seconds=200, step_seconds=5,
    )
    assert (
        [row["group_key"] for row in coarse["routes"]]
        == [row["group_key"] for row in fine["routes"]]
    )


def test_scan_reports_hand_checkable_curve(isolated_workspace: None) -> None:
    """单节点共享双锁的曲线与同锁恢复链手算一致。"""
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", first_seconds=0, last_seconds=120, step_seconds=60,
    )
    row = _route(payload, "PM1")
    assert row["status"] == "supported"
    facts = row["facts"]
    assert facts["rounds_per_cycle"] == 2
    assert facts["units_per_cycle"] == 2.0
    assert facts["physical_members_per_unit"] == 1
    assert facts["process_nodes"] == [{"ordinal": 1, "alias": "PM1", "chambers": ["PM1"]}]
    # 扫描事实不带假设清单，避免每台设备重复传输长文案。
    assert "assumptions" not in facts
    columns = row["points"]
    assert columns["process_seconds"] == [0.0, 60.0, 120.0]
    assert columns["physical_wafers_per_hour"][0] == pytest.approx(
        3600.0 * 2.0 / fixtures.LOCK_RECOVERY_CHAIN_SECONDS, rel=1e-3,
    )
    # 加工时间足够长时由加工腔决定：周期 = 2 × (换片 + 加工)。
    assert columns["period_seconds"][-1] == pytest.approx(
        2 * (fixtures.VACUUM_SWAP_SECONDS + 120.0), abs=2e-3,
    )
    assert columns["limiting_kind"][0] == "waiting-chain"
    assert payload["device"]["id"] == fixtures.DEVICE_ID


def test_test_points_land_on_the_scan_curve(isolated_workspace: None) -> None:
    """单工序测试的数据点必须落在这条路线的扫描曲线上。"""
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", first_seconds=0, last_seconds=100, step_seconds=10,
    )
    curve = _route(payload, "PM1")["points"]
    point = _point(payload, "合成测试（WAC）")
    assert point["status"] == "supported"
    assert point["clean_types"] == ["wacclean"]
    assert point["route_name"] == "PM1"
    assert point["process_seconds"] == [fixtures.PROCESS_SECONDS]
    assert point["key_process_seconds"] == pytest.approx(fixtures.PROCESS_SECONDS)
    # 采样点恰好覆盖 90 秒，因此数据点与曲线在该处一致。
    index = curve["process_seconds"].index(fixtures.PROCESS_SECONDS)
    assert point["physical_wafers_per_hour"] == pytest.approx(
        curve["physical_wafers_per_hour"][index], abs=0.01,
    )
    assert point["period_seconds"] == pytest.approx(
        curve["period_seconds"][index], abs=2e-3,
    )
    assert point["limiting_kind"] == curve["limiting_kind"][index]


def test_clean_filter_selects_tests_only(isolated_workspace: None) -> None:
    """清洁筛选只挑测试；曲线口径仍是无清洁，因此两种筛选的曲线完全相同。"""
    wac = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", cleans="wacclean",
    )
    pre = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", cleans="preclean",
    )
    no_clean = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node", cleans="none",
    )
    assert [row["name"] for row in wac["tests"]] == ["合成测试（WAC）"]
    assert [row["name"] for row in pre["tests"]] == ["合成测试（Pre）"]
    assert no_clean["tests"] == []
    assert (
        wac["routes"][0]["points"]["physical_wafers_per_hour"]
        == pre["routes"][0]["points"]["physical_wafers_per_hour"]
    )
    assert wac["filters"]["cleans"] == ["wacclean"]


def test_scan_points_respect_limit(isolated_workspace: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试点数量超过上限时截断并显式标记。"""
    monkeypatch.setattr(theory_capacity_service, "POINT_LIMIT", 1)
    payload = theory_capacity_service.read_theory_capacity_scan(
        fixtures.DEVICE_ID, kinds="single-node",
    )
    assert len(payload["tests"]) == 1
    assert payload["test_truncated"] is True
    assert payload["test_total"] == 1


def test_scan_result_is_cached_per_device_revision(isolated_workspace: None) -> None:
    """同一设备、筛选与区间直接命中缓存。"""
    window = {"kinds": "single-node", "first_seconds": 0, "last_seconds": 60, "step_seconds": 60}
    first = theory_capacity_service.read_theory_capacity_scan(fixtures.DEVICE_ID, **window)
    second = theory_capacity_service.read_theory_capacity_scan(fixtures.DEVICE_ID, **window)
    assert first is second
    assert len(theory_capacity_service._SCAN_CACHE) == 1
    assert len(theory_capacity_service._ROUTE_INDEX_CACHE) == 1
    assert len(theory_capacity_service._TEST_INDEX_CACHE) == 1


def test_derivation_uses_test_process_seconds_without_cleaning(
    isolated_workspace: None,
) -> None:
    """测试推导使用实际加工时间，并剥离 Clean 引用后建图。"""
    payload = theory_capacity_service.read_theory_capacity_derivation(
        fixtures.DEVICE_ID, test_id=fixtures.TEST_ID,
    )
    assert payload["target"]["kind"] == "test"
    assert payload["target"]["name"] == "合成测试（WAC）"
    assert len(payload["derivations"]) == 1
    entry = payload["derivations"][0]
    assert entry["status"] == "supported"
    assert entry["route_name"] == "PM1"
    assert entry["process_seconds"] == [fixtures.PROCESS_SECONDS]
    expected_period = 2 * (fixtures.VACUUM_SWAP_SECONDS + fixtures.PROCESS_SECONDS)
    assert entry["period_seconds"] == pytest.approx(expected_period, rel=1e-6)
    assert entry["physical_wafers_per_hour"] == pytest.approx(
        3600.0 * 2.0 / expected_period, rel=1e-3,
    )
    assert entry["limiting_kind"] == "resource"
    assert "PM1" in entry["limiting_label"]
    # 完整推导证据：事件、等待边、服务组成、资源负荷与关键链。
    assert entry["events"] and entry["edges"] and entry["services"]
    assert entry["resource_work_seconds"]
    assert entry["critical_chain"]
    assert "tolerance_seconds" not in entry
    edge = next(item for item in entry["edges"] if item["label"].startswith("process-cycle"))
    assert edge["parts"] and edge["detail"]
    service = next(item for item in entry["services"] if item["key"] == edge["label"])
    assert service["seconds"] == pytest.approx(
        sum(part["seconds"] for part in service["parts"]),
    )
    # 服务表不得出现 Clean 相关条目，模型口径固定为无清洁。
    assert all("clean" not in item["key"].casefold() for item in entry["services"])
    assert all("清洁" not in item["detail"] for item in entry["services"])


def test_derivation_supports_route_target(isolated_workspace: None) -> None:
    """路线推导使用模板默认加工时间，供未选择测试时查看计算过程。"""
    payload = theory_capacity_service.read_theory_capacity_derivation(
        fixtures.DEVICE_ID, route_name="PM1",
    )
    assert payload["target"] == {
        "kind": "route", "id": "PM1", "name": "PM1", "group": "合成路线",
    }
    entry = payload["derivations"][0]
    assert entry["process_seconds"] == [theory_capacity_service.DEFAULT_PROCESS_SECONDS]
    assert entry["status"] == "supported"


def test_derivation_reports_missing_route_configuration(isolated_workspace: None) -> None:
    """测试缺少该路线的加工参数时必须明确说明，而不是给出零产能。"""
    theory_capacity_service.get_workspace_test = (
        lambda device_id, test_id, path=None: {**fixtures.test_case(), "routeConfigs": {}}
    )
    payload = theory_capacity_service.read_theory_capacity_derivation(
        fixtures.DEVICE_ID, test_id=fixtures.TEST_ID,
    )
    entry = payload["derivations"][0]
    assert entry["status"] == "unsupported"
    assert entry["reason"] == "missing-route-config"


def test_derivation_requires_target(isolated_workspace: None) -> None:
    """既没有测试也没有路线时明确报错，而不是返回空载荷。"""
    with pytest.raises(ValueError) as context:
        theory_capacity_service.read_theory_capacity_derivation(fixtures.DEVICE_ID)
    assert "必须指定" in str(context.value)
    case = fixtures.test_case()
    case["rounds"] = []
    theory_capacity_service.get_workspace_test = lambda device_id, test_id, path=None: case
    with pytest.raises(ValueError) as context:
        theory_capacity_service.read_theory_capacity_derivation(
            fixtures.DEVICE_ID, test_id=fixtures.TEST_ID,
        )
    assert "路径模板" in str(context.value)


@pytest.mark.parametrize(
    "window, message",
    [
        ({"kinds": "unknown-kind"}, "kinds"),
        ({"locks": "shared"}, "locks"),
        ({"cleans": "wash"}, "cleans"),
        ({"step_seconds": 0}, "扫描步长"),
        ({"step_seconds": "abc"}, "step"),
        ({"first_seconds": 300, "last_seconds": 10}, "扫描终点"),
        ({"step_seconds": 1}, "扫描点数"),
    ],
)
def test_scan_rejects_invalid_arguments(
    isolated_workspace: None, window: dict, message: str,
) -> None:
    """非法筛选取值与区间必须报错，不能静默改变口径。"""
    with pytest.raises(ValueError) as context:
        theory_capacity_service.read_theory_capacity_scan(fixtures.DEVICE_ID, **window)
    assert message in str(context.value)


def test_http_boundaries_serve_index_scan_and_derivation(isolated_workspace: None) -> None:
    """三个接口都必须按查询参数返回 JSON，并对缺失参数返回 400。"""
    handler = object.__new__(http.ConfigEditorHandler)
    handler._send_json = Mock()

    handler.path = f"/api/theory-capacity/route-index?device={fixtures.DEVICE_ID}"
    handler.do_GET()
    payload = handler._send_json.call_args.args[0]
    assert payload["ok"] is True
    assert payload["route_index"]["route_total"] == 5

    handler._send_json.reset_mock()
    handler.path = (
        f"/api/theory-capacity/scan?device={fixtures.DEVICE_ID}"
        "&kinds=parallel&locks=shared-bidirectional&cleans=wacclean&first=0&last=60&step=60"
    )
    handler.do_GET()
    payload = handler._send_json.call_args.args[0]
    assert payload["ok"] is True
    assert payload["filters"] == {
        "kinds": ["parallel"], "locks": ["shared-bidirectional"], "cleans": ["wacclean"],
    }
    assert [row["route_name"] for row in payload["routes"]] == ["PM1/PM2"]

    handler._send_json.reset_mock()
    handler.path = (
        f"/api/theory-capacity/derivation?device={fixtures.DEVICE_ID}&route=PM1"
    )
    handler.do_GET()
    payload = handler._send_json.call_args.args[0]
    assert payload["ok"] is True
    assert payload["target"]["kind"] == "route"

    handler._send_json.reset_mock()
    handler.path = "/api/theory-capacity/scan"
    handler.do_GET()
    sent, status = handler._send_json.call_args.args
    assert sent["ok"] is False
    assert status == HTTPStatus.BAD_REQUEST

    handler._send_json.reset_mock()
    handler.path = f"/api/theory-capacity/derivation?device={fixtures.DEVICE_ID}"
    handler.do_GET()
    sent, status = handler._send_json.call_args.args
    assert sent["ok"] is False
    assert status == HTTPStatus.BAD_REQUEST

    handler._send_json.reset_mock()
    handler.path = f"/api/theory-capacity/scan?device={fixtures.DEVICE_ID}&kinds=wash"
    handler.do_GET()
    sent, status = handler._send_json.call_args.args
    assert sent["ok"] is False
    assert status == HTTPStatus.BAD_REQUEST


def test_http_scan_rejects_unknown_device(isolated_workspace: None) -> None:
    """未知设备返回 400 与业务消息，而不是 500。"""
    handler = object.__new__(http.ConfigEditorHandler)
    handler._send_json = Mock()
    handler.path = "/api/theory-capacity/route-index?device=missing-device"
    handler.do_GET()
    sent, status = handler._send_json.call_args.args
    assert sent["ok"] is False
    assert "设备不存在" in sent["error"]
    assert status == HTTPStatus.BAD_REQUEST


def test_scan_payload_stays_json_serializable(isolated_workspace: None) -> None:
    """载荷必须可直接序列化，且列式曲线的各列等长。"""
    payload = theory_capacity_service.read_theory_capacity_scan(fixtures.DEVICE_ID)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert isinstance(encoded, str)
    for row in payload["routes"]:
        columns = row["points"]
        if not columns:
            continue
        lengths = {len(values) for values in columns.values()}
        assert len(lengths) == 1


def test_missing_algorithm_repository_still_computes_capacity(
    isolated_workspace: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """平台静态编译和建图已独立，缺少算法编译器不能影响理论产能接口。"""
    import sys

    monkeypatch.setitem(sys.modules, "src.compiler", None)
    monkeypatch.setattr(theory_capacity_service, "_CAPACITY_RUNTIME", None)
    payload = theory_capacity_service.read_theory_capacity_scan(fixtures.DEVICE_ID)
    assert payload['ok'] is True
    assert any(row['status'] == 'supported' for row in payload['routes'])
