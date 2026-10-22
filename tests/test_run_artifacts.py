"""结构化运行制品的持久化、轻量读取、旧格式兼容与保留期契约。

全部文件使用隔离临时目录；最小 MoveList 和冻结计划只验证存储边界，不依赖
设备主数据、真实算法或前端构建产物。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.backend.artifacts import repository
from app.backend.artifacts import run_artifacts


@pytest.fixture
def artifact_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将所有制品目录与可恢复备份限定在当前测试临时根目录。"""
    export_root = tmp_path / "exports"
    paths = {
        "EXPORT_DIR": export_root,
        "RUN_EXPORT_DIR": export_root / "runs",
        "RESULT_EXPORT_DIR": export_root / "results",
        "LOG_EXPORT_DIR": export_root / "logs",
        "DATA_DIR": tmp_path / "data",
    }
    for module in (repository, run_artifacts):
        for name, value in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
        monkeypatch.setattr(module, "_RESULTS", {})
        monkeypatch.setattr(module, "_REPRODUCTION_LOGS", {})
    return export_root


def _output_with_frozen_plan() -> dict:
    """构造含累计动作、计划时间、失败诊断和冻结回放输入的最小输出。"""
    return {
        "MoveList": [{"MoveID": 1, "StartTime": 0, "EndTime": 2, "PlannedEndTime": 1}],
        "RecomputePoints": [{"Time": 1}],
        "FailureContext": {"Stage": "algorithm-deadlock"},
        "ReplayContext": {"schema": "machine-replay-context-v1", "plan": {"device": {}}, "updates": [{}]},
        "RunMetricsMetadata": {"cpuTimeMs": 3, "recomputeCount": 1},
    }


