"""比较指定真实工艺的少量 LoadLock 循环候选，不声称穷尽或有限批次收益。

沿用无清洁事件模型，保留各 PM 独立工艺时间、端口和全部 Pump/Vent 计时。
候选为 AB 单片先收后补/先补后收，以及 AABB 两片压力批的两种 ATM 顺序。
输出须通过独立物料回放；不处理清洁、重入、启动收尾及多端口混排。
"""
from itertools import product
from math import lcm

from app.backend.theory_capacity.compact_period import calculate_period
from prototypes.loadlock_research.structures import build_vacuum, add_atmosphere, close_atmosphere, episode_options
from prototypes.loadlock_research.verification import replay_witness, linear_program_period


def evaluate_template(timing, nodes, port, width, atmosphere_order, phase=0):
    """为给定锁重复宽度和 ATM 局部次序求最短周期，返回通过回放的见证。

    width=1 为 AB，width=2 为 AABB；ATM 窗口按 VAC 释放先后服务。
    phase 改变锁模式相对 PM 分配的起点，不改变 PM 访问骨架。
    """
    rounds = lcm(2 * width, *(len(node) for node in nodes))
    word = tuple((('LA', 'LB')[((index + phase) // width) % 2],) * 2 + ('S',)
                 for index in range(rounds))
    model, vacuum, lock_events = build_vacuum(timing, nodes, word)
    groups = {}
    for station, events in lock_events.items():
        # 从该锁连续块的首轮切开，避免把跨周期的两片错误拆开。
        if width == 2:
            start = next(index for index, event in enumerate(events)
                         if (model.actions[event].round + phase) % width == 0)
            events = events[start:] + events[:start]
        groups[station] = tuple(tuple(events[index:index + width]) for index in range(0, len(events), width))
    windows = episode_options(model, groups)
    orders = [tuple(atmosphere_order)] * len(windows)
    if any(order not in window[3] for order, window in zip(orders, windows)):
        raise ValueError('ATM 顺序不满足容量约束')
    tasks, _ = add_atmosphere(model, windows, orders, loadport=port)
    releases, cursor = [], 0
    for window, order in zip(windows, orders):
        releases.append((window[1][-1], tuple(range(cursor, cursor + len(order)))))
        cursor += len(order)
    sequence = tuple(task for _, group in sorted(releases) for task in group)
    close_atmosphere(model, tasks, sequence)
    upper = 1.0 + sum(max(0.0, delay) for _, _, delay, _ in model.edges)
    period = model.solve(upper)
    if period is None:
        return dict(feasible=False, reason='event-graph-infeasible')
    witness = dict(word=word, groups=groups, orders=orders, tasks=tasks,
                   task_materials=model.task_materials, atmosphere_sequence=sequence,
                   vacuum_sequence=vacuum, graph=model.evidence(period))
    audit = replay_witness(witness, [len(node) for node in nodes])
    return dict(feasible=audit['passed'], seconds_per_wafer=period / rounds,
                rounds=rounds, width=width, atmosphere_order=atmosphere_order,
                phase=phase, audit=audit, witness=witness)


def compare_templates(timing, nodes, port):
    """比较有限模板并用独立 LP 复算最佳图；收益仅代表同工艺无限稳态。"""
    reference = evaluate_template(timing, nodes, port, 1, 'RF')
    if not reference['feasible']:
        raise ValueError('原 AB 回收后补片模板未通过事件模型回放')
    baseline = reference['seconds_per_wafer']
    # 既有精简核仅开放部分形态；支持时交叉核对，三道及以上串行保留显式图。
    if len(nodes) <= 2:
        compact = calculate_period(timing, nodes, ('LA', 'LB'), 'shared-bidirectional', 'VTR', 'ATR', port)['seconds_per_unit']
        if abs(baseline - compact) > 1e-5:
            raise ValueError('原模板与既有精简核不一致')
    candidates = [evaluate_template(timing, nodes, port, width, order, phase)
                  for width, orders in ((1, ('RF', 'FR')), (2, ('RRFF', 'RFRF')))
                  for order, phase in product(orders, range(2 * width))]
    feasible = [row for row in candidates if row['feasible']]
    best = min(feasible, key=lambda row: row['seconds_per_wafer']) if feasible else None
    if best:
        verification = linear_program_period(best['witness']['graph'])
        if not verification['success'] or abs(verification['period_seconds'] / best['rounds'] - best['seconds_per_wafer']) > 1e-5:
            raise ValueError('独立 LP 与事件图不一致')
        best['linear_program'] = verification
    return dict(baseline_seconds_per_wafer=baseline, best=best,
                candidates=[{key: value for key, value in row.items() if key != 'witness'} for row in candidates],
                certificate='finite-template-comparison-only', finite_batch_improvement=None)
