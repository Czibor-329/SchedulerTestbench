"""验证 Buffer 配置补齐的可恢复、幂等与能力完整性，文件只写入 tmp_path。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import complete_twins_buffer_robot_config as completion
from tests.support.twins_buffer_fixtures import buffer_device


def test_completion_preserves_data_and_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """补齐后具有完整缓存矩阵、映射及原文件备份，重复执行不再改写。"""
    directory = tmp_path / "datasets" / "device-id"
    directory.mkdir(parents=True)
    device_path = directory / "device.json"
    metadata_path = directory / "metadata.json"
    device_path.write_text(json.dumps(buffer_device()), encoding="utf-8")
    metadata_path.write_text(json.dumps({"id": "device-id", "fingerprint": "old"}), encoding="utf-8")
    original = {path.name: path.read_bytes() for path in (device_path, metadata_path)}
    backup_root = tmp_path / "backups"
    monkeypatch.setattr(completion, "BACKUP_ROOT", backup_root)
    arguments = ["--store-dir", str(directory.parent), "--device-id", "device-id"]

    assert completion.main(arguments) == 0
    assert {path.name: path.read_bytes() for path in (device_path, metadata_path)} == original
    assert not backup_root.exists()
    assert completion.main([*arguments, "--apply"]) == 0

    device = json.loads(device_path.read_text(encoding="utf-8"))
    robot = device["Robots"]["ATMRobot"]
    assert robot["PickTime"]["Buffer1"] == 7.0
    assert robot["PickTime"]["Buffer2"] == 11.0
    assert robot["PlaceTime"]["Buffer2"] == 20.0
    assert device["Stations"]["Buffer1"]["PickPrepareTime"] == {"ATMRobot": 2.0}
    assert device["Stations"]["Buffer1"]["PlacePrepareTime"] == {"ATMRobot": 1.5}
    for name in ("Buffer1", "Buffer2"):
        arm = robot["ArmInfo"]["ArmA"]
        assert name in arm["AccessibleStations"]
        assert arm["SlotsStationMap"][name] == {
            str(slot): [{"Key": name, "Value": target} for target in (1, 2)]
            for slot in (1, 2)
        }
    assert robot["ArmInfo"]["DisabledArm"] == buffer_device()["Robots"]["ATMRobot"]["ArmInfo"]["DisabledArm"]
    matrix = completion._transfer_times(robot)
    assert len(matrix) == len(robot["PrepTransTime"])
    for buffer in ("Buffer1", "Buffer2"):
        for station in device["Stations"]:
            for source, destination in ((buffer, station), (station, buffer)):
                for transfer_type, seconds in ((0, 1.3), (1, 2.7)):
                    assert matrix[source, destination, transfer_type] == (0 if source == destination else seconds)

    backup = next(backup_root.iterdir())
    assert {name: (backup / name).read_bytes() for name in original} == original
    from app.backend.workspace.repository import _device_fingerprint
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["fingerprint"] == _device_fingerprint(device)
    completed = {path.name: path.read_bytes() for path in (device_path, metadata_path)}
    assert completion.main([*arguments, "--apply"]) == 0
    assert {path.name: path.read_bytes() for path in (device_path, metadata_path)} == completed
    assert len(list(backup_root.iterdir())) == 1


def test_missing_transfer_reference_is_rejected() -> None:
    """没有转位参照时明确报错，不能静默填零。"""
    robot = {"PrepTransTime": []}
    with pytest.raises(ValueError, match="缺少 Buffer 转位时间参照"):
        completion._complete_transfers(robot, ["Buffer1", "Buffer2"])
