"""Move 依赖代表图：格式、分叉汇合与提交历史、缺失/环/时间错误。"""
from app.backend.validation.move_validation import ValidationErrorCode, validate_move_list
from tests.support.move_validation_fixtures import move


def _update():
    """依赖测试只需要一个无物料 PM，不混入搬运或设备计时约束。"""
    return {"Stations": {"PM1": {"Type": "ProcessChamber", "Capacity": 1}}, "Robots": {}, "Materials": []}


def test_validation_codes_are_unique_and_invalid_moves_have_a_code():
    """格式错误保留稳定错误码，不退化为非结构化异常。"""
    codes = [item.value for item in ValidationErrorCode]
    assert len(codes) == len(set(codes))
    issues = validate_move_list(None, ["invalid"], {})
    assert any("[MVL-FMT-001]" in issue for issue in issues)


def test_dependency_graph_accepts_duplicate_edges_and_committed_predecessors():
    """一个分叉汇合图同时覆盖重复边去重、本代前驱及已完成历史引用。"""
    history = {99: move(99, 6, 0, 0, ModuleName="PM1")}
    moves = [
        move(1, 6, 0, 1, ModuleName="PM1", PreMoveID=[99]),
        move(2, 6, 1, 2, ModuleName="PM1", PreMoveID=[1, 1]),
        move(3, 6, 2, 3, ModuleName="PM1", PreMoveID=[1, 1]),
        move(4, 6, 3, 4, ModuleName="PM1", PreMoveID=[2, 3, 2, 3, 99]),
    ]
    assert validate_move_list(None, moves, _update(), external_predecessors=history) == []


def test_invalid_dependency_graphs_report_missing_cycle_and_time_codes():
    """每种独立错误保留一张最小反例图，已提交历史也必须满足时间边界。"""
    cases = [
        ("未提交的前驱", [move(101, 6, 2, 3, ModuleName="PM1", PreMoveID=[999])],
         {1: move(1, 6, 0, 1, ModuleName="PM1")}, ValidationErrorCode.PREDECESSOR_MISSING),
        ("本代依赖环", [move(1, 6, 0, 0, ModuleName="PM1", PreMoveID=[2]), move(2, 6, 0, 0, ModuleName="PM1", PreMoveID=[1])],
         {}, ValidationErrorCode.DEPENDENCY_CYCLE),
        ("历史动作尚未完成", [move(101, 6, 2, 3, ModuleName="PM1", PreMoveID=[1])],
         {1: move(1, 6, 0, 5, ModuleName="PM1")}, ValidationErrorCode.PREDECESSOR_TIME_CONFLICT),
    ]
    for name, moves, history, expected in cases:
        issues = validate_move_list(None, moves, _update(), external_predecessors=history)
        assert any(f"[{expected.value}]" in issue for issue in issues), (name, issues)
