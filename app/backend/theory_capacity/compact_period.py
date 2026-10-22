"""固定四种加工路线、三种锁分工的独立周期计算核，覆盖当前九个有效组合。

只依赖 input_model 的只读计时查询；模板展开、资源负荷、周期约束与二分均在本文件。
不调用旧建图器或求解器，不读取配置；可选 trace 输出原始计算证据供页面解释。
输入是已解析的计时和有序加工候选，假定无清洁、无中转、无驻留/Q-time、单片压力批、
同一源汇 LoadPort、无限连续供片；双腔由上游完成逻辑计时合并后传入成员数。
与现有实现按同一求解容差比较，而非要求浮点最后一位相同。
依据：D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""
from math import isfinite, lcm
from typing import NamedTuple
from app.backend.theory_capacity.input_model import DeviceDurations

SECONDS_PER_HOUR = 3600.0
DEFAULT_TOLERANCE = 1e-7
RELAXATION_EPSILON = 1e-12
MAXIMUM_ITERATIONS = 100
SHARED, DEDICATED, SINGLE = "shared-bidirectional", "dedicated-entry-exit", "single-bidirectional"
WITH_WAFER, EMPTY_HAND = 1, 0


class _Access(NamedTuple):
    """一次取放开始事件；门占站点，body 同时占机器人和站点。"""
    index: int
    station: str
    robot: str
    kind: str
    before: float
    body: float
    after: float
    process: float


def _seconds(value):
    """拒绝非法时长及溢出，防止约束静默失效。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError("时间必须是有限非负数")
    return float(value)


def _feasible(period, event_count, constraints, offsets=None):
    """检查给定周期是否存在可行相位；第 |V| 轮仍增长意味着正环。"""
    offsets = offsets if offsets is not None else [0.0] * event_count
    for _ in range(event_count):
        changed = False
        for source, target, delay, shift in constraints:
            required = offsets[source] + delay - shift * period
            if required > offsets[target] + RELAXATION_EPSILON:
                offsets[target] = required
                changed = True
        if not changed:
            return True
    return False


