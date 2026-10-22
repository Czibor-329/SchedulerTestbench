"""标准接口快照的可变隔离、别名与扩展类型契约。"""

from dataclasses import dataclass

from app.backend.payload_snapshot import copy_payload


def test_snapshot_preserves_shared_references_without_aliasing_input() -> None:
    """同一 payload 内的共享 Route 仍共享，复制后的修改不影响输入。"""
    route = {"steps": [{"slots": [1, 2]}]}
    source = {"first": route, "second": route}
    snapshot = copy_payload(source)
    assert snapshot["first"] is snapshot["second"]
    snapshot["first"]["steps"][0]["slots"].append(3)
    assert source["first"]["steps"][0]["slots"] == [1, 2]


def test_snapshot_preserves_cycles_and_non_json_extensions() -> None:
    """扩展对象、元组键和循环引用不因快速 JSON 路径改变类型。"""
    @dataclass
    class Extension:
        """模拟算法扩展对象中的可变字段。"""
        values: list[int]

    source = {("PM1", 1): Extension([2])}
    source["self"] = source
    snapshot = copy_payload(source)
    assert snapshot["self"] is snapshot
    assert isinstance(snapshot[("PM1", 1)], Extension)
    snapshot[("PM1", 1)].values.append(3)
    assert source[("PM1", 1)].values == [2]
