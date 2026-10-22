"""固定 Cycle 模板的周期产能分析。

输入为周期事件及等待边，单位均为秒：第 k 期的目标事件必须晚于第
k-cycle_shift 期的源事件 minimum_delay_seconds 秒。求解最小可行周期，
同时返回限制周期的闭合等待链及可行相位。仅分析输入图明确表达的约束；
不会自动把设备 JSON 转换为完整物理模型，也不替代 Machine 校验。
共享双锁、无清洁的单工序并行模型提供具名构造器；其它模板可显式输入事件图。
文档依据：D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping, Sequence

SECONDS_PER_HOUR = 3600.0
DEFAULT_TOLERANCE_SECONDS = 1e-7
MAXIMUM_SOLVER_ITERATIONS = 100


@dataclass(frozen=True)
class WaitingConstraint:
    """有向等待边；cycle_shift 是跨越的完整周期数，label 解释物理原因。"""

    source: str
    target: str
    minimum_delay_seconds: float
    cycle_shift: int = 0
    label: str = ""


@dataclass(frozen=True)
class CapacityModel:
    """一个重复周期的事件图、产出物理片数和各资源每周期占用下界。

    resource_work_seconds 是解释用资源负荷，不得包含可并行的其它资源等待。
    求解器也将每项负荷加入周期下界，避免调用方遗漏单独的资源工作量。
    units_per_cycle 明确按物理产品片计数；Dummy 不计产出。
    """

    name: str
    events: tuple[str, ...]
    constraints: tuple[WaitingConstraint, ...]
    units_per_cycle: float
    resource_work_seconds: Mapping[str, float]
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapacityReport:
    """周期数值、吞吐、资源下界、关键等待链和可行事件相位。"""

    model_name: str
    period_seconds: float
    seconds_per_unit: float
    physical_wafers_per_hour: float
    resource_period_lower_bound_seconds: float
    resource_upper_bound_wafers_per_hour: float | None
    critical_chain: tuple[WaitingConstraint, ...]
    event_offsets_seconds: Mapping[str, float]
    tolerance_seconds: float
    assumptions: tuple[str, ...]

    def to_dict(self) -> dict:
        """生成可直接保存为 JSON 的完整报告。"""
        return asdict(self)


def _finite_nonnegative(value: float, field: str) -> float:
    """拒绝负时长、NaN、无穷大和布尔值，避免生成不可解释的产能。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须是有限非负数")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} 必须是有限非负数")
    return number


def _validated_edges(model: CapacityModel) -> tuple[WaitingConstraint, ...]:
    """校验事件、跨期索引和负荷；将资源工作量作为显式周期下界边。"""
    if not model.events or len(set(model.events)) != len(model.events):
        raise ValueError("events 必须非空且事件名不能重复")
    if any(not isinstance(event, str) or not event for event in model.events):
        raise ValueError("事件名必须是非空字符串")
    if _finite_nonnegative(model.units_per_cycle, "units_per_cycle") <= 0:
        raise ValueError("每周期物理产出必须大于零")
    edges = list(model.constraints)
    for edge in edges:
        if edge.source not in model.events or edge.target not in model.events:
            raise ValueError(f"等待边引用未知事件：{edge}")
        _finite_nonnegative(edge.minimum_delay_seconds, "minimum_delay_seconds")
        if isinstance(edge.cycle_shift, bool) or not isinstance(edge.cycle_shift, int) or edge.cycle_shift < 0:
            raise ValueError("cycle_shift 必须是非负整数")
    for resource, workload in model.resource_work_seconds.items():
        _finite_nonnegative(workload, f"资源 {resource} 的负荷")
        edges.append(WaitingConstraint(model.events[0], model.events[0], workload, 1, f"{resource} 每周期工作量"))
    return tuple(edges)


def _check_period(events: Sequence[str], edges: Sequence[WaitingConstraint], period: float):
    """用最长路松弛检查周期是否可行；正环返回独立的闭合等待链见证。"""
    offsets = {event: 0.0 for event in events}
    predecessor: dict[str, WaitingConstraint] = {}
    changed = None
    for _ in events:
        changed = None
        for edge in edges:
            candidate = offsets[edge.source] + edge.minimum_delay_seconds - edge.cycle_shift * period
            if candidate > offsets[edge.target] + 1e-12:
                offsets[edge.target] = candidate
                predecessor[edge.target] = edge
                changed = edge.target
        if changed is None:
            minimum = min(offsets.values())
            return True, {key: value - minimum for key, value in offsets.items()}, ()
    # 第 |V| 轮仍能增长，沿前驱走 |V| 步即可进入正环。
    current = changed
    for _ in events:
        current = predecessor[current].source
    first = current
    chain = []
    while True:
        edge = predecessor[current]
        chain.append(edge)
        current = edge.source
        if current == first:
            break
    return False, offsets, tuple(reversed(chain))


