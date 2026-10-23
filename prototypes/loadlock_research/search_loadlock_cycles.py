"""PSE300 固定 PM Swap 骨架的离线循环搜索，设备数据只读。

从仓库根目录运行 venv/Scripts/python.exe prototypes/loadlock_research/search_loadlock_cycles.py。
枚举 1/2 片压力批、进出锁、VAC 取放方式与 ATM 循环交织，用周期图精确定时。
输出包含搜索范围、未覆盖行为、最优性边界、源文件哈希和可独立复核的事件证据。
不修改 Cycle、前端、配置或 datasets，也不把数学图可行冒充生产 MoveList 验收。
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
from itertools import product
import json
from math import lcm
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.backend.theory_capacity.compact_period import calculate_period
from app.backend.theory_capacity.device_input import _device_timing
from app.backend.theory_capacity.input_model import CapacityInput, DeviceDurations
from prototypes.loadlock_research.structures import (
    add_atmosphere, atmosphere_cycles, build_vacuum, close_atmosphere,
    episode_options, partitions, vacuum_words,
)
from prototypes.loadlock_research.verification import replay_witness, verify_result

PSE300 = '460fad299cda43298409d7ce16d54906'
SHAPES = {'single': (('PM1',),), 'serial': (('PM1',), ('PM2',)),
          'parallel': (('PM1', 'PM2', 'PM3'),), 'mixed': (('PM1',), ('PM2', 'PM3'))}
COMPARISON_TOLERANCE = 1e-5


def read_timing(path):
    """从显式设备 JSON 读取计时并返回 SHA-256；不访问缓存或测试持久数据。"""
    data = path.read_bytes()
    payload = json.loads(data.decode('utf-8-sig'))
    stations, robots, _ = _device_timing(payload)
    if any(stations[name].capacity != 2 for name in ('LA', 'LB')):
        raise ValueError('研究入口要求 LA/LB 各两个槽')
    if robots['ATR'].capacity != 1 or robots['VTR'].capacity != 2:
        raise ValueError('研究入口要求单臂 ATR 与双臂 VTR')
    return DeviceDurations(CapacityInput(stations, robots, [])), hashlib.sha256(data).hexdigest()


def process_lower_bound(timing, nodes):
    """固定均匀 PM 轮转的必要下界：每腔完整换片及加工占用除以候选数。"""
    return max((timing.pick_pre('VTR', name) + timing.pick_t('VTR', name)
                + timing.move('VTR', name, name, 1) + timing.place_t('VTR', name)
                + timing.place_post('VTR', name) + seconds) / len(node)
               for node in nodes for name, seconds in node.items())


def single_pm_robot_lower_bound(timing):
    """单 PM 的每轮必要搬运下界；放松压力/ATM，穷尽合法锁取放边界。

PM1 是每轮共同边界，因此不同轮的锁选择也不能低于这些单轮代价的最小值。
同锁分离动作仍须关门再开门；不把任何指定锁模板的恢复负荷当作全局下界。
"""
    bounds = []
    for word in vacuum_words(1):
        model, _, locks = build_vacuum(timing, ({'PM1': 0.0},), word)
        for events in locks.values():
            if len(events) == 2:
                model.station_edge(events[0], events[1])
        bounds.append(model.solve(100000.0))
    # solve 返回可行上端点；减去二分容差后才可作为保守下界。
    return max(0.0, min(value for value in bounds if value is not None) - 1e-6)


def search(timing, shape, process, rounds, seconds_limit=60.0, family='all', initial=None, return_policy='any',
           partition_index=0, partition_count=1):
    """搜索给定轮数的全部指定候选族或截至时间预算；始终报告是否枚举完成。

