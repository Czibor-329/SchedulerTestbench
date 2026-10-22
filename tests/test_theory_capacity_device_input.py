"""平台静态设备输入的独立契约：标准计时、候选访问、双腔通道和拒绝边界。"""
from copy import deepcopy

import pytest

from app.backend.theory_capacity.device_input import compile_capacity_input, paired_loadlock_groups
from app.backend.theory_capacity.device_graph import analyze_cycle_capacity_model
from app.backend.theory_capacity.topology import analyze_capacity_topology
from tests.support import theory_capacity_fixtures as fixtures


def static_input():
    """构造标准协议的小型无清洁输入，不经过生产编译器或工作空间。"""
    init = fixtures.device_init()
    rows = []
    for stage in fixtures.single_node_route()['stages']:
        rows.append({'StepID': stage['stepId'], 'PostStepID': stage['postStepIds'],
            'NeedProcess': stage['needProcess'], 'Visits': [
                {'StationName': visit['stationName'], 'ProcessRecipe': 'R' if stage['needProcess'] else '',
                 'MoveTimeOffset': {}, 'QTimeLimit': -1, 'ResidencyConstraint': -1}
                for visit in stage['visits']]})
    return init, {'ProcessRecipes': [{'Name': 'R', 'ModuleName': 'PM1', 'Time': 90}],
                  'Materials': [{'ID': 1, 'Route': {'Name': 'PM1', 'RouteSteps': rows}}]}


def test_theory_capacity_input_preserves_process_offset_and_source():
    """加工时间按配方加偏移，完整服务为原子换片；解析不修改源载荷。"""
    init, update = static_input()
    update['Materials'][0]['Route']['RouteSteps'][4]['Visits'][0]['MoveTimeOffset'] = {'9': 10}
    before = deepcopy((init, update))
    problem = compile_capacity_input(init, update)
    result = analyze_cycle_capacity_model(problem, analyze_capacity_topology(problem))
    assert result.report.seconds_per_unit == pytest.approx(109, abs=1e-6)
    assert (init, update) == before


@pytest.mark.parametrize('field', ['BeforeInPM', 'AfterOutPM'])
def test_theory_capacity_input_rejects_cleaning_visits(field):
    """直接标准输入的清洁义务不能在解析时丢失。"""
    init, update = static_input()
    update['Materials'][0]['Route']['RouteSteps'][4]['Visits'][0][field] = [{'Name': 'WAC'}]
    with pytest.raises(ValueError, match='仅支持无清洁'):
        compile_capacity_input(init, update)


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_theory_capacity_input_rejects_invalid_transfer_seconds(value):
    """负数、NaN 和无穷转位时间都不能进入数学图。"""
    init, update = static_input()
    init['Robots']['VTR']['PrepTransTime'][0]['Time'] = value
    with pytest.raises(ValueError, match='有限非负'):
        compile_capacity_input(init, update)


def test_theory_capacity_input_rejects_unreachable_station():
    """入口不在 ATM 可达范围时拒绝，避免构造虚假路径。"""
    init, update = static_input()
    update['Materials'][0]['Route']['RouteSteps'][2]['Visits'] = [{'StationName': 'PM1'}]
    with pytest.raises(ValueError, match='无法访问'):
        compile_capacity_input(init, update)


def test_theory_capacity_input_rejects_route_branch():
    """模板只允许一条完整访问链，不按第一条分支静默截断。"""
    init, update = static_input()
    update['Materials'][0]['Route']['RouteSteps'][0]['PostStepID'] = [1, 3]
    with pytest.raises(ValueError, match='分支'):
        compile_capacity_input(init, update)


def test_theory_capacity_reads_actual_pair_mapping():
    """从协议 SlotsStationMap 提取两成员压力通道，而不是按站点名字猜测。"""
    init, _ = static_input()
    arm = init['Robots']['VTR']['ArmInfo']['Arm1']
    arm['SlotsStationMap'] = {'EntryPair': {'1': [{'Key': 'LA', 'Value': 1}], '2': [{'Key': 'LB', 'Value': 1}]}}
    assert paired_loadlock_groups(init['Robots'], init['Stations']) == {'EntryPair': ['LA', 'LB']}
