"""验证索引查询与原全表查询等价，以及协议副本的隔离语义。"""

import random

from realtime_scheduler.backend.validation import move_validation
from realtime_scheduler.backend.validation.move_validation_helpers import _IndexedMoves, _related_move
from realtime_scheduler.backend.execution.run_state import _copy_move_list


def test_index_matches_full_scan_with_dependencies_ambiguity_and_time_tolerance():
    """索引不能漏掉多候选歧义、非邻接依赖或时间容差边界。"""
    randomizer = random.Random(42)
    for _ in range(200):
        end = randomizer.choice([10.0, 30000.0, 1e9])
        prepare = {"MoveID": 1, "EndTime": end, "Station": "LL",
                   "MatIDList": randomizer.choice([[], [1], [2]]),
                   "RelatedActionType": randomizer.choice([0, 1, 2, None])}
        moves = []
        for index in range(30):
            move_type = randomizer.choice([0, 1, 2, 4, 9])
            station_field = "SrcStationList" if move_type in (0, 2) else "DestStationList" if move_type == 1 else "StationList"
            moves.append({"MoveID": index + 2, "MoveType": move_type,
                          "StartTime": end + randomizer.choice([-1e-6, 0, 1e-6, 2e-6, 5]),
                          station_field: [randomizer.choice(["LL", "PM"])],
                          "MatIDList": randomizer.choice([[], [1], [2]]),
                          "PreMoveID": randomizer.choice([[], [1], [3]])})
        assert _related_move(prepare, _IndexedMoves(moves)) is _related_move(prepare, moves)


def test_move_copy_isolates_nested_extensions_and_preserves_types():
    """优化复制后，数组和嵌套扩展的修改不能污染来源。"""
    source = [{"MoveID": 1, "MatIDList": [1], "Extension": {2: [{"value": 3}]}}]
    copied = _copy_move_list(source)
    assert copied == source
    copied[0]["MatIDList"].append(2)
    copied[0]["Extension"][2][0]["value"] = 4
    assert source[0]["MatIDList"] == [1]
    assert source[0]["Extension"][2][0]["value"] == 3


def test_reproduction_output_keeps_nested_data_isolated():
    """日志复制保留扩展字段类型并隔离后续的输入修改。"""
    from copy import deepcopy
    from realtime_scheduler.backend.execution.run_state import ReproductionLog

    info = {"MoveList": [{"MoveID": 1, "MatIDList": [2], "Extension": {3: [4]}}], "Feedback": [{"detail": [5]}]}
    expected = deepcopy(info)
    log = ReproductionLog()
    log.add("AlgOutput", info)
    info["MoveList"][0]["MatIDList"].append(6)
    info["MoveList"][0]["Extension"][3].append(7)
    info["Feedback"][0]["detail"].append(8)
    assert log.entries[0]["Info"] == expected
