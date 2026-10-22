"""每测试每次运行的结构化制品存储、读取与保留期清理。

运行 UUID 是目录和 API 的稳定标识；目录只保存冻结输入、累计 MoveList、复现
事件和轻量摘要，不参与设备主数据或交换包。完整目录在临时区写完后原子启用。
格式 v2 的保留时间取 manifest 创建时间，读取不会延长原运行的保留期。
旧平铺文件保持原样，由 repository 兼容读取，本模块不扫描或转换旧结果。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from app.backend.bootstrap import (
    ARTIFACT_RETENTION_SECONDS,
    ARTIFACT_SCHEMA_VERSION,
    EXPORT_DIR,
    RUN_EXPORT_DIR,
    _EXPORTS_LOCK,
    _REPRODUCTION_LOGS,
    _REPRODUCTION_LOGS_LOCK,
    _RESULTS,
    _RESULTS_LOCK,
)
from app.backend.workspace.repository import format_reproduction_log


RUN_MANIFEST_KIND = "ct-test-run"
STORE_MANIFEST_KIND = "ct-run-artifacts"
RUN_ID_LENGTH = 32
MANIFEST_IDENTITY_FIELDS = (
    "deviceId", "deviceName", "testId", "testName", "group", "batchId", "strategy",
    "startedAt", "completedAt", "status", "validation", "error", "legacySource",
)


def _valid_run_id(artifact_id: str) -> bool:
    """只接受无路径语义的十六进制运行 ID。"""
    return len(artifact_id) == RUN_ID_LENGTH and all(
        character in "0123456789abcdef" for character in artifact_id
    )


def _timestamp_text(timestamp: float) -> str:
    """将文件或运行时间编码为明确时区的 UTC ISO 时间。"""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _read_json(path: Path) -> Any:
    """读取 UTF-8 JSON；调用方负责区分文件缺失与格式损坏。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_bundle_json(path: Path, payload: Any) -> None:
    """在尚未公开的运行临时目录写 JSON，不修改调用方对象。"""
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")


def _check_schema(manifest: Mapping[str, Any], *, legacy_allowed: bool = False) -> None:
    """拒绝无法解释的格式版本，防止新版制品被旧服务覆盖。"""
    version = manifest.get("schemaVersion")
    supported = (1, ARTIFACT_SCHEMA_VERSION) if legacy_allowed else (ARTIFACT_SCHEMA_VERSION,)
    if isinstance(version, bool) or not isinstance(version, int) or version not in supported:
        raise ValueError(f"不支持的运行制品 schemaVersion：{version}")


def _check_store_schema() -> Optional[Dict[str, Any]]:
    """读取制品根格式标记；无标记的旧平铺目录按 v1 处理。"""
    path = EXPORT_DIR / "manifest.json"
    if not path.is_file():
        return None
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError("运行制品根 manifest 必须为 JSON 对象")
    _check_schema(manifest, legacy_allowed=True)
    return manifest


def _write_store_schema() -> None:
    """新运行写入 v2 根标记，保留已有备份引用并清除废弃的迁移状态。"""
    existing = _check_store_schema()
    payload = dict(existing or {})
    payload.update({"kind": STORE_MANIFEST_KIND, "schemaVersion": ARTIFACT_SCHEMA_VERSION})
    payload.pop("migrationInProgress", None)
    if existing == payload:
        return
    _persist_store_manifest(payload)


def _persist_store_manifest(payload: Mapping[str, Any]) -> None:
    """原子提交根格式标记，失败时清理临时文件。"""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    temporary = EXPORT_DIR / f".manifest-{uuid.uuid4().hex}.tmp"
    try:
        _write_bundle_json(temporary, payload)
        os.replace(temporary, EXPORT_DIR / "manifest.json")
    finally:
        temporary.unlink(missing_ok=True)


def _owned_run_path(path: Path) -> bool:
    """确认目录解析后仍直接属于运行目录，避免递归操作越界。"""
    return path.resolve().parent == RUN_EXPORT_DIR.resolve()


