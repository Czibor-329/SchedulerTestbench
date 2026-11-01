"""批量阶段进度契约：上下文隔离、单调计数与 spawn 进程通信，不依赖设备数据。"""
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

from app.backend.execution.batch_progress import (
    bind_batch_progress, reset_batch_progress, report_batch_progress,
)


def test_stage_progress_is_monotonic_and_ignores_baseline():
    """重算计数与保存阶段推进圆环，Baseline 和释放后的上下文不能污染所选测试。"""
    store = {}
    token = bind_batch_progress(store, 0, "cycle")
    try:
        report_batch_progress("recomputing", 2, 4, strategy="cycle")
        assert store[0]["progress"] == 40
        report_batch_progress("recomputing", 1, 4, strategy="heuristic")
        assert store[0]["completedRecomputes"] == 2
        report_batch_progress("validating", strategy="cycle")
        assert store[0]["progress"] == 80
        report_batch_progress("saving", strategy="cycle")
        assert store[0]["progress"] == 95
        assert store[0]["completedRecomputes"] == 2
    finally:
        reset_batch_progress(token)
    report_batch_progress("recomputing", 4, 4)
    assert store[0]["progress"] == 95


def test_spawn_worker_publishes_progress_before_returning():
    """真实 spawn worker 通过共享字典发布重算快照，父进程无需等待整批结束。"""
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        store = manager.dict()
        with ProcessPoolExecutor(max_workers=1, mp_context=context,
                                 initializer=bind_batch_progress, initargs=(store, 7, "cycle")) as executor:
            executor.submit(report_batch_progress, "recomputing", 1, 2, "cycle").result(timeout=15)
            assert store[7]["completedRecomputes"] == 1
            assert store[7]["progress"] == 40
