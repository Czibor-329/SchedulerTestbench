"""重算时按本代真实使用次数推进 Dummy 分配，保留下一次已规划路径。"""

import pytest

from app.backend.execution.recompute_state import restore_dummy_routes_from_algorithm_output


def _entry(name, job, count=1):
    """构造带来源工步的一次或多次可复用分配。"""
    return {"TaskID": "2", "PJobName": job, "AlgorithmCount": count,
            "RouteRecipe": {"Name": name, "RouteSteps": [{"StepID": 0, "Visits": [{"StationName": "DummyPort"}]}]}}


@pytest.mark.parametrize("running", [False, True])
def test_returned_dummy_keeps_next_assignment_even_during_unloaded_pretrans(running):
    """无载 PreTrans 可以引用下一片；回港不取消它已获分配的下一条 Route。"""
    update = {"Materials": [{"ID": 1000000, "Count": 6, "CurrentModuleName": "DummyPort",
                             "SrcPortName": "DummyPort", "StepID": 8}],
              "MoveStates": [{"MoveID": 1, "MoveState": 0}] if running else []}
    output = {"DummyReturnInfo": {"1000000": [_entry("old", "old-job"), _entry("next", "next-job")]},
              "MoveList": [{"MoveID": 1, "MoveType": 5, "StartTime": 0,
                            "MatIDList": [1000000], "TaskID": ["2"]}]}
    restore_dummy_routes_from_algorithm_output(update, output, initial_dummy_counts={"1000000": 5})
    dummy = update["Materials"][0]
    assert dummy["Route"]["Name"] == "next"
    assert dummy["PJobName"] == "next-job"
    assert dummy["StepID"] == 0
    assert dummy["Count"] == 6


def test_repeated_assignment_count_is_not_confused_with_lifetime_count():
    """同一分配可使用两次；绝对寿命十次不代表本代十次分配均完成。"""
    update = {"Materials": [{"ID": 1000000, "Count": 10, "CurrentModuleName": "DummyPort"}]}
    output = {"DummyReturnInfo": {"1000000": [_entry("repeat", "job", 2), _entry("future", "job2")]}}
    restore_dummy_routes_from_algorithm_output(update, output, initial_dummy_counts={"1000000": 9})
    assert update["Materials"][0]["Route"]["Name"] == "repeat"


def test_exhausted_assignments_restore_stock_without_resetting_lifetime():
    """分配全部完成后才回到未分配库存，寿命仍必须保留。"""
    update = {"Materials": [{"ID": 1000000, "Count": 8, "CurrentModuleName": "DummyPort"}]}
    output = {"DummyReturnInfo": {"1000000": [_entry("done", "job")]}}
    restore_dummy_routes_from_algorithm_output(update, output, initial_dummy_counts={"1000000": 7})
    dummy = update["Materials"][0]
    assert dummy["Route"]["RouteSteps"] == []
    assert dummy["PJobName"] == ""
    assert dummy["Count"] == 8


def test_decreasing_dummy_lifetime_is_not_silently_repaired():
    """错误寿命快照应报告矛盾，而不是选择一个猜测的分配。"""
    update = {"Materials": [{"ID": 1000000, "Count": 3, "CurrentModuleName": "DummyPort"}]}
    output = {"DummyReturnInfo": {"1000000": [_entry("route", "job")]}}
    with pytest.raises(ValueError, match="累计使用次数倒退"):
        restore_dummy_routes_from_algorithm_output(update, output, initial_dummy_counts={"1000000": 4})