family=swap 只枚举锁 Swap，batch 枚举每锁至多一个真空窗口，all 不限分段数。
时间预算不是最优性证据；中止时保留可行最好结果。initial 可提供更优的先验秒/片。
"""
    if return_policy == 'original' and shape != 'single':
        raise ValueError('原锁返回比较当前只支持单 PM')
    nodes = tuple({name: process for name in node} for node in SHAPES[shape])
    if rounds % lcm(*(len(node) for node in nodes)):
        raise ValueError('周期轮数必须覆盖 PM 分配相位')
    baseline = calculate_period(timing, nodes, ('LA', 'LB'), 'shared-bidirectional', 'VTR', 'ATR', 'LP1')
    best = min(baseline['seconds_per_unit'], initial or float('inf'))
    pm_lower = process_lower_bound(timing, nodes)
    robot_lower = single_pm_robot_lower_bound(timing) if shape == 'single' else 0.0
    lower = max(pm_lower, robot_lower)
    report = dict(shape=shape, process_seconds=process, rounds=rounds, family=family, return_policy=return_policy,
                  baseline_seconds_per_wafer=baseline['seconds_per_unit'],
                  lower_bound_seconds_per_wafer=lower, best_seconds_per_wafer=best,
                  process_lower_bound=pm_lower, robot_lower_bound=robot_lower,
                  incumbent_source='previous-search' if initial is not None else 'existing-cycle',
                  completed=False, counts=dict(words=0, pressure_partitions=0, atmosphere_orders=0,
                                               timing_checks=0, improvements=0, audit_rejections=0), witness=None)
    if best <= lower + COMPARISON_TOLERANCE:
        report.update(completed=True, certificate='fixed-PM-resource-bound' if pm_lower >= robot_lower else 'fixed-skeleton-robot-bound', elapsed_seconds=0.0)
        return report
    started, counts = monotonic(), report['counts']
    words = vacuum_words(rounds)
    # 先访问常见模式，使预算内尽快获得可解释见证；后面的完整枚举仍覆盖它们。
    seed_words = []
    for width in (1, 2):
        entries = tuple(('LA', 'LB')[(index // width) % 2] for index in range(rounds))
        for offset in range(rounds):
            exits = entries[offset:] + entries[:offset]
            for kind in ('S', 'D', 'P'):
                if kind != 'S' or entries == exits:
                    seed_words.append(tuple(zip(entries, exits, (kind,) * rounds)))
    from itertools import chain
    try:
        for word_index, word in enumerate(chain(seed_words, words)):
            if monotonic() - started > seconds_limit:
                break
            if word_index % partition_count != partition_index:
                continue
            if family == 'swap' and any(kind != 'S' for _, _, kind in word):
                continue
            latency = sum(len(node) for node in nodes) + 1
            if return_policy == 'original' and any(exit_lock != word[(index - latency) % rounds][0]
                    for index, (_, exit_lock, _) in enumerate(word)):
                continue
            counts['words'] += 1
            vacuum_model, vacuum, lock_events = build_vacuum(timing, nodes, word)
            group_options = [tuple(partitions(vacuum_model, lock_events[station])) for station in ('LA', 'LB')]
            for groups_pair in product(*group_options):
                if family == 'batch' and any(len(group) > 1 for group in groups_pair):
                    continue
                counts['pressure_partitions'] += 1
                groups = dict(zip(('LA', 'LB'), groups_pair))
                windows = episode_options(vacuum_model, groups)
                for orders in product(*(window[3] for window in windows)):
                    if monotonic() - started > seconds_limit:
                        raise TimeoutError
                    # 设备计时对象只读；仅复制候选自身的事件与边。
                    model = deepcopy(vacuum_model, {id(timing): timing})
                    tasks, chains = add_atmosphere(model, windows, orders)
                    counts['atmosphere_orders'] += 1
                    target = best * rounds - COMPARISON_TOLERANCE
                    if model.offsets(target) is None:
                        continue
                    for sequence in atmosphere_cycles(chains):
                        if monotonic() - started > seconds_limit:
                            raise TimeoutError
                        original = close_atmosphere(model, tasks, sequence)
                        counts['timing_checks'] += 1
                        period = model.solve(best * rounds - COMPARISON_TOLERANCE)
                        if period is not None:
                            witness = dict(word=word, groups=groups, orders=orders, tasks=tasks,
                                task_materials=model.task_materials, atmosphere_sequence=sequence,
                                vacuum_sequence=vacuum, graph=model.evidence(period))
                            audit = replay_witness(witness, [len(node) for node in nodes])
                            if not audit['passed']:
                                counts['audit_rejections'] += 1
                                del model.edges[original:]
                                del model.labels[original:]
                                continue
                            best = period / rounds
                            counts['improvements'] += 1
                            report.update(best_seconds_per_wafer=best, witness=witness)
                            print(f'{shape} P={process:g} q={rounds}: {best:.6f} s/wafer', flush=True)
                        del model.edges[original:]
                        del model.labels[original:]
        else:
            report['completed'] = True
    except TimeoutError:
        pass
    report.update(elapsed_seconds=monotonic() - started,
                  certificate='baseline-or-enumerated-family' if report['completed'] else 'best-found-only')
    if best <= lower + COMPARISON_TOLERANCE:
        report['certificate'] = 'fixed-PM-resource-bound' if pm_lower >= robot_lower else 'fixed-skeleton-robot-bound'
    return report


def parallel_search(timing, shape, process, rounds, seconds_limit, family, initial, return_policy, workers):
    """把完整离散字空间确定性分片到独立进程，合并上界与逐片穷尽状态。"""
    from concurrent.futures import ProcessPoolExecutor
    started = monotonic()
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(search, timing, shape, process, rounds, seconds_limit, family,
                                   initial, return_policy, index, workers) for index in range(workers)]
        reports = [future.result() for future in futures]
    best = deepcopy(min(reports, key=lambda row: row['best_seconds_per_wafer']))
    best['completed'] = all(row['completed'] for row in reports)
    best['counts'] = {key: sum(row['counts'][key] for row in reports) for key in best['counts']}
    best['workers'] = workers
    best['partitions_completed'] = [row['completed'] for row in reports]
    best['elapsed_seconds'] = monotonic() - started
    if not best['certificate'].endswith('-bound'):
        best['certificate'] = 'baseline-or-enumerated-family' if best['completed'] else 'best-found-only'
    return best


def main():
    """解析研究范围，逐项保存结果；独立预算使某个难例不阻塞其它参数点。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-file', type=Path, default=ROOT / 'app/data/datasets' / PSE300 / 'device.json')
    parser.add_argument('--shape', choices=SHAPES, default='single')
    parser.add_argument('--process', type=float, nargs='+', default=[0, 10, 30, 60, 120])
    parser.add_argument('--rounds', type=int, nargs='+', default=[2, 4])
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--family', choices=('all', 'swap', 'batch'), default='all')
    parser.add_argument('--return-policy', choices=('any', 'original'), default='any')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp/loadlock-research/pse300-search.json')
    args = parser.parse_args()
    if args.return_policy == 'original' and args.shape != 'single':
        parser.error('本轮原锁比较只开放单 PM，避免把不满足原锁约束的既有模板当上界')
    if not 1 <= args.workers <= 6:
        parser.error('workers 必须在 1 到 6 之间')
    if any(value < 0 for value in args.process) or any(value < 1 or value > 6 for value in args.rounds):
        parser.error('加工时间须非负，轮数须在 1 到 6 之间')
    timing, digest = read_timing(args.device_file)
    output = dict(device='PSE300', device_file=str(args.device_file), device_sha256=digest,
                  scope='No Clean, LP1, fixed PM Swap, FIFO within pressure batches; offline event model',
                  limitations=['No production MoveList replay or cold-start proof',
                               'No persistent second wafer across PM swaps',
                               'FIFO checked by repeated material replay of improving candidates',
                               'Budgeted search does not prove global optimality'], results=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for process in args.process:
        best = None
        for rounds in args.rounds:
            if args.workers == 1:
                result = search(timing, args.shape, process, rounds, args.seconds, args.family, best, args.return_policy)
            else:
                result = parallel_search(timing, args.shape, process, rounds, args.seconds,
                                         args.family, best, args.return_policy, args.workers)
            result['verification'] = verify_result(result, [len(node) for node in SHAPES[args.shape]])
            best = result['best_seconds_per_wafer']
            output['results'].append(result)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({key: value for key, value in result.items() if key != 'witness'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
