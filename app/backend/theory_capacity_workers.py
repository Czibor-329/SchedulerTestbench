"""理论产能扫描的进程池与有界结果缓存。

独立扫描点在最多四个子进程中执行完整 Cycle 排程及物理校验；相同计划和
配置共享 Future，避免并发请求重复计算。只在内存中缓存，失败可重试，退出时回收。
"""
import atexit
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import multiprocessing
import os
from threading import Lock

MAX_CAPACITY_WORKERS = 4
MAX_CACHED_BATCHES = 512


def _calculate_batch(plan_json, multiplier, configuration):
    """子进程入口：载入平台编译边界，返回经过完整物理校验的批次摘要。"""
    import json
    if '_capacity_periodic' in json.loads(plan_json):
        from app.backend.theory_capacity_periodic import solve_periodic_plan
        try:
            return solve_periodic_plan(plan_json)
        except Exception as error:
            # 带复现日志的业务异常不一定支持 pickle，不能使整个进程池失效。
            raise ValueError(str(error)) from None
    from app.backend.theory_capacity_cleaning import _solve_batch
    return _solve_batch(plan_json, multiplier, configuration)


class CapacityBatchWorkers:
    """持有扫描专用执行器与缓存；构造不启动进程，solve 才按需创建执行器。"""

    def __init__(self, executor_factory=None, cache_size=MAX_CACHED_BATCHES):
        """允许测试注入隔离执行器；cache_size 限制已完成摘要与在途请求的保留量。"""
        self._executor_factory = executor_factory or self._create_executor
        self._executor = None
        self._cache = OrderedDict()
        self._lock = Lock()
        self._cache_size = cache_size

    @staticmethod
    def _create_executor():
        """创建受限的 Windows spawn 进程池，绕过 CPU 密集型排程的线程锁限制。"""
        return ProcessPoolExecutor(max_workers=min(MAX_CAPACITY_WORKERS, os.cpu_count() or 1),
                                   mp_context=multiprocessing.get_context("spawn"))

    def solve(self, requests):
        """并行求解 (计划 JSON, 批次倍数, 配置文本) 列表，按输入顺序返回独立副本。

        缓存键含完整设备、任务、清洁和 Cycle 配置；异常不会作为有效结果缓存。
        """
        pending = []
        with self._lock:
            if self._executor is None:
                self._executor = self._executor_factory()
            for key in requests:
                future = self._cache.get(key)
                if future is None or (future.done() and future.exception() is not None):
                    future = self._executor.submit(_calculate_batch, *key)
                    self._cache[key] = future
                self._cache.move_to_end(key)
                pending.append(future)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return [deepcopy(future.result()) for future in pending]

    def close(self):
        """清空内存缓存并回收执行器；未开始的排程取消，运行中的排程正常结束。"""
        with self._lock:
            executor, self._executor = self._executor, None
            self._cache.clear()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


capacity_batch_workers = CapacityBatchWorkers()
atexit.register(capacity_batch_workers.close)