def _file_metadata(path: Path) -> Dict[str, Any]:
    """记录制品相对文件名、实际字节数和校验指纹。"""
    content = path.read_bytes()
    return {"path": path.name, "sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _write_run_directory(
    artifact_id: str,
    output: Optional[Mapping[str, Any]],
    reproduction_log: Optional[Sequence[Mapping[str, Any]]],
    summary: Mapping[str, Any],
    execution_logs: Sequence[Any],
    created_at: float,
) -> None:
    """完整构造运行目录并原子启用；失败时清除本次临时文件。

    参数 ID 与创建时间由本次运行提供。没有输出的失败仍保存摘要；
    已存在的运行目录不会被覆盖。
    """
    RUN_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = RUN_EXPORT_DIR / artifact_id
    if target.exists():
        raise FileExistsError(f"运行制品已存在：{artifact_id}")
    temporary = RUN_EXPORT_DIR / f".{artifact_id}.writing-{uuid.uuid4().hex}"
    temporary.mkdir()
    try:
        files: Dict[str, Any] = {}
        context = None
        if output is not None:
            output_payload = dict(output)
            context = output_payload.pop("ReplayContext", None)
            if context is not None and not isinstance(context, Mapping):
                raise ValueError("ReplayContext 必须是 JSON 对象")
            _write_bundle_json(temporary / "movelist.json", output_payload)
            files["output"] = _file_metadata(temporary / "movelist.json")
        if isinstance(context, Mapping):
            _write_bundle_json(temporary / "replay-context.json", context)
            files["replayContext"] = _file_metadata(temporary / "replay-context.json")
        if reproduction_log is not None:
            (temporary / "reproduction-log.json").write_text(
                format_reproduction_log(reproduction_log), encoding="utf-8",
            )
            files["reproductionLog"] = _file_metadata(temporary / "reproduction-log.json")
        summary_payload = {**dict(summary), "artifactId": artifact_id, "createdAt": _timestamp_text(created_at)}
        _write_bundle_json(temporary / "summary.json", summary_payload)
        files["summary"] = _file_metadata(temporary / "summary.json")
        (temporary / "run.log").write_text(
            "".join(f"{entry}\n" for entry in execution_logs), encoding="utf-8",
        )
        files["executionLog"] = _file_metadata(temporary / "run.log")
        manifest = {
            "kind": RUN_MANIFEST_KIND,
            "schemaVersion": ARTIFACT_SCHEMA_VERSION,
            "artifactId": artifact_id,
            "createdAt": _timestamp_text(created_at),
            **{key: summary[key] for key in MANIFEST_IDENTITY_FIELDS if key in summary},
            "hasOutput": output is not None,
            "hasReproductionLog": reproduction_log is not None,
            "files": files,
        }
        _write_bundle_json(temporary / "manifest.json", manifest)
        os.replace(temporary, target)
    finally:
        if temporary.exists() and _owned_run_path(temporary):
            shutil.rmtree(temporary)


def save_run_artifacts(
    output: Optional[Mapping[str, Any]],
    reproduction_log: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    *,
    execution_logs: Sequence[Any] = (),
) -> Dict[str, str]:
    """保存每测试每次运行的完整制品，返回结果、摘要和日志 API 地址。

    ``output`` 可为空，表示尚未生成可回放动作的失败或取消；``summary`` 提供
    测试归属与轻量指标，``execution_logs`` 为用户可读的阶段日志。每次生成新 ID，
    同测试重复运行不覆盖历史，所有文件写入完成前均不对读取 API 可见。
    """
    artifact_id = uuid.uuid4().hex
    with _EXPORTS_LOCK:
        _check_store_schema()
        remove_expired_run_artifacts()
        _write_store_schema()
        _write_run_directory(artifact_id, output, reproduction_log, summary, execution_logs, time.time())
    response = {
        "artifactId": artifact_id,
        "summaryUrl": f"/api/artifacts/{artifact_id}/summary",
        "logUrl": f"/api/logs/{artifact_id}",
        "logFileName": f"ct-input-log-{artifact_id[:8]}.json",
    }
    if output is not None:
        response.update({
            "resultId": artifact_id,
            "resultUrl": f"/api/results/{artifact_id}",
            "ganttUrl": f"/movelist_gantt_viewer.html?src=/api/results/{artifact_id}",
        })
        if isinstance(output.get("ReplayContext"), Mapping):
            response["contextUrl"] = f"/api/results/{artifact_id}?view=replay-context"
    return response


def read_run_manifest(artifact_id: str) -> Optional[Dict[str, Any]]:
    """定位已完整提交的运行，未完成的临时目录不可读取。"""
    _check_store_schema()
    if not _valid_run_id(artifact_id):
        return None
    path = RUN_EXPORT_DIR / artifact_id / "manifest.json"
    if not path.is_file() or not _owned_run_path(path.parent):
        return None
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        raise ValueError("运行 manifest 必须为 JSON 对象")
    _check_schema(manifest)
    return manifest


