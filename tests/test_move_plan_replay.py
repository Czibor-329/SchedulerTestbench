"""完整校验终态复用与中途重算事件推进的语义回归。"""

from copy import deepcopy

import pytest

from app.backend.execution.algorithm_runtime import PlatformMoveListRuntime
from app.backend.execution.run_state import _planned_events, advance_platform_move_list_to_update
from app.backend.validation import move_replay
from app.backend.validation.move_validation import MoveStateReplay
from tests.support.move_validation_fixtures import dual_chamber_update, dual_transfer_moves


def test_completed_validation_state_matches_every_event_replay(monkeypatch) -> None:
    """终态复用必须保留门、压力、持片、Step、占用窗口和已完成动作。"""
    update, moves = dual_chamber_update(), dual_transfer_moves()
    reference = MoveStateReplay(None, moves, update)
    events = list(_planned_events(moves))
    for _, _, notification in events:
        reference.update_move_state(notification, snapshot=False)
    runtime = PlatformMoveListRuntime(update, {"MoveList": moves})

    def reject_duplicate_replay(*args):
        """完整通过的本代不应再次执行物理动作。"""
        pytest.fail("重复执行已完整校验的动作")

    monkeypatch.setattr(move_replay, "_start_move", reject_duplicate_replay)
    notifications = advance_platform_move_list_to_update(runtime, 11)
    assert notifications == [event[2] for event in events]
    assert runtime.state == reference.state
    assert runtime.current_plan == reference.materialized_plan


def test_intermediate_cutoff_still_replays_running_actions() -> None:
    """中途切点必须保留 Running 和待落地动作，随后允许应用本代终态。"""
    update, moves = dual_chamber_update(), dual_transfer_moves()
    runtime = PlatformMoveListRuntime(update, {"MoveList": moves})
    advance_platform_move_list_to_update(runtime, 1.5)
    assert runtime._tracker.running_move_ids == frozenset({3})
    assert runtime.state.robots["VACRobot"].hands[1] is None
    advance_platform_move_list_to_update(runtime, 11)
    assert runtime.state.robots["ATMRobot"].hands[1].material_id == 101


def test_modified_notification_invalidates_validated_completion() -> None:
    """现场动作改变计划时间后必须继续真实回放，不能套用旧终态。"""
    validation = move_replay.validate_move_plan(None, dual_transfer_moves(), dual_chamber_update())
    replay = MoveStateReplay.from_validation(validation)
    replay.update_move_state({"MoveID": 1, "MoveState": 0, "StartTime": 0.1}, snapshot=False)
    assert replay.finish_validated_plan(11) is False


def test_validation_and_runtime_do_not_modify_input() -> None:
    """快速路径同样隔离原始 update、Move 数组与扩展元数据。"""
    update, moves = dual_chamber_update(), dual_transfer_moves()
    moves[0]["Extension"] = {"values": [1]}
    original = deepcopy((update, moves))
    runtime = PlatformMoveListRuntime(update, {"MoveList": moves})
    advance_platform_move_list_to_update(runtime, 11)
    runtime.current_plan[0]["Extension"]["values"].append(2)
    assert (update, moves) == original


def test_serial_events_preserve_tolerance_and_zero_duration_order() -> None:
    """同桶先完成已运行动作，再按原始开始时刻及 MoveID 落地零时长动作。"""
    moves = [
        {"MoveID": 1, "StartTime": 0, "EndTime": 1},
        {"MoveID": 3, "StartTime": 1.0000001, "EndTime": 1.0000002},
        {"MoveID": 2, "StartTime": 1.0000001, "EndTime": 1.0000001},
    ]
    assert [(kind, event["MoveID"]) for kind, _, event in _planned_events(moves)] == [
        ("start", 1), ("finish", 1), ("start", 2), ("finish", 2), ("start", 3), ("finish", 3),
    ]
