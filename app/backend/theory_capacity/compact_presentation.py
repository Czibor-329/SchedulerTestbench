"""精简周期核的页面数据适配：扫描列、资源说明及循环交互证据。

消费已解析的 CapacityInput，不读取工作空间，不调用旧建图器/求解器。
数值求解只由 compact_period 完成；此处为同一次求解的 trace 加中文标签，
提供全部约束及一个可行相位下的余量，余量并非唯一的设备实际等待时间。
"""
from app.backend.theory_capacity.compact_period import calculate_period, DEFAULT_TOLERANCE
from app.backend.theory_capacity.input_model import DeviceDurations

CATEGORY_LABELS = {
    "vacuum": "VAC 动作与转位", "atmosphere": "ATM 动作与转位",
    "preparation": "目标开门准备", "handoff": "入口准时交接",
    "lock": "锁恢复与交接", "process": "PM 加工与复用", "port": "LoadPort 复用",
}
ACTION_LABELS = {"swap": "交换", "pick": "取片", "place": "放片"}
SCAN_TOLERANCE = 1e-3


def _limiting_kind(result, tolerance):
    """使用求解容差区分资源下界和额外交接限制。"""
    return "resource" if result["period_seconds"] - result["resource_period_lower_bound_seconds"] <= tolerance else "waiting-chain"


def _action_parts(timing, action, sign=1):
    """返回动作的设备计时分项；sign=-1 表示准时交接公式中的扣除项。"""
    prefix = f"{action.robot} · {action.station}"
    values = [(f"{prefix} 取片", timing.pick_t(action.robot, action.station))] if action.kind != "place" else []
    if action.kind == "swap":
        values.append((f"{prefix} 站内带片转位", timing.move(action.robot, action.station, action.station, 1)))
    if action.kind != "pick":
        values.append((f"{prefix} 放片", timing.place_t(action.robot, action.station)))
    return [{"label": label + ("（扣除）" if sign < 0 else ""), "seconds": sign * value} for label, value in values]


def _constraint_evidence(timing, topology, left, right, category):
    """按核的计时边界解释约束；返回有符号分项与原因，不重新求解或建图。"""
    parts = _action_parts(timing, left)
    travel_type = 1 if left.kind in ("pick", "swap") else 0
    travel = timing.move(left.robot, left.station, right.station, travel_type) if category in ("vacuum", "atmosphere", "preparation") and left.station != right.station else 0
    travel_part = {"label": f"{left.robot} · {left.station} → {right.station} {'带片' if travel_type else '空手'}转位", "seconds": travel}
    if category in ("vacuum", "atmosphere"):
        parts.append(travel_part)
        reason = f"{left.robot} 必须先完成 {left.station} 的动作，" + (f"再转位到 {right.station}，" if left.station != right.station else "同站衔接无需跨站转位，") + "才能开始下一动作。"
    elif category == "preparation":
        sequential = topology.lock_mode == "dedicated-entry-exit" and right.station in topology.pressure_domains and right.kind == "place"
        if sequential:
            parts.append(travel_part)
        parts.append({"label": f"{right.station} · {right.robot} 侧开门", "seconds": right.before})
        reason = "分向锁放片必须在到达后开门，因此转位和开门串行相加。" if sequential else "目标开门可与机器人转位重叠；本边只约束前动作和开门，转位由另一条机器人顺序边约束，两条同时满足。"
    elif category == "handoff":
        parts.extend([{"label": f"{left.station} 关门", "seconds": left.after}, {"label": f"{left.station} 加工（当前工艺时间）", "seconds": left.process}])
        parts.append({"label": f"{right.robot} · {right.station} → {left.station} 带片转位（扣除）", "seconds": -timing.move(right.robot, right.station, left.station, 1)})
        parts.extend(_action_parts(timing, right, -1))
        reason = "入口取片应按加工腔释放时刻倒推；扣除入口取片和带片转位时间，避免提前取空入口并触发切压。"
    else:
        parts.extend([{"label": f"{left.station} · {left.robot} 侧关门", "seconds": left.after}, {"label": f"{left.station} 加工（当前工艺时间）", "seconds": left.process}])
        if category == "lock" and left.robot != right.robot:
            pump = right.robot == topology.vacuum_robot
            pressure = getattr(timing.chambers[left.station], "pump_time" if pump else "vent_time")
            parts.append({"label": f"{left.station} {'抽气' if pump else '充气'}", "seconds": pressure})
        parts.append({"label": f"{right.station} · {right.robot} 侧开门", "seconds": right.before})
        reason = f"{left.station} 再次被访问前，必须完成前动作、关门" + ("和压力恢复" if category == "lock" and left.robot != right.robot else "及加工" if category == "process" else "") + "，再完成下一动作的开门准备。"
    return parts, reason


