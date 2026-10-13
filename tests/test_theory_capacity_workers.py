"""理论产能并行扫描：验证结果一致、缓存隔离、失败重试和重复请求复用。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.backend import theory_capacity_workers as workers
from app.backend.theory_capacity_cleaning import analyze_cleaning_batch, parse_cleaning_scenario
from app.backend.theory_capacity_presets import build_preset_device, read_theory_capacity_preset
from app.backend import theory_capacity_service as capacity
from tests.support.theory_capacity_fixtures import cleaning_device_overview


def test_completed_requests_reuse_results_without_sharing_mutable_responses(monkeypatch):
    """重复点只求解一次；设备计划或配置变化失效，调用者不能污染缓存摘要。"""
    calls = []

    def calculate(*key):
        """记录实际求解边界，返回可变摘要用于副本隔离验证。"""
        calls.append(key)
        return {"rates": [1]}

    monkeypatch.setattr(workers, "_calculate_batch", calculate)
    pool = workers.CapacityBatchWorkers(lambda: ThreadPoolExecutor(max_workers=2), cache_size=2)
    key = ("plan-a", 1, "config-a")
    try:
        result = pool.solve([key, key])
        result[0]["rates"].append(2)
        assert result[1] == pool.solve([key])[0] == {"rates": [1]}
        assert calls == [key]
        pool.solve([("plan-b", 1, "config-a"), ("plan-b", 1, "config-b")])
        pool.solve([key])
        assert len(calls) == 4  # 有界缓存淘汰旧条目后重新求解。
    finally:
        pool.close()


def test_failed_calculation_can_retry(monkeypatch):
    """失败不能变成永久缓存，后续有效请求可以恢复。"""
    attempts = []

    def calculate(*key):
        """首次模拟求解失败，后续返回有效摘要。"""
        attempts.append(key)
        if len(attempts) == 1:
            raise ValueError("invalid batch")
        return {"rate": 2}

    monkeypatch.setattr(workers, "_calculate_batch", calculate)
    pool = workers.CapacityBatchWorkers(lambda: ThreadPoolExecutor(max_workers=2))
    try:
        with pytest.raises(ValueError, match="invalid batch"):
            pool.solve([("plan", 1, "config")])
        assert pool.solve([("plan", 1, "config")]) == [{"rate": 2}]
        assert len(attempts) == 2
    finally:
        pool.close()


@pytest.mark.parametrize("clean", ["preclean", "postclean", "wacclean", "dummy", "dummywac"])
def test_parallel_scan_matches_serial_validated_batches(monkeypatch, clean):
    """五种清洁保持原始逐点精度及物理校验，不通过抽稀或近似曲线提速。"""
    overview = cleaning_device_overview()
    monkeypatch.setattr(capacity, "get_workspace_device_overview", lambda _: overview)
    values = {"mode": "batch", "clean": clean, "wafers": 6, "dummy_visits": 1, "wac_interval": 2}
    report = read_theory_capacity_preset("test", first_seconds=10, last_seconds=30, step_seconds=10,
                                         process_seconds=20, cleaning_values=values)
    device, route, port = build_preset_device(overview, "single-node", "shared-bidirectional")
    scenario = parse_cleaning_scenario(values)
    for index, seconds in enumerate(report["points"]["process_seconds"]):
        serial = analyze_cleaning_batch(device, route, port, seconds, scenario)
        assert report["points"]["physical_wafers_per_hour"][index] == round(serial["physical_wafers_per_hour"], 3)
        assert serial["physical_validation_passed"] is True
    assert report["derivation"]["physical_validation_passed"] is True
