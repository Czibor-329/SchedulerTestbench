"""把 TWINS 设备目录中缺失的测试集从本地快照恢复回主数据。

用途：2026-10-13 的整库写入把 TWINS 的三个测试组（双腔案例、双腔训练集1、
双腔测试）与一个未分组测试从 ``app/data/datasets/<设备 UUID>/tests`` 移除，
只剩 ``公司示例集``。本脚本按稳定测试 ID 比对快照与主数据，把缺失的
``test.json`` 复制回去，并调用平台自身的目录规范化重写索引与组清单。

脚本幂等：只补充主数据中不存在的测试 ID，已存在的文件不覆盖。写入前把整个
设备目录备份到 ``.tmp/dataset-restore-backup-<时间戳>/``，数据结构与
``schemaVersion`` 不变。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET_STORE = PROJECT_ROOT / "app" / "data" / "datasets"
DEFAULT_SOURCE = PROJECT_ROOT / ".tmp" / "spark-preview-data" / "datasets" / "Twins" / "tests"
BACKUP_ROOT = PROJECT_ROOT / ".tmp"
DEVICE_ID = "0c32dbfb6fb748eb99365fec835c4646"


def _device_directory(store_dir: Path, device_id: str) -> Path:
    """按 ``metadata.json`` 中的稳定 ID 定位设备目录。"""
    for candidate in sorted(store_dir.iterdir()):
        metadata_file = candidate / "metadata.json"
        if not candidate.is_dir() or not metadata_file.is_file():
            continue
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if isinstance(metadata, Mapping) and str(metadata.get("id") or "") == device_id:
            return candidate
    raise ValueError(f"数据目录中找不到设备 {device_id}")


def _test_files(tests_dir: Path) -> Dict[str, Path]:
    """按测试 ID 收集目录布局下的完整测试文件。"""
    return {
        path.parent.name: path
        for path in sorted(tests_dir.glob("*/test.json"))
    }


def _backup(device_dir: Path) -> Path:
    """复制整个设备目录，返回备份目录。"""
    target = BACKUP_ROOT / f"dataset-restore-backup-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(device_dir, target)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    """比对快照与主数据，补齐缺失测试并触发目录规范化。"""
    parser = argparse.ArgumentParser(description="恢复 TWINS 缺失的测试集文件")
    parser.add_argument("--store-dir", type=Path, default=DATASET_STORE)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--device-id", default=DEVICE_ID)
    parser.add_argument("--apply", action="store_true", help="实际写入，缺省只做比对")
    args = parser.parse_args(argv)

    device_dir = _device_directory(args.store_dir, args.device_id)
    tests_dir = device_dir / "tests"
    if not args.source.is_dir():
        raise ValueError(f"快照目录不存在：{args.source}")
    current = _test_files(tests_dir)
    snapshot = _test_files(args.source)
    missing = sorted(set(snapshot) - set(current))
    print(f"设备目录：{device_dir}")
    print(f"主数据测试：{len(current)}，快照测试：{len(snapshot)}，待恢复：{len(missing)}")
    if not missing and not args.apply:
        print("没有缺失测试，无需恢复")
        return 0

    backup_directory = None
    if missing:
        backup_directory = _backup(device_dir)
        for test_id in missing:
            target_dir = tests_dir / test_id
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot[test_id], target_dir / "test.json")

    # 由平台自身的目录迁移器重写整库：补回缺失测试分组、刷新摘要索引并统一
    # schemaVersion。复制来的文件保留快照时间戳，平台的外部变更检测不会触发，
    # 因此这里显式执行同一迁移与写入路径。
    from app.backend.workspace import repository

    catalog = repository._read_workspace_catalog_directory(args.store_dir)
    catalog["version"] = repository.WORKSPACE_STORE_VERSION
    repository._migrate_workspace_catalog(catalog)
    repository._write_workspace_catalog_unlocked(args.store_dir, catalog)
    restored = _test_files(tests_dir)
    groups = json.loads((device_dir / "groups.json").read_text(encoding="utf-8"))
    index = json.loads(
        (tests_dir / repository.WORKSPACE_TEST_INDEX_FILE).read_text(encoding="utf-8")
    )
    print(f"已恢复 {len(missing)} 个测试；现有测试 {len(restored)} 个，索引 {len(index)} 条")
    print(f"测试组：{json.dumps(groups.get('testGroups'), ensure_ascii=False)}")
    if backup_directory is not None:
        print(f"备份目录：{backup_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
