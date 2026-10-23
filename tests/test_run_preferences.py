"""本地运行设置偏好的格式、校验与持久化回归测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backend.preferences.repository import (
    RUN_PREFERENCES_SCHEMA_VERSION,
    read_analysis_preferences,
    read_run_preferences,
    update_analysis_preferences,
    update_run_preferences,
)


def _settings(**overrides) -> dict:
    """构造一份完整有效的运行设置，并允许覆盖目标字段。"""
    value = {
        "hongYeCheck": True,
        "skipBaseline": True,
        "executionTimingEnabled": False,
        "maximumWorkers": 4,
        "validationWorkers": 2,
        "cleanValidationTypes": ["preclean", "postclean", "wacclean", "dummy", "dummywac"],
    }
    value.update(overrides)
    return value


def _analysis_settings(**overrides) -> dict:
    """构造一份完整有效的个人结果分析设置。"""
    value = {
        "metricIds": [
            "throughput", "average_recompute_time", "company_capacity_baseline",
            "company_capacity_ratio", "bottleneck_candidates", "makespan",
            "validation", "cpu_time", "resource_utilization", "departure_interval_cv",
            "process_chamber_dwell", "robot_wafer_dwell", "system_residence",
            "system_residence_cv", "loadlock_wafers_per_cycle",
            "loadlock_full_cycle_ratio", "loadlock_empty_cycle_ratio",
        ],
        "windowMode": "steady",
        "timeBudgetSeconds": 120,
    }
    value.update(overrides)
    return value


def test_preferences_round_trip_keeps_run_and_analysis_settings_separate(tmp_path):
    """缺省读取无写入，两类设置分别保存，更新分析偏好不覆盖运行设置。"""
    path = tmp_path / "preferences.json"
    assert read_run_preferences(path) == _settings()
    assert read_analysis_preferences(path) == _analysis_settings() and not path.exists()
    run = _settings(maximumWorkers=30, validationWorkers=15)
    assert update_run_preferences(run, path) == run
    assert json.loads(path.read_text(encoding="utf-8")) == {"schemaVersion": RUN_PREFERENCES_SCHEMA_VERSION, "runSettings": run, "analysisSettings": _analysis_settings()}
    analysis = _analysis_settings(metricIds=["throughput", "makespan"], windowMode="full", timeBudgetSeconds=300)
    assert update_analysis_preferences(analysis, path) == analysis
    assert read_analysis_preferences(path) == analysis and read_run_preferences(path) == run


def test_invalid_preferences_never_modify_saved_settings(tmp_path):
    """分别选取范围、类型和枚举错误；所有失败写入都保留原文件。"""
    path = tmp_path / "preferences.json"
    update_run_preferences(_settings(), path)
    original = path.read_bytes()
    cases = [
        (update_run_preferences, _settings(maximumWorkers=31), "maximumWorkers"),
        (update_run_preferences, _settings(validationWorkers=16), "validationWorkers"),
        (update_run_preferences, _settings(hongYeCheck=1), "布尔值"),
        (update_run_preferences, _settings(cleanValidationTypes=["unknown"]), "cleanValidationTypes"),
        (update_analysis_preferences, _analysis_settings(metricIds=["unknown"]), "metricIds"),
        (update_analysis_preferences, _analysis_settings(windowMode="recent"), "windowMode"),
        (update_analysis_preferences, _analysis_settings(timeBudgetSeconds=60), "timeBudgetSeconds"),
    ]
    for save, value, field in cases:
        with pytest.raises(ValueError, match=field):
            save(value, path)
        assert path.read_bytes() == original, field


def test_newer_preferences_are_rejected_without_overwriting_file(tmp_path):
    """未知高版本不能被旧服务静默重写。"""
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"schemaVersion": RUN_PREFERENCES_SCHEMA_VERSION + 1, "runSettings": _settings()}), encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(ValueError, match="版本过新"):
        read_run_preferences(path)
    assert path.read_bytes() == original


def test_released_preference_migrations_keep_values_backups_and_idempotence(tmp_path):
    """各已发布版本各保留一个代表输入，共用备份、用户值与幂等检查。"""
    old_v1 = _settings()
    old_v1.pop("cleanValidationTypes")
    old_v2 = _settings()
    old_v2.pop("executionTimingEnabled")
    v3 = json.loads((Path(__file__).parent / "fixtures/run_preferences_v3.json").read_text(encoding="utf-8"))
    cases = [
        ({"schemaVersion": 1, "runSettings": old_v1}, _settings(), _analysis_settings()),
        ({"schemaVersion": 2, "runSettings": old_v2}, _settings(), _analysis_settings()),
        (v3, _settings(**{key: value for key, value in v3["runSettings"].items() if key in _settings()}), _analysis_settings()),
        ({"schemaVersion": 4, "runSettings": {**_settings(executionTimingEnabled=True), "compatibilityMode": True}, "analysisSettings": _analysis_settings(windowMode="full")}, _settings(executionTimingEnabled=True), _analysis_settings(windowMode="full")),
        ({"schemaVersion": 5, "runSettings": _settings(), "analysisSettings": {"metricIds": ["average_recompute_time", "throughput", "company_capacity_baseline", "company_capacity_improvement", "baseline_improvement"], "windowMode": "steady", "timeBudgetSeconds": 300}}, _settings(), _analysis_settings(metricIds=["throughput", "average_recompute_time", "company_capacity_baseline", "company_capacity_ratio"], timeBudgetSeconds=300)),
    ]
    for original, run, analysis in cases:
        version = original["schemaVersion"]
        directory = tmp_path / str(version)
        directory.mkdir()
        path = directory / "preferences.json"
        path.write_text(json.dumps(original), encoding="utf-8")
        assert read_run_preferences(path) == run, version
        assert read_analysis_preferences(path) == analysis, version
        assert json.loads(path.read_text(encoding="utf-8"))["schemaVersion"] == RUN_PREFERENCES_SCHEMA_VERSION
        backup = path.with_suffix(f".json.v{version}.bak")
        assert json.loads(backup.read_text(encoding="utf-8")) == original
        current_bytes, backup_bytes = path.read_bytes(), backup.read_bytes()
        assert read_run_preferences(path) == run
        assert path.read_bytes() == current_bytes and backup.read_bytes() == backup_bytes
