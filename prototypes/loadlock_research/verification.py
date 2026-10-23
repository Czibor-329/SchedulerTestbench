"""独立研究复核：线性规划求周期、按连续事件回放物料和机器人持片。

不使用候选分段的容量判定作为复核依据；对若干展开周期直接扫描取放事件。
这是研究模型的审计，不代替平台 MoveList 校验，也不证明空机启动可达。
"""
from math import isclose


def linear_program_period(graph):
    """用独立 Gurobi LP 复算事件图；显式管理环境，不修改全局求解器配置。"""
    import gurobipy as gp
    with gp.Env(empty=True) as environment:
        environment.setParam('OutputFlag', 0)
        environment.start()
        with gp.Model(env=environment) as model:
            offsets = model.addVars(len(graph['actions']), lb=-gp.GRB.INFINITY)
            period = model.addVar(lb=0.0, obj=1.0)
            for source, target, delay, shift in graph['edges']:
                model.addConstr(offsets[target] - offsets[source] + shift * period >= delay)
            model.optimize()
            success = model.Status == gp.GRB.OPTIMAL
            return dict(success=success, period_seconds=float(period.X) if success else None,
                        status=int(model.Status), solver='Gurobi LP')


def replay_witness(witness, chamber_counts, repetitions=8):
    """按真实时序展开多周期；独立检查物料、手臂容量、槽位与加工阶段。

按第一个取片推断稳态初始驻片，之后每次访问必须与真实库存一致。
晶圆以发片轮次为身份；最终只统计内部完整周期，避免裁剪边缘的残片干扰。
"""
    graph = witness['graph']
    actions, offsets, period = graph['actions'], graph['offsets'], graph['period_seconds']
    rounds = len(witness['word'])
    latency = sum(chamber_counts) + 1
    task_mapping = {}
    for task, metadata in zip(witness['tasks'], witness['task_materials']):
        for event in task:
            task_mapping[event] = metadata
    pm_nodes = {}
    for event in witness['vacuum_sequence']:
        action = actions[event]
        if action['station'].startswith('PM'):
            pm_nodes[event] = sum(1 for previous in witness['vacuum_sequence']
                                  if previous < event and actions[previous]['round'] == action['round']
                                  and actions[previous]['station'].startswith('PM'))
    operations = []
    resource_intervals = {}
    supply_times = []
    for cycle in range(-2, repetitions + 2):
        for action in actions:
            event, kind, station, robot = action['index'], action['kind'], action['station'], action['robot']
            start = offsets[event] + cycle * period
            end = start + action['body']
            resource_intervals.setdefault(robot, []).append((start, end, event))
            resource_intervals.setdefault(station, []).append((start - action['before'],
                end + action['after'] + action['process'], event))
            if robot == 'ATR':
                metadata = task_mapping[event]
                material = (cycle + metadata['material_shift']) * rounds + actions[metadata['vacuum_event']]['round']
                if metadata['kind'] == 'R':
                    material -= latency
                send = material if kind == 'place' else None
                receive = material if kind == 'pick' else None
                if kind == 'pick' and station.startswith('LP'):
                    supply_times.append((start, material))
            else:
                incoming = cycle * rounds + action['round']
                if event in pm_nodes:
                    node = pm_nodes[event]
                    send = incoming - sum(chamber_counts[:node])
                    receive = send - chamber_counts[node]
                else:
                    send = incoming - latency if kind in ('place', 'swap') else None
                    receive = incoming if kind in ('pick', 'swap') else None
            operations.append((start, end, event, station, robot, send, receive, kind))
    issues = []
    for resource, intervals in resource_intervals.items():
        ordered = sorted(intervals)
        for left, right in zip(ordered, ordered[1:]):
            if right[0] < left[1] - 2e-5:
                issues.append(f'{resource} overlaps events {left[2]}/{right[2]}')
                break
    # 首次观察的取出物料就是切片开始前已经驻留的物料；之后不再容许凭空补片。
    inventory, held = {}, {'ATR': set(), 'VTR': set()}
    maximum = {'ATR': 0, 'VTR': 0, 'LA': 0, 'LB': 0}
    operations.sort()
    first_material_operation = {}
    for start, end, event, station, robot, send, receive, kind in operations:
        if receive is not None:
            first_material_operation.setdefault(receive, (station, 'receive'))
        if send is not None:
            first_material_operation.setdefault(send, (robot, 'send'))
    for material, (location, _) in first_material_operation.items():
        if location in held:
            held[location].add(material)
        elif location != 'LP1':
            inventory.setdefault(location, set()).add(material)
    # 截取两端会推断额外库存，因此只在时间零以后记录容量，物料匹配始终复核。
    for start, end, event, station, robot, send, receive, kind in operations:
        contents = inventory.setdefault(station, set())
        if receive is not None:
            if station != 'LP1' and receive not in contents:
                issues.append(f'event {event}: missing material {receive} at {station}')
            elif station != 'LP1':
                contents.remove(receive)
            held[robot].add(receive)
        if 0 <= start < repetitions * period:
            maximum[robot] = max(maximum[robot], len(held[robot]))
        if send is not None:
            if send not in held[robot]:
                issues.append(f'event {event}: {robot} does not hold {send}')
            else:
                held[robot].remove(send)
            if station != 'LP1':
                contents.add(send)
        if 0 <= start < repetitions * period and station in maximum:
            maximum[station] = max(maximum[station], len(contents))
    for resource, limit in [('ATR', 1), ('VTR', 2), ('LA', 2), ('LB', 2)]:
        if maximum[resource] > limit:
            issues.append(f'{resource} capacity {maximum[resource]} > {limit}')
    supplies = [material for time, material in sorted(supply_times) if 0 <= time < repetitions * period]
    fifo = all(right == left + 1 for left, right in zip(supplies, supplies[1:]))
    if not fifo:
        issues.append('ATM supply is not globally FIFO')
    return dict(passed=not issues, issues=list(dict.fromkeys(issues))[:20],
                repeated_periods=repetitions, maximum_occupancy=maximum, fifo=fifo)


def verify_result(result, chamber_counts):
    """同时核对 LP 数值与独立多周期回放，返回可存档证据。"""
    witness = result.get('witness')
    if not witness:
        return dict(status='baseline-only-no-new-witness')
    linear = linear_program_period(witness['graph'])
    matched = linear['success'] and isclose(linear['period_seconds'], witness['graph']['period_seconds'], abs_tol=2e-5)
    replay = replay_witness(witness, chamber_counts)
    return dict(passed=bool(matched and replay['passed']), linear_program=linear,
                period_matches=matched, replay=replay, production_move_validation=False)
