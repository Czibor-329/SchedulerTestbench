"""标准复现日志的快照所有权与缺省拓扑契约。"""

from app.backend.execution.run_state import ReproductionLog


def test_schedule_log_isolates_nested_payload_and_prefers_runtime_snapshot() -> None:
    """一次复制仍隔离逐片 Route，update 现场字段优先于设备初值。"""
    device = {"Stations": {"PM1": {"State": 0}}, "Robots": {"R": {"State": 0}}}
    update = {"Stations": {"PM1": {"State": 1}}, "Materials": [{"Route": {"Steps": [1]}}]}
    reproduction = ReproductionLog()
    reproduction.add_schedule(device, update, 10)
    update["Materials"][0]["Route"]["Steps"].append(2)
    update["Stations"]["PM1"]["State"] = 2
    device["Robots"]["R"]["State"] = 2
    entry = reproduction.entries[0]
    assert entry["Describe"] == "AlgSchedule" and entry["SimTime"] == 10
    assert entry["Info"]["Materials"][0]["Route"]["Steps"] == [1]
    assert entry["Info"]["Stations"]["PM1"]["State"] == 1
    assert entry["Info"]["Robots"]["R"]["State"] == 0
