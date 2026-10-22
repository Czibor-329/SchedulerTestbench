"""单次运行的派生分析缓存，随运行目录一起过期。

缓存按已合并输出的内容指纹、设备/工序上下文、统计窗口、指标选择和分析实现版本
区分。仅缓存计算成功的结果，不复制设备主数据，不修改 manifest 的保留期时间。
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping

from app.backend import bootstrap
from app.backend.artifacts import run_artifacts


ANALYSIS_CACHE_SCHEMA_VERSION = 1
MAXIMUM_CACHED_ANALYSES_PER_RESULT = 16
ANALYSIS_IMPLEMENTATION_DIGEST = hashlib.sha256(
    (Path(__file__).resolve().parents[1] / "analysis.py").read_bytes()
).hexdigest()


def _write_analysis_cache(path: Path, payload: Mapping[str, Any]) -> None:
    """用短临时文件名原子提交缓存，避免 Windows 在长运行目录中超过路径长度。"""
    temporary = path.parent / f".{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _cache_path(result_id: str, parameters: Mapping[str, Any]) -> Path | None:
    """为存在的输出定位派生缓存，结果删除后不创建或恢复已过期目录。"""
    manifest = run_artifacts.read_run_manifest(result_id)
    if manifest is None:
        return None
    output_file = (manifest.get("files") or {}).get("output")
    if not isinstance(output_file, Mapping) or not output_file.get("sha256"):
        return None
    identity = {
        "schemaVersion": ANALYSIS_CACHE_SCHEMA_VERSION,
        "analysisImplementation": ANALYSIS_IMPLEMENTATION_DIGEST,
        "outputSha256": output_file["sha256"],
        "parameters": parameters,
    }
    cache_key = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return run_artifacts.RUN_EXPORT_DIR / result_id / "analysis" / f"{cache_key}.json"


def read_cached_analysis(result_id: str, parameters: Mapping[str, Any]) -> dict | None:
    """读取匹配口径的成功分析；缺失或损坏的派生缓存视为未命中，不影响原结果。"""
    with bootstrap._EXPORTS_LOCK:
        path = _cache_path(result_id, parameters)
        if path is None or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, Mapping) or payload.get("schemaVersion") != ANALYSIS_CACHE_SCHEMA_VERSION:
            return None
        response = payload.get("response")
        return dict(response) if isinstance(response, Mapping) else None


def save_cached_analysis(
    result_id: str, parameters: Mapping[str, Any], response: Mapping[str, Any],
) -> None:
    """原子保存已完成的指标，限制单结果缓存数量；并发清理后不重新创建运行目录。"""
    with bootstrap._EXPORTS_LOCK:
        path = _cache_path(result_id, parameters)
        if path is None:
            return
        try:
            path.parent.mkdir(exist_ok=True)
            _write_analysis_cache(path, {
                "schemaVersion": ANALYSIS_CACHE_SCHEMA_VERSION,
                "response": dict(response),
            })
            paths = sorted(path.parent.glob("*.json"), key=lambda item: item.stat().st_mtime_ns)
            for stale_path in paths[:-MAXIMUM_CACHED_ANALYSES_PER_RESULT]:
                stale_path.unlink(missing_ok=True)
        except OSError:
            # 缓存是可重建的派生数据；磁盘暂时不可写时仍返回已经完成的指标。
            return
