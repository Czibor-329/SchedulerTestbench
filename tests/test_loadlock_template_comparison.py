"""无清洁模板对比的端口计时、PM 相位与物料回放契约；不做整例收益断言。"""
from copy import deepcopy
import json
import shutil

import pytest

from app.backend.theory_capacity.compact_period import calculate_period
from app.backend.theory_capacity.input_model import DeviceDurations
from prototypes.loadlock_research.template_comparison import evaluate_template
from prototypes.loadlock_research.physical_witness import validate_physical_witness
from prototypes.loadlock_research.search_loadlock_cycles import ROOT, PSE300, read_timing
from tests.support.capacity_problem_builders import cycle_problem


@pytest.mark.parametrize('candidates', [[['PM1']], [['PM1', 'PM2', 'PM3']], [['PM1'], ['PM2', 'PM3']]])
def test_original_template_matches_kernel_for_each_fixed_pm_shape(candidates):
    """显式 AB 图覆盖三腔轮转和两工序接力，不能拿单 PM 周期替代。"""
    timing = DeviceDurations(cycle_problem(process_candidates=candidates, wafer_count=1))
    nodes = tuple({name: 20.0 for name in node} for node in candidates)
    result = evaluate_template(timing, nodes, 'LP1', 1, 'RF')
    expected = calculate_period(timing, nodes, ('LA', 'LB'), 'shared-bidirectional', 'VTR', 'ATR', 'LP1')
    assert result['feasible'], result['audit']
    assert result['seconds_per_wafer'] == pytest.approx(expected['seconds_per_unit'], abs=1e-5)


def test_two_wafer_batch_restores_three_pm_assignment_after_twelve_rounds():
    """AABB 的四轮与三 PM 相位须展开十二轮，且保持真实物料身份。"""
    timing = DeviceDurations(cycle_problem(process_candidates=[['PM1', 'PM2', 'PM3']], wafer_count=1))
    result = evaluate_template(timing, ({'PM1': 10.0, 'PM2': 20.0, 'PM3': 30.0},), 'LP1', 2, 'RRFF', 1)
    assert result['rounds'] == 12
    assert result['feasible'], result['audit']


def test_nondefault_port_uses_its_own_access_and_door_times():
    """LP2 较慢的取片必须进入周期和端口复用边，不能仍使用 LP1 时间。"""
    timing = DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1))
    base = evaluate_template(timing, ({'PM1': 0.0},), 'LP1', 1, 'RF')
    timing.chambers['LP2'] = deepcopy(timing.chambers['LP1'])
    timing.chambers['LP2'].name = 'LP2'
    robot = timing.robots['ATR']
    robot.scope.append('LP2')
    robot.pick_time['LP2'] = 1000.0
    robot.place_time['LP2'] = robot.place_time.get('LP1', 0.0)
    robot.transfer_time.update({tuple('LP2' if value == 'LP1' else value for value in key): value
                               for key, value in list(robot.transfer_time.items()) if 'LP1' in key})
    result = evaluate_template(timing, ({'PM1': 0.0},), 'LP2', 1, 'RF')
    assert result['feasible'], result.get('audit')
    assert result['seconds_per_wafer'] > base['seconds_per_wafer'] + 900


@pytest.mark.dataset
@pytest.mark.integration
@pytest.mark.parametrize('nodes,port', [(({'PM1': 10.0, 'PM2': 10.0, 'PM3': 10.0},), 'LP1'),
    (({'PM1': 80.0, 'PM2': 80.0, 'PM3': 80.0},), 'LP3'),
    (({'PM2': 10.0}, {'PM1': 20.0, 'PM4': 20.0}), 'LP2')])
def test_physical_multi_pm_witness_preserves_slots_processes_and_home_port(tmp_path, nodes, port):
    """真实 PSE300 副本验证多腔物料接力、初始手槽指向与原端口回片。"""
    source = ROOT / 'app/data/datasets' / PSE300 / 'device.json'
    copied = tmp_path / 'device.json'
    shutil.copyfile(source, copied)
    timing, _ = read_timing(copied)
    device = json.loads(copied.read_text(encoding='utf-8-sig'))
    candidate = evaluate_template(timing, nodes, port, 2, 'RRFF')
    result = validate_physical_witness(candidate['witness'], device, timing, repetitions=1)
    assert result['passed'], result['issues']
    processes = [move for move in result['moves'] if move['MoveType'] == 9]
    assert len(processes) == candidate['rounds'] * len(nodes)
    assert {move['ModuleName'] for move in processes} == set().union(*nodes)
