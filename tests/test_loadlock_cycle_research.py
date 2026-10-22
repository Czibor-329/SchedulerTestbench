"""离线 LoadLock 搜索的能力边界：周期定时、容量分段、证据独立性与物理见证。

普通测试使用 tests/support 的确定性最小设备；真实 PSE300 只用于显式标记的
dataset/integration 测试，并先复制到 tmp_path。生产策略和主数据均不修改。
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from app.backend.theory_capacity.compact_period import calculate_period
from app.backend.theory_capacity.input_model import DeviceDurations
from scripts.loadlock_research.event_model import EventModel
from scripts.loadlock_research.structures import (
    add_atmosphere, atmosphere_orders, build_vacuum, close_atmosphere, episode_options, partitions,
)
from scripts.loadlock_research.verification import replay_witness
from scripts.search_loadlock_cycles import PSE300, ROOT, read_timing, search
from tests.support.capacity_problem_builders import cycle_problem


def test_atmosphere_orders_respect_two_slots_and_keep_refill_first():
    """一出一入可先补片；两出两入不能在满锁上先补第三片。"""
    assert set(atmosphere_orders(1, 1)) == {('R', 'F'), ('F', 'R')}
    assert all(order[0] == 'R' for order in atmosphere_orders(2, 2))
    assert list(atmosphere_orders(0, 0)) == []


def test_pressure_partition_never_pumps_three_raw_wafers():
    """三次 Swap 必须分批；周期两端的两次 Swap 仍可属于同一批。"""
    timing = DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1))
    model, _, locks = build_vacuum(timing, ({'PM1': 0.0},), (('LA', 'LA', 'S'),) * 3)
    choices = tuple(partitions(model, locks['LA']))
    assert all(len(episode) <= 2 for groups in choices for episode in groups)
    assert any(episode == (locks['LA'][-1], locks['LA'][0]) for groups in choices for episode in groups)


@pytest.mark.parametrize('process', [0.0, 10.0, 120.0])
def test_original_template_matches_independent_existing_kernel(process):
    """原 A/B 单片回收后补片序列与既有周期核一致，防止模型漏算门或切压。"""
    timing = DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1))
    nodes = ({'PM1': process},)
    model, _, locks = build_vacuum(timing, nodes, (('LA', 'LA', 'S'), ('LB', 'LB', 'S')))
    groups = {name: tuple((event,) for event in events) for name, events in locks.items()}
    windows = episode_options(model, groups)
    tasks, _ = add_atmosphere(model, windows, [('R', 'F')] * 2)
    close_atmosphere(model, tasks, (0, 1, 2, 3))
    reference = calculate_period(timing, nodes, ('LA', 'LB'), 'shared-bidirectional', 'VTR', 'ATR', 'LP1')
    assert model.solve(1000.0) / 2 == pytest.approx(reference['seconds_per_unit'], abs=1e-5)


def test_saved_evidence_survives_candidate_backtracking():
    """保留见证不得引用搜索中会被回退删除的边列表。"""
    model = EventModel(DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1)))
    event = model.access('PM1', 'VTR', 'swap')
    model.edge(event, event, 17.0, 1, 'resource')
    saved = model.evidence(17.0)
    model.edges.clear()
    model.labels.clear()
    assert saved['edges'] == [(event, event, 17.0, 1)]
    assert saved['labels'] == ['resource']


def test_infeasible_same_period_cycle_is_rejected():
    """同期正环不能被更长周期修复。"""
    model = EventModel(DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1)))
    event = model.access('PM1', 'VTR', 'swap')
    model.edge(event, event, 1.0, 0)
    assert model.solve(10000.0) is None


def test_budget_exhaustion_never_claims_enumeration_complete():
    """预算到期只能保留现有上界，不能生成搜索穷尽证书。"""
    report = search(DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1)), 'single', 0.0, 2, seconds_limit=0)
    assert report['completed'] is False
    assert report['certificate'] == 'best-found-only'


def test_process_bound_can_certify_without_enumeration():
    """现有循环达到固定 PM 占用下界时，无须搜索其它锁结构。"""
    report = search(DeviceDurations(cycle_problem(process_candidates=[['PM1']], wafer_count=1)), 'single', 1000.0, 2)
    assert report['certificate'] == 'fixed-PM-resource-bound'
    assert report['best_seconds_per_wafer'] == pytest.approx(report['lower_bound_seconds_per_wafer'])
    assert report['counts']['words'] == 0


@pytest.mark.dataset
@pytest.mark.integration
def test_pse300_refill_first_witness_passes_real_move_validation(tmp_path):
    """真实设备先补后收的稳态见证须保持 FIFO、容量及标准 MoveList 物理合法。"""
    from scripts.loadlock_research.physical_witness import validate_physical_witness
    source = ROOT / 'app/data/datasets' / PSE300 / 'device.json'
    if not source.is_file():
        pytest.skip('恢复 PSE300 设备主数据后可运行物理见证验收')
    copied = tmp_path / 'device.json'
    shutil.copyfile(source, copied)
    timing, _ = read_timing(copied)
    result = search(timing, 'single', 10.0, 2, seconds_limit=10)
    assert result['completed']
    assert result['best_seconds_per_wafer'] == pytest.approx(32.34, abs=1e-5)
    witness = result['witness']
    assert replay_witness(witness, [1])['passed']
    physical = validate_physical_witness(witness, json.loads(copied.read_text(encoding='utf-8-sig')), timing)
    assert physical['issues'] == []
    # 错配供片身份必须被独立回放发现，不能仅凭周期图可行报告通过。
    corrupted = deepcopy(witness)
    corrupted['task_materials'][0]['material_shift'] += 1
    assert replay_witness(corrupted, [1])['passed'] is False
