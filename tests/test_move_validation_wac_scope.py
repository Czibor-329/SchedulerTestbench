"""验证单腔 PJob 隔离与双腔全局 WAC 待办的边界。"""

from realtime_scheduler.backend.validation.move_validation import (
    MachineState,
    MaterialState,
    MoveStateReplay,
    SlotPhase,
    SlotState,
    validate_move_list,
)
from tests.support.move_validation_fixtures import move


def _dual_chamber_wac_update() -> dict:
    """构造两个 PJob 共用双腔全局 WAC 周期的最小快照。"""
    return {
        "Stations": {"PM1": {
            "Type": "MultiProcessChamber",
            "Capacity": 2,
            "StateVariables": {"ProcessCount": {"Value": {"Value": 2}}},
        }},
        "Robots": {},
        "Materials": [],
        "ProcessJobs": [
            {
                "JobName": pjob_name,
                "OriginRoute": {"RouteSteps": [{
                    "StepID": 4,
                    "Visits": [{
                        "StationName": "PM1",
                        "ProcessRecipe": "ProductRecipe",
                        "AfterOutPM": [{
                            "CheckConditions": {"WAC": [{
                                "TaskName": "WacClean",
                                "UpdateStateVariables": ["ProcessCount"],
                            }]},
                            "ExecuteOrder": [{
                                "StateVariableName": "ProcessCount",
                                "ThresholdValueList": [2, 9999],
                            }],
                        }],
                    }],
                }]},
            }
            for pjob_name in ("P1", "P2")
        ],
    }


def test_dual_chamber_wac_clears_pending_obligation_across_pjobs() -> None:
    """双腔全局 WAC 完成后不得残留另一个 PJob 的同腔室待办。"""
    state = MachineState.from_sources(None, _dual_chamber_wac_update())
    state.pending_wac_obligations.add(("PM1", "P1", "ProcessCount", "WacClean"))
    clean_move = move(
        1, 9, 0, 10,
        ModuleName="PM1", MatIDList=[], StepIDList=[], SlotList=[1, 2],
        PJobName=["P2"], CleanTaskName="WacClean",
        IsLastCleanTaskMove=True,
    )
    replay = MoveStateReplay(None, [clean_move], state)
    replay.update_move_state({"MoveID": 1, "MoveState": MoveStateReplay.RUNNING})
    replay.update_move_state({"MoveID": 1, "MoveState": MoveStateReplay.DONE})

    assert replay.state.pending_wac_obligations == set()
    assert replay.state.wac_counter_value(
        replay.state.stations["PM1"], "P1", "ProcessCount",
    ) == 0

    replay.state.stations["PM1"].slots[1] = SlotState(
        phase=SlotPhase.UNPROCESSED,
        material=MaterialState(102, pjob_name="P1", step_id=4),
    )
    assert validate_move_list(
        None,
        [move(
            2, 9, 11, 21,
            ModuleName="PM1", MatIDList=[102], StepIDList=[4], SlotList=[1],
            PJobName=["P1"], ProcessRecipe="ProductRecipe",
        )],
        replay.state,
    ) == []
