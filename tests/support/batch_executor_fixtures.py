"""提供批量取消测试的可控线程池，明确区分运行中与尚未启动的 Future。

本夹具不执行自动排队调度；指定索引才启动真实测试线程，其余 Future 保持可取消。
事件用于确认提交、停止和回调完成，不以 sleep 或墙钟推断业务状态。
"""

from __future__ import annotations

from concurrent.futures import Future
import threading
from typing import Any, Callable


class ControlledBatchExecutor:
    """仅启动指定测试索引的线程池，其他任务留在可取消队列。"""

    def __init__(self, test_count: int, running_indexes: tuple[int, ...] = (0,)) -> None:
        """记录预计提交数量与运行索引，并建立具名的完成与停止事件。"""
        self.test_count = test_count
        self.running_indexes = running_indexes
        self.futures: list[Future] = []
        self.threads: list[threading.Thread] = []
        self.finished = [threading.Event() for _ in range(test_count)]
        self.submitted = threading.Event()
        self.shutdown_called = threading.Event()

    def submit(self, operation: Callable[..., Any], *arguments: Any) -> Future:
        """启动声明的运行项；尚未启动项保留原生 Future 的取消语义。"""
        index = len(self.futures)
        future = Future()
        self.futures.append(future)

        if index in self.running_indexes:
            future.set_running_or_notify_cancel()

            def execute() -> None:
                """执行单项并在其 done callback 全部返回后标记完成。"""
                try:
                    future.set_result(operation(*arguments))
                except BaseException as error:
                    future.set_exception(error)
                finally:
                    self.finished[index].set()

            thread = threading.Thread(target=execute, name=f"controlled-test-{index}", daemon=True)
            self.threads.append(thread)
            thread.start()

        if len(self.futures) == self.test_count:
            self.submitted.set()
        return future

    def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
        """模拟线程池停止；运行项继续完成，排队项按调用参数取消。"""
        if cancel_futures:
            for future in self.futures:
                future.cancel()
        self.shutdown_called.set()
        if wait:
            for thread in self.threads:
                thread.join()
