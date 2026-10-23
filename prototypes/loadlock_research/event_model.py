"""研究用周期事件图：动作时间、机器人顺序、模块复用与压力批的显式约束。

输入为平台只读 DeviceDurations，输出为周期秒数和可核对的动作相位。
本模块不调用生产 Cycle，不假定两把锁交替；不生成生产 MoveList。
仅研究单片搬运、双臂 VAC、单臂 ATM、无清洁和无限供片。
时间约定参见 D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""
from dataclasses import asdict, dataclass
from math import isfinite


EPSILON = 1e-8


@dataclass(frozen=True)
class Access:
    """取放本体的开始事件；开关门只占站点，取放本体同时占机器人和站点。"""
    index: int
    station: str
    robot: str
    kind: str
    before: float
    body: float
    after: float
    process: float = 0.0
    round: int = -1


class EventModel:
    """保存事件与 x[target] >= x[source] + delay - shift*T 的周期约束。"""

    def __init__(self, timing):
        """绑定只读设备计时；每个候选拥有独立的事件和约束。"""
        self.timing = timing
        self.actions = []
        self.edges = []
        self.labels = []
        self.carried_after = {}

    def access(self, station, robot, kind, process=0.0, round=-1):
        """读取设备取放与站内转位时间，追加动作并返回其索引。"""
        timing = self.timing
        before = timing.place_pre(robot, station) if kind == 'place' else timing.pick_pre(robot, station)
        after = timing.pick_post(robot, station) if kind == 'pick' else timing.place_post(robot, station)
        body = timing.place_t(robot, station) if kind == 'place' else timing.pick_t(robot, station)
        if kind == 'swap':
            body += timing.move(robot, station, station, 1) + timing.place_t(robot, station)
        values = (before, body, after, process)
        if any(not isfinite(value) or value < 0 for value in values):
            raise ValueError('研究输入含非法动作时间')
        index = len(self.actions)
        self.actions.append(Access(index, station, robot, kind, *values, round))
        return index

    def edge(self, source, target, delay, shift=0, label=''):
        """追加因果约束；shift 必须为整数，允许跨周期引用。"""
        self.edges.append((source, target, float(delay), int(shift)))
        self.labels.append(label)

    def station_edge(self, source, target, shift=0, pressure=0.0):
        """前次关门、加工/切压完成后，下一次访问才可开门。"""
        left, right = self.actions[source], self.actions[target]
        self.edge(source, target, left.body + left.after + left.process + pressure + right.before,
                  shift, 'station:' + left.station)

    def robot_edge(self, source, target, shift=0):
        """独占搬运与目标开门分别约束，保留关门和离站转位的并行。"""
        left, right = self.actions[source], self.actions[target]
        carried = int(bool(self.carried_after.get(source, left.kind in ('pick', 'swap'))))
        # 与 Machine 的 destination_approach_duration 一致：独立同站访问不加跨站转位。
        # Swap 内部换臂转位已计入 body，不能再次省略或重复累加。
        travel = 0.0 if left.station == right.station else self.timing.move(
            left.robot, left.station, right.station, carried)
        self.edge(source, target, left.body + travel, shift, 'robot:' + left.robot)
        if right.kind != 'pick' and left.station != right.station:
            self.edge(source, target, left.body + right.before, shift, 'prepare:' + right.station)

    def robot_cycle(self, sequence):
        """串接一台机器人完整周期的动作，最后一条边跨一个周期。"""
        for index, source in enumerate(sequence):
            self.robot_edge(source, sequence[(index + 1) % len(sequence)], int(index + 1 == len(sequence)))

    def offsets(self, period):
        """用 Bellman-Ford 判定周期并返回可行相位；正环返回 None。"""
        values = [0.0] * len(self.actions)
        weighted = [(source, target, delay - shift * period) for source, target, delay, shift in self.edges]
        for _ in values:
            changed = False
            for source, target, weight in weighted:
                required = values[source] + weight
                if required > values[target] + EPSILON:
                    values[target] = required
                    changed = True
            if not changed:
                return values
        return None

    def solve(self, upper, tolerance=1e-6):
        """在给定可行上界内求最小周期；上界不可行时返回 None。"""
        if self.offsets(upper) is None:
            return None
        lower = 0.0
        while upper - lower > tolerance:
            middle = (lower + upper) / 2
            if self.offsets(middle) is None:
                lower = middle
            else:
                upper = middle
        return upper

    def evidence(self, period):
        """导出原始事件、约束和相位，供另一求解器与多周期校验复核。"""
        offsets = self.offsets(period)
        if offsets is None:
            raise ValueError('不能导出不可行的周期')
        return dict(actions=[asdict(row) for row in self.actions], edges=list(self.edges),
                    labels=list(self.labels), offsets=offsets, period_seconds=period)
