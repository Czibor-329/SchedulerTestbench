"""公司产能 Baseline CSV 的校验、持久化与读取。

该参考数据独立于设备与测试集主数据。它只用于首页展示同一设备、同一用例的
公司 WPH，不参与排程输入、校验或设备包交换。
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping

from realtime_scheduler.backend.bootstrap import DATA_DIR
from realtime_scheduler.backend.workspace.repository import _workspace_catalog_guard, _write_json_atomic
from realtime_scheduler.backend.time_utils import _workspace_timestamp


COMPANY_CAPACITY_BASELINE_SCHEMA_VERSION = 1
COMPANY_CAPACITY_BASELINE_FILE_NAME = "company_capacity_baselines.json"
COMPANY_CAPACITY_BASELINE_REQUIRED_COLUMNS = frozenset({"device_name", "test_name", "baseline_wph"})


def _baseline_path(data_directory: Path | None = None) -> Path:
    """返回独立基准快照的持久化路径。"""
    return (data_directory or DATA_DIR) / COMPANY_CAPACITY_BASELINE_FILE_NAME


def _normalized_row(row: Mapping[str, Any], line_number: int) -> dict[str, Any]:
    """校验并规范化一行 CSV，拒绝缺少匹配键和非正 WPH 的数据。"""
    device_name = str(row.get("device_name") or "").strip()
    test_name = str(row.get("test_name") or "").strip()
    raw_wph = str(row.get("baseline_wph") or "").strip()
    if not device_name or not test_name:
        raise ValueError(f"第 {line_number} 行的 device_name 和 test_name 不能为空")
    try:
        baseline_wph = float(raw_wph)
    except ValueError as error:
        raise ValueError(f"第 {line_number} 行 baseline_wph 必须是数值") from error
    if not math.isfinite(baseline_wph) or baseline_wph <= 0:
        raise ValueError(f"第 {line_number} 行 baseline_wph 必须是大于 0 的有限数值")
    return {"deviceName": device_name, "testName": test_name, "baselineWph": baseline_wph}


def import_company_capacity_baselines(csv_content: bytes, data_directory: Path | None = None) -> dict[str, Any]:
    """导入 UTF-8 CSV 并以一份完整快照原子替换已有公司产能基准。

    Args:
        csv_content: 含 ``device_name,test_name,baseline_wph`` 标题的 UTF-8 CSV。
        data_directory: 测试或部署时可替换的数据根目录。

    Returns:
        已保存的行数和涉及的设备名称。
    """
    try:
        text = csv_content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Baseline CSV 必须使用 UTF-8 编码") from error
    reader = csv.DictReader(io.StringIO(text))
    headers = set(reader.fieldnames or [])
    if not COMPANY_CAPACITY_BASELINE_REQUIRED_COLUMNS.issubset(headers):
        expected = ", ".join(sorted(COMPANY_CAPACITY_BASELINE_REQUIRED_COLUMNS))
        raise ValueError(f"Baseline CSV 必须包含标题列：{expected}")
    rows = [_normalized_row(row, index) for index, row in enumerate(reader, 2)]
    if not rows:
        raise ValueError("Baseline CSV 至少需要一条数据")
    keys = [(row["deviceName"].casefold(), row["testName"].casefold()) for row in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("Baseline CSV 中同一设备与用例不能重复")
    path = _baseline_path(data_directory)
    payload = {
        "kind": "company-capacity-baselines",
        "schemaVersion": COMPANY_CAPACITY_BASELINE_SCHEMA_VERSION,
        "updatedAt": _workspace_timestamp(),
        "rows": rows,
    }
    with _workspace_catalog_guard(path):
        _write_json_atomic(path, payload)
    return {"rowCount": len(rows), "deviceNames": sorted({row["deviceName"] for row in rows})}


def read_company_capacity_baselines(data_directory: Path | None = None) -> list[dict[str, Any]]:
    """读取当前快照；文件不存在时返回空列表，损坏或新版本则明确报错。"""
    path = _baseline_path(data_directory)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取公司产能 Baseline：{error}") from error
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != COMPANY_CAPACITY_BASELINE_SCHEMA_VERSION:
        raise ValueError("公司产能 Baseline 版本不受当前平台支持")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("公司产能 Baseline 缺少 rows 数组")
    return [_normalized_row({
        "device_name": row.get("deviceName"), "test_name": row.get("testName"), "baseline_wph": row.get("baselineWph"),
    }, index) for index, row in enumerate(rows, 1) if isinstance(row, Mapping)]
