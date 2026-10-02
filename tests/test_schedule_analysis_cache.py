"""单结果分析缓存的复用、口径隔离与保留期测试。

采用最小加工动作与临时运行目录，验证二次打开不再读取/分析 MoveList；不使用墙钟
预算，也不依赖公司测试数据或浏览器渲染。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.backend import schedule_analysis_service as service
from app.backend.artifacts import repository, run_artifacts


@pytest.fixture
def analysis_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    """保存含冻结回放上下文的最小运行，并按所属模块隔离新旧导出路径。"""
    root = tmp_path / "exports"
    for module in (run_artifacts, repository):
        monkeypatch.setattr(module, "EXPORT_DIR", root)
        monkeypatch.setattr(module, "RUN_EXPORT_DIR", root / "runs")
    monkeypatch.setattr(repository, "RESULT_EXPORT_DIR", root / "results")
    monkeypatch.setattr(repository, "LOG_EXPORT_DIR", root / "logs")
    output = {
        "MoveList": [{"MoveType": 9, "ModuleName": "PM1", "StartTime": 0, "EndTime": 10}],
        "RunMetricsMetadata": {"cpuTimeMs": 4, "recomputeCount": 2},
        "ReplayContext": {"plan": {"device": {}}, "updates": []},
    }
    fields = repository.save_run_artifacts(output, [], {"status": "succeeded"})
    return fields["resultId"], root / "runs" / fields["resultId"]


def test_schedule_analysis_cache_hit_skips_result_read_and_calculation(
    analysis_run: tuple[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同口径缓存落盘后再次打开不读取大结果，且保持 CPU 与重算统计契约。"""
    result_id, directory = analysis_run
    payload = {"resultId": result_id, "windowMode": "full", "metricGroups": ["basic"]}
    response = service.analyze_schedule_request(payload)
    assert response["analysis"]["cpuTimeMs"] == 4
    assert response["analysis"]["averageRecomputeTimeMs"] == 2
    assert len(list((directory / "analysis").glob("*.json"))) == 1
    monkeypatch.setattr(service, "read_result", Mock(side_effect=AssertionError("不应再读取 MoveList")))
    monkeypatch.setattr(service, "analyze_schedule_performance", Mock(side_effect=AssertionError("不应重复分析")))
    assert service.analyze_schedule_request(payload) == response


def test_schedule_analysis_cache_separates_window_metrics_and_device(
    analysis_run: tuple[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设备、工序上下文、窗口和指标改变时不复用旧统计结果。"""
    result_id, directory = analysis_run
    calculate = Mock(wraps=service.analyze_schedule_performance)
    monkeypatch.setattr(service, "analyze_schedule_performance", calculate)
    base = {"resultId": result_id, "windowMode": "full", "metricGroups": ["basic"]}
    variants = [base, {**base, "windowMode": "steady"}, {**base, "metricGroups": ["basic", "resources"]},
                {**base, "device": {"Stations": {}, "Robots": {}}}, {**base, "context": {"fixture": True}}]
    for payload in variants:
        service.analyze_schedule_request(payload)
    assert calculate.call_count == len(variants)
    assert len(list((directory / "analysis").glob("*.json"))) == len(variants)


def test_schedule_analysis_cache_corruption_recomputes_without_touching_output(
    analysis_run: tuple[str, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """派生缓存损坏可重建，完整输出和 manifest 保留期时间保持不变。"""
    result_id, directory = analysis_run
    payload = {"resultId": result_id, "metricGroups": ["basic"]}
    original_manifest = (directory / "manifest.json").read_bytes()
    original_output = (directory / "movelist.json").read_bytes()
    expected = service.analyze_schedule_request(payload)
    cache_path = next((directory / "analysis").glob("*.json"))
    cache_path.write_text("broken", encoding="utf-8")
    calculate = Mock(wraps=service.analyze_schedule_performance)
    monkeypatch.setattr(service, "analyze_schedule_performance", calculate)
    assert service.analyze_schedule_request(payload) == expected
    assert calculate.call_count == 1
    assert (directory / "manifest.json").read_bytes() == original_manifest
    assert (directory / "movelist.json").read_bytes() == original_output
    assert json.loads(cache_path.read_text(encoding="utf-8"))["schemaVersion"] == 1


def test_schedule_analysis_cache_expiry_does_not_recreate_run(
    analysis_run: tuple[str, Path],
) -> None:
    """缓存命中不续期；清理运行目录后旧请求不能从缓存复活结果。"""
    result_id, directory = analysis_run
    payload = {"resultId": result_id, "metricGroups": ["basic"]}
    service.analyze_schedule_request(payload)
    repository.remove_expired_artifacts(maximum_age_seconds=0)
    with pytest.raises(ValueError, match="结果不存在或已过期"):
        service.analyze_schedule_request(payload)
    assert not directory.exists()


def test_schedule_analysis_cache_rejects_invalid_context_before_hit(analysis_run: tuple[str, Path]) -> None:
    """已有有效缓存不能掩盖当前请求中的设备或工序类型错误。"""
    result_id, _ = analysis_run
    service.analyze_schedule_request({"resultId": result_id, "metricGroups": ["basic"]})
    with pytest.raises(ValueError, match="device 必须"):
        service.analyze_schedule_request({"resultId": result_id, "device": []})
