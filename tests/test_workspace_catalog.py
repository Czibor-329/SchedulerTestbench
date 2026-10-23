"""工作区保存的代表场景：对象生命周期、组隔离和可恢复迁移。

只使用合成 init 与 tmp_path；文件布局、单测试访问范围分别由交换包和存储复杂度测试负责。
不再依赖已经不存在的算法输入 JSON 或手工复制旧版测试文件的目录布局。
"""
import json
from unittest.mock import patch

import pytest

from app.backend import application as server
from tests.support.workspace_fixtures import workspace_device


def test_device_and_test_lifecycle_preserves_independent_saved_values(tmp_path):
    """创建、修改、删除只影响目标对象；重复 init 保留设备 ID，读取结果互不别名。"""
    store = tmp_path / "datasets"
    first, created = server.import_workspace_device("设备A", workspace_device(), store)
    repeated, repeated_created = server.import_workspace_device("设备A改名", workspace_device(), store)
    other, _ = server.import_workspace_device("设备B", workspace_device("LP2"), store)
    assert created and not repeated_created and repeated["id"] == first["id"]
    assert repeated["name"] == "设备A改名"
    payload = {"name": "原始", "group": "代表组", "strategy": "cycle", "options": {"seed": 1}, "rounds": [{"currentTime": 0, "cjobs": []}]}
    saved = server.create_workspace_test(first["id"], payload, store)
    copied = server.create_workspace_test(first["id"], {**payload, "name": "副本"}, store)
    updated = server.update_workspace_test(first["id"], copied["id"], {**payload, "name": "修改后", "options": {"seed": 9}}, store)
    assert updated["id"] == copied["id"] and updated["options"]["seed"] == 9
    loaded = server.get_workspace_device(first["id"], store)
    by_id = {item["id"]: item for item in loaded["tests"]}
    assert by_id[saved["id"]]["options"]["seed"] == 1
    assert by_id[copied["id"]]["options"]["seed"] == 9
    by_id[saved["id"]]["options"]["seed"] = 100
    reread = {item["id"]: item for item in server.get_workspace_device(first["id"], store)["tests"]}
    assert reread[saved["id"]]["options"]["seed"] == 1
    server.delete_workspace_test(first["id"], saved["id"], store)
    assert [item["id"] for item in server.get_workspace_device(first["id"], store)["tests"]] == [copied["id"]]
    server.delete_workspace_device(first["id"], store)
    assert [item["id"] for item in server.list_workspace_devices(store)] == [other["id"]]
    with pytest.raises(ValueError, match="设备不存在"):
        server.get_workspace_device(first["id"], store)


def test_groups_preserve_empty_groups_names_and_targeted_deletion(tmp_path):
    """空组可保存，不同组允许同名测试；改名和删除不污染其它组。"""
    store = tmp_path / "datasets"
    device, _ = server.import_workspace_device("设备", workspace_device(), store)
    device_id = device["id"]
    grouped = server.create_workspace_test_group(device_id, "空组", store)
    assert "空组" in grouped
    assert server.get_workspace_device(device_id, store)["tests"] == []
    first = server.create_workspace_test(device_id, {"name": "同名", "group": "A"}, store)
    second = server.create_workspace_test(device_id, {"name": "同名", "group": "B"}, store)
    assert first["name"] == second["name"] == "同名"
    renamed = server.rename_workspace_test_group(device_id, "A", "改名组", store)
    assert {item["id"]: item["group"] for item in renamed["tests"]} == {first["id"]: "改名组", second["id"]: "B"}
    deleted = server.delete_workspace_test_group(device_id, "改名组", store)
    assert deleted["deletedTestCount"] == 1
    assert [item["id"] for item in deleted["tests"]] == [second["id"]]
    server.delete_workspace_test_group(device_id, "空组", store)
    assert "空组" not in server.get_workspace_device(device_id, store)["testGroups"]


def test_legacy_catalog_migration_recovers_interruption_and_is_idempotent(tmp_path):
    """正常及中断残留目录均恢复同一旧目录，原文件留备份，完成标记避免重复迁移。"""
    for interrupted in (False, True):
        root = tmp_path / ("interrupted" if interrupted else "normal")
        data = root / "data"
        data.mkdir(parents=True)
        legacy = data / "workspaces.json"
        device, _ = server.import_workspace_device("旧设备", workspace_device(), legacy)
        test = server.create_workspace_test(device["id"], {"name": "迁移案例"}, legacy)
        original = json.loads(legacy.read_text(encoding="utf-8"))
        store = root / "datasets"
        if interrupted:
            (store / "stale-device-dir").mkdir(parents=True)
        with (patch.object(server, "DATA_DIR", data), patch.object(server, "WORKSPACE_STORE_PATH", store)):
            catalog = server._read_workspace_catalog_unlocked(store)
            assert [item["id"] for item in catalog["devices"]] == [device["id"]]
            assert server.get_workspace_device(device["id"], store)["tests"][0]["id"] == test["id"]
            assert not (store / "stale-device-dir").exists() and not legacy.exists()
            backup = data / "workspaces.json.legacy.json"
            assert json.loads(backup.read_text(encoding="utf-8")) == original
            assert not server._workspace_data_update_required(store)
            assert not server._prepare_workspace_data(store)
            assert json.loads(backup.read_text(encoding="utf-8")) == original
            if not interrupted:
                (store / server.WORKSPACE_STORE_VERSION_FILE).unlink()
                assert server._workspace_data_update_required(store)
                with patch.object(server, "_migrate_workspace_catalog", wraps=server._migrate_workspace_catalog) as migrate:
                    assert server._prepare_workspace_data(store)
                    assert not server._prepare_workspace_data(store)
                assert migrate.call_count == 1
