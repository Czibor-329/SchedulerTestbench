"""重算加工计数的真实完成边界回归。

用最小双槽 PM 分别覆盖产品加工和空腔 WAC，验证完成投影不能提前改写
AlgSchedule.StateVariables/MaterialCount，且同一 Running 跨多代只完成一次。
"""

from copy import deepcopy

import pytest

from app.backend.execution.algorithm_runtime import PlatformMoveListRuntime
from app.backend.execution.run_state import (
    _build_platform_recompute_update,
    advance_platform_move_list_to_update,
)
from app.backend.validation.move_validation import MoveStateReplay


def _dual_process_plan(*, running_wac: bool) -> tuple[dict, dict]:
    """构造 1～11 秒执行的双片产品加工或阈值为 3 的空腔 WAC。"""
    product_materials = [101, 102]
    update = {
        "CurrentTime": 0.0,
        "Robots": {"VACRobot": {
            "Type": "VTMRobot", "Capacity": 2,
            "ArmInfo": {"ArmA": {
                "Name": "ArmA", "IsEnable": True, "SlotIDs": [1, 2],
                "AccessibleStations": ["LP1", "PM1"],
            }},
        }},
        "Stations": {"PM1": {
            "Type": "MultiProcessChamber", "Slots": [1, 2], "Capacity": 2,
            "StateVariables": {"ProcessCount": {
                "Name": "ProcessCount", "Type": 1,
                "Value": {"Value": 3 if running_wac else 0},
            }},
            "MaterialCount": {"1": 0, "2": 0},
        }},
        "Materials": [] if running_wac else [{
            "ID": material_id, "CurrentModuleName": "LP1", "SlotID": slot_id,
            "PJobName": "P1", "StepID": 0,
        } for slot_id, material_id in enumerate(product_materials, start=1)],
        "ProcessJobs": [{
            "JobName": "P1", "MatList": [] if running_wac else product_materials,
            "OriginRoute": {"RouteSteps": [{"StepID": 4, "Visits": [{
                "StationName": "PM1", "ProcessRecipe": "ProductRecipe",
                "AfterOutPM": [{
                    "CheckConditions": {"WAC": [{
                        "TaskName": "WacClean", "UpdateStateVariables": ["ProcessCount"],
                    }]},
                    "ExecuteOrder": [{
                        "StateVariableName": "ProcessCount", "ThresholdValueList": [3, 9999],
                    }],
                }],
            }]}]},
        }],
        "ControlJobs": [],
        "ProcessRecipes": [{
            "Name": "ProductRecipe", "ModuleName": "PM1", "Time": 10,
            "Weight": {"ProcessCount": 1},
        }],
    }
    move = {
        "MoveID": 1, "MoveType": 9, "ModuleName": "PM1",
        "StartTime": 1.0, "EndTime": 11.0, "SlotList": [1, 2],
        "MatIDList": [] if running_wac else product_materials,
        "PJobName": ["P1"] if running_wac else ["P1", "P1"],
        "ProcessRecipe": "WacRecipe" if running_wac else "ProductRecipe",
    }
    if running_wac:
        move.update({"CleanTaskName": "WacClean", "IsLastCleanTaskMove": True})
    prefix = []
    if not running_wac:
        update["Stations"]["LP1"] = {"Type": "LoadPort", "Slots": [1, 2], "Capacity": 2}
        prefix = [{
            "MoveID": 10, "MoveType": 0, "ModuleName": "VACRobot",
            "StartTime": 0.0, "EndTime": 0.1, "MatIDList": product_materials,
            "SrcStationList": ["LP1", "LP1"], "SrcSlotList": [1, 2],
            "RobotSlotList": [1, 2], "StepIDList": [1, 1],
        }, {
            "MoveID": 11, "MoveType": 5, "ModuleName": "VACRobot",
            "StartTime": 0.1, "EndTime": 0.2, "MatIDList": product_materials,
            "SrcStationList": ["LP1"], "DestStationList": ["PM1"],
            "RobotSlotList": [1, 2],
        }, {
            "MoveID": 12, "MoveType": 6, "ModuleName": "PM1",
            "StartTime": 0.1, "EndTime": 0.2, "RelatedRobotType": 1,
        }, {
            "MoveID": 13, "MoveType": 1, "ModuleName": "VACRobot",
            "StartTime": 0.2, "EndTime": 0.4, "MatIDList": product_materials,
            "DestStationList": ["PM1", "PM1"], "DestSlotList": [1, 2],
            "RobotSlotList": [1, 2], "StepIDList": [4, 4],
        }, {
            "MoveID": 14, "MoveType": 7, "ModuleName": "PM1",
            "StartTime": 0.4, "EndTime": 1.0,
        }]
    return update, {"MoveList": [*prefix, move], "Feedback": []}


