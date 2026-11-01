"""批量测试阶段进度：隔离每个 worker 的上下文，向父进程共享轻量快照，不影响执行结果。"""
from contextvars import ContextVar

ALGORITHM_PROGRESS = 80
VALIDATION_PROGRESS = 80
SAVE_PROGRESS = 95
_context = ContextVar("batch_execution_progress", default=None)


def bind_batch_progress(store, index, strategy):
    """绑定当前测试的共享字典、索引与所选策略，返回用于恢复上下文的 token。"""
    return _context.set((store, index, strategy) if store is not None else None)


def reset_batch_progress(token):
    """释放当前测试上下文，防止线程池下一项继承旧测试。"""
    _context.reset(token)


def report_batch_progress(stage, completed=0, total=1, strategy=None):
    """发布真实阶段与重算计数；百分比为阶段估算，共享通道关闭时不影响业务执行。"""
    context = _context.get()
    if context is None:
        return
    store, index, selected = context
    if strategy is not None and strategy != selected:
        return
    total = max(1, total, completed)
    percent = {"recomputing": int(ALGORITHM_PROGRESS * completed / total),
               "validating": VALIDATION_PROGRESS, "saving": SAVE_PROGRESS}[stage]
    try:
        previous = store.get(index, {})
        store[index] = {"progress": max(previous.get("progress", 0), percent),
                        "progressStage": stage,
                        "completedRecomputes": max(previous.get("completedRecomputes", 0), completed),
                        "expectedRecomputes": max(previous.get("expectedRecomputes", 1), total)}
    except (OSError, EOFError):
        pass
