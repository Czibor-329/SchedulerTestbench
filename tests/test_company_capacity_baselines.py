"""公司产能 Baseline CSV 的独立持久化与输入校验测试。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from realtime_scheduler.backend.company_capacity_baselines import (
    COMPANY_CAPACITY_BASELINE_FILE_NAME,
    COMPANY_CAPACITY_DIRECTORY_NAME,
    COMPANY_CAPACITY_LEGACY_BASELINE_FILE_NAME,
    import_company_capacity_baselines,
    read_company_capacity_baselines,
)


class CompanyCapacityBaselineTests(unittest.TestCase):
    """确保 CSV 基准不混入设备主数据，且错误输入不会静默变成零产能。"""

    def test_import_replaces_snapshot_and_reads_normalized_rows(self) -> None:
        """有效 CSV 以完整快照保存，并保留设备、用例与数值的匹配关系。"""
        with TemporaryDirectory() as directory:
            result = import_company_capacity_baselines(
                "device_name,test_name,baseline_wph\n12kChamber,test1,87.775\nTWINS,test2,94.3\n".encode(),
                Path(directory),
            )
            self.assertEqual(2, result["rowCount"])
            snapshot = Path(directory) / COMPANY_CAPACITY_DIRECTORY_NAME / COMPANY_CAPACITY_BASELINE_FILE_NAME
            self.assertTrue(snapshot.is_file())
            self.assertFalse((Path(directory) / COMPANY_CAPACITY_LEGACY_BASELINE_FILE_NAME).exists())
            self.assertEqual(
                [{"deviceName": "12kChamber", "testName": "test1", "baselineWph": 87.775},
                 {"deviceName": "TWINS", "testName": "test2", "baselineWph": 94.3}],
                read_company_capacity_baselines(Path(directory)),
            )

    def test_read_falls_back_to_legacy_root_snapshot(self) -> None:
        """尚未迁到独立目录的旧文件仍可读取，避免已导入产能突然消失。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / COMPANY_CAPACITY_LEGACY_BASELINE_FILE_NAME).write_text(
                '{"kind":"company-capacity-baselines","schemaVersion":1,"rows":['
                '{"deviceName":"TWINS","testName":"TS-1","baselineWph":87.979}]}',
                encoding="utf-8",
            )
            self.assertEqual(
                [{"deviceName": "TWINS", "testName": "TS-1", "baselineWph": 87.979}],
                read_company_capacity_baselines(root),
            )

    def test_status_text_and_duplicate_keys_are_rejected(self) -> None:
        """Excel 中的异常状态不得被导入为数值或任意覆盖。"""
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "必须是数值"):
                import_company_capacity_baselines(
                    "device_name,test_name,baseline_wph\nTWINS,test1,算法报错\n".encode(), Path(directory),
                )
            with self.assertRaisesRegex(ValueError, "不能重复"):
                import_company_capacity_baselines(
                    "device_name,test_name,baseline_wph\nTWINS,test1,90\ntwins,TEST1,91\n".encode(), Path(directory),
                )
