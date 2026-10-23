"""平台门、库存站、Robot 传输与状态占用校验测试。"""

from __future__ import annotations

from app.backend.validation.move_validation import (
    MoveStateReplay,
    SlotPhase,
    ValidationErrorCode,
    validate_move_list,
)
from tests.support.move_validation_fixtures import (
    dual_chamber_update as _dual_chamber_update,
    dual_transfer_moves as _dual_transfer_moves,
    move as _move,
)

def test_dual_slot_pick_place_and_full_loadlock_transition_are_physically_valid() -> None:
    """同一 Arm 双槽搬两片、LL 满载充气都应通过平台校验。"""
    assert validate_move_list(None, _dual_transfer_moves(), _dual_chamber_update()) == []

def test_repeated_prepare_and_complete_are_idempotent() -> None:
    """门已开可再次开门，门已关也可再次关门。"""
    update = {
        "Stations": {
            "PM1": {"Type": "ProcessChamber", "Capacity": 1},
        },
        "Robots": {},
        "Materials": [],
    }
    moves = [
        _move(1, 6, 0, 1, ModuleName="PM1"),
        _move(2, 6, 1, 2, ModuleName="PM1"),
        _move(3, 7, 2, 3, ModuleName="PM1"),
        _move(4, 7, 3, 4, ModuleName="PM1"),
    ]

    assert validate_move_list(None, moves, update) == []

    replay = MoveStateReplay(None, moves, update)
    for move in moves:
        replay.update_move_state(
            {"MoveID": move["MoveID"], "MoveState": MoveStateReplay.RUNNING},
            snapshot=False,
        )
        replay.update_move_state(
            {"MoveID": move["MoveID"], "MoveState": MoveStateReplay.DONE},
            snapshot=False,
        )
    assert replay.state.stations["PM1"].door.value == "closed"


def test_repeated_door_action_still_rejects_busy_overlap() -> None:
    """幂等开关门不能绕过门机构的时间互斥。"""
    update = {
        "Stations": {
            "PM1": {"Type": "ProcessChamber", "Capacity": 1},
        },
        "Robots": {},
        "Materials": [],
    }
    moves = [
        _move(1, 6, 0, 2, ModuleName="PM1"),
        _move(2, 6, 1, 3, ModuleName="PM1"),
    ]

    issues = validate_move_list(None, moves, update)
    assert issues
    assert ValidationErrorCode.STATION_TRANSFER_BUSY.value in issues[0]

def test_align_move_completes_placed_material_without_advancing_route_step() -> None:
    """MoveType=11 应完成待对准状态，同时保持物料位置与 StepID。"""
    update = {
        "Stations": {
            "Aligner": {"Type": "Aligner", "Capacity": 1, "Slots": [1]},
        },
        "Robots": {},
        "Materials": [{
            "ID": 101,
            "CurrentModuleName": "Aligner",
            "SlotID": 1,
            "StepID": 2,
        }],
    }
    moves = [
        _move(
            1,
            11,
            5,
            8,
            ModuleName="Aligner",
            MatIDList=[101],
            StepIDList=[2],
            SlotList=[1],
        ),
    ]

    assert validate_move_list(None, moves, update) == []
    replay = MoveStateReplay(None, moves, update)
    replay.state.stations["Aligner"].slots[1].phase = SlotPhase.UNPROCESSED
    replay.update_move_state({"MoveID": 1, "MoveState": MoveStateReplay.RUNNING})
    replay.update_move_state({"MoveID": 1, "MoveState": MoveStateReplay.DONE})
    slot = replay.state.stations["Aligner"].slots[1]
    material = slot.material
    assert material is not None
    assert material.material_id == 101
    assert material.step_id == 2
    assert slot.phase is SlotPhase.COMPLETED

