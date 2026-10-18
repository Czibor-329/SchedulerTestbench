"""枚举固定 PM Swap 轮之间的 LoadLock 访问及容量合法的压力批分段。

搜索族每轮引入一片生片、交出一片成品；允许同锁 Swap、先放后取、先取后放。
每个压力批至多两片，抽气仅带生片、充气仅带成品；允许空锁反向切压。
ATM 每次只搬一片，枚举每锁的回盒/供片次序及跨锁循环交织。
这不是全体物理动作空间：不含跨 PM 轮长期保留第二片、非 FIFO 发片或清洁。
"""
from itertools import combinations, product

from scripts.loadlock_research.event_model import EventModel


def vacuum_words(rounds, locks=('LA', 'LB')):
    """枚举入口、出口与取放方式；S 为同锁交换，D/P 为先放/先取。"""
    options = [(entry, exit_lock, kind) for entry in locks for exit_lock in locks
               for kind in (('S', 'D', 'P') if entry == exit_lock else ('D', 'P'))]
    yield from product(options, repeat=rounds)


def build_vacuum(timing, nodes, word):
    """展开 PM 固定轮转和全部 VAC 动作，返回图、访问序列与各锁访问。"""
    model = EventModel(timing)
    vacuum, locks, chambers = [], {'LA': [], 'LB': []}, {}
    for round, (entry, exit_lock, kind) in enumerate(word):
        held = 1
        selected = [(entry, 'swap')] if kind == 'S' else (
            [(exit_lock, 'place'), (entry, 'pick')] if kind == 'D' else
            [(entry, 'pick'), (exit_lock, 'place')])
        for station, operation in selected:
            action = model.access(station, 'VTR', operation, round=round)
            held += int(operation == 'pick') - int(operation == 'place')
            model.carried_after[action] = held
            vacuum.append(action)
            locks[station].append(action)
        for node in nodes:
            station = tuple(node)[round % len(node)]
            action = model.access(station, 'VTR', 'swap', node[station], round)
            vacuum.append(action)
            chambers.setdefault(station, []).append(action)
    model.robot_cycle(vacuum)
    for visits in chambers.values():
        for index, action in enumerate(visits):
            model.station_edge(action, visits[(index + 1) % len(visits)], int(index + 1 == len(visits)))
    return model, vacuum, locks


def _valid_episode(model, events, capacity):
    """从全生片起点模拟真空侧槽位数量；每次 Swap 先取后放。"""
    raw = sum(model.actions[event].kind in ('pick', 'swap') for event in events)
    finished = sum(model.actions[event].kind in ('place', 'swap') for event in events)
    if raw > capacity or finished > capacity:
        return False
    contents = raw
    for event in events:
        kind = model.actions[event].kind
        contents -= int(kind in ('pick', 'swap'))
        contents += int(kind in ('place', 'swap'))
        if not 0 <= contents <= capacity:
            return False
    return True


def partitions(model, events, capacity=2):
    """枚举环形真空访问的全部非空分段；每段之间安排一次 Vent/Pump。"""
    if not events:
        yield ()
        return
    count = len(events)
    for mask in range(1, 1 << count):
        starts = [index for index in range(count) if mask & (1 << index)]
        episodes = []
        for index, start in enumerate(starts):
            end = starts[(index + 1) % len(starts)]
            if end <= start:
                end += count
            episode = tuple(events[cursor % count] for cursor in range(start, end))
            if not _valid_episode(model, episode, capacity):
                break
            episodes.append(episode)
        else:
            yield tuple(episodes)


def atmosphere_orders(returns, feeds, capacity=2):
    """枚举一个大气服务窗口的容量合法回收/供片顺序，FIFO 保留成员次序。"""
    count = returns + feeds
    if not count:
        return
    for positions in combinations(range(count), returns):
        chosen = set(positions)
        order = tuple('R' if index in chosen else 'F' for index in range(count))
        contents = returns
        for kind in order:
            contents += 1 if kind == 'F' else -1
            if not 0 <= contents <= capacity:
                break
        else:
            yield order