def calculate_preset_payload(problem, topology, members, window):
    """返回页面扫描列及单点推导；window 为秒制起止步长，不修改输入。"""
    if not topology.supported:
        raise ValueError(topology.detail)
    stages = problem.wafers[0].stages
    timing = DeviceDurations(problem)
    nodes = tuple({name: stages[node.stage_index].process_time_by_chamber.get(name, stages[node.stage_index].proc)
                   for name in node.chambers} for node in topology.process_nodes)
    arguments = dict(timing=timing, loadlocks=topology.pressure_domains, lock_mode=topology.lock_mode,
                     vacuum_robot=topology.vacuum_robot, atmosphere_robot=topology.atmosphere_robot,
                     load_port=stages[0].chamber, members_per_unit=members)
    trace = {}
    result = calculate_period(process_nodes=nodes, trace=trace, **arguments)
    facts = dict(route_name=topology.route_name, lock_mode=topology.lock_mode, route_kind=topology.route_kind,
                 pressure_domains=list(topology.pressure_domains), vacuum_robot=topology.vacuum_robot,
                 atmosphere_robot=topology.atmosphere_robot, rounds_per_cycle=trace["rounds"],
                 units_per_cycle=result["units_per_cycle"], physical_members_per_unit=members,
                 loadlock_batch_size=1, source_station=stages[0].chamber, sink_station=stages[-1].chamber,
                 inbound_relays=[], outbound_relays=[], chamber_counts=[len(node) for node in nodes],
                 process_nodes=[dict(ordinal=index + 1, alias="/".join(node), chambers=list(node))
                                for index, node in enumerate(nodes)])
    # 原始动作与约束直接来自数值核，展示层不再重新建立一份事件图。
    actions = trace["accesses"]
    per_round = len(actions) // trace["rounds"]
    minimum_offset = min(trace["offsets"])
    events = [dict(id=str(row.index), station=row.station, robot=row.robot,
                   lane="vacuum" if row.robot == topology.vacuum_robot else "atmosphere",
                   round=row.index // per_round + 1,
                   label=f"{row.station} {ACTION_LABELS[row.kind]}",
                   phase_seconds=trace["offsets"][row.index] - minimum_offset,
                   before_seconds=row.before, body_seconds=row.body, after_seconds=row.after,
                   process_seconds=row.process) for row in actions]
    edges = []
    for index, ((source, target, delay, shift), category) in enumerate(zip(trace["constraints"], trace["categories"])):
        if category == "resource":
            continue  # 负荷下界单列，不伪装成动作的自循环。
        left, right = actions[source], actions[target]
        parts, reason = _constraint_evidence(timing, topology, left, right, category)
        elapsed = trace["offsets"][target] - trace["offsets"][source] + shift * result["period_seconds"]
        edges.append(dict(id=str(index), source=str(source), target=str(target), category=category,
                          label=CATEGORY_LABELS[category], minimum_delay_seconds=delay, cycle_shift=shift,
                          detail=reason, parts=parts,
                          slack_seconds=max(0.0, elapsed - delay)))
    loads = {}
    for key, work in trace["loads"].items():
        label = f"机器人 {key[1]}" if isinstance(key, tuple) else (
            f"LoadLock {key}" if key in topology.pressure_domains else
            f"加工腔 {key}" if any(key in node for node in nodes) else f"LoadPort {key}")
        loads[label] = work
    kind = _limiting_kind(result, DEFAULT_TOLERANCE)
    derivation = dict(result, facts=facts, model_name="compact-period", resource_work_seconds=loads,
                      resource_upper_bound_wafers_per_hour=3600 * result["units_per_cycle"] / max(loads.values()),
                      limiting_kind=kind, limiting_label=max(loads, key=loads.get) if kind == "resource" else "交接约束共同限制",
                      cycle_diagram=dict(events=[events[row.index] for row in
                          trace["vacuum_sequence"] + trace["atmosphere_sequence"]], edges=edges),
                      scope_note="无清洁、无限连续供片、固定轮转；精简核验证全部周期约束，不含启动与收尾。")
    points = {key: [] for key in ("process_seconds", "period_seconds", "seconds_per_unit", "physical_wafers_per_hour", "limiting_kind")}
    first, last, step = window
    for index in range(int(round((last - first) / step)) + 1):
        seconds = first + index * step
        point = calculate_period(process_nodes=tuple({name: seconds for name in node} for node in nodes),
                                 tolerance_seconds=SCAN_TOLERANCE, **arguments)
        point.update(process_seconds=seconds, limiting_kind=_limiting_kind(point, SCAN_TOLERANCE))
        for key in points:
            points[key].append(point[key] if key == "limiting_kind" else round(point[key], 2 if key == "physical_wafers_per_hour" else 3))
    return points, derivation
