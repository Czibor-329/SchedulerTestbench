"""启动不转换历史运行制品，并继续执行原有保留期清理。

隔离真实启动入口的工作区、算法和 HTTP 生命周期，验证旧文件不会被解析、
复制或改写；废弃的迁移状态不阻塞监听，不影响已有备份。
"""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.backend.artifacts import repository, run_artifacts

main_module = importlib.import_module("app.backend.main")


@pytest.fixture
def startup_artifact_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离新旧制品目录，并跳过与结果存储无关的工作区整理和算法预热。"""
    export_root = tmp_path / "exports"
    paths = {
        "EXPORT_DIR": export_root,
        "RUN_EXPORT_DIR": export_root / "runs",
        "RESULT_EXPORT_DIR": export_root / "results",
        "LOG_EXPORT_DIR": export_root / "logs",
        "DATA_DIR": tmp_path / "data",
    }
    for module in (repository, run_artifacts):
        for name, path in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, path)
        monkeypatch.setattr(module, "_RESULTS", {})
        monkeypatch.setattr(module, "_REPRODUCTION_LOGS", {})
    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(main_module, "_workspace_data_update_required", lambda: False)
    monkeypatch.setattr(main_module, "_has_separate_legacy_workspace_directory", lambda path: False)
    monkeypatch.setattr(main_module, "discover_other_algorithms", Mock())
    monkeypatch.setattr(main_module, "configure_logging", Mock())
    monkeypatch.setattr(main_module, "log_startup", Mock())
    monkeypatch.setattr("sys.argv", ["ct-scheduler", "--port", "9876"])
    return export_root


@pytest.mark.parametrize("marker_version", [None, 1, 2])
def test_run_artifact_startup_listens_without_loading_or_migrating_legacy_files(
    startup_artifact_store: Path, monkeypatch: pytest.MonkeyPatch, marker_version: int | None,
) -> None:
    """旧数据甚至含坏 JSON 或中断标记也不阻止启动，监听前只检查版本及年龄。"""
    legacy_paths = [startup_artifact_store / directory / f"{'a' * 32}.json" for directory in ("results", "logs")]
    for path in legacy_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"legacy JSON must not be parsed during startup")
    backup = startup_artifact_store.parent / "data" / "migration-backups" / "original"
    backup.mkdir(parents=True)
    backup_file = backup / "saved.json"
    backup_file.write_bytes(b"original recoverable backup")
    marker_path = startup_artifact_store / "manifest.json"
    if marker_version is not None:
        marker_path.write_text(json.dumps({
            "kind": "ct-run-artifacts", "schemaVersion": marker_version,
            "migrationInProgress": True, "migrationBackup": str(backup),
        }), encoding="utf-8")
    protected_paths = [*legacy_paths, backup_file, *([marker_path] if marker_path.exists() else [])]
    originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in protected_paths}
    original_reader = run_artifacts._read_json

    def read_only_new_metadata(path: Path) -> object:
        """一旦启动尝试加载历史大文件就立即失败，避免以耗时阈值验证。"""
        assert path not in legacy_paths
        return original_reader(path)

    monkeypatch.setattr(run_artifacts, "_read_json", read_only_new_metadata)
    monkeypatch.setattr(run_artifacts.shutil, "copytree", Mock(side_effect=AssertionError("启动不得备份旧结果")))
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    server_factory = Mock(return_value=server)
    monkeypatch.setattr(main_module, "ThreadingHTTPServer", server_factory)
    main_module.main()
    server_factory.assert_called_once_with(("127.0.0.1", 9876), main_module.ConfigEditorHandler)
    server.serve_forever.assert_called_once_with()
    server.server_close.assert_called_once_with()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in originals} == originals
    assert not (startup_artifact_store / "runs").exists()
    if marker_version is None:
        assert not marker_path.exists()
    assert list(backup.parent.iterdir()) == [backup]


def test_run_artifact_startup_cleans_expired_legacy_files_without_parsing(
    startup_artifact_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沿用旧文件修改时间清理 24 小时制品，未过期文件和备份保留。"""
    result_directory = startup_artifact_store / "results"
    result_directory.mkdir(parents=True)
    expired = result_directory / f"{'b' * 32}.json"
    recent = result_directory / f"{'c' * 32}.json"
    for path in (expired, recent):
        path.write_bytes(b"invalid JSON need not be loaded for retention")
    expired_time = time.time() - run_artifacts.ARTIFACT_RETENTION_SECONDS - 1
    os.utime(expired, (expired_time, expired_time))
    server = Mock()
    server.serve_forever.side_effect = KeyboardInterrupt
    monkeypatch.setattr(main_module, "ThreadingHTTPServer", Mock(return_value=server))
    main_module.main()
    assert not expired.exists()
    assert recent.read_bytes() == b"invalid JSON need not be loaded for retention"
    server.serve_forever.assert_called_once_with()


def test_run_artifact_startup_rejects_unsupported_store_before_listening(
    startup_artifact_store: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取消旧结果迁移后仍拒绝无法理解的新版制品，防止误清理。"""
    startup_artifact_store.mkdir()
    marker = startup_artifact_store / "manifest.json"
    marker.write_text('{"schemaVersion": 3}', encoding="utf-8")
    original = marker.read_bytes()
    server_factory = Mock()
    monkeypatch.setattr(main_module, "ThreadingHTTPServer", server_factory)
    with pytest.raises(ValueError, match="schemaVersion"):
        main_module.main()
    server_factory.assert_not_called()
    assert marker.read_bytes() == original
