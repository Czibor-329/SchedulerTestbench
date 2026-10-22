"""无清洁动作计时的独立对照：数学事件图与迁移前已验证的生产重复轨迹证据。

使用确定的合成设备；覆盖三种锁分工、零/短/长加工、原子换片以及门转位重叠。
不读取公司数据或用模型生成期望值，也不加载算法仓库；轨迹证据含原始 MoveList 哈希。
"""
import pytest


from app.backend.theory_capacity.device_graph import analyze_cycle_capacity_model
import json
from pathlib import Path

REFERENCE_TRACES = {row["key"]: row for row in json.loads(
    (Path(__file__).parent / "support/capacity_reference_traces.json").read_text(encoding="utf-8"))["cases"]}
from app.backend.theory_capacity.topology import analyze_capacity_topology
from tests.support.capacity_problem_builders import cycle_problem

COMBINATIONS = [
    ('shared-bidirectional', nodes) for nodes in (
        [['PM1']], [['PM1'], ['PM2']], [['PM1', 'PM2', 'PM3']], [['PM1'], ['PM2', 'PM3']])
] + [
    ('dedicated-entry-exit', nodes) for nodes in (
        [['PM1']], [['PM1'], ['PM2']], [['PM1', 'PM2', 'PM3']])
] + [('single-bidirectional', nodes) for nodes in ([['PM1']], [['PM1', 'PM2', 'PM3']])]


def no_clean_problem(mode, nodes, seconds):
    """构造单 PJob 连续供片；只固定路线授权，不变更站点物理容量。"""
    problem = cycle_problem(process_candidates=nodes, wafer_count=100,
        process_seconds=[seconds] * len(nodes))
    for wafer in problem.wafers:
        locks = [stage for stage in wafer.stages if stage.stage_type == 'loadlock']
        if mode != 'shared-bidirectional':
            for index, stage in enumerate(locks):
                station = 'LB' if mode == 'dedicated-entry-exit' and index else 'LA'
                stage.chamber = station
                stage.cands = [station]
    return problem


@pytest.mark.parametrize('mode,nodes', COMBINATIONS)
@pytest.mark.parametrize('seconds', [0.0, 10.0, 80.0, 240.0])
def test_action_graph_matches_repeated_cycle(mode, nodes, seconds):
    """中段连续五个完整轮转的 PM 到达间隔必须等于独立事件图解。"""
    problem = no_clean_problem(mode, nodes, seconds)
    topology = analyze_capacity_topology(problem)
    assert topology.supported, topology.detail
    analysis = analyze_cycle_capacity_model(problem, topology)
    evidence = REFERENCE_TRACES[f"rotation:{mode}:{nodes}:{seconds}"]
    assert evidence["physical_validation_passed"]
    arrivals = sorted(row["seconds"] for row in evidence["swap_arrivals"]
        if set(nodes[0]).intersection(row["stations"]))
    stride = analysis.facts.rounds_per_cycle
    middle = len(arrivals) // 2
    periods = [arrivals[i + stride] - arrivals[i]
        for i in range(middle, middle + 5 * stride)]
    assert periods == pytest.approx([analysis.report.period_seconds] * len(periods), abs=0.001)


def test_edges_explain_their_exact_delay_and_robot_load_excludes_doors():
    """分项与边一一对应，机器人工作量不混入可并行的站点门动作。"""
    problem = no_clean_problem('single-bidirectional', [['PM1']], 10.0)
    analysis = analyze_cycle_capacity_model(problem, analyze_capacity_topology(problem))
    model = analysis.model
    services = model.service_index()
    for edge in model.model.constraints:
        assert edge.minimum_delay_seconds == pytest.approx(services[edge.label].seconds)
    assert services['atm:move:LP1->LP1'].seconds == 0
    # VAC = LL Pick 2 + 转位 3 + PM Swap (2+3+2) + 转位 3 + LL Place 2。
    assert model.model.resource_work_seconds['真空机器人 VTR'] == 17