def _write_legacy_file(path: Path, payload: object) -> None:
    """写入未带根格式标记的 v1 文件，供原地兼容读取测试使用。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_run_artifacts_save_bundle_preserves_contract_and_source(artifact_store: Path) -> None:
    """结果和日志共用运行 ID，文件指纹与原始输入一致。"""
    output = _output_with_frozen_plan()
    reproduction = [{"Describe": "Input", "Info": [{"value": 1}]}]
    response = repository.save_run_artifacts(
        output, reproduction, {"testId": "test-one", "testName": "测试一", "status": "failed"},
        execution_logs=["开始排程", "算法死锁"],
    )
    artifact_id = response["artifactId"]
    directory = artifact_store / "runs" / artifact_id
    assert response["resultId"] == artifact_id
    assert response["logUrl"] == f"/api/logs/{artifact_id}"
    assert response["summaryUrl"] == f"/api/artifacts/{artifact_id}/summary"
    assert response["contextUrl"] == f"/api/results/{artifact_id}?view=replay-context"
    assert set(path.name for path in directory.iterdir()) == {
        "manifest.json", "movelist.json", "replay-context.json", "reproduction-log.json", "summary.json", "run.log",
    }
    manifest = repository.read_run_manifest(artifact_id)
    assert manifest["schemaVersion"] == 2
    assert manifest["testName"] == "测试一"
    assert manifest["status"] == "failed"
    assert "ReplayContext" not in json.loads((directory / "movelist.json").read_text(encoding="utf-8"))
    assert repository.read_result(artifact_id) == output
    assert repository.read_reproduction_log(artifact_id) == reproduction
    assert (directory / "run.log").read_text(encoding="utf-8") == "开始排程\n算法死锁\n"
    for metadata in manifest["files"].values():
        content = (directory / metadata["path"]).read_bytes()
        assert metadata["sizeBytes"] == len(content)
        assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
    assert output == _output_with_frozen_plan()


def test_run_artifacts_repeated_test_gets_independent_ids(artifact_store: Path) -> None:
    """同一个稳定测试可保留两次不同运行，不能以 testId 覆盖。"""
    first = repository.save_run_artifacts({"MoveList": [{"MoveID": 1}]}, [], {"testId": "same-test"})
    second = repository.save_run_artifacts({"MoveList": [{"MoveID": 2}]}, [], {"testId": "same-test"})
    assert first["artifactId"] != second["artifactId"]
    assert repository.read_result(first["artifactId"])["MoveList"] == [{"MoveID": 1}]
    assert repository.read_result(second["artifactId"])["MoveList"] == [{"MoveID": 2}]


def test_run_artifacts_disk_read_survives_cache_reset_and_isolated_mutation(artifact_store: Path) -> None:
    """服务内存清空和调用方修改返回值，不改变磁盘上的冻结数据。"""
    response = repository.save_run_artifacts(_output_with_frozen_plan(), [{"Describe": "Input"}], {})
    artifact_id = response["artifactId"]
    repository._RESULTS.clear()
    repository._REPRODUCTION_LOGS.clear()
    value = repository.read_result(artifact_id)
    value["MoveList"][0]["MoveID"] = 99
    value["ReplayContext"]["plan"]["device"]["changed"] = True
    assert repository.read_result(artifact_id) == _output_with_frozen_plan()
    assert repository.read_reproduction_log(artifact_id) == [{"Describe": "Input"}]


def test_run_artifacts_lightweight_read_does_not_parse_other_payloads(artifact_store: Path) -> None:
    """甘特图轻量输出、摘要和回放上下文分别避开无关的大文件。"""
    response = repository.save_run_artifacts(_output_with_frozen_plan(), [], {"moveCount": 1})
    artifact_id = response["artifactId"]
    directory = artifact_store / "runs" / artifact_id
    context_path = directory / "replay-context.json"
    context_content = context_path.read_text(encoding="utf-8")
    context_path.write_text("无效 JSON，用于证明未被解析", encoding="utf-8")
    assert "ReplayContext" not in repository.read_result(artifact_id, include_replay_context=False)
    assert repository.read_result_summary(artifact_id)["moveCount"] == 1
    context_path.write_text(context_content, encoding="utf-8")
    (directory / "movelist.json").write_text("无效 JSON，用于证明未被解析", encoding="utf-8")
    assert repository.read_replay_context(artifact_id) == _output_with_frozen_plan()["ReplayContext"]
    assert repository.read_result_summary(artifact_id)["moveCount"] == 1


def test_run_artifacts_atomic_save_keeps_partial_bundle_unreadable(artifact_store: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """中途写失败不得留下可读运行或临时目录。"""
    original_writer = run_artifacts._write_bundle_json

    def fail_summary(path: Path, payload: object) -> None:
        """在输出和上下文已写完后模拟摘要存储失败。"""
        if path.name == "summary.json":
            raise OSError("模拟磁盘写入失败")
        original_writer(path, payload)

    monkeypatch.setattr(run_artifacts, "_write_bundle_json", fail_summary)
    with pytest.raises(OSError, match="模拟磁盘"):
        repository.save_run_artifacts(_output_with_frozen_plan(), [], {})
    assert list((artifact_store / "runs").iterdir()) == []


def test_run_artifacts_failure_without_output_still_has_logs_and_summary(artifact_store: Path) -> None:
    """未生成动作的取消和失败仍保留输入、摘要，不提供空回放链接。"""
    response = repository.save_run_artifacts(None, [{"Describe": "Input"}], {"status": "cancelled", "error": "用户取消"})
    artifact_id = response["artifactId"]
    assert "resultId" not in response
    assert "ganttUrl" not in response
    assert repository.read_result(artifact_id) is None
    assert repository.read_replay_context(artifact_id) is None
    assert repository.read_result_summary(artifact_id)["status"] == "cancelled"
    assert repository.read_reproduction_log(artifact_id) == [{"Describe": "Input"}]


def test_run_artifacts_keep_legacy_files_and_links_when_saving_new_run(artifact_store: Path) -> None:
    """新旧格式并存，旧 ID 原地可读，不合并同 ID 的独立结果和日志。"""
    result_id = log_id = "a" * 32
    output = _output_with_frozen_plan()
    reproduction = [{"Describe": "Input", "Info": [{"testCaseName": "历史测试"}]}]
    old_result = artifact_store / "results" / f"{result_id}.json"
    old_log = artifact_store / "logs" / f"{log_id}.json"
    _write_legacy_file(old_result, output)
    _write_legacy_file(old_log, reproduction)
    historical_time = time.time() - 100
    os.utime(old_result, (historical_time, historical_time))
    original_files = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (old_result, old_log)}
    new_run = repository.save_run_artifacts({"MoveList": []}, [], {})
    assert repository.read_result(result_id) == output
    assert repository.read_replay_context(result_id) == output["ReplayContext"]
    assert "ReplayContext" not in repository.read_result(result_id, include_replay_context=False)
    assert repository.read_reproduction_log(log_id) == reproduction
    assert repository.read_run_manifest(result_id) is None
    assert repository.read_result(new_run["artifactId"]) == {"MoveList": []}
    assert [path.name for path in (artifact_store / "runs").iterdir()] == [new_run["artifactId"]]
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in original_files} == original_files
    assert not (artifact_store.parent / "data" / "migration-backups").exists()


def test_run_artifacts_stale_migration_marker_does_not_block_save_or_cleanup(artifact_store: Path) -> None:
    """遗留中断标记不再要求续迁移，保留旧文件、已提交目录及备份引用。"""
    first = repository.save_run_artifacts(_output_with_frozen_plan(), [], {})
    first_manifest = artifact_store / "runs" / first["artifactId"] / "manifest.json"
    committed_content = first_manifest.read_bytes()
    backup = artifact_store.parent / "data" / "migration-backups" / "original"
    backup_file = backup / "results" / f"{'c' * 32}.json"
    _write_legacy_file(backup_file, {"MoveList": []})
    original_backup = backup_file.read_bytes()
    old_path = artifact_store / "results" / backup_file.name
    _write_legacy_file(old_path, {"MoveList": [{"MoveID": 1}]})
    original_legacy = old_path.read_bytes()
    marker_path = artifact_store / "manifest.json"
    _write_legacy_file(marker_path, {
        "kind": "ct-run-artifacts", "schemaVersion": 2,
        "migrationInProgress": True, "migrationBackup": str(backup),
    })
    assert repository.remove_expired_artifacts() == {"results": 0, "logs": 0}
    second = repository.save_run_artifacts({"MoveList": []}, [], {})
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert "migrationInProgress" not in marker
    assert marker["migrationBackup"] == str(backup)
    assert old_path.read_bytes() == original_legacy
    assert first_manifest.read_bytes() == committed_content
    assert backup_file.read_bytes() == original_backup
    assert repository.read_result(first["artifactId"]) == _output_with_frozen_plan()
    assert repository.read_result(second["artifactId"]) == {"MoveList": []}
    assert repository.clear_exported_artifacts() == {"results": 3, "logs": 2}
    assert backup_file.read_bytes() == original_backup


def test_run_artifacts_reject_newer_schema_before_mutating(artifact_store: Path) -> None:
    """不支持的根格式禁止读写和清理，旧内容保持不变。"""
    marker = artifact_store / "manifest.json"
    _write_legacy_file(marker, {"kind": "ct-run-artifacts", "schemaVersion": 3})
    original = marker.read_bytes()
    for operation in (
        lambda: repository.save_run_artifacts(None, [], {}),
        repository.remove_expired_artifacts,
        lambda: repository.read_result("a" * 32),
    ):
        with pytest.raises(ValueError, match="schemaVersion"):
            operation()
    assert marker.read_bytes() == original
    assert not (artifact_store / "runs").exists()


def test_run_artifacts_retention_removes_whole_run_and_preserves_backups(artifact_store: Path) -> None:
    """保留期按运行创建时间计算，不受文件最近访问或修改影响。"""
    old = repository.save_run_artifacts(_output_with_frozen_plan(), [], {"testId": "old"})
    recent = repository.save_run_artifacts({"MoveList": []}, [], {"testId": "recent"})
    old_directory = artifact_store / "runs" / old["artifactId"]
    manifest_path = old_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_time = time.time()
    manifest["createdAt"] = datetime.fromtimestamp(
        current_time - run_artifacts.ARTIFACT_RETENTION_SECONDS - 1, timezone.utc,
    ).isoformat()
    _write_legacy_file(manifest_path, manifest)
    (old_directory / "analysis").mkdir()
    (old_directory / "analysis" / "cached.json").write_text("{}", encoding="utf-8")
    backup = artifact_store.parent / "data" / "migration-backups" / "retained.json"
    _write_legacy_file(backup, {"recoverable": True})
    unrelated = artifact_store / "search-tree.prof"
    unrelated.write_text("不属于运行目录", encoding="utf-8")
    assert repository.remove_expired_artifacts(current_time=current_time) == {"results": 1, "logs": 1}
    assert not old_directory.exists()
    assert repository.read_result(old["artifactId"]) is None
    assert repository.read_result(recent["artifactId"]) == {"MoveList": []}
    assert backup.is_file() and unrelated.is_file()
    assert repository.clear_exported_artifacts() == {"results": 1, "logs": 1}
    assert backup.is_file() and unrelated.is_file()


def test_run_artifacts_new_save_expires_previous_whole_run(artifact_store: Path) -> None:
    """生成新制品时自动清理过期目录，无需依赖服务重启。"""
    old = repository.save_run_artifacts({"MoveList": []}, [], {})
    old_directory = artifact_store / "runs" / old["artifactId"]
    manifest_path = old_directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["createdAt"] = datetime.fromtimestamp(
        time.time() - run_artifacts.ARTIFACT_RETENTION_SECONDS - 1, timezone.utc,
    ).isoformat()
    _write_legacy_file(manifest_path, manifest)
    recent = repository.save_run_artifacts({"MoveList": []}, [], {})
    assert not old_directory.exists()
    assert repository.read_result(recent["artifactId"]) == {"MoveList": []}


def test_run_artifacts_read_ids_cannot_escape_export_root(artifact_store: Path) -> None:
    """结果和摘要入口拒绝路径穿越、非 UUID 及缺失制品。"""
    for invalid_id in ("../secret", "a" * 31, "g" * 32, "a" * 32):
        assert repository.read_run_manifest(invalid_id) is None
        assert repository.read_result_summary(invalid_id) is None
        assert repository.read_replay_context(invalid_id) is None