def _read_run_file(artifact_id: str, filename: str, role: str) -> Any:
    """按固定文件职责读取已提交制品，不采用 manifest 中可注入的文件路径。"""
    manifest = read_run_manifest(artifact_id)
    if manifest is None or role not in (manifest.get("files") or {}):
        return None
    path = RUN_EXPORT_DIR / artifact_id / filename
    return _read_json(path) if path.is_file() else None


def read_run_output(artifact_id: str, *, include_replay_context: bool = True) -> Optional[Dict[str, Any]]:
    """读取累计输出；轻量模式只读取 movelist，不打开回放上下文。"""
    payload = _read_run_file(artifact_id, "movelist.json", "output")
    if not isinstance(payload, dict):
        return None
    if include_replay_context:
        context = read_run_replay_context(artifact_id)
        if context is not None:
            payload["ReplayContext"] = context
    return payload


def read_run_replay_context(artifact_id: str) -> Optional[Dict[str, Any]]:
    """单独读取冻结计划与重算 update，不解析累计 MoveList。"""
    payload = _read_run_file(artifact_id, "replay-context.json", "replayContext")
    return payload if isinstance(payload, dict) else None


def read_run_summary(artifact_id: str) -> Optional[Dict[str, Any]]:
    """只读取可用于卡片和历史定位的轻量摘要。"""
    payload = _read_run_file(artifact_id, "summary.json", "summary")
    return payload if isinstance(payload, dict) else None


def read_run_reproduction_log(artifact_id: str) -> Optional[list[Dict[str, Any]]]:
    """读取逐事件复现日志，保留既有 input_data JSON 协议。"""
    payload = _read_run_file(artifact_id, "reproduction-log.json", "reproductionLog")
    return payload if isinstance(payload, list) else None


def _remove_run_directories(*, expiry_timestamp: Optional[float]) -> Dict[str, int]:
    """删除已提交的专用运行目录，并同步旧寻址 ID 的内存缓存。"""
    counts = {"results": 0, "logs": 0}
    # 仅校验支持版本；旧迁移状态已废弃，不阻塞兼容读取和正常清理。
    _check_store_schema()
    if not RUN_EXPORT_DIR.is_dir():
        return counts
    for directory in RUN_EXPORT_DIR.iterdir():
        if not directory.is_dir() or not _valid_run_id(directory.name) or not _owned_run_path(directory):
            continue
        manifest = read_run_manifest(directory.name)
        if manifest is None:
            continue
        if expiry_timestamp is not None:
            try:
                created_at = datetime.fromisoformat(str(manifest.get("createdAt") or "")).timestamp()
            except (TypeError, ValueError, OverflowError):
                continue
            if created_at > expiry_timestamp:
                continue
        try:
            shutil.rmtree(directory)
        except OSError:
            continue
        counts["results"] += int(bool(manifest.get("hasOutput")))
        counts["logs"] += int(bool(manifest.get("hasReproductionLog")))
        with _RESULTS_LOCK:
            _RESULTS.pop(directory.name, None)
        with _REPRODUCTION_LOGS_LOCK:
            _REPRODUCTION_LOGS.pop(directory.name, None)
    return counts


def remove_expired_run_artifacts(
    *, maximum_age_seconds: Optional[float] = None, current_time: Optional[float] = None,
) -> Dict[str, int]:
    """按 manifest 创建时间删除整个过期运行，返回结果和日志数量。"""
    maximum_age = ARTIFACT_RETENTION_SECONDS if maximum_age_seconds is None else max(0.0, float(maximum_age_seconds))
    expiry = (time.time() if current_time is None else float(current_time)) - maximum_age
    with _EXPORTS_LOCK:
        return _remove_run_directories(expiry_timestamp=expiry)


def clear_run_artifacts() -> Dict[str, int]:
    """清除结构化运行制品；不操作迁移备份或其他导出文件。"""
    with _EXPORTS_LOCK:
        return _remove_run_directories(expiry_timestamp=None)



__all__ = (
    "save_run_artifacts", "read_run_manifest", "read_run_output", "read_run_replay_context", "read_run_summary",
    "read_run_reproduction_log", "remove_expired_run_artifacts", "clear_run_artifacts",
)