def test_pick_after_aligner_service_accepts_next_robot_step() -> None:
    """Aligner 放片、对准、取片链应允许 Pick 输出后继 Robot StepID。"""
    update = {
        "Stations": {
            "LP1": {"Type": "LoadPort", "Capacity": 1, "Slots": [1]},
            "Aligner": {"Type": "Aligner", "Capacity": 1, "Slots": [1]},
        },
        "Robots": {
            "ATR": {
                "Type": "ATMRobot",
                "Capacity": 1,
                "ArmInfo": {
                    "ArmA": {
                        "Name": "ArmA",
                        "IsEnable": True,
                        "SlotIDs": [1],
                        "AccessibleStations": ["LP1", "Aligner"],
                    },
                },
            },
        },
        "Materials": [{
            "ID": 101,
            "CurrentModuleName": "LP1",
            "SlotID": 1,
            "StepID": 0,
        }],
    }
    moves = [
        _move(1, 6, 0, 1, ModuleName="LP1", MatIDList=[101], StepIDList=[0], SlotList=[1]),
        _move(
            2, 0, 1, 2, ModuleName="ATR", MatIDList=[101], StepIDList=[1],
            SrcStationList=["LP1"], SrcSlotList=[1], RobotSlotList=[1],
        ),
        _move(3, 7, 2, 3, ModuleName="LP1", MatIDList=[101], StepIDList=[1], SlotList=[1]),
        _move(4, 6, 2, 3, ModuleName="Aligner", MatIDList=[101], StepIDList=[1], SlotList=[1]),
        _move(
            5, 1, 3, 4, ModuleName="ATR", MatIDList=[101], StepIDList=[2],
            DestStationList=["Aligner"], DestSlotList=[1], RobotSlotList=[1],
        ),
        _move(6, 7, 4, 5, ModuleName="Aligner", MatIDList=[101], StepIDList=[2], SlotList=[1]),
        _move(
            7, 11, 5, 6, ModuleName="Aligner", MatIDList=[101],
            StepIDList=[2], SlotList=[1],
        ),
        _move(8, 6, 6, 7, ModuleName="Aligner", MatIDList=[101], StepIDList=[2], SlotList=[1]),
        _move(
            9, 0, 7, 8, ModuleName="ATR", MatIDList=[101], StepIDList=[3],
            SrcStationList=["Aligner"], SrcSlotList=[1], RobotSlotList=[1],
        ),
    ]

    assert validate_move_list(None, moves, update) == []


def test_inventory_return_is_pickable_without_a_process_move():
    """DummyPort 复用与 Buffer 中转共用取放流程，只保留物料与工步的业务差异。"""
    for station, station_type, material_id, steps in [
        ("DummyPort", "DummyPort", 100002, (7, 8, 0, 1)),
        ("Buffer1", "Buffer", 101, (2, 2, 2, 3)),
    ]:
        update = {
            "Stations": {station: {"Type": station_type, "Capacity": 1, "Slots": [1]}},
            "Robots": {"ATR": {"Type": "ATMRobot", "Capacity": 1, "ArmInfo": {
                "ArmA": {"Name": "ArmA", "IsEnable": True, "SlotIDs": [1], "AccessibleStations": [station]}}}},
            "Materials": [{"ID": material_id, "CurrentModuleName": station, "SlotID": 1, "StepID": 0}],
        }
        ready, returned, next_ready, next_pick = steps
        moves = [
            _move(1, 6, 0, 1, ModuleName=station, MatIDList=[material_id], StepIDList=[0], SlotList=[1]),
            _move(2, 0, 1, 2, ModuleName="ATR", MatIDList=[material_id], StepIDList=[1], SrcStationList=[station], SrcSlotList=[1], RobotSlotList=[1]),
            _move(3, 7, 2, 3, ModuleName=station, MatIDList=[material_id], StepIDList=[1], SlotList=[1]),
            _move(4, 6, 3, 4, ModuleName=station, MatIDList=[material_id], StepIDList=[ready], SlotList=[1]),
            _move(5, 1, 4, 5, ModuleName="ATR", MatIDList=[material_id], StepIDList=[returned], DestStationList=[station], DestSlotList=[1], RobotSlotList=[1]),
            _move(6, 7, 5, 6, ModuleName=station, MatIDList=[material_id], StepIDList=[returned], SlotList=[1]),
            _move(7, 6, 6, 7, ModuleName=station, MatIDList=[material_id], StepIDList=[next_ready], SlotList=[1]),
            _move(8, 0, 7, 8, ModuleName="ATR", MatIDList=[material_id], StepIDList=[next_pick], SrcStationList=[station], SrcSlotList=[1], RobotSlotList=[1]),
        ]
        assert validate_move_list(None, moves, update) == [], station_type