def _recompute_snapshot(runtime: PlatformMoveListRuntime, time: float) -> tuple[dict, object, list]:
    """推进真实时间并构造包含完成投影位置、真实计数及在途通知的 update。"""
    advance_platform_move_list_to_update(runtime, time)
    projected, committed = runtime.project_started_moves(time)
    new_round = deepcopy(runtime.current_update)
    new_round.update({"CurrentTime": time, "Materials": []})
    update = _build_platform_recompute_update(
        runtime, new_round, time,
        runtime.running_move_states, projected_state=projected,
        completed_process_state=runtime.state,
    )
    return update, projected, committed


@pytest.mark.parametrize("running_wac", [False, True])
def test_recompute_exports_only_completed_process_variables(running_wac: bool) -> None:
    """加工中不累加，WAC 中不清零；逐槽计数也不得提前应用完成投影。"""
    initial, output = _dual_process_plan(running_wac=running_wac)
    original = deepcopy((initial, output))
    runtime = PlatformMoveListRuntime(initial, output, device=initial)
    update, projected, _ = _recompute_snapshot(runtime, 5.0)

    actual_count = 3 if running_wac else 0
    projected_count = 0 if running_wac else 1
    assert projected.stations["PM1"].state_variables["ProcessCount"] == projected_count
    station = update["Stations"]["PM1"]
    assert station["StateVariables"]["ProcessCount"]["Value"]["Value"] == actual_count
    assert station["MaterialCount"] == {"1": 0, "2": 0}
    assert station["TimeToAvailableOfSlot"] == {"1": 6.0, "2": 6.0}
    assert [(item["MoveID"], item["MoveState"]) for item in update["MoveStates"]] == [(1, MoveStateReplay.RUNNING)]
    assert (initial, output) == original


@pytest.mark.parametrize("running_wac", [False, True])
def test_running_process_crosses_two_recomputations_without_early_commit(running_wac: bool) -> None:
    """同一 Running 跨两轮空计划重算仍保持原值，在真实结束时只更新一次。"""
    initial, output = _dual_process_plan(running_wac=running_wac)
    runtime = PlatformMoveListRuntime(initial, output, device=initial)
    for time in (5.0, 7.0):
        update, projected, committed = _recompute_snapshot(runtime, time)
        assert update["Stations"]["PM1"]["StateVariables"]["ProcessCount"]["Value"]["Value"] == (3 if running_wac else 0)
        assert len(update["MoveStates"]) == 1
        runtime.replace_plan(update, {"MoveList": []}, time, "测试重算", committed, initial_state=projected)
        assert runtime.current_plan == []
        assert runtime.state.stations["PM1"].state_variables["ProcessCount"] == (3 if running_wac else 0)

    notifications = advance_platform_move_list_to_update(runtime, 11.0)
    assert [(item["MoveID"], item["MoveState"]) for item in notifications] == [(1, MoveStateReplay.DONE)]
    assert runtime.running_move_states == []
    assert runtime.state.stations["PM1"].state_variables["ProcessCount"] == (0 if running_wac else 1)
    assert advance_platform_move_list_to_update(runtime, 12.0) == []
    assert runtime.state.stations["PM1"].state_variables["ProcessCount"] == (0 if running_wac else 1)


def test_recompute_rejects_reusing_running_move_id() -> None:
    """跨代不可让新动作覆盖仍有结束回调的旧 MoveID。"""
    initial, output = _dual_process_plan(running_wac=False)
    runtime = PlatformMoveListRuntime(initial, output, device=initial)
    update, projected, _ = _recompute_snapshot(runtime, 5.0)
    projected.ensure_station("PM2", 1)
    conflicting = {"MoveList": [{
        "MoveID": 1, "MoveType": 9, "ModuleName": "PM2", "MatIDList": [],
        "StartTime": 12.0, "EndTime": 13.0,
    }]}
    with pytest.raises(ValueError, match="正在运行的 MoveID"):
        runtime.replace_plan(update, conflicting, 5.0, "测试重算", [], initial_state=projected)


def test_builtin_recompute_keeps_its_planning_projection_contract() -> None:
    """内置策略使用独立规划投影；真实计数选择只在标准算法包边界启用。"""
    initial, output = _dual_process_plan(running_wac=False)
    runtime = PlatformMoveListRuntime(initial, output, device=initial)
    _, projected, _ = _recompute_snapshot(runtime, 5.0)
    update = _build_platform_recompute_update(
        runtime, initial, 5.0, runtime.running_move_states, projected_state=projected,
    )
    assert update["Stations"]["PM1"]["StateVariables"]["ProcessCount"]["Value"]["Value"] == 1
    assert runtime.state.stations["PM1"].state_variables["ProcessCount"] == 0
