"""真实完成事件到企业重算快照的计时、计数和标识变量回归。"""

import pytest

from app.backend.execution.recompute_state import apply_machine_state_to_update
from app.backend.execution.run_state import _apply_running_resource_times
from app.backend.validation.move_replay import MoveStateReplay
from app.backend.validation.move_state import (
    DoorState, MachineState, MaterialState, RobotState, SlotPhase, SlotState, StationState,
)


def _payload():
    """仅声明本次观察量及一个真实产品标识，不依赖开发机设备数据。"""
    return {
        "CurrentTime": 10,
        "Stations": {"PM1": {"StateVariables": {
            "IdleTime": {"Type": 0, "Value": {"Value": 4.0}},
            "ProcessCount": {"Type": 1, "Value": {"Value": 8.0}},
            "DummyCount": {"Type": 1, "Value": {"Value": 7.0}},
            "LastRouteName": {"Type": 3, "Value": {"Value": "old-route"}},
            "LastPJobName": {"Type": 7, "Value": {"Value": "old-lot"}},
        }}},
        "Robots": {},
        "Materials": [{"ID": 0, "LotID": "lot-1", "Route": {"Name": "route-1"}}],
    }


def _state():
    """构造初始 IdleTime 已累计四秒的物理 PM。"""
    state = MachineState(stations={"PM1": StationState(
        "PM1", "ProcessChamber", {1: SlotState()},
        state_variables={"IdleTime": 4.0, "ProcessCount": 8.0, "DummyCount": 7.0},
    )})
    state.seed_runtime_observations(_payload())
    return state


def test_idle_clock_continues_across_snapshot_and_generation_without_reset():
    """占片/重算不会重置时钟，只有 Process/Clean 完成事件更新起点。"""
    state = _state()
    update = _payload()
    apply_machine_state_to_update(update, state, 13)
    assert update["Stations"]["PM1"]["StateVariables"]["IdleTime"]["Value"]["Value"] == 7
    state.refresh_validation_metadata(update)
    assert state.stations["PM1"].idle_reference_time == 6
    state.record_process_completion(state.stations["PM1"], {"CleanTaskName": "PreClean"}, 15)
    apply_machine_state_to_update(update, state, 18)
    assert update["Stations"]["PM1"]["StateVariables"]["IdleTime"]["Value"]["Value"] == 3


def test_product_identity_uses_declared_type_and_dummy_does_not_overwrite_it():
    """字符串变量按 Type 更新，不能把显示名 LastPJobName 硬解为 PJob。"""
    state = _state()
    state.record_process_completion(state.stations["PM1"], {"MatIDList": [0]}, 15)
    state.record_process_completion(state.stations["PM1"], {
        "MatIDList": [1000000], "CleanTaskName": "PreDummyClean",
    }, 16)
    update = _payload()
    apply_machine_state_to_update(update, state, 18)
    variables = update["Stations"]["PM1"]["StateVariables"]
    assert variables["LastRouteName"]["Value"]["Value"] == "route-1"
    assert variables["LastPJobName"]["Value"]["Value"] == "lot-1"


@pytest.mark.parametrize("last", [False, True])
def test_each_completed_dummy_resets_declared_counters_not_just_last_pass(last):
    """Dummy 寿命递增、清洗完成数和 PM 复位变量是三个不同口径。"""
    state = _state()
    station = state.stations["PM1"]
    station.slots[1] = SlotState(SlotPhase.UNPROCESSED, MaterialState(1000000, "job", 4))
    state.dummy_material_counts["1000000"] = 2
    state.clean_task_state_variables["PreDummyClean"] = {"IdleTime", "ProcessCount", "DummyCount"}
    move = {"MoveID": 1, "MoveType": 9, "ModuleName": "PM1", "MatIDList": [1000000],
            "SlotList": [1], "StepIDList": [4], "PJobName": ["job"],
            "ProcessRecipe": "/PreJobDummy", "CleanTaskName": "PreDummyClean",
            "IsLastCleanTaskMove": last, "StartTime": 10, "EndTime": 12}
    replay = MoveStateReplay(None, [move], state)
    replay.update_move_state({"MoveID": 1, "MoveState": 0, "StartTime": 10, "EndTime": -1})
    assert replay.state.stations["PM1"].state_variables["ProcessCount"] == 8
    replay.update_move_state({"MoveID": 1, "MoveState": 1, "StartTime": 10, "EndTime": 12})
    assert replay.state.stations["PM1"].state_variables["ProcessCount"] == 0
    assert replay.state.stations["PM1"].state_variables["DummyCount"] == 0
    assert replay.state.dummy_material_counts["1000000"] == 3
    assert replay.state.stations["PM1"].idle_reference_time == 12


def test_arm_access_counts_once_per_physical_arm_and_never_for_pretrans():
    """同臂双槽一次访问只加一次；该接口仅由真实取放完成回调调用。"""
    robot = RobotState("ATR", arm_slots={"ArmA": {1, 2}, "ArmB": {3, 4}},
                       arm_use_counts={"ArmA": 5, "ArmB": 0})
    robot.record_access([1, 2])
    assert robot.arm_use_counts == {"ArmA": 6, "ArmB": 0}
    robot.record_access([3])
    assert robot.arm_use_counts == {"ArmA": 6, "ArmB": 1}


def test_inflight_pretrans_reserves_robot_without_freezing_reference_stations():
    """无载 PreTrans 引用了站名，不代表访问或占用了那个站的物料槽。"""
    update = {"Robots": {"ATR": {"TimeToAvailable": 0}}, "Stations": {
        "LP1": {"TimeToAvailableOfSlot": {"1": 0}},
        "DummyPort": {"TimeToAvailableOfSlot": {"1": 0}},
    }}
    move = {"MoveID": 1, "MoveType": 5, "ModuleName": "ATR", "EndTime": 12,
            "SrcStationList": ["LP1"], "SrcSlotList": [],
            "DestStationList": ["DummyPort"], "DestSlotList": [1]}
    _apply_running_resource_times(update, [move], 10, [{"MoveID": 1, "MoveState": 0}])
    assert update["Robots"]["ATR"]["TimeToAvailable"] == 2
    assert update["Stations"]["LP1"]["TimeToAvailableOfSlot"]["1"] == 0
    assert update["Stations"]["DummyPort"]["TimeToAvailableOfSlot"]["1"] == 0


def test_physical_snapshot_prunes_only_explicitly_unloaded_materials():
    """从物理现场构造快照时仅删除已卸载片，不影响在途材料或原状态。"""
    state = _state()
    state.stations["PM1"].slots[1].material = MaterialState(0)
    snapshot = state.clone()
    snapshot.remove_materials({0})
    assert snapshot.stations["PM1"].slots[1].material is None
    assert state.stations["PM1"].slots[1].material.material_id == 0
