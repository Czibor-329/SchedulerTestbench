"""迁移前冻结已通过物理校验的计时证据；仅输出隔离测试夹具，不修改主数据。"""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_theory_capacity_timing as suite

original = suite.start_schedule_by_cycle
records = []

def capture(problem):
    """保存当前生产排程的入腔事件及完整 MoveList 哈希，保留来源与物理校验证据。"""
    result = original(problem)
    encoded = json.dumps(result.machine_moves, sort_keys=True, ensure_ascii=False)
    records.append({'physical_validation_passed': result.cycle_diagnostics['physicalValidationPassed'],
        'moves_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
        'swap_arrivals': [{'seconds': move['EndTime'], 'stations': move.get('StationList', [])}
            for move in result.machine_moves if move['MoveType'] == 4]})
    return result

suite.start_schedule_by_cycle = capture
for seconds in (0.0, 10.0, 80.0, 240.0):
    for mode, nodes in suite.COMBINATIONS:
        suite.test_action_graph_matches_repeated_cycle(mode, nodes, seconds)
        records[-1]['key'] = f'rotation:{mode}:{nodes}:{seconds}'
for seconds in (10.0, 240.0):
    for mode in ('shared-bidirectional', 'dedicated-entry-exit', 'single-bidirectional'):
        suite.test_asymmetric_doors_and_self_rotation_match_execution(mode, seconds)
        records[-1]['key'] = f'asymmetric:{mode}:{seconds}'
suite.test_dedicated_serial_pressure_change_prepares_after_arrival()
records[-1]['key'] = 'dedicated-pressure'
target = ROOT / 'tests/support/capacity_reference_traces.json'
target.write_text(json.dumps({'provenance': '2026-10-14 迁移前生产 Cycle 合成设备排程，43 例均通过物理校验及原周期断言；固定证据不依赖算法安装。',
    'cases': records}, ensure_ascii=False, indent=2), encoding='utf-8')
print(len(records), target)
