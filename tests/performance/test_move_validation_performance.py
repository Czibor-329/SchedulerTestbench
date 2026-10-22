"""固定 Windows 环境的 75 片晶圆完整校验与推进门禁。

真实数据仅从 CT_MOVE_VALIDATION_FIXTURE 指向的隔离 JSON 副本读取；普通回归
显式跳过。夹具为公司 Dummy 场景的首轮：75 片产品、8 片 Dummy、3035 个 Move。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from app.backend.execution.algorithm_runtime import PlatformMoveListRuntime
from app.backend.execution.run_state import _planned_events, advance_platform_move_list_to_update
from app.backend.execution.runtime_snapshot import validation_sources_view
from app.backend.validation.move_validation import MoveStateReplay


PRODUCT_WAFER_COUNT = 75
DUMMY_WAFER_COUNT = 8
MOVE_COUNT = 3035
FIXTURE_SCHEMA_VERSION = 1
FIXTURE_SHA256 = "077501307d67085c81b6d24348db79a795fb83bc3c45782f2eb8340c87f16c80"
BUDGET_PATH = Path(__file__).parent / "config/budgets.json"


@pytest.mark.performance
@pytest.mark.dataset
def test_75_wafers_validation_and_advancement_maximum() -> None:
    """计入输入隔离、物理校验、通知生成和最终推进，30 次最大值不超过 100ms。"""
    if os.environ.get("CT_RUN_MOVE_VALIDATION_PERFORMANCE") != "1":
        pytest.skip("固定 Windows 性能机设置 CT_RUN_MOVE_VALIDATION_PERFORMANCE=1 后运行")
    fixture_path = Path(os.environ.get("CT_MOVE_VALIDATION_FIXTURE", ""))
    if not fixture_path.is_file():
        pytest.skip("需通过 CT_MOVE_VALIDATION_FIXTURE 提供已校验哈希的隔离真实夹具")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    canonical = json.dumps(fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == FIXTURE_SHA256
    assert fixture["schemaVersion"] == FIXTURE_SCHEMA_VERSION
    materials = fixture["update"]["Materials"]
    assert sum(bool(material.get("PJobName")) for material in materials) == PRODUCT_WAFER_COUNT
    assert sum(not material.get("PJobName") for material in materials) == DUMMY_WAFER_COUNT
    assert len(fixture["output"]["MoveList"]) == MOVE_COUNT
    budgets = json.loads(BUDGET_PATH.read_text(encoding="utf-8"))
    cutoff = max(move["EndTime"] for move in fixture["output"]["MoveList"]) + 1
    samples = []
    for index in range(budgets["warmupCount"] + budgets["sampleCount"]):
        started = time.perf_counter()
        runtime = PlatformMoveListRuntime(fixture["update"], fixture["output"], device=fixture["device"])
        notifications = advance_platform_move_list_to_update(runtime, cutoff)
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert len(notifications) == MOVE_COUNT * 2
        assert not runtime._tracker.running_move_ids
        if index >= budgets["warmupCount"]:
            samples.append(elapsed_ms)
    maximum_ms = max(samples)
    budget_ms = budgets["absoluteMilliseconds"]["validation75WafersMaximum"]
    assert maximum_ms <= budget_ms, f"75 片校验加推进最大 {maximum_ms:.2f}ms，预算 {budget_ms}ms"
    # 独立逐事件回放放在计时之外，核对真实 3035 条动作的最终物理快照。
    reference = MoveStateReplay(None, fixture["output"]["MoveList"], validation_sources_view(
        fixture["device"], fixture["update"],
    ))
    for _, _, notification in _planned_events(fixture["output"]["MoveList"]):
        reference.update_move_state(notification, snapshot=False)
    assert runtime.state == reference.state
    assert runtime.current_plan == reference.materialized_plan