def calculate_period(timing: DeviceDurations, process_nodes, loadlocks, lock_mode,
                     vacuum_robot, atmosphere_robot, load_port, *,
                     members_per_unit=1, tolerance_seconds=DEFAULT_TOLERANCE, trace=None):
    """计算完整相位周期及物理产能，返回数值字典，不修改输入。

    process_nodes 为有序的 ({腔名: 加工秒数}, ...)；形状支持 (1)、(1,1)、(3)、(1,2)。
    loadlocks 为一或两把逻辑锁，分向时顺序为入口、出口；机器人与端口传入实际名称。
    timing 只提供设备计时查询，members_per_unit 为 1/2；容差单位秒且必须为正。
    调用方负责输入解析与上述无清洁范围校验；本核拒绝非法组合、计时及缺失能力。
    trace 若为字典，成功后写入动作、分类约束、负荷和可行相位；不改变数值结果。
    """
    shape = tuple(len(node) for node in process_nodes)
    allowed = {SHARED: ((1,), (1, 1), (3,), (1, 2)),
               DEDICATED: ((1,), (1, 1), (3,)), SINGLE: ((1,), (3,))}
    chambers = [name for node in process_nodes for name in node]
    required_locks = 1 if lock_mode == SINGLE else 2
    if shape not in allowed.get(lock_mode, ()) or len(loadlocks) != required_locks:
        raise ValueError("不支持的加工组合或锁分工")
    names = [load_port, *loadlocks, *chambers]
    if len(set(names)) != len(names) or any(name not in timing.chambers for name in names):
        raise ValueError("站点必须存在且不能重入或混用角色")
    if type(members_per_unit) is not int or members_per_unit not in (1, 2):
        raise ValueError("每逻辑单元必须包含 1 或 2 个物理成员")
    tolerance = _seconds(tolerance_seconds)
    if not tolerance:
        raise ValueError("求解容差必须大于零")
    vacuum, atmosphere = timing.robots[vacuum_robot], timing.robots[atmosphere_robot]
    if vacuum.capacity < 2 or not vacuum.can_swap or atmosphere.capacity != 1:
        raise ValueError("需要可换片的双持片位 VAC 和单持片位 ATM")
    if any(timing.chambers[name].type != "LoadLock" or
           timing.chambers[name].capacity < (2 if lock_mode == SHARED else 1) for name in loadlocks):
        raise ValueError("锁类型或容量不满足模板要求")
    rounds = lcm(2 if lock_mode == SHARED else 1, *shape)
    accesses, constraints, loads = [], [], {}
    categories = []
    vacuum_sequence, atmosphere_sequence, visits, process_sequences = [], [], [], {}

    def access(station, robot, kind, process=0.0):
        """展开动作；只累计站点占用，机器人负荷在串接时单独计算。"""
        if station not in timing.robots[robot].scope:
            raise ValueError(f"机器人 {robot} 无法访问 {station}")
        before = timing.place_pre(robot, station) if kind == "place" else timing.pick_pre(robot, station)
        after = timing.pick_post(robot, station) if kind == "pick" else timing.place_post(robot, station)
        body = timing.pick_t(robot, station) if kind != "place" else timing.place_t(robot, station)
        if kind == "swap":
            body = _seconds(body) + _seconds(timing.move(robot, station, station, WITH_WAFER)) + _seconds(timing.place_t(robot, station))
        row = _Access(len(accesses), station, robot, kind, *map(_seconds, (before, body, after, process)))
        accesses.append(row)
        loads[station] = loads.get(station, 0.0) + row.before + row.body + row.after + row.process
        return row

    def connect(left, right, delay, shift, category):
        """保存 target >= source + delay - shift*T 的数值约束。"""
        constraints.append((left.index, right.index, _seconds(delay), shift))
        categories.append(category)

    # 展开完整锁/PM 轮转：共享锁 Swap，其余锁按 Pick/Place 分开；ATM 固定回补顺序。
    for index in range(rounds):
        entry = loadlocks[index % 2] if lock_mode == SHARED else loadlocks[0]
        exit_lock = loadlocks[-1] if lock_mode == DEDICATED else entry
        incoming = access(entry, vacuum_robot, "swap" if lock_mode == SHARED else "pick")
        vacuum_sequence.append(incoming)
        for node in process_nodes:
            chamber = tuple(node)[index % len(node)]
            row = access(chamber, vacuum_robot, "swap", node[chamber])
            vacuum_sequence.append(row)
            process_sequences.setdefault(chamber, []).append(row)
        outgoing = incoming if lock_mode == SHARED else access(exit_lock, vacuum_robot, "place")
        if lock_mode != SHARED:
            vacuum_sequence.append(outgoing)
        receive = access(exit_lock, atmosphere_robot, "pick")
        home = access(load_port, atmosphere_robot, "place")
        raw = access(load_port, atmosphere_robot, "pick")
        feed = access(entry, atmosphere_robot, "place")
        atmosphere_sequence.extend((feed, receive, home, raw) if lock_mode == DEDICATED else (receive, home, raw, feed))
        visits.append((incoming, outgoing, receive, feed))

    # 机器人独占工作与目标开门分别约束，普通接近可重叠，分向锁 Place 必须到达再开门。
    for sequence in (vacuum_sequence, atmosphere_sequence):
        work = 0.0
        for index, left in enumerate(sequence):
            right, shift = sequence[(index + 1) % len(sequence)], int(index + 1 == len(sequence))
            travel = 0.0 if left.station == right.station else _seconds(timing.move(
                left.robot, left.station, right.station, WITH_WAFER if left.kind in ("pick", "swap") else EMPTY_HAND))
            connect(left, right, left.body + travel, shift, "vacuum" if left.robot == vacuum_robot else "atmosphere")
            if right.kind != "pick" and left.station != right.station:
                sequential = lock_mode == DEDICATED and right.station in loadlocks and right.kind == "place"
                connect(left, right, left.body + (travel if sequential else 0.0) + right.before, shift, "preparation")
            work += left.body + travel
        loads[("robot", sequence[0].robot)] = work

    # 独立入口 Pick 的准时到达约束：不能提前取空入口，从而提前触发空锁切压。
    if lock_mode != SHARED:
        first_node = tuple(process_nodes[0])
        for index, (incoming, _, _, _) in enumerate(visits):
            chamber = first_node[index % len(first_node)]
            previous = process_sequences[chamber][(index // len(first_node) - 1) % len(process_sequences[chamber])]
            travel = _seconds(timing.move(vacuum_robot, incoming.station, chamber, WITH_WAFER))
            delay = previous.body + previous.after + previous.process - travel - incoming.body
            if delay > 0:
                connect(previous, incoming, delay, int(index < len(first_node)), "handoff")

    # 每个站点独立复用；锁链同时指定本次动作之后的压力转换。
    station_sequences = [(rows, [0.0] * len(rows)) for rows in process_sequences.values()]
    port_rows = [row for row in atmosphere_sequence if row.station == load_port]
    station_sequences.append((port_rows, [0.0] * len(port_rows)))
    for station in loadlocks:
        rows, pressures = [], []
        pump, vent = (_seconds(getattr(timing.chambers[station], field)) for field in ("pump_time", "vent_time"))
        for incoming, outgoing, receive, feed in visits:
            if lock_mode == SHARED and incoming.station != station:
                continue
            if lock_mode == SHARED:
                selected = (incoming, receive, feed), (vent, 0.0, pump)
            elif lock_mode == SINGLE:
                selected = (incoming, outgoing, receive, feed), (0.0, vent, 0.0, pump)
            elif incoming.station == station:
                selected = (incoming, feed), (vent, pump)
            else:
                selected = (outgoing, receive), (vent, pump)
            rows.extend(selected[0])
            pressures.extend(selected[1])
        loads[station] += sum(pressures)
        station_sequences.append((rows, pressures))
    for rows, pressures in station_sequences:
        for index, left in enumerate(rows):
            right = rows[(index + 1) % len(rows)]
            category = "lock" if left.station in loadlocks else "process" if left.station in chambers else "port"
            connect(left, right, left.body + left.after + left.process + pressures[index] + right.before, int(index + 1 == len(rows)), category)

    # 加入资源负荷下界，二分求最短可行周期；与现有求解器使用相同松弛误差。
    constraints.extend((0, 0, _seconds(work), 1) for work in loads.values())
    categories.extend(["resource"] * len(loads))
    event_count = len(accesses)
    if not _feasible(0.0, event_count, [edge for edge in constraints if edge[3] == 0]):
        raise ValueError("存在同期正环")
    if _feasible(0.0, event_count, constraints):
        raise ValueError("模型没有正周期下界")
    lower, upper = 0.0, _seconds(max(1.0, sum(edge[2] for edge in constraints)))
    for _ in range(MAXIMUM_ITERATIONS):
        if upper - lower <= tolerance:
            break
        middle = lower + (upper - lower) / 2
        if _feasible(middle, event_count, constraints):
            upper = middle
        else:
            lower = middle
    else:
        raise ValueError("浮点精度不足以达到指定容差")
    offsets = [0.0] * event_count
    if not _feasible(upper, event_count, constraints, offsets):
        raise RuntimeError("未得到可行周期")
    if trace is not None:
        trace.update(accesses=accesses, constraints=constraints, categories=categories, loads=loads,
                     offsets=offsets, rounds=rounds, vacuum_sequence=vacuum_sequence, atmosphere_sequence=atmosphere_sequence)
    units = rounds * members_per_unit
    return {"period_seconds": upper, "units_per_cycle": units, "seconds_per_unit": upper / units,
            "physical_wafers_per_hour": SECONDS_PER_HOUR * units / upper,
            "resource_period_lower_bound_seconds": max(loads.values())}
