"""平台理论产能入口的仓库边界：纯数学求解和 CLI 不需要算法仓库。"""

import json
from pathlib import Path
import subprocess
import sys


PLATFORM_ROOT = Path(__file__).resolve().parents[1]


def test_theory_capacity_solver_works_without_algorithm_repository():
    """在隔离解释器中禁止算法命名空间，仍能导入平台求解器并解出手算周期。"""
    script = """
import sys
sys.path.insert(0, sys.argv[1])
sys.modules['src'] = None
from app.backend.theory_capacity.solver import CapacityModel, WaitingConstraint, analyze_capacity
model = CapacityModel('单事件', ('A',), (WaitingConstraint('A', 'A', 12.5, 1, '节拍'),), 1, {})
report = analyze_capacity(model)
assert abs(report.period_seconds - 12.5) < 1e-6
assert abs(report.physical_wafers_per_hour - 288) < 1e-5
assert not any(name.startswith('src.') for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', script, str(PLATFORM_ROOT)],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_theory_capacity_cli_writes_report_without_algorithm_repository(tmp_path):
    """从任意工作目录运行迁移后的 CLI，明确禁止算法依赖并核对 JSON 输出。"""
    model_path, report_path = tmp_path / 'model.json', tmp_path / 'output' / 'report.json'
    model_path.write_text(json.dumps({
        'name': '单事件', 'events': ['A'], 'units_per_cycle': 1,
        'constraints': [{'source': 'A', 'target': 'A', 'minimum_delay_seconds': 12.5,
                         'cycle_shift': 1, 'label': '节拍'}],
    }, ensure_ascii=False), encoding='utf-8')
    script = """
import runpy, sys
sys.modules['src'] = None
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    completed = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', script,
        str(PLATFORM_ROOT / 'scripts/analyze_cycle_capacity.py'), str(model_path),
        '--output', str(report_path)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert abs(report['period_seconds'] - 12.5) < 1e-6
    assert abs(report['physical_wafers_per_hour'] - 288) < 1e-5


def test_theory_capacity_page_api_works_without_algorithm_repository(tmp_path):
    """禁用整个算法命名空间并移开配置路径，正式固定组合接口仍完成输入解析和求解。"""
    script = """
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ['CT_ALGORITHM_ROOT'] = sys.argv[2]
sys.modules['src'] = None
from app.backend import theory_capacity_service as service
from app.backend.theory_capacity_presets import read_theory_capacity_preset
from tests.support.theory_capacity_fixtures import device_overview
service.get_workspace_device_overview = lambda _: device_overview()
for lock, kinds in [('shared-bidirectional', ['single-node', 'serial', 'parallel', 'mixed']),
                    ('dedicated-entry-exit', ['single-node', 'serial', 'parallel']),
                    ('single-bidirectional', ['single-node', 'parallel'])]:
    for kind in kinds:
        result = read_theory_capacity_preset('test', kind=kind, lock=lock,
            first_seconds=10, last_seconds=20, step_seconds=10, process_seconds=10)
        assert result['ok'] and result['derivation']['physical_wafers_per_hour'] > 0
assert not any(name.startswith('src.') for name in sys.modules)
"""
    completed = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', script, str(PLATFORM_ROOT),
        str(tmp_path / 'missing-algorithm')], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
