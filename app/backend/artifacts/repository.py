"""运行制品的公共访问、临时数据清理、批量日志下载与 Baseline 持久化。

结构化运行目录由 run_artifacts 拥有；本模块协调旧平铺制品的兼容
读取和统一保留期，不参与算法执行、回放指标计算或设备交换包编码。
"""

from __future__ import annotations

from app.backend.bootstrap import *
from app.backend.time_utils import _workspace_timestamp
from app.backend.execution.run_state import *
from app.backend.execution.service import execute_plan
from app.backend.workspace.repository import *
from app.backend.workspace.catalog_service import *
from app.backend.artifacts.run_artifacts import (
    clear_run_artifacts,
    read_run_manifest,
    read_run_output,
    read_run_replay_context,
    read_run_reproduction_log,
    read_run_summary,
    remove_expired_run_artifacts,
    save_run_artifacts as _save_run_artifacts,
)


def _shared_export_root() -> bool:
    """限定结构化清理与旧平铺目录属于同一导出根，支持隔离的旧接口测试。"""
    return RESULT_EXPORT_DIR.parent == LOG_EXPORT_DIR.parent == RUN_EXPORT_DIR.parent == EXPORT_DIR


def remove_expired_artifacts(
    *,
    maximum_age_seconds: Optional[float] = None,
    current_time: Optional[float] = None,
) -> Dict[str, int]:
    """删除超过保留期的临时结果和复现日志。

    ``maximum_age_seconds`` 默认使用平台的制品保留期，``current_time`` 仅用于
    测试时固定当前时间。返回结果和日志各自删除的文件数量；仍在保留期内的文件
    及其内存缓存不受影响。
    """
    retention_seconds = (
        ARTIFACT_RETENTION_SECONDS
        if maximum_age_seconds is None
        else max(0.0, float(maximum_age_seconds))
    )
    expiry_timestamp = (
        time.time() if current_time is None else float(current_time)
    ) - retention_seconds
    deleted_counts = {"results": 0, "logs": 0}
    caches = {"results": _RESULTS, "logs": _REPRODUCTION_LOGS}
    cache_locks = {
        "results": _RESULTS_LOCK,
        "logs": _REPRODUCTION_LOGS_LOCK,
    }
    with _EXPORTS_LOCK:
        if _shared_export_root():
            deleted_counts.update(remove_expired_run_artifacts(
                maximum_age_seconds=maximum_age_seconds,
                current_time=current_time,
            ))
        for name, directory in (("results", RESULT_EXPORT_DIR), ("logs", LOG_EXPORT_DIR)):
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                try:
                    expired = path.is_file() and path.stat().st_mtime <= expiry_timestamp
                except OSError:
                    continue
                if not expired:
                    continue
                try:
                    path.unlink()
                except OSError:
                    continue
                deleted_counts[name] += 1
                with cache_locks[name]:
                    caches[name].pop(path.stem, None)
    return deleted_counts


def save_result(output: Dict[str, Any]) -> str:
    """暂存甘特图数据，写入前自动清除超过保留期的旧制品。"""
    result_id = uuid.uuid4().hex
    with _EXPORTS_LOCK:
        remove_expired_artifacts()
        _write_json_atomic(RESULT_EXPORT_DIR / f"{result_id}.json", output)
        with _RESULTS_LOCK:
            _RESULTS[result_id] = output
            _RESULTS.move_to_end(result_id)
            while len(_RESULTS) > MAX_SAVED_RESULTS:
                _RESULTS.popitem(last=False)
    return result_id


def save_run_artifacts(
    output: Optional[Mapping[str, Any]],
    reproduction_log: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    *,
    execution_logs: Sequence[Any] = (),
) -> Dict[str, str]:
    """以一个事务保存运行制品，写入前统一清理新目录和旧平铺格式。

    输出可为空；摘要提供测试归属和指标，可读阶段日志与复现事件分开保存。
    返回值保持结果和日志 API 字段，并追加制品 ID、摘要及上下文地址。
    """
    with _EXPORTS_LOCK:
        remove_expired_artifacts()
        return _save_run_artifacts(output, reproduction_log, summary, execution_logs=execution_logs)