def episode_options(model, groups):
    """返回每个真空段之后的 ATM 窗口及其所有容量合法局部顺序。"""
    windows = []
    for station, episodes in groups.items():
        for index, events in enumerate(episodes):
            following = episodes[(index + 1) % len(episodes)]
            returns = sum(model.actions[event].kind in ('place', 'swap') for event in events)
            feeds = sum(model.actions[event].kind in ('pick', 'swap') for event in following)
            windows.append((station, events, following, tuple(atmosphere_orders(returns, feeds))))
    return windows


def add_atmosphere(model, windows, orders, loadport='LP1'):
    """追加指定端口的 ATM 任务与压力链，返回各锁任务链；默认端口 LP1。"""
    tasks, chains = [], {'LA': [], 'LB': []}
    model.task_materials = []
    for (station, events, following, _), order in zip(windows, orders):
        for left, right in zip(events, events[1:]):
            model.station_edge(left, right, int(right < left))
        lock_events = []
        returned = iter(event for event in events if model.actions[event].kind in ('place', 'swap'))
        supplied = iter(event for event in following if model.actions[event].kind in ('pick', 'swap'))
        for kind in order:
            if kind == 'R':
                first = model.access(station, 'ATR', 'pick')
                last = model.access(loadport, 'ATR', 'place')
                lock_events.append(first)
                material_event = next(returned)
                material_shift = -int(material_event > events[-1])
            else:
                first = model.access(loadport, 'ATR', 'pick')
                last = model.access(station, 'ATR', 'place')
                lock_events.append(last)
                material_event = next(supplied)
                material_shift = int(material_event <= events[-1])
            model.task_materials.append(dict(kind=kind, vacuum_event=material_event,
                                            material_shift=material_shift))
            model.robot_edge(first, last)
            chains[station].append(len(tasks))
            tasks.append((first, last))
        # ATM 窗口接在最后一个 VAC 动作之后，窗口事件属于该 VAC 动作所在期。
        last, first = events[-1], following[0]
        shift = int(first <= last)
        if lock_events:
            model.station_edge(last, lock_events[0], pressure=model.timing.chambers[station].vent_time)
            for left, right in zip(lock_events, lock_events[1:]):
                model.station_edge(left, right)
            model.station_edge(lock_events[-1], first, shift, model.timing.chambers[station].pump_time)
        else:
            raise ValueError('不应生成无物料服务的 ATM 窗口')
    # 按所属 VAC 段末动作排序；跨期段在环中归属于其末动作所在期。
    for station in chains:
        windows_for_station = [(events[-1], order) for (name, events, _, _), order in zip(windows, orders)
                               if name == station]
        batches, cursor = [], 0
        for last, order in windows_for_station:
            batches.append((last, chains[station][cursor:cursor + len(order)]))
            cursor += len(order)
        chains[station] = [task for _, batch in sorted(batches) for task in batch]
    return tasks, chains


def interleavings(left, right):
    """枚举两个线性任务链的全部保序交织。"""
    total = len(left) + len(right)
    for positions in combinations(range(total), len(left)):
        selected = set(positions)
        first, second = iter(left), iter(right)
        yield tuple(next(first) if index in selected else next(second) for index in range(total))


def atmosphere_cycles(chains):
    """枚举各锁循环顺序的旋转与跨锁交织；保留相对 VAC 的全部切口。"""
    left, right = chains['LA'], chains['LB']
    seen = set()
    for a in range(max(1, len(left))):
        for b in range(max(1, len(right))):
            for sequence in interleavings(left[a:] + left[:a], right[b:] + right[:b]):
                if sequence not in seen:
                    seen.add(sequence)
                    yield sequence


def close_atmosphere(model, tasks, sequence):
    """串接 ATM 任务并约束同一 LP 的开关门复用；返回追加前的边数。"""
    original = len(model.edges)
    ports = []
    for index, task in enumerate(sequence):
        first, last = tasks[task]
        following = tasks[sequence[(index + 1) % len(sequence)]][0]
        model.robot_edge(last, following, int(index + 1 == len(sequence)))
        ports.append(first if model.timing.chambers[model.actions[first].station].type == 'LoadPort' else last)
    for index, port in enumerate(ports):
        model.station_edge(port, ports[(index + 1) % len(ports)], int(index + 1 == len(ports)))
    return original
