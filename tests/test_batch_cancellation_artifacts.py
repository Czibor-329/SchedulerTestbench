"""批量取消的制品归属与计数契约，覆盖排队取消和迟到的运行结果。

执行与存储使用最小领域夹具，状态容器完全隔离；事件和原生 Future 控制并发顺序。
不读取设备主数据，不写入真实 exports，不用 sleep 或墙钟断言判断业务结果。
"""

from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import wait as wait_for_futures
from dataclasses import replace
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.backend import wiring
from app.backend.execution import batch_service
from tests.support.batch_executor_fixtures import ControlledBatchExecutor
from tests.support.run_artifact_fixtures import saved_run_fields


EVENT_GUARD_SECONDS = 3


def _minimal_batch_device(test_count: int) -> dict:
    """构造只表达测试身份的设备，计划与算法由独立的边界桩提供。"""
    return {
        "id": "device-one", "name": "设备一", "device": {"Stations": {}, "Robots": {}},
        "tests": [
            {"id": f"test-{index}", "name": f"测试 {index}", "group": "回归"}
            for index in range(test_count)
        ],
    }


def _successful_execution() -> tuple[dict, dict, None]:
    """返回成功且已有累计输出的算法结果，供取消后的收尾路径使用。"""
    return ({
        "output": {"MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 2}]},
        "reproductionLog": [], "logs": ["算法完成"], "updates": [{}],
        "cpuTimeMs": 1, "totalElapsedMs": 2, "makespan": 2,
        "moveCount": 1, "validation": "passed",
    }, {"status": "skipped"}, None)


@pytest.fixture
def isolated_batch_state(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """隔离批次字典、取消事件和保存回调，记录每个测试的最终制品内容。"""
    saved: list[dict] = []

    def save(output, reproduction, summary, **options):
        """用不同运行 ID 记录制品，保留日志与无输出语义但不访问磁盘。"""
        artifact_id = f"artifact-{summary['testId']}"
        saved.append({
            "output": output, "reproduction": reproduction, "summary": dict(summary),
            "executionLogs": options.get("execution_logs"),
        })
        return saved_run_fields(artifact_id, has_output=output is not None)

    dependencies = replace(
        wiring.build_batch_service_dependencies(),
        save_run_artifacts=save,
        batch_runs=OrderedDict(), batch_runs_lock=threading.RLock(), batch_cancel_events={},
    )
    monkeypatch.setattr(batch_service, "_DEPENDENCIES", dependencies)
    monkeypatch.setattr(batch_service, "_BATCH_RUNS", dependencies.batch_runs)
    monkeypatch.setattr(batch_service, "_BATCH_RUNS_LOCK", dependencies.batch_runs_lock)
    monkeypatch.setattr(batch_service, "_BATCH_CANCEL_EVENTS", dependencies.batch_cancel_events)
    return saved


def test_cancelled_cards_receive_queued_and_late_running_artifact_urls(
    monkeypatch: pytest.MonkeyPatch, isolated_batch_state: list[dict],
) -> None:
    """取消先冻结卡片状态，随后保存的运行项与排队项日志仍能从卡片定位。"""
    device = _minimal_batch_device(2)
    executor = ControlledBatchExecutor(2)
    algorithm_started = threading.Event()
    algorithm_release = threading.Event()
    background_threads: list[threading.Thread] = []

    def run_algorithm(*_arguments, **_options):
        """保证取消发生于算法运行中，释放后才交回原本成功的结果。"""
        algorithm_started.set()
        assert algorithm_release.wait(EVENT_GUARD_SECONDS), "测试必须显式释放算法"
        return _successful_execution()

    def background_thread(**options):
        """记录真实批次后台线程，测试结束前必须确认它已退出。"""
        thread = threading.Thread(**options)
        background_threads.append(thread)
        return thread

    monkeypatch.setattr(batch_service, "threading", SimpleNamespace(Event=threading.Event, Thread=background_thread))
    monkeypatch.setattr(batch_service, "ThreadPoolExecutor", lambda **_options: executor)
    monkeypatch.setattr(batch_service, "get_workspace_batch_run_context", lambda *_arguments: device)
    monkeypatch.setattr(batch_service, "build_workspace_batch_plan", lambda *_arguments, **_options: {"device": device["device"]})
    monkeypatch.setattr(batch_service, "_execute_workspace_test_with_baseline", run_algorithm)

    try:
        initial = batch_service.start_workspace_test_batch(
            device["id"], "回归", "heuristic", {}, maximum_workers=1,
            hongye_check=False, skip_baseline=True,
        )
        assert executor.submitted.wait(EVENT_GUARD_SECONDS), "应已提交全部测试"
        assert algorithm_started.wait(EVENT_GUARD_SECONDS), "第一项应处于运行中"
        cancelled = batch_service.cancel_workspace_batch_run(initial["batchId"])
        assert [item["status"] for item in cancelled["items"]] == ["cancelled", "cancelled"]
        assert cancelled["cancelled"] == 2
        assert executor.shutdown_called.wait(EVENT_GUARD_SECONDS), "排队 Future 应先取消并归档"

        queued_card = batch_service.read_workspace_batch_run(initial["batchId"])["items"][1]
        assert executor.futures[1].cancelled()
        assert queued_card["artifactId"] == "artifact-test-1"
        assert queued_card["logUrl"] == "/api/logs/artifact-test-1"
        assert queued_card["summaryUrl"] == "/api/artifacts/artifact-test-1/summary"
        assert queued_card["status"] == "cancelled"
        assert "resultUrl" not in queued_card

        algorithm_release.set()
        assert executor.finished[0].wait(EVENT_GUARD_SECONDS), "迟到保存和 done callback 必须完成"
        final = batch_service.read_workspace_batch_run(initial["batchId"])
        assert final["status"] == "cancelled"
        assert final["failed"] == 0
        assert final["cancelled"] == 2
        assert [item["status"] for item in final["items"]] == ["cancelled", "cancelled"]
        assert final["items"][0]["logUrl"] == "/api/logs/artifact-test-0"
        assert final["items"][0]["summaryUrl"] == "/api/artifacts/artifact-test-0/summary"
        assert len(isolated_batch_state) == 2, "每个测试恰好保存一次"
        assert {record["summary"]["testId"] for record in isolated_batch_state} == {"test-0", "test-1"}
        assert all(record["summary"]["status"] == "cancelled" for record in isolated_batch_state)
        assert all(record["executionLogs"] == ["用户终止调度"] for record in isolated_batch_state)
    finally:
        algorithm_release.set()
        for thread in [*executor.threads, *background_threads]:
            thread.join(EVENT_GUARD_SECONDS)
            assert not thread.is_alive(), "测试不得遗留会访问已恢复状态的后台线程"


def test_queued_cancellations_are_saved_and_not_counted_as_failures(
    monkeypatch: pytest.MonkeyPatch, isolated_batch_state: list[dict],
) -> None:
    """全部任务仍排队时取消，保存各项日志且按状态统计取消而非失败。"""
    device = _minimal_batch_device(3)
    executor = ControlledBatchExecutor(3, running_indexes=())
    cancel_event = threading.Event()
    cancel_event.set()
    build_plan = Mock(side_effect=AssertionError("排队取消不能开始构建计划"))
    monkeypatch.setattr(batch_service, "ThreadPoolExecutor", lambda **_options: executor)
    monkeypatch.setattr(batch_service, "build_workspace_batch_plan", build_plan)

    result = batch_service._execute_workspace_test_batch(
        device, device["tests"], "回归", "heuristic", {}, maximum_workers=1,
        hongye_check=False, skip_baseline=True, cancel_event=cancel_event,
    )

    assert result["status"] == "cancelled"
    assert result["testCount"] == 3
    assert result["completed"] == 3
    assert result["succeeded"] == result["failed"] == 0
    assert result["cancelled"] == 3
    assert len(result["items"]) == len(isolated_batch_state) == 3
    assert all(item["status"] == "cancelled" and "logUrl" in item for item in result["items"])
    assert all(future.cancelled() for future in executor.futures)
    assert all(record["executionLogs"] == ["用户终止调度"] for record in isolated_batch_state)
    build_plan.assert_not_called()


def test_cancellation_at_final_completion_boundary_keeps_cancelled_batch_status(
    monkeypatch: pytest.MonkeyPatch, isolated_batch_state: list[dict],
) -> None:
    """取消恰好到达最后一个 Future 完成边界时，批次状态仍须采信取消事件。"""
    device = _minimal_batch_device(2)
    executor = ControlledBatchExecutor(2, running_indexes=(0, 1))
    cancel_event = threading.Event()

    def cancel_when_queue_finishes(futures, **options):
        """在最后一批完成结果交还编排器时取消，精确控制竞态边界。"""
        done, pending = wait_for_futures(futures, **options)
        if not pending:
            cancel_event.set()
        return done, pending

    monkeypatch.setattr(batch_service, "ThreadPoolExecutor", lambda **_options: executor)
    monkeypatch.setattr(batch_service, "wait", cancel_when_queue_finishes)
    monkeypatch.setattr(batch_service, "build_workspace_batch_plan", lambda *_arguments, **_options: {"device": device["device"]})
    monkeypatch.setattr(batch_service, "_execute_workspace_test_with_baseline", lambda *_arguments, **_options: _successful_execution())

    result = batch_service._execute_workspace_test_batch(
        device, device["tests"], "回归", "heuristic", {}, maximum_workers=2,
        hongye_check=False, skip_baseline=True, cancel_event=cancel_event,
    )

    assert cancel_event.is_set()
    assert result["status"] == "cancelled"
    assert result["ok"] is False
    assert result["succeeded"] == result["testCount"] == 2
    assert result["cancelled"] == result["failed"] == 0
    assert len(isolated_batch_state) == 2