def read_result(result_id: str, *, include_replay_context: bool = True) -> Optional[Dict[str, Any]]:
    """读取累计输出；轻量模式不读取独立上下文，旧平铺格式仍可恢复。"""
    value = read_run_output(result_id, include_replay_context=include_replay_context)
    if value is not None:
        return value
    with _RESULTS_LOCK:
        value = _RESULTS.get(result_id)
        if value is not None:
            value = deepcopy(value)
            if not include_replay_context:
                value.pop("ReplayContext", None)
            return value
    if len(result_id) == 32 and all(char in "0123456789abcdef" for char in result_id.lower()):
        path = RESULT_EXPORT_DIR / f"{result_id}.json"
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, Mapping):
                value = dict(value)
                if not include_replay_context:
                    value.pop("ReplayContext", None)
                return value
    return None


def read_replay_context(result_id: str) -> Optional[Dict[str, Any]]:
    """读取冻结回放输入；只有旧平铺格式才需要解析完整结果。"""
    context = read_run_replay_context(result_id)
    if context is not None:
        return context
    if read_run_manifest(result_id) is not None:
        return None
    result = read_result(result_id)
    context = result.get("ReplayContext") if isinstance(result, Mapping) else None
    return deepcopy(dict(context)) if isinstance(context, Mapping) else None


def read_result_summary(result_id: str) -> Optional[Dict[str, Any]]:
    """读取运行摘要，不加载完整 MoveList 或复现事件。"""
    return read_run_summary(result_id)


def save_reproduction_log(entries: Sequence[Mapping[str, Any]]) -> str:
    """暂存 input_data 格式日志，写入前自动清除超过保留期的旧制品。"""
    log_id = uuid.uuid4().hex
    payload = deepcopy(list(entries))
    with _EXPORTS_LOCK:
        remove_expired_artifacts()
        _write_text_atomic(LOG_EXPORT_DIR / f"{log_id}.json", format_reproduction_log(payload))
        with _REPRODUCTION_LOGS_LOCK:
            _REPRODUCTION_LOGS[log_id] = payload
            _REPRODUCTION_LOGS.move_to_end(log_id)
            while len(_REPRODUCTION_LOGS) > MAX_SAVED_RESULTS:
                _REPRODUCTION_LOGS.popitem(last=False)
    return log_id


def read_reproduction_log(log_id: str) -> Optional[List[Dict[str, Any]]]:
    """读取一次运行的可复现日志；服务重启后可从磁盘恢复。"""
    value = read_run_reproduction_log(log_id)
    if value is not None:
        return value
    with _REPRODUCTION_LOGS_LOCK:
        value = _REPRODUCTION_LOGS.get(log_id)
        if value is not None:
            return deepcopy(value)
    if len(log_id) == 32 and all(char in "0123456789abcdef" for char in log_id.lower()):
        path = LOG_EXPORT_DIR / f"{log_id}.json"
        if path.is_file():
            value = json.loads(path.read_text(encoding="utf-8"))
            return deepcopy(value) if isinstance(value, list) else None
    return None