def analyze_capacity(model: CapacityModel, *, tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS) -> CapacityReport:
    """求输入事件模型的最小周期，返回误差不超过 tolerance_seconds 的可行上端点。

    同期正环意味着任意周期都不可行。其它正环的边时长之和除以跨期数之和，
    给出该环的周期下界。二分检查所有环，避免枚举简单环的指数复杂度。
    该结果是固定图的模型理论产能；调用方必须说明事件图的适用范围。
    """
    tolerance = _finite_nonnegative(tolerance_seconds, "tolerance_seconds")
    if tolerance <= 0:
        raise ValueError("求解误差必须大于零")
    edges = _validated_edges(model)
    same_cycle_edges = tuple(edge for edge in edges if edge.cycle_shift == 0)
    if not _check_period(model.events, same_cycle_edges, 0.0)[0]:
        raise ValueError("存在同期正环：固定事件顺序不可能执行")
    if _check_period(model.events, edges, 0.0)[0]:
        raise ValueError("模型没有正周期下界，不能报告有限产能")
    lower = 0.0
    upper = max(1.0, sum(edge.minimum_delay_seconds for edge in edges))
    if not math.isfinite(upper):
        raise ValueError("总时长超出浮点数范围")
    witness = ()
    for _ in range(MAXIMUM_SOLVER_ITERATIONS):
        if upper - lower <= tolerance:
            break
        middle = (lower + upper) / 2
        feasible, _, chain = _check_period(model.events, edges, middle)
        if feasible:
            upper = middle
        else:
            lower = middle
            witness = chain
    else:
        raise ValueError("指定精度在浮点数范围内无法达到")
    feasible, offsets, _ = _check_period(model.events, edges, upper)
    if not feasible:
        raise RuntimeError("求解器未产生可行周期")
    resource_floor = max(model.resource_work_seconds.values(), default=0.0)
    return CapacityReport(
        model.name, upper, upper / model.units_per_cycle,
        SECONDS_PER_HOUR * model.units_per_cycle / upper,
        resource_floor,
        SECONDS_PER_HOUR * model.units_per_cycle / resource_floor if resource_floor else None,
        witness, offsets, tolerance, model.assumptions,
    )


def shared_parallel_model(
    process_seconds: Sequence[float], *, loadlock_swap_seconds: float = 8.0,
    chamber_swap_seconds: float = 8.0, vacuum_travel_seconds: float = 4.0,
    atmosphere_service_seconds: float = 16.0, atmosphere_between_locks_seconds: float = 4.0,
    pump_seconds: float = 20.0, vent_seconds: float = 20.0,
    physical_members_per_unit: int = 1,
) -> CapacityModel:
    """构造共享 A/B 双锁、单工序 1/2/3 腔固定轮转的简化周期模型。

    Swap 时间是完整服务占用；加工从 PM 服务结束计到下次 PM 服务开始。
    ATM service 包括回原盒、再取生片和补同锁，锁间转位另计；压力服务可
    与机器人、其它锁并行。未表达门/转位重叠、清洁、尾对和跨端口服务，
    因此默认参数是教学假设，不能当成生产设备标定值。
    """
    if len(process_seconds) not in (1, 2, 3):
        raise ValueError("内置模型只支持单工序 1/2/3 台腔室")
    if isinstance(physical_members_per_unit, bool) or physical_members_per_unit not in (1, 2):
        raise ValueError("每逻辑单元的物理成员数必须是 1 或 2")
    durations = tuple(_finite_nonnegative(value, "process_seconds") for value in process_seconds)
    parameters = (loadlock_swap_seconds, chamber_swap_seconds, vacuum_travel_seconds,
                  atmosphere_service_seconds, atmosphere_between_locks_seconds, pump_seconds, vent_seconds)
    for value in parameters:
        _finite_nonnegative(value, "服务时长")
    rounds = math.lcm(2, len(durations))
    events = tuple(f"{kind}{index}" for index in range(rounds) for kind in ("LL", "PM", "ATM"))
    edges = []

    def connect(source_kind, index, target_kind, next_index, delay, label):
        """把展开轮号折回一个完整周期，并保存真实跨期数。"""
        edges.append(WaitingConstraint(f"{source_kind}{index}", f"{target_kind}{next_index % rounds}",
                                       delay, next_index // rounds, label))

    for index in range(rounds):
        chamber = index % len(durations)
        connect("LL", index, "PM", index, loadlock_swap_seconds + vacuum_travel_seconds, "VAC 从锁换片后到 PM")
        connect("PM", index, "LL", index + 1, chamber_swap_seconds + vacuum_travel_seconds, "VAC 从 PM 到下一把锁")
        connect("PM", index, "PM", index + len(durations), chamber_swap_seconds + durations[chamber], f"PM{chamber + 1} 加工完成后再换片")
        connect("LL", index, "ATM", index, loadlock_swap_seconds + vent_seconds, "该锁换片并 Vent 后 ATM 才能回补")
        connect("ATM", index, "LL", index + 2, atmosphere_service_seconds + pump_seconds, "同锁回补并 Pump 后 VAC 再访问")
        connect("ATM", index, "ATM", index + 1, atmosphere_service_seconds + atmosphere_between_locks_seconds, "单臂 ATM 按顺序服务下一把锁")
    loads = {"VAC": rounds * (loadlock_swap_seconds + chamber_swap_seconds + 2 * vacuum_travel_seconds),
             "ATM": rounds * (atmosphere_service_seconds + atmosphere_between_locks_seconds)}
    for index, process in enumerate(durations):
        loads[f"PM{index + 1}"] = rounds / len(durations) * (process + chamber_swap_seconds)
    for lock in ("A", "B"):
        loads[f"LoadLock {lock}"] = rounds / 2 * (loadlock_swap_seconds + vent_seconds + atmosphere_service_seconds + pump_seconds)
    return CapacityModel(
        "共享双锁单工序固定轮转", events, tuple(edges), rounds * physical_members_per_unit, loads,
        ("无限连续供片、固定时长、无清洁、无 Dummy", "Swap 与加工的边界采用完整服务结束/开始",
         "有效服务时长是显式输入，不从设备 JSON 自动推断", "全部事件相位可以重新安排；生产 Machine 未必采用该最优相位"),
    )
