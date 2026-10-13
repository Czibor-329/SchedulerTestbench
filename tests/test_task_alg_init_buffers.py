"""验证任务级 AlgInit 与标准 update 对 Buffer 模块的保留边界。

Route 通过 ``BufferOption`` 请求共享 Buffer 资源，不能只按 Visit 命名收敛模块；
标准算法要求此时设备拓扑存在显式 ``Type=Buffer`` 站点。本模块用最小双腔设备
覆盖“按类型保留 Buffer”与“零 BufferOption 仍收敛模块”两条相邻契约。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.backend.execution.plan_builder import (
    BuildState,
    build_round_update,
    build_task_alg_init,
    extract_init_data,
)


def _station(
    station_type: str,
    name: str,
    *,
    capacity: int,
    slots: List[int],
    **fields: Any,
) -> Dict[str, Any]:
    """构造带实时字段的最小标准 Station。"""
    return {
        "Type": station_type,
        "Name": name,
        "Capacity": capacity,
        "Slots": list(slots),
        "State": 0,
        "TimeToAvailableOfSlot": {str(slot): 0 for slot in slots},
        **fields,
    }


def _dual_chamber_device() -> Dict[str, Any]:
    """构造含双腔 PM、LoadLock、Cooler 与两个 Buffer 的最小 IToolTopo。"""
    return {
        "Stations": {
            "P1": _station("LoadPort", "P1", capacity=25, slots=list(range(1, 26))),
            "LA": _station(
                "LoadLock", "LA", capacity=2, slots=[1, 2],
                PrePrepareTime=[
                    {"LastItem": "VTR", "CurrentItem": "ATR", "Time": 9, "PrePrepareType": "VentTime"},
                    {"LastItem": "ATR", "CurrentItem": "VTR", "Time": 9, "PrePrepareType": "PumpTime"},
                ],
                LastItem="",
            ),
            "PM1": _station(
                "MultiProcessChamber", "PM1", capacity=2, slots=[1, 2],
            ),
            "PM2": _station(
                "MultiProcessChamber", "PM2", capacity=2, slots=[1, 2],
            ),
            "Cooler": _station("Cooler", "Cooler", capacity=2, slots=[1, 2]),
            "Buffer1": _station("Buffer", "Buffer1", capacity=25, slots=list(range(1, 26))),
            "Buffer2": _station("Buffer", "Buffer2", capacity=25, slots=list(range(1, 26))),
        },
        "Robots": {
            "ATR": {
                "Type": "ATMRobot",
                "Name": "ATR",
                "Capacity": 1,
                "State": 0,
                "TimeToAvailable": 0,
                "PickTime": {"P1": 2.0, "LA": 1.0},
                "PlaceTime": {"P1": 2.0, "LA": 1.0},
                "PrepTransTime": [],
                "ArmInfo": {
                    "ArmA": {
                        "Name": "ArmA",
                        "IsEnable": True,
                        "SlotIDs": [1],
                        "AccessibleStations": ["P1", "LA"],
                        "SlotAtStation": "P1",
                        "UseCount": 0,
                        "SlotsStationMap": {},
                    },
                },
            },
            "VTR": {
                "Type": "VTMRobot",
                "Name": "VTR",
                "Capacity": 2,
                "State": 0,
                "TimeToAvailable": 0,
                "PickTime": {"LA": 1.0, "PM1": 1.5},
                "PlaceTime": {"LA": 1.0, "PM1": 1.5},
                "PrepTransTime": [],
                "ArmInfo": {
                    "ArmA": {
                        "Name": "ArmA",
                        "IsEnable": True,
                        "SlotIDs": [1, 2],
                        "AccessibleStations": ["LA", "PM1"],
                        "SlotAtStation": "LA",
                        "UseCount": 0,
                        "SlotsStationMap": {},
                    },
                },
            },
        },
    }


def _route(buffer_option: int) -> Dict[str, Any]:
    """构造只经过 PM1 的源—加工—汇 Route，并按需要声明 BufferOption。"""
    return {
        "name": "RouteA",
        "group": "RouteA",
        "bufferOption": buffer_option,
        "prePJobCleanRefs": [],
        "postPJobCleanRefs": [],
        "postCJobCleanRefs": [],
        "stages": [
            {"stepId": 0, "postStepIds": [1], "needProcess": False, "visits": [{"stationName": "P1"}]},
            {"stepId": 1, "postStepIds": [2], "needProcess": False, "visits": [{"stationName": "ATR"}]},
            {"stepId": 2, "postStepIds": [3], "needProcess": False, "visits": [{"stationName": "LA"}]},
            {"stepId": 3, "postStepIds": [4], "needProcess": False, "visits": [{"stationName": "VTR"}]},
            {
                "stepId": 4,
                "postStepIds": [5],
                "needProcess": True,
                "visits": [{"stationName": "PM1", "slotIds": "1,2", "processRecipe": "PMRecipe"}],
            },
            {"stepId": 5, "postStepIds": [6], "needProcess": False, "visits": [{"stationName": "VTR"}]},
            {"stepId": 6, "postStepIds": [7], "needProcess": False, "visits": [{"stationName": "LA"}]},
            {"stepId": 7, "postStepIds": [8], "needProcess": False, "visits": [{"stationName": "ATR"}]},
            {"stepId": 8, "postStepIds": [], "needProcess": False, "visits": [{"stationName": "P1"}]},
        ],
    }


def _plan(buffer_option: int) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """构造单轮单 PJob 计划，并返回计划与当前轮配置。"""
    route = _route(buffer_option)
    rounds = [{
        "currentTime": 0,
        "cjobs": [{
            "taskId": "1",
            "jobType": 0,
            "priority": 1,
            "taskMode": 0,
            "loadPort": "P1",
            "pjobs": [{
                "jobName": "P1",
                "routeRef": route["name"],
                "loadPort": "P1",
                "waferCount": 1,
            }],
        }],
    }]
    plan = {
        "device": extract_init_data(_dual_chamber_device()),
        "recipes": [{"name": "PMRecipe", "time": 20, "modules": ["PM1"], "weight": {}}],
        "cleans": [],
        "routes": [route],
        "rounds": rounds,
    }
    plan["device"] = build_task_alg_init(plan["device"], [route], rounds, [])
    return plan, rounds[0]


def test_buffer_option_keeps_typed_buffer_modules_in_task_alg_init() -> None:
    """BufferOption 非零时按类型保留全部 Buffer，未引用的其它模块仍被裁剪。"""
    plan, _round = _plan(2)

    stations = set(plan["device"]["Stations"])
    assert {"Buffer1", "Buffer2"} <= stations
    assert stations == {"P1", "LA", "PM1", "Buffer1", "Buffer2"}
    assert "PM2" not in stations and "Cooler" not in stations


def test_buffer_option_none_still_trims_unreferenced_buffer_modules() -> None:
    """零 BufferOption 不请求 Buffer，任务级 AlgInit 继续保持最小拓扑。"""
    plan, _round = _plan(0)

    assert set(plan["device"]["Stations"]) == {"P1", "LA", "PM1"}
    assert set(plan["device"]["Robots"]) == {"ATR", "VTR"}


def test_buffer_option_update_carries_standard_buffer_snapshot() -> None:
    """发出的标准 update 必须携带 Buffer 的实时字段快照。"""
    plan, round_config = _plan(2)

    update = build_round_update(plan, round_config, 0.0, BuildState())

    assert set(update["Stations"]) == {"P1", "LA", "PM1", "Buffer1", "Buffer2"}
    assert set(update["Stations"]["Buffer1"]) == {
        "Type", "State", "TimeToAvailableOfSlot",
    }
    assert update["Stations"]["Buffer1"]["Type"] == "Buffer"
    assert update["Stations"]["Buffer1"]["TimeToAvailableOfSlot"] == {
        str(slot): 0 for slot in range(1, 26)
    }