def build_workspace_batch_log_archive(batch_id: str) -> Tuple[bytes, str]:
    """打包一个批量任务中已生成的测试复现日志。

    参数 ``batch_id`` 为批量任务 ID。返回 ZIP 二进制内容及推荐下载文件名；每条
    日志采用与单条日志下载相同的逐行 JSON 格式，压缩包中的 ``manifest.json``
    记录测试集、运行状态及对应文件名。批量任务不存在或尚未生成任何日志时抛出异常。
    """
    # 装配模块依赖本制品仓库，因此在调用期读取批量状态，避免模块初始化环。
    from app.backend.wiring import read_workspace_batch_run

    batch = read_workspace_batch_run(batch_id)
    if batch is None:
        raise LookupError("批量任务不存在或已过期")

    manifest_items: List[Dict[str, Any]] = []
    archive_buffer = BytesIO()
    with ZipFile(archive_buffer, "w", compression=ZIP_DEFLATED) as archive:
        for item in sorted(batch.get("items") or [], key=lambda value: int(value.get("index", 0))):
            if not isinstance(item, Mapping):
                continue
            log_url = str(item.get("logUrl") or "")
            log_id = log_url.rsplit("/", 1)[-1]
            reproduction_log = read_reproduction_log(log_id) if log_id else None
            manifest_item = {
                "index": int(item.get("index", 0)) + 1,
                "testId": str(item.get("testId") or ""),
                "testName": str(item.get("testName") or ""),
                "status": str(item.get("status") or "queued"),
                "logFile": "",
            }
            if reproduction_log is not None:
                safe_name = re.sub(
                    r'[\\/:*?"<>|\x00-\x1f]+', "_", manifest_item["testName"],
                ).strip(" ._") or f"测试{manifest_item['index']}"
                log_file = f"t{manifest_item['index']:02d}_{safe_name}.json"
                archive.writestr(log_file, format_reproduction_log(reproduction_log))
                manifest_item["logFile"] = log_file
            manifest_items.append(manifest_item)

        exported_count = sum(bool(item["logFile"]) for item in manifest_items)
        if not exported_count:
            raise ValueError("本批次尚无可导出的复现日志")
        archive.writestr(
            "manifest.json",
            json.dumps({
                "batchId": batch_id,
                "deviceName": str(batch.get("deviceName") or ""),
                "group": str(batch.get("group") or ""),
                "strategy": str(batch.get("strategy") or ""),
                "exportedLogCount": exported_count,
                "items": manifest_items,
            }, ensure_ascii=False, indent=2),
        )
    def readable_segment(value: Any, fallback: str) -> str:
        """把展示名称转换为可安全用于下载文件名的片段。"""
        return re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(value or "")).strip(" ._") or fallback

    device_name = readable_segment(Path(str(batch.get("deviceName") or "")).stem, "设备")
    group_name = readable_segment(batch.get("group"), "测试组")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return archive_buffer.getvalue(), f"批量复现日志-{device_name}-{group_name}-{timestamp}.zip"


def clear_exported_artifacts() -> Dict[str, int]:
    """删除全部已导出的结果和复现日志，并同步清空内存缓存。

    返回值包含结果和日志各自删除的制品数量。该操作处理专用运行目录和旧平铺
    JSON，不会影响设备、测试集、迁移备份或其他导出文件。
    """
    deleted_counts = {"results": 0, "logs": 0}
    with _EXPORTS_LOCK:
        if _shared_export_root():
            deleted_counts.update(clear_run_artifacts())
        for name, directory in (("results", RESULT_EXPORT_DIR), ("logs", LOG_EXPORT_DIR)):
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                if path.is_file():
                    path.unlink()
                    deleted_counts[name] += 1
        with _RESULTS_LOCK:
            _RESULTS.clear()
        with _REPRODUCTION_LOGS_LOCK:
            _REPRODUCTION_LOGS.clear()
    return deleted_counts


def _persist_workspace_baseline(
    device_id: str,
    test_id: str,
    baseline: Mapping[str, Any],
    path: Path = WORKSPACE_STORE_PATH,
) -> bool:
    """保存某个测试的 Baseline；测试夹具不存在于目录时返回 False。"""
    with _workspace_catalog_guard(path):
        catalog = _read_workspace_catalog_unlocked(path)
        device = next((item for item in catalog["devices"] if item.get("id") == device_id), None)
        if device is None:
            return False
        test_case = next((item for item in (device.get("tests") or []) if item.get("id") == test_id), None)
        if test_case is None:
            return False
        test_case["baseline"] = deepcopy(dict(baseline))
        device["updatedAt"] = _workspace_timestamp()
        _write_workspace_catalog_unlocked(path, catalog)
        return True




__all__ = tuple(name for name in globals() if not name.startswith('__'))
