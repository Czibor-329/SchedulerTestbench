"""无清洁轮转模板的动作级周期约束，保留门、转位、压力和机器人并行。

事件取 Pick/Place/Swap 的开始；同资源按固定顺序复用，异资源可重叠。
只读取设备和路线事实，不运行 Cycle，不从排程结果拟合周期。重入与中转链
仍由原聚合建图器负责。文档：D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""
from dataclasses import dataclass

from app.backend.theory_capacity.solver import WaitingConstraint
from app.backend.theory_capacity.contracts import CycleServiceLifetime, CycleServicePart
from app.backend.theory_capacity.input_model import DeviceDurations

WITH_WAFER = 1
EMPTY_HAND = 0


@dataclass(frozen=True)
class StationAccess:
    """一次站点访问；before/body/after 分别占用站点、双方、站点。"""

    event: str
    station: str
    robot: str
    action: str
    before: float
    body: float
    after: float
    process: float = 0.0


class CycleActionTimingGraph:
    """从固定轮转顺序生成差分约束及独占资源负荷，无外部副作用。"""

    def __init__(self, problem, facts):
        """读取 CapacityInput 计时和已验证的路线事实，初始化一个完整相位周期。"""
        self.problem, self.facts = problem, facts
        self.durations = DeviceDurations(problem)
        self.rounds = facts.rounds_per_cycle
        self.accesses = {}
        self.services = {}
        self.edges = []
        self.loads = {}

    def service(self, key, station, robot, category, parts, detail):
        """登记具名分项，返回服务键；重复服务必须具有完全相同的定义。"""
        item = CycleServiceLifetime(key, category, station, robot,
            sum(seconds for _, seconds in parts),
            tuple(CycleServicePart(label, seconds) for label, seconds in parts), detail)
        if key in self.services and self.services[key] != item:
            raise ValueError(f"服务定义不一致：{key}")
        self.services[key] = item
        return key

    def edge(self, left, right, shift, parts, detail, service_key=None):
        """将物理等待分项写成非负跨期边；分项总和严格等于边时长。"""
        key = service_key or f"timing:{left.event}->{right.event}:{shift}:{len(self.edges)}"
        self.service(key, right.station, right.robot, 'timing', parts, detail)
        self.edges.append(WaitingConstraint(left.event, right.event,
            sum(value for _, value in parts), shift, key))

    def access(self, event, station, robot, action, key, process=0.0):
        """登记站点完整服务和动作事件；Swap 使用一次开门及真实站内转位。"""
        timing = self.durations
        before = timing.place_pre(robot, station) if action == 'place' else timing.pick_pre(robot, station)
        after = timing.pick_post(robot, station) if action == 'pick' else timing.place_post(robot, station)
        if action == 'swap':
            body_parts = [(f'{robot} 取片', timing.pick_t(robot, station)),
                          (f'{robot} 站内转位', timing.move(robot, station, station, WITH_WAFER)),
                          (f'{robot} 放片', timing.place_t(robot, station))]
        else:
            body_parts = [(f'{robot} {"取片" if action == "pick" else "放片"}',
                timing.pick_t(robot, station) if action == 'pick' else timing.place_t(robot, station))]
        body = sum(seconds for _, seconds in body_parts)
        self.service(key, station, robot, 'process-swap' if key.startswith('process-swap:') else 'transfer',
            [(f'{station} 开门准备', before), *body_parts, (f'{station} 关门', after)],
            f'{robot} 在 {station} {action}（门与机器人分别占用）')
        row = StationAccess(event, station, robot, action, before, body, after, process)
        self.accesses[event] = row
        chamber = self.problem.chambers[station]
        prefix = 'LoadLock' if chamber.type == 'LoadLock' else '加工腔' if process or station in {
            c for node in self.facts.process_nodes for c in node.chambers} else 'LoadPort'
        name = f'{prefix} {station}'
        self.loads[name] = self.loads.get(name, 0.0) + before + body + after + process
        return row

    def robot_chain(self, sequence, prefix):
        """串接机器人；源站 Pick 可提前开门，目标站准备与接近转位并行。"""
        work = 0.0
        for index, left in enumerate(sequence):
            right = sequence[(index + 1) % len(sequence)]
            shift = int(index + 1 == len(sequence))
            carried = left.action in ('pick', 'swap')
            travel = 0.0 if left.station == right.station else self.durations.move(
                left.robot, left.station, right.station, WITH_WAFER if carried else EMPTY_HAND)
            key = f'{prefix}:move:{left.station}->{right.station}'
            self.service(key, right.station, left.robot, 'transfer',
                [(f'{left.robot} 转位 {left.station}→{right.station}', travel)], '不同站点间机器人转位')
            self.edge(left, right, shift, [('前动作', left.body), ('转位', travel)],
                f'{left.robot} 完成 {left.station} 动作并到达 {right.station}')
            # 对持片前往目标的动作，开门不能早于该次接近开始；与转位取 max 而非求和。
            if right.action != 'pick' and left.station != right.station:
                changes_environment = (
                    self.facts.lock_mode == 'dedicated-entry-exit'
                    and right.station in self.facts.pressure_domains and right.action == 'place')
                # 分向锁的放片事务会先完成空锁切压。Machine 对发生环境转换的
                # 目标禁用接近期间开门，必须等机器人到达后再 Prepare。
                self.edge(left, right, shift,
                    [('前动作', left.body), ('切压目标到达', travel if changes_environment else 0.0),
                     ('目标开门', right.before)],
                    f'{right.station} 切压后到达再开门' if changes_environment
                    else f'{right.station} 在机器人接近期间准备')
            work += left.body + travel
        label = '真空机器人' if prefix == 'vacuum' else '大气机器人'
        self.loads[f'{label} {sequence[0].robot}'] = work

    def station_chain(self, sequence, pressure_kinds=()):
        """串接同站访问；关门、加工或切压完成后才允许下次开门。"""
        kinds = pressure_kinds or ('',) * len(sequence)
        for index, (left, kind) in enumerate(zip(sequence, kinds)):
            right = sequence[(index + 1) % len(sequence)]
            pressure = 0.0
            if kind:
                chamber = self.problem.chambers[left.station]
                pressure = float((chamber.pump_time if kind == 'pump' else chamber.vent_time) or 0.0)
                self.service(f'pressure:{left.station}:{kind}', left.station, '', 'pressure',
                    [(f'{left.station} {kind}', pressure)], f'{left.station} {kind}')
                self.loads[f'LoadLock {left.station}'] += pressure
            shift = int(index + 1 == len(sequence))
            node = next((node for node in self.facts.process_nodes if left.station in node.chambers), None)
            service_key = f'process-cycle:{left.station}:{node.ordinal}' if node is not None else None
            self.edge(left, right, shift,
                [('前动作', left.body), ('关门', left.after), ('加工', left.process),
                 ('切压', pressure), ('下次开门', right.before)],
                f'{left.station} 关门及服务完成后再次开放', service_key=service_key)

    def build(self):
        """按三种锁分工建立 VAC/ATM 顺序，返回边、服务、资源负荷和事件名。"""
        facts = self.facts
        vacuum, atmosphere = [], []
        visits = []
        process_accesses = {}
        for index in range(self.rounds):
            entry = facts.pressure_domains[index % 2] if facts.lock_mode == 'shared-bidirectional' else facts.pressure_domains[0]
            exit_lock = facts.pressure_domains[-1] if facts.lock_mode == 'dedicated-entry-exit' else entry
            shared = facts.lock_mode == 'shared-bidirectional'
            incoming = self.access(f'LOCK{index}' if shared else f'LOCK_IN{index}', entry,
                facts.vacuum_robot, 'swap' if shared else 'pick',
                f'vacuum-swap:{entry}' if shared else f'vacuum-pick:{entry}')
            vacuum.append(incoming)
            for node_index, node in enumerate(facts.process_nodes):
                chamber = node.chambers[index % len(node.chambers)]
                row = self.access(f'PM{node_index + 1}_{index}', chamber, facts.vacuum_robot,
                    'swap', f'process-swap:{chamber}', node.process_seconds_of(chamber))
                vacuum.append(row)
                process_accesses.setdefault(chamber, []).append(row)
            outgoing = incoming if shared else self.access(f'LOCK_OUT{index}', exit_lock,
                facts.vacuum_robot, 'place', f'vacuum-place:{exit_lock}')
            if not shared:
                vacuum.append(outgoing)
            receive = self.access(f'ATM_receive{index}', exit_lock, facts.atmosphere_robot,
                'pick', f'atm-receive:{exit_lock}')
            home = self.access(f'ATM_home{index}', facts.sink_station, facts.atmosphere_robot,
                'place', f'atm-home:{facts.sink_station}')
            raw = self.access(f'ATM_raw{index}', facts.source_station, facts.atmosphere_robot,
                'pick', f'atm-raw:{facts.source_station}')
            feed = self.access(f'ATM_feed{index}', entry, facts.atmosphere_robot,
                'place', f'atm-feed:{entry}')
            atmosphere.extend([feed, receive, home, raw] if facts.lock_mode == 'dedicated-entry-exit'
                else [receive, home, raw, feed])
            visits.append((incoming, outgoing, receive, feed))
        self.robot_chain(vacuum, 'vacuum')
        self.robot_chain(atmosphere, 'atm')
        # Machine 的独立 LL Pick 避免过早持片：到达首 PM 不早于其加工结束。
        # 源取片完成与转位可贴近目标释放，不能让入口提早取空从而提前空充。
        if facts.lock_mode != 'shared-bidirectional':
            first_node = facts.process_nodes[0]
            for index, (incoming, _, _, _) in enumerate(visits):
                chamber = first_node.chambers[index % len(first_node.chambers)]
                previous_index = index - len(first_node.chambers)
                previous = self.accesses[f'PM1_{previous_index % self.rounds}']
                travel = self.durations.move(facts.vacuum_robot, incoming.station, chamber, WITH_WAFER)
                delay = previous.body + previous.after + previous.process - travel - incoming.body
                if delay > 0:
                    self.edge(previous, incoming, int(previous_index < 0),
                        [('首 PM 释放前的准时取片下界', delay)], f'取片后准时到达 {chamber}')
        for rows in process_accesses.values():
            self.station_chain(rows)
        # LoadPort 的全槽共门，回盒关门与下一次取片开门不能重叠。
        for station in {facts.source_station, facts.sink_station}:
            self.station_chain([row for row in atmosphere if row.station == station])
        for station in facts.pressure_domains:
            rows, kinds = [], []
            for incoming, outgoing, receive, feed in visits:
                if facts.lock_mode == 'shared-bidirectional' and incoming.station == station:
                    rows.extend([incoming, receive, feed])
                    kinds.extend(['vent', '', 'pump'])
                elif facts.lock_mode == 'single-bidirectional':
                    rows.extend([incoming, outgoing, receive, feed])
                    kinds.extend(['', 'vent', '', 'pump'])
                elif facts.lock_mode == 'dedicated-entry-exit':
                    if incoming.station == station:
                        rows.extend([incoming, feed])
                        kinds.extend(['vent', 'pump'])
                    else:
                        rows.extend([outgoing, receive])
                        kinds.extend(['vent', 'pump'])
            self.station_chain(rows, kinds)
        return tuple(self.edges), tuple(self.services.values()), self.loads, tuple(self.accesses)