@pytest.mark.parametrize('mode', ['shared-bidirectional', 'dedicated-entry-exit', 'single-bidirectional'])
@pytest.mark.parametrize('seconds', [10.0, 240.0])
def test_asymmetric_doors_and_self_rotation_match_execution(mode, seconds):
    """PM 取放开门不等且站内转位不等于门时长，仍须与真实 Swap 一致。"""
    problem = no_clean_problem(mode, [['PM1']], seconds)
    chamber = problem.chambers['PM1']
    chamber.pick_prepare_time['VTR'] = 3.83
    chamber.place_prepare_time['VTR'] = 1.31
    chamber.pick_complete_time['VTR'] = 0.75
    chamber.place_complete_time['VTR'] = 0.75
    robot = problem.robots['VTR']
    robot.pick_time['PM1'], robot.place_time['PM1'] = 5.83, 5.30
    robot.transfer_time[('PM1', 'PM1', 1)] = 1.65
    analysis = analyze_cycle_capacity_model(problem, analyze_capacity_topology(problem))
    assert analysis.model.service_index()['process-swap:PM1'].seconds == pytest.approx(17.36)
    evidence = REFERENCE_TRACES[f"asymmetric:{mode}:{seconds}"]
    assert evidence["physical_validation_passed"]
    arrivals = sorted(row["seconds"] for row in evidence["swap_arrivals"] if "PM1" in row["stations"])
    stride = analysis.facts.rounds_per_cycle
    for index in range(40, 50):
        assert arrivals[index + stride] - arrivals[index] == pytest.approx(
            analysis.report.period_seconds, abs=0.001)


def test_dedicated_serial_pressure_change_prepares_after_arrival():
    """分向串行的空锁切压后必须到达再开门，短工艺可形成两轮重复。"""
    problem = no_clean_problem('dedicated-entry-exit', [['PM1'], ['PM2']], 10.0)
    # 只保留复现切压/到达边界所需的非对称时间，独立于设备 JSON。
    timing_rows = {
        'LP1': {'ATR': (1.10, 1.10, 1.10, 1.10)},
        'LA': {'ATR': (0.64, 0.64, 1.03, 1.03), 'VTR': (0.87, 0.87, 0.88, 0.88)},
        'LB': {'ATR': (0.53, 0.53, 0.92, 0.92), 'VTR': (0.83, 0.83, 0.90, 0.90)},
        'PM1': {'VTR': (3.83, 1.31, 0.75, 0.75)},
        'PM2': {'VTR': (1.31, 1.31, 0.75, 0.75)},
    }
    for name, robots in timing_rows.items():
        chamber = problem.chambers[name]
        for robot, (pick_pre, place_pre, pick_post, place_post) in robots.items():
            chamber.pick_prepare_time[robot] = pick_pre
            chamber.place_prepare_time[robot] = place_pre
            chamber.pick_complete_time[robot] = pick_post
            chamber.place_complete_time[robot] = place_post
    for robot_name, pick, place, transfer in [('ATR', 4.15, 4.10, 1.10), ('VTR', 5.83, 5.30, 1.65)]:
        robot = problem.robots[robot_name]
        robot.pick_time = dict.fromkeys(robot.scope, pick)
        robot.place_time = dict.fromkeys(robot.scope, place)
        robot.prep_trans_time = [{'Time': transfer}]
    for name, pump, vent in [('LA', 19.26, 19.30), ('LB', 19.76, 19.24)]:
        problem.chambers[name].pump_time = pump
        problem.chambers[name].vent_time = vent
    # builder 的路线阶段保存压力时长；同步阶段与设备，模拟正式编译输入。
    for wafer in problem.wafers:
        for stage in wafer.stages:
            if stage.stage_type == 'loadlock':
                chamber = problem.chambers[stage.chamber]
                stage.proc = chamber.pump_time if stage.chamber == 'LA' else chamber.vent_time
    analysis = analyze_cycle_capacity_model(problem, analyze_capacity_topology(problem))
    evidence = REFERENCE_TRACES["dedicated-pressure"]
    assert evidence["physical_validation_passed"]
    arrivals = sorted(row["seconds"] for row in evidence["swap_arrivals"] if "PM1" in row["stations"])
    assert analysis.report.seconds_per_unit == pytest.approx(54.01, abs=0.001)
    # 单轮可以交替长短；两轮总时长重复且平均产能与图解一致。
    for index in range(40, 50):
        assert (arrivals[index + 2] - arrivals[index]) / 2 == pytest.approx(
            analysis.report.seconds_per_unit, abs=0.001)
