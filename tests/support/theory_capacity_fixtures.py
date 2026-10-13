"""理论产能页面服务的隔离夹具：一台最小合成设备、它的路径与一个测试。

本模块只提供纯数据 builder 和只读的依赖替换，不读取或修改 ``app/data/datasets/``。
时间参数是为测试设计的合成值，可与手算结果逐项核对：

- VAC 一次换片 = 0.5 + 2.5 + 0.5 的取 + 放 = 7 秒；ATM 一次取放 = 3 秒；
- LoadLock 抽气与充气各 20 秒，机器人转位 3 秒；
- 因此单节点共享双锁的同锁恢复链 = 3 + 3 + 3 + 23 + 27 = 68 秒，
  两轮一周期时理论产能上限为 3600 × 2 / 68 片/小时。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence

LOAD_PORT = "LP1"
LOAD_LOCK_A = "LA"
LOAD_LOCK_B = "LB"
PROCESS_CHAMBERS = ("PM1", "PM2", "PM3", "PM4")
ATMOSPHERE_ROBOT = "ATR"
VACUUM_ROBOT = "VTR"

ATMOSPHERE_ROBOT_SECONDS = 2.0
VACUUM_ROBOT_SECONDS = 2.5
DOOR_SECONDS = 0.5
MOVE_SECONDS = 3.0
PUMP_SECONDS = 20.0
VENT_SECONDS = 20.0

# 手算参照：一次真空换片、一次大气取放、同锁恢复链、重入模板的逐段链。
VACUUM_PICK_SECONDS = DOOR_SECONDS + VACUUM_ROBOT_SECONDS + DOOR_SECONDS
VACUUM_SWAP_SECONDS = 2 * VACUUM_PICK_SECONDS
ATMOSPHERE_PICK_SECONDS = DOOR_SECONDS + ATMOSPHERE_ROBOT_SECONDS + DOOR_SECONDS
LOCK_RECOVERY_CHAIN_SECONDS = (
    2 * (ATMOSPHERE_PICK_SECONDS + MOVE_SECONDS)
    + (ATMOSPHERE_PICK_SECONDS + MOVE_SECONDS)
    + (ATMOSPHERE_PICK_SECONDS + PUMP_SECONDS)
    + (VACUUM_SWAP_SECONDS + VENT_SECONDS)
)

DEVICE_ID = "synthetic-theory-device"
TEST_ID = "synthetic-theory-test"
TEST_ID_PRECLEAN = "synthetic-theory-preclean-test"
PROCESS_SECONDS = 90.0
PRECLEAN_SECONDS = 21.0
WAC_CLEAN_NAME = "WAC Clean · 11s"
PRECLEAN_NAME = "PreClean · 21s"


def _robot_timing(robots: tuple[str, ...], seconds: float) -> Dict[str, float]:
    """按机器人集合生成同一取放时长。"""
    return {robot: seconds for robot in robots}


def _station(
    name: str,
    station_type: str,
    capacity: int,
    slots: tuple[int, ...],
    robots: tuple[str, ...],
    *,
    loadlock: bool = False,
) -> Dict[str, Any]:
    """构造一个站点；LoadLock 额外带抽气与充气方向。"""
    data: Dict[str, Any] = {
        "Type": station_type,
        "Name": name,
        "Capacity": capacity,
        "Slots": list(slots),
        "PickPrepareTime": _robot_timing(robots, DOOR_SECONDS),
        "PickCompleteTime": _robot_timing(robots, DOOR_SECONDS),
        "PlacePrepareTime": _robot_timing(robots, DOOR_SECONDS),
        "PlaceCompleteTime": _robot_timing(robots, DOOR_SECONDS),
        "PostCompleteTime": {},
        "State": 0,
        "TimeToAvailableOfSlot": {str(slot): 0 for slot in slots},
    }
    if loadlock:
        data["PrePrepareTime"] = [
            {
                "LastItem": ATMOSPHERE_ROBOT,
                "CurrentItem": VACUUM_ROBOT,
                "Time": PUMP_SECONDS,
                "PrePrepareType": "PumpTime",
            },
            {
                "LastItem": VACUUM_ROBOT,
                "CurrentItem": ATMOSPHERE_ROBOT,
                "Time": VENT_SECONDS,
                "PrePrepareType": "VentTime",
            },
        ]
    return data


def _robot(
    name: str,
    robot_type: str,
    capacity: int,
    scope: tuple[str, ...],
    arm_slots: tuple[int, ...],
    pick_seconds: float,
) -> Dict[str, Any]:
    """构造双臂/单臂机器手；转位表覆盖带片与空手两种类型。"""
    return {
        "Type": robot_type,
        "Name": name,
        "Capacity": capacity,
        "PickTime": {station: pick_seconds for station in scope},
        "PlaceTime": {station: pick_seconds for station in scope},
        "PrepTransTime": [
            {
                "SrcStation": source,
                "DestStation": destination,
                "TransType": transfer_type,
                "Time": MOVE_SECONDS,
            }
            for source in scope
            for destination in scope
            for transfer_type in (0, 1)
        ],
        "ArmPointerPair": [],
        "CanMultiTrans": robot_type == "VTMRobot",
        "ArmInfo": {
            f"Arm{index + 1}": {
                "Name": f"Arm{index + 1}",
                "IsEnable": True,
                "SlotIDs": [slot],
                "AccessibleStations": list(scope),
                "SlotAtStation": scope[0],
                "UseCount": 0,
                "SlotsStationMap": {},
            }
            for index, slot in enumerate(arm_slots)
        },
        "State": 0,
        "TimeToAvailable": 0.0,
    }


def cleaning_device_overview() -> Dict[str, Any]:
    """双 LoadPort、六片 Dummy 库存的单腔设备，用于真实清洁与跨 Job 交接测试。"""
    device = device_overview()
    stations = device["device"]["Stations"]
    stations["LP2"] = _station("LP2", "LoadPort", 25, tuple(range(1, 26)), (ATMOSPHERE_ROBOT,))
    stations["DummyPort"] = _station("DummyPort", "DummyPort", 6, tuple(range(1, 7)), (ATMOSPHERE_ROBOT,))
    device["device"]["Robots"][ATMOSPHERE_ROBOT] = _robot(
        ATMOSPHERE_ROBOT, "ATMRobot", 1, ("LP1", "LP2", "DummyPort", "LA", "LB"), (1,),
        ATMOSPHERE_ROBOT_SECONDS)
    return device


def device_init() -> Dict[str, Any]:
    """构造最小单片设备 init：一个 LoadPort、两把锁、两个加工腔、两台机器手。"""
    return {
        "Stations": {
            LOAD_PORT: _station(
                LOAD_PORT, "LoadPort", 25, tuple(range(1, 26)), (ATMOSPHERE_ROBOT,),
            ),
            LOAD_LOCK_A: _station(
                LOAD_LOCK_A, "LoadLock", 2, (1, 2),
                (ATMOSPHERE_ROBOT, VACUUM_ROBOT), loadlock=True,
            ),
            LOAD_LOCK_B: _station(
                LOAD_LOCK_B, "LoadLock", 2, (1, 2),
                (ATMOSPHERE_ROBOT, VACUUM_ROBOT), loadlock=True,
            ),
            "PM1": _station("PM1", "ProcessChamber", 1, (1,), (VACUUM_ROBOT,)),
            "PM2": _station("PM2", "ProcessChamber", 1, (1,), (VACUUM_ROBOT,)),
            "PM3": _station("PM3", "ProcessChamber", 1, (1,), (VACUUM_ROBOT,)),
            "PM4": _station("PM4", "ProcessChamber", 1, (1,), (VACUUM_ROBOT,)),
        },
        "Robots": {
            ATMOSPHERE_ROBOT: _robot(
                ATMOSPHERE_ROBOT, "ATMRobot", 1,
                (LOAD_PORT, LOAD_LOCK_A, LOAD_LOCK_B), (1,),
                ATMOSPHERE_ROBOT_SECONDS,
            ),
            VACUUM_ROBOT: _robot(
                VACUUM_ROBOT, "VTMRobot", 2,
                (LOAD_LOCK_A, LOAD_LOCK_B, *PROCESS_CHAMBERS), (1, 2),
                VACUUM_ROBOT_SECONDS,
            ),
        },
    }


def _stage(
    step_id: int,
    *,
    kind: str,
    stations: tuple[str, ...],
    need_process: bool = False,
    post: tuple[int, ...] = (),
) -> Dict[str, Any]:
    """构造一个 Route Step。"""
    return {
        "stepId": step_id,
        "postStepIds": list(post),
        "needProcess": need_process,
        "kind": kind,
        "visits": [{"stationName": station} for station in stations],
    }


def _cycle_stages(
    process_stations: tuple[tuple[str, ...], ...],
    *,
    entry: tuple[str, ...] = (LOAD_LOCK_A, LOAD_LOCK_B),
    exit_stations: tuple[str, ...] | None = None,
    source: str = LOAD_PORT,
) -> List[Dict[str, Any]]:
    """构造 ``LP → ATR → 入口锁 → 加工链 → 出口锁 → ATR → LP`` 的标准 Step 链。"""
    exit_candidates = entry if exit_stations is None else exit_stations
    stages: List[Dict[str, Any]] = [
        _stage(0, kind="station", stations=(source,)),
        _stage(1, kind="robot", stations=(ATMOSPHERE_ROBOT,)),
        _stage(2, kind="station", stations=entry),
        _stage(3, kind="robot", stations=(VACUUM_ROBOT,)),
    ]
    for candidates in process_stations:
        stages.append(_stage(
            len(stages), kind="station", stations=candidates, need_process=True,
        ))
        stages.append(_stage(len(stages), kind="robot", stations=(VACUUM_ROBOT,)))
    stages.append(_stage(len(stages), kind="station", stations=exit_candidates))
    stages.append(_stage(len(stages), kind="robot", stations=(ATMOSPHERE_ROBOT,)))
    stages.append(_stage(len(stages), kind="station", stations=(source,)))
    for index, stage in enumerate(stages[:-1]):
        stage["postStepIds"] = [index + 1]
    return stages


def single_node_route() -> Dict[str, Any]:
    """单节点共享双锁路线：``LP → LA/LB → PM1 → LA/LB → LP``。"""
    return {"name": "PM1", "group": "合成路线", "stages": _cycle_stages((("PM1",),))}


def parallel_route() -> Dict[str, Any]:
    """并行共享双锁路线：两个候选加工腔。"""
    return {
        "name": "PM1/PM2",
        "group": "合成路线",
        "stages": _cycle_stages((("PM1", "PM2"),)),
    }


def reentrant_route() -> Dict[str, Any]:
    """独立重入路线：``PM1 → PM2 → PM1 → PM2``，共享同一批物理腔。"""
    return {
        "name": "PM1 → PM2 → PM1 → PM2",
        "group": "合成路线",
        "stages": _cycle_stages((("PM1",), ("PM2",), ("PM1",), ("PM2",))),
    }


def unsupported_route() -> Dict[str, Any]:
    """三道工序且中间一道并行：既不属四种轮转形态，也没有重入交叠。"""
    return {
        "name": "PM1 → PM2/PM3 → PM4",
        "group": "合成路线",
        "stages": _cycle_stages((("PM1",), ("PM2", "PM3"), ("PM4",))),
    }


def broken_route() -> Dict[str, Any]:
    """入口锁被写成加工腔：机器手不可达，编译阶段必须报错。"""
    return {
        "name": "PM1(坏入口)",
        "group": "合成路线",
        "stages": _cycle_stages((("PM1",),), entry=("PM2",)),
    }


def device_overview() -> Dict[str, Any]:
    """构造设备概览对象，形状与 ``get_workspace_device_overview`` 一致。"""
    return {
        "id": DEVICE_ID,
        "name": "合成理论产能设备",
        "updatedAt": "2026-01-01T00:00:00+08:00",
        "device": device_init(),
        "routes": [
            single_node_route(),
            parallel_route(),
            reentrant_route(),
            unsupported_route(),
            broken_route(),
        ],
        "cleans": [
            {
                "name": WAC_CLEAN_NAME,
                "cleanType": "wacclean",
                "recipeTime": 11.0,
                "triggerCount": 2,
                "modules": ["PM1", "PM2"],
            },
            {
                "name": PRECLEAN_NAME,
                "cleanType": "preclean",
                "recipeTime": PRECLEAN_SECONDS,
                "triggerCount": 5,
                "modules": ["PM1"],
            },
        ],
        "routeAliases": {},
        "robotSlots": None,
        "tests": [
            {"id": TEST_ID, "name": "合成测试（WAC）", "group": "合成组"},
            {"id": TEST_ID_PRECLEAN, "name": "合成测试（Pre）", "group": "合成组"},
        ],
    }


def _route_config(
    *,
    process_seconds: float,
    before_clean_refs: Sequence[str] = (),
    pre_pjob_clean_refs: Sequence[str] = (),
) -> Dict[str, Any]:
    """构造单节点路线的测试侧参数，可挂接清洁引用。"""
    return {
        "bufferOption": 0,
        "prePJobCleanRefs": list(pre_pjob_clean_refs),
        "postPJobCleanRefs": [],
        "postCJobCleanRefs": [],
        "stages": {
            "4": {
                "processTime": process_seconds,
                "recipeTime": process_seconds,
                "qTimeLimit": -1,
                "residencyConstraint": -1,
                "beforeCleanRefs": list(before_clean_refs),
                "afterCleanRefs": [WAC_CLEAN_NAME] if not before_clean_refs else [],
                "processRecipe": "合成_Step4",
                "processType": "",
                "weight": "{}",
                "moveTimeOffset": "{}",
                "slotIds": "1",
            },
        },
    }


def test_case(test_id: str = TEST_ID) -> Dict[str, Any]:
    """构造只引用单节点路线的测试：默认带 WAC 清洁，另一个带 PreClean。"""
    preclean = test_id == TEST_ID_PRECLEAN
    route_config = _route_config(
        process_seconds=PROCESS_SECONDS,
        before_clean_refs=(PRECLEAN_NAME,) if preclean else (),
        pre_pjob_clean_refs=(PRECLEAN_NAME,) if preclean else (),
    )
    return {
        "id": test_id,
        "name": "合成测试（Pre）" if preclean else "合成测试（WAC）",
        "group": "合成组",
        "strategy": "cycle",
        "roundCount": 1,
        "times": [0.0],
        "options": {},
        "cleans": [],
        "routeConfigs": {"PM1": route_config},
        "rounds": [{
            "currentTime": 0.0,
            "cjobs": [{
                "taskId": f"T{1 if not preclean else 2}",
                "loadPort": LOAD_PORT,
                "cjobCycle": 1,
                "jobType": 0,
                "priority": 1,
                "taskMode": 0,
                "pJobNameList": ["P1"],
                "pjobs": [{
                    "jobName": "P1",
                    "taskId": "1",
                    "waferCount": 4,
                    "matList": [],
                    "routeRef": "PM1",
                    "loadPort": LOAD_PORT,
                    "priority": 1,
                }],
            }],
        }],
        "schemaVersion": 9,
    }


def install(monkeypatch: Any) -> None:
    """把页面服务的数据读取替换为隔离夹具，不触碰工作区主数据。"""
    from app.backend import theory_capacity_service

    overview = device_overview()

    def read_device(device_id: str, path: Any = None) -> Mapping[str, Any]:
        if device_id != DEVICE_ID:
            raise ValueError(f"设备不存在：{device_id}")
        return deepcopy(overview)

    def read_test(device_id: str, test_id: str, path: Any = None) -> Mapping[str, Any]:
        if device_id != DEVICE_ID:
            raise ValueError(f"设备不存在：{device_id}")
        if test_id not in (TEST_ID, TEST_ID_PRECLEAN):
            raise ValueError(f"测试集不存在：{test_id}")
        return test_case(test_id)

    monkeypatch.setattr(theory_capacity_service, "get_workspace_device_overview", read_device)
    monkeypatch.setattr(theory_capacity_service, "get_workspace_test", read_test)
