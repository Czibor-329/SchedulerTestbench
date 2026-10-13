"""清洁产能模型：用隔离设备验证五种清洁、Job 边界和有限批次口径。"""
from copy import deepcopy
import pytest

from app.backend import theory_capacity_service as capacity
from app.backend.theory_capacity_presets import build_preset_device, read_theory_capacity_preset
from app.backend.theory_capacity_cleaning import (
    analyze_cleaning_batch, parse_cleaning_scenario, summarize_cleaning_moves,
)
from tests.support.theory_capacity_fixtures import cleaning_device_overview


@pytest.mark.parametrize("kind", ["single-node", "parallel", "serial", "mixed"])
@pytest.mark.parametrize("clean,task,visits", [
    ("preclean", "PreClean", 1), ("postclean", "PostClean", 1),
    ("wacclean", "WacClean", 1), ("dummy", "PreDummyClean", 2), ("dummywac", "PreWacClean", 2),
])
def test_cleaning_counts_follow_each_chamber_and_job(kind, clean, task, visits):
    """每腔每 Job 实际执行清洁；DummyWAC 每次带片后均有空腔段。"""
    device, route, port = build_preset_device(cleaning_device_overview(), kind, "shared-bidirectional")
    scenario = parse_cleaning_scenario({"clean": clean, "wafers": 6, "jobs": 2, "wac_interval": 2})
    report = analyze_cleaning_batch(device, route, port, 20, scenario)
    assert report["batch_wafers"] == 12
    assert report["physical_validation_passed"] is True
    assert report["physical_wafers_per_hour"] == pytest.approx(3600 * 12 / report["period_seconds"])
    rows = [row for row in report["cleaning_summary"] if row["task"] == task]
    counts = {row["station"]: row["count"] for row in rows}
    chambers = {visit["stationName"] for stage in route["stages"] if stage.get("needProcess") for visit in stage["visits"]}
    assert set(counts) == chambers
    for chamber, count in counts.items():
        candidates = next(len(stage["visits"]) for stage in route["stages"] if stage.get("needProcess") and any(v["stationName"] == chamber for v in stage["visits"]))
        expected = (6 // candidates // 2) * 2 if clean == "wacclean" else visits * 2
        assert count == expected
    if clean == "dummywac":
        assert {row["station"]: row["count"] for row in report["cleaning_summary"] if row["task"] == "WacClean"} == counts


def test_drain_repeats_complete_job_without_inventing_overlap():
    """排空模式完整相加，尾清洁超过最后产品回盒时仍计入分母。"""
    device, route, port = build_preset_device(cleaning_device_overview(), "single-node", "shared-bidirectional")
    values = {"clean": "postclean", "post_seconds": 500, "wafers": 5, "alternation": "drain"}
    single = analyze_cleaning_batch(device, route, port, 20, parse_cleaning_scenario(values))
    three = analyze_cleaning_batch(device, route, port, 20, parse_cleaning_scenario({**values, "jobs": 3}))
    assert single["period_seconds"] > single["last_product_return_seconds"]
    assert three["period_seconds"] == 3 * single["period_seconds"]
    assert three["physical_wafers_per_hour"] == pytest.approx(single["physical_wafers_per_hour"])
    assert three["cleaning_summary"][0]["count"] == 3


def test_wac_does_not_merge_partial_counts_across_jobs():
    """两个 Job 各不足阈值，不能按总片数误加 WAC。"""
    device, route, port = build_preset_device(cleaning_device_overview(), "single-node", "shared-bidirectional")
    scenario = parse_cleaning_scenario({"clean": "wacclean", "wafers": 3, "jobs": 2, "wac_interval": 5})
    report = analyze_cleaning_batch(device, route, port, 20, scenario)
    assert report["cleaning_summary"] == []


@pytest.mark.parametrize("values", [{"wafers": 0}, {"jobs": 1.5}, {"wac_interval": "NaN"},
    {"clean": "unknown"}, {"clean": "dummy,preclean"}, {"alternation": "random"}, {"dummy_inventory": True}])
def test_invalid_parameters_are_not_silently_coerced(values):
    """非法片数、清洁类型及交替方式均明确拒绝。"""
    with pytest.raises(ValueError):
        parse_cleaning_scenario(values)


def test_continuous_jobs_require_real_ports():
    """连续交接不复制物理端口或扩大槽位容量。"""
    device, route, port = build_preset_device(cleaning_device_overview(), "single-node", "shared-bidirectional")
    with pytest.raises(ValueError, match="LoadPort"):
        analyze_cleaning_batch(device, route, port, 20, parse_cleaning_scenario({"jobs": 3}))


def test_api_compares_same_batch_and_preserves_device(monkeypatch):
    """曲线单点一致，清洁损失对比同批次，设备与已有路线不受修改。"""
    device = cleaning_device_overview()
    original = deepcopy(device)
    monkeypatch.setattr(capacity, "get_workspace_device_overview", lambda _: device)
    report = read_theory_capacity_preset("test", first_seconds=10, last_seconds=30, step_seconds=10,
        process_seconds=20, cleaning_values={"mode": "batch", "clean": "preclean,postclean,wacclean", "wafers": 6})
    detail = report["derivation"]
    assert detail["model_kind"] == "cycle-batch"
    assert detail["batch_wafers"] == 6
    assert detail["physical_wafers_per_hour"] == report["points"]["physical_wafers_per_hour"][1]
    assert detail["baseline_wafers_per_hour"] == report["baseline_points"]["physical_wafers_per_hour"][1]
    assert "theory_points" in report
    assert device == original


def test_missing_product_return_is_not_reported_as_capacity():
    """有耗时的残缺 MoveList 不能被当作有效产能。"""
    with pytest.raises(ValueError, match="回原槽"):
        summarize_cleaning_moves([{"MoveType": 9, "StartTime": 0, "EndTime": 20}], {1: ("LP1", 1)}, {})


@pytest.mark.parametrize("clean,parameter", [
    ("preclean", "pre_seconds"), ("postclean", "post_seconds"),
    ("wacclean", "wac_seconds"), ("dummy", "dummy_seconds"), ("dummywac", "wac_seconds"),
])
def test_cleaning_duration_changes_only_cleaned_curve(monkeypatch, clean, parameter):
    """滑块改时长只影响清洁排程；同设备同批次无清洁曲线和单点保持一致。"""
    device = cleaning_device_overview()
    monkeypatch.setattr(capacity, "get_workspace_device_overview", lambda _: device)
    values = {"mode": "batch", "clean": clean, "wafers": 25}
    short = read_theory_capacity_preset("test", first_seconds=10, last_seconds=30, step_seconds=10,
        process_seconds=20, cleaning_values={**values, parameter: 30})
    long = read_theory_capacity_preset("test", first_seconds=10, last_seconds=30, step_seconds=10,
        process_seconds=20, cleaning_values={**values, parameter: 300})
    assert short["baseline_points"] == long["baseline_points"]
    assert short["derivation"]["baseline_seconds"] == long["derivation"]["baseline_seconds"]
    assert short["points"] != long["points"]
    assert long["derivation"]["physical_validation_passed"] is True
    assert long["derivation"]["batch_wafers"] == 25


@pytest.mark.parametrize("clean", ["preclean", "postclean", "wacclean", "dummy", "dummywac"])
def test_zero_seconds_remains_a_valid_cleaning_scenario(clean):
    """页面零秒档仍使用真实清洁义务；保留瞬时动作，Dummy 搬运仍计入耗时。"""
    device, route, port = build_preset_device(cleaning_device_overview(), "single-node", "shared-bidirectional")
    values = {"clean": clean, "pre_seconds": 0, "post_seconds": 0, "wac_seconds": 0,
              "dummy_seconds": 0, "wac_interval": 2, "dummy_visits": 2, "dummy_inventory": 3}
    report = analyze_cleaning_batch(device, route, port, 20, parse_cleaning_scenario(values))
    assert report["physical_validation_passed"] is True
    assert report["batch_wafers"] == 25
    assert report["cleaning_summary"]
    assert all(row["seconds"] == 0 for row in report["cleaning_summary"])
