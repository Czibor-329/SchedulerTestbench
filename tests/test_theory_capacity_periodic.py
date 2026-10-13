"""连续供片周期契约：独立 PJob、WAC 相位复位、周期证据与入口数据隔离。"""
from copy import deepcopy
import pickle

import pytest

from app.backend import theory_capacity_service as capacity
from app.backend import theory_capacity_periodic as periodic
from app.backend import theory_capacity_workers as workers
from app.backend.theory_capacity_presets import read_theory_capacity_preset
from tests.support.theory_capacity_fixtures import cleaning_device_overview


@pytest.fixture
def isolated_periodic_device(monkeypatch):
    """合成设备仅驻留内存，单点按相同生产求解边界串行执行，避免测试启动进程。"""
    device = cleaning_device_overview()
    monkeypatch.setattr(capacity, 'get_workspace_device_overview', lambda _: device)
    monkeypatch.setattr(workers.capacity_batch_workers, 'solve',
        lambda requests: [workers._calculate_batch(*request) for request in requests])
    return device


@pytest.mark.parametrize('clean', ['preclean', 'postclean', 'wacclean', 'dummy', 'dummywac'])
def test_clean_period_is_verified_interior_window_and_preserves_device(isolated_periodic_device, clean):
    """周期分项守恒且基线来自原论文模型，Dummy 不计产品，原设备槽位不变。"""
    original = deepcopy(isolated_periodic_device)
    arguments = dict(first_seconds=20, last_seconds=30, step_seconds=10, process_seconds=20)
    report = read_theory_capacity_preset('isolated', **arguments, cleaning_values={
        'mode': 'periodic', 'clean': clean, 'wac_interval': 2, 'dummy_visits': 1, 'dummy_inventory': 3})
    baseline = read_theory_capacity_preset('isolated', **arguments)
    entry = report['derivation']
    timing = entry['period_timing']
    assert entry['model_kind'] == 'cycle-periodic'
    assert timing['verified_repeats'] == 3
    assert timing['start_seconds'] > 0
    assert timing['end_seconds'] - timing['start_seconds'] == pytest.approx(timing['full_period_seconds'], abs=.003)
    assert timing['full_period_seconds'] / timing['divisor'] == pytest.approx(entry['period_seconds'], abs=.003)
    assert sum(row['seconds'] for row in timing['segments']) == pytest.approx(entry['period_seconds'], abs=.005)
    assert timing['output_wafers'] == (2 if clean == 'wacclean' else 13)
    assert entry['physical_validation_passed'] is True
    assert entry['physical_wafers_per_hour'] == report['points']['physical_wafers_per_hour'][0]
    assert report['baseline_points'] == baseline['points']
    assert entry['baseline_wafers_per_hour'] == baseline['derivation']['physical_wafers_per_hour']
    assert isolated_periodic_device == original


@pytest.mark.parametrize('interval,expected', [(1, 2), (2, 2), (3, 6)])
def test_wac_period_restores_lock_and_counter_phase_at_zero_process(isolated_periodic_device, interval, expected):
    """零秒产品配方仍由 Place/Swap 识别产出；周期不是固定的 13 片。"""
    report = read_theory_capacity_preset('isolated', first_seconds=0, last_seconds=1, step_seconds=1,
        process_seconds=0, cleaning_values={'mode': 'periodic', 'clean': 'wacclean', 'wac_interval': interval})
    assert report['derivation']['period_timing']['output_wafers'] == expected
    assert report['derivation']['physical_validation_passed'] is True


def test_changed_clean_seconds_changes_period_without_changing_reference(isolated_periodic_device):
    """清洁不应被接口忽略；参数修改后重算当前曲线，论文基线数值不变。"""
    reports = [read_theory_capacity_preset('isolated', first_seconds=90, last_seconds=100, step_seconds=10,
        process_seconds=90, cleaning_values={'mode': 'periodic', 'clean': 'preclean', 'pre_seconds': seconds})
        for seconds in (0, 200)]
    assert reports[1]['derivation']['period_seconds'] > reports[0]['derivation']['period_seconds']
    assert reports[0]['baseline_points'] == reports[1]['baseline_points']


def test_same_duration_without_repeated_actions_is_rejected():
    """仅时间间隔相同不能证明进入重复排程；动作或资源变化仍须拒绝。"""
    anchors = list(range(0, 90, 10))
    moves = [{'StartTime': start + 1, 'EndTime': start + 2, 'MoveType': 9,
              'Station': f'PM{index}', 'MatIDList': [index]} for index, start in enumerate(anchors)]
    with pytest.raises(ValueError, match='中段周期'):
        periodic.find_repeating_window(moves, anchors, 1)


@pytest.mark.parametrize('values', [{'clean': 'wacclean', 'wac_interval': 4},
    {'clean': 'dummy', 'dummy_inventory': 4}, {'clean': 'dummywac', 'dummy_visits': 4}])
def test_periodic_counts_are_bounded_before_expanding_supply(isolated_periodic_device, values):
    """计数过大先拒绝，不能将有限扫描展开成不受控的海量 Job。"""
    with pytest.raises(ValueError, match='1～3'):
        read_theory_capacity_preset('isolated', first_seconds=0, last_seconds=1, step_seconds=1,
            cleaning_values={'mode': 'periodic', **values})


def test_periodic_worker_serializes_errors_without_breaking_pool(monkeypatch):
    """带不可序列化附加上下文的异常转为稳定 ValueError，保持后续扫描可用。"""
    class LocalError(Exception):
        """模拟无法由子进程 pickle 还原的带上下文业务异常。"""
    def fail(_):
        """模拟求解失败。"""
        raise LocalError('period unavailable')
    monkeypatch.setattr(periodic, 'solve_periodic_plan', fail)
    with pytest.raises(ValueError) as captured:
        workers._calculate_batch('{"_capacity_periodic": {}}', 1, '')
    assert str(pickle.loads(pickle.dumps(captured.value))) == 'period unavailable'
