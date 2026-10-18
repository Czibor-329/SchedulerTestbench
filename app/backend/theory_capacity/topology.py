"""平台无清洁理论产能的静态路线分类与可建图条件。

只识别完整静态路线；没有生产策略的冷启动、残留晶圆或重算相位恢复。
固定模板边界见 D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""
from dataclasses import dataclass

from app.backend.theory_capacity.input_model import CapacityInput

LOCK_MODE_SHARED = 'shared-bidirectional'
LOCK_MODE_DEDICATED = 'dedicated-entry-exit'
LOCK_MODE_SINGLE = 'single-bidirectional'
ROUTE_KIND_SINGLE_NODE = 'single-node'
ROUTE_KIND_PARALLEL = 'parallel'
ROUTE_KIND_SERIAL = 'serial'
ROUTE_KIND_MIXED = 'mixed'
ROUTE_KIND_REENTRANT = 'reentrant'


@dataclass(frozen=True)
class UnsupportedTopology:
    """无法建图时的稳定原因码与用户可见说明。"""
    reason: str
    detail: str
    supported: bool = False


@dataclass(frozen=True)
class ProcessNode:
    """一道加工工序的位置及固定轮转的候选腔顺序。"""
    stage_index: int
    chambers: tuple[str, ...]


@dataclass(frozen=True)
class CapacityTopology:
    """设备建图器消费的静态事实。"""
    route_name: str
    lock_mode: str
    route_kind: str
    pressure_domains: tuple[str, ...]
    atmosphere_robot: str
    vacuum_robot: str
    entry_stage_index: int
    exit_stage_index: int
    process_nodes: tuple[ProcessNode, ...]
    supported: bool = True


def analyze_capacity_topology(problem: CapacityInput):
    """识别锁分工、加工链及必要能力；不支持的输入返回结构化拒绝。"""
    if not problem.wafers:
        return UnsupportedTopology('empty-input', '路线中没有参考产品')
    if problem.pre_clean or problem.post_clean:
        return UnsupportedTopology('cleaning-not-supported', '独立理论产能当前仅支持无清洁')
    reference = problem.wafers[0]
    stages = reference.stages
    for product in problem.wafers:
        for stage in product.stages:
            if stage.residency >= 0 or any(value >= 0 for value in stage.residency_by_chamber.values()):
                return UnsupportedTopology('residency-not-supported', '理论产能不支持有限驻留约束')
            if stage.qtime >= 0 or any(value >= 0 for value in stage.qtime_by_chamber.values()):
                return UnsupportedTopology('qtime-not-supported', '理论产能不支持 Q-time 约束')
            if stage.clean_trigger or stage.clean_time or stage.clean_task:
                return UnsupportedTopology('cleaning-not-supported', '独立理论产能当前仅支持无清洁')
    locks = [index for index, stage in enumerate(stages) if stage.stage_type == 'loadlock']
    if len(locks) != 2:
        return UnsupportedTopology('unsupported-loadlock-layout', '完整路线需要入口和出口两个 LoadLock 访问')
    entry_index, exit_index = locks
    entry, exit_stage = stages[entry_index], stages[exit_index]
    incoming = tuple(entry.cands or [entry.chamber])
    outgoing = tuple(exit_stage.cands or [exit_stage.chamber])
    if incoming == outgoing and len(incoming) in (1, 2):
        mode = LOCK_MODE_SINGLE if len(incoming) == 1 else LOCK_MODE_SHARED
        domains = incoming
    elif len(incoming) == len(outgoing) == 1 and incoming != outgoing:
        mode, domains = LOCK_MODE_DEDICATED, (*incoming, *outgoing)
    else:
        return UnsupportedTopology('unsupported-loadlock-layout', '不支持该压力通道的进出组合')
    vacuum, atmosphere = entry.out_robot, entry.in_robot
    if vacuum not in problem.robots or atmosphere not in problem.robots:
        return UnsupportedTopology('missing-robot', '路线缺少大气或真空机器人')
    if problem.robots[vacuum].capacity < 2 or not problem.robots[vacuum].can_swap:
        return UnsupportedTopology('vacuum-swap-not-supported', '真空机器人需要两个独立手槽和 Swap 能力')
    if problem.robots[atmosphere].capacity != 1:
        return UnsupportedTopology('atmosphere-hand-count-not-supported', '大气机器人需要一个逻辑持片位')
    minimum_slots = 2 if mode == LOCK_MODE_SHARED else 1
    if any(problem.chambers[name].capacity < minimum_slots for name in domains):
        return UnsupportedTopology('loadlock-capacity-not-supported', 'LoadLock 容量不足以执行当前进出模板')
    nodes = tuple(ProcessNode(index, tuple(stage.cands or [stage.chamber]))
                  for index, stage in enumerate(stages) if entry_index < index < exit_index)
    if not nodes or any(stages[node.stage_index].stage_type != 'process' for node in nodes):
        return UnsupportedTopology('unsupported-route-shape', '真空段必须是完整加工链')
    if any(stages[node.stage_index].in_robot != vacuum or stages[node.stage_index].out_robot != vacuum for node in nodes):
        return UnsupportedTopology('unsupported-route-shape', '当前仅支持一台真空机器人服务的加工链')
    repeated = any(set(left.chambers).intersection(right.chambers)
                   for index, left in enumerate(nodes) for right in nodes[index + 1:])
    if repeated:
        kind = ROUTE_KIND_REENTRANT
    elif len(nodes) == 1:
        kind = ROUTE_KIND_SINGLE_NODE if len(nodes[0].chambers) == 1 else ROUTE_KIND_PARALLEL
    elif all(len(node.chambers) == 1 for node in nodes):
        kind = ROUTE_KIND_SERIAL
    elif len(nodes) == 2 and sorted(len(node.chambers) for node in nodes) == [1, 2]:
        kind = ROUTE_KIND_MIXED
    else:
        return UnsupportedTopology('unsupported-route-shape', '该加工组合没有对应的固定周期模板')
    if (mode == LOCK_MODE_SINGLE and kind not in (ROUTE_KIND_SINGLE_NODE, ROUTE_KIND_PARALLEL, ROUTE_KIND_REENTRANT)
            or mode == LOCK_MODE_DEDICATED and kind == ROUTE_KIND_MIXED):
        return UnsupportedTopology('unsupported-route-shape', '该锁分工暂不支持所选加工组合')
    return CapacityTopology(reference.route_name, mode, kind, domains, atmosphere, vacuum,
                            entry_index, exit_index, nodes)
