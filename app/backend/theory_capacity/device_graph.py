"""设备级 Cycle 理论产能建图器：把路线事实与设备计时写成固定周期事件图。

本模块是 ``solver`` 通用求解器与 Cycle 模板事实之间的桥：

- ``solver.py`` 只依赖标准库、只接受显式秒数，不读设备 JSON；
- 本模块把编译后的 ``CapacityInput``（含双腔逻辑对片视图）与 平台静态识别器输出的
  ``CapacityTopology`` 转换为该求解器可解的等待边图，并输出可逐项核对的设备
  派生服务时长表。

本模块不参与生产排程。非重入、无中转站的页面固定组合交给
``timing_graph.CycleActionTimingGraph`` 建立动作开始事件图：原子 Swap
只有一次开门、Pick、站内转位、Place、一次关门；站点门与机器人跨站转位
分别建边，允许真实的重叠；机器人已经在目标站时不生成跨站转位。
序列中的同资源动作互斥，压力与加工仍等待关门完成。图中每条等待边的分项和
恰好等于边时长。资源负荷不把站点门占用累计为机器人占用。

重入与含中转站的旧接口仍使用本文件的聚合模型，未纳入本次动作级一致性保证。
理论值假定一个固定源/汇端口、无限连续供片且轮转相位已经稳定；有限片数、
不同端口、Job 边界与实时重算的回盒统计不是同一口径。

覆盖范围（其余输入必须显式拒绝，不得静默近似）：

- 锁分工：共享双向双锁、固定单进单出、唯一同锁进出；
- 路线形态：单节点、纯串行、纯并行、混合，以及独立重入的逐片加工链；
- 不含清洁、Dummy、驻留上限、Q-time、双片压力批与级联重入。

文档依据：``D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md``。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from app.backend.theory_capacity.solver import (
    CapacityModel,
    CapacityReport,
    WaitingConstraint,
    analyze_capacity,
)
from app.backend.theory_capacity.device_input import detect_dual_chamber
from app.backend.theory_capacity.input_model import DeviceDurations, CapacityInput, RouteStage
from app.backend.theory_capacity.topology import (
    LOCK_MODE_DEDICATED,
    LOCK_MODE_SHARED,
    LOCK_MODE_SINGLE,
    ROUTE_KIND_MIXED,
    ROUTE_KIND_PARALLEL,
    ROUTE_KIND_REENTRANT,
    ROUTE_KIND_SERIAL,
    ROUTE_KIND_SINGLE_NODE,
)

# 每逻辑单元的物理成员数：双腔设备的一个逻辑对片含两个物理成员。
SINGLE_CHAMBER_PHYSICAL_MEMBERS = 1
DUAL_CHAMBER_PHYSICAL_MEMBERS = 2
SUPPORTED_PHYSICAL_MEMBER_COUNTS = (
    SINGLE_CHAMBER_PHYSICAL_MEMBERS,
    DUAL_CHAMBER_PHYSICAL_MEMBERS,
)

# 转位类型：带片与空手；语义见模块文档。
TRANSFER_WITH_WAFER = 1
TRANSFER_EMPTY_HAND = 0

# 固定模型每周期压力批为一个逻辑单元。两片批需要重建锁内依赖与产出计数，
# 本模块明确拒绝而不是近似。
DEFAULT_LOADLOCK_BATCH_SIZE = 1

# 共享双向双锁固定使用两个逻辑压力域（A/B 交替）。
SHARED_PRESSURE_DOMAIN_COUNT = 2

# ATM 四段式回补阶段；阶段常量同时用作事件名后缀与服务键前缀。
ATM_RECEIVE = "atm-receive"
ATM_HOME = "atm-home"
ATM_RAW = "atm-raw"
ATM_FEED = "atm-feed"

# 共享锁与唯一同锁先回收成品再补生片；固定分向锁先补入口再收出口。
ATM_STAGE_ORDER = {
    LOCK_MODE_SHARED: (ATM_RECEIVE, ATM_HOME, ATM_RAW, ATM_FEED),
    LOCK_MODE_SINGLE: (ATM_RECEIVE, ATM_HOME, ATM_RAW, ATM_FEED),
    LOCK_MODE_DEDICATED: (ATM_FEED, ATM_RECEIVE, ATM_HOME, ATM_RAW),
}

_SERVICE_CATEGORY_VACUUM = "vacuum-swap"
_SERVICE_CATEGORY_PROCESS = "process-swap"
_SERVICE_CATEGORY_PRESSURE = "pressure"
_SERVICE_CATEGORY_TRANSFER = "transfer"
_SERVICE_CATEGORY_ATMOSPHERE = "atmosphere"

_LIMITING_RESOURCE = "resource"
_LIMITING_WAITING_CHAIN = "waiting-chain"

# 扫描曲线使用的周期求解误差。曲线用于比较趋势，毫秒级精度已足够；收紧到
# 求解器默认的 1e-7 会让每个采样点多做一倍二分迭代。
SCAN_TOLERANCE_SECONDS = 1e-3

_LOCK_SHARED_PREFIX = "LOCK"
_LOCK_IN_PREFIX = "LOCK_IN"
_LOCK_OUT_PREFIX = "LOCK_OUT"
_PROCESS_NODE_PREFIX = "PM"
_ATMOSPHERE_PREFIX = "ATM_"

# Dummy 物料的编号下界；产品晶圆按更小的编号界定，与编译器的 Dummy 约定一致。
_DUMMY_MATERIAL_ID_FLOOR = 100000


class CycleCapacityUnsupported(ValueError):
    """本建图器无法表达的输入。

    :param reason: 稳定错误码，供接口与页面按码分类，不解析中文文案。
    :param detail: 面向使用者的中文原因说明。
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


from app.backend.theory_capacity.contracts import (
    CycleServicePart, CycleServiceLifetime, CycleProcessNode, CycleCapacityFacts,
    CycleCapacityModel, CycleCapacityAnalysis, CycleCapacityPoint, CycleCapacityScan,
)


def _finite_seconds(value: Any, field_name: str) -> float:
    """读取设备计时字段；缺失按 0，非法值明确报错。"""
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CycleCapacityUnsupported(
            "invalid-device-timing",
            f"设备计时字段 {field_name} 必须是非负有限数字，实际为 {value!r}。",
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0.0:
        raise CycleCapacityUnsupported(
            "invalid-device-timing",
            f"设备计时字段 {field_name} 必须是非负有限数字，实际为 {number!r}。",
        )
    return number


def _least_common_multiple(values: Sequence[int]) -> int:
    """求一组正整数的最小公倍数；空集合按 1。"""
    result = 1
    for value in values:
        number = int(value)
        if number < 1:
            raise CycleCapacityUnsupported(
                "invalid-chamber-count",
                f"轮转公倍数要求正整数，实际为 {number}。",
            )
        result = result // gcd(result, number) * number
    return result


def _reference_wafer(problem: CapacityInput) -> Any:
    """返回路线形态的参考晶圆；输入没有产品晶圆时拒绝建图。"""
    wafers = [
        wafer for wafer in problem.wafers
        if int(getattr(wafer, "mat_id", 0)) < _DUMMY_MATERIAL_ID_FLOOR
    ]
    if not wafers:
        raise CycleCapacityUnsupported(
            "empty-input",
            "编译结果不含产品晶圆，无法建立固定周期事件图。",
        )
    return min(wafers, key=lambda wafer: int(wafer.wid))


def physical_members_per_unit(device_stations: Mapping[str, Any]) -> int:
    """返回一个逻辑循环单元包含的物理晶圆数；双腔设备为两片。"""
    return (
        DUAL_CHAMBER_PHYSICAL_MEMBERS
        if detect_dual_chamber(device_stations)
        else SINGLE_CHAMBER_PHYSICAL_MEMBERS
    )


def reference_process_seconds(
    problem: CapacityInput,
    topology: Any,
) -> Tuple[Mapping[str, float], ...]:
    """按工序顺序返回每个候选腔的加工秒数，取自编译后的参考晶圆。

    编译层已把 Route 的 Recipe 时间、双腔逻辑视图与 ``MoveTimeOffset`` 折算
    成 ``RouteStage.proc`` 与 ``RouteStage.process_time_by_chamber``；这里只读取，不重新
    解释 Route，保证扫描与单点推导使用同一输入口径。
    """
    wafer = _reference_wafer(problem)
    stages = list(wafer.stages)
    result: List[Mapping[str, float]] = []
    for node in topology.process_nodes:
        index = int(node.stage_index)
        if index < 0 or index >= len(stages):
            raise CycleCapacityUnsupported(
                "topology-stage-mismatch",
                f"工序下标 {index} 超出参考晶圆的 {len(stages)} 个工序。",
            )
        stage = stages[index]
        by_chamber = {
            str(chamber): _finite_seconds(seconds, f"工序 {index} 加工时间")
            for chamber, seconds in dict(stage.process_time_by_chamber or {}).items()
        }
        for chamber in node.chambers:
            by_chamber.setdefault(
                str(chamber), _finite_seconds(stage.proc, f"工序 {index} 加工时间"),
            )
        result.append(by_chamber)
    return tuple(result)


def _relay_stages(
    problem: CapacityInput,
    topology: Any,
) -> Tuple[str, str, Tuple[RouteStage, ...], Tuple[RouteStage, ...]]:
    """返回参考路线的源站、汇站与真空锁两侧顺序经过的中转工序。

    真空段之外的中转站（Aligner、Buffer、Cooler 等）由 ATM 顺序经过，会影响
    ATM 每轮工作量，因此必须单独取出而不是忽略。
    """
    wafer = _reference_wafer(problem)
    stages = list(wafer.stages)
    entry = int(topology.entry_stage_index)
    exit_index = int(topology.exit_stage_index)
    source = str(stages[0].chamber)
    sink = str(stages[-1].chamber)
    inbound = tuple(stages[1:entry]) if entry > 1 else ()
    outbound = tuple(stages[exit_index + 1:-1]) if exit_index + 1 < len(stages) - 1 else ()
    return source, sink, inbound, outbound


class _ServiceBook:
    """服务时长登记簿：同一段服务只登记一次并记录组成项。"""

    def __init__(self) -> None:
        self._services: dict[str, CycleServiceLifetime] = {}
        self._order: List[str] = []

    def register(
        self,
        key: str,
        *,
        category: str,
        station: str,
        robot: str,
        parts: Sequence[Tuple[str, float]],
        detail: str,
    ) -> CycleServiceLifetime:
        """登记一段聚合服务；同键重复登记时保留首次结果。"""
        existing = self._services.get(key)
        if existing is not None:
            return existing
        entries = tuple(
            CycleServicePart(label=str(label), seconds=float(seconds))
            for label, seconds in parts
        )
        service = CycleServiceLifetime(
            key=key,
            category=category,
            station=str(station),
            robot=str(robot),
            seconds=sum(entry.seconds for entry in entries),
            parts=entries,
            detail=str(detail),
        )
        self._services[key] = service
        self._order.append(key)
        return service

    def find(self, key: str) -> Optional[CycleServiceLifetime]:
        """按键查找服务；未登记返回 ``None``。"""
        return self._services.get(key)

    def seconds(self, key: str) -> float:
        """返回已登记服务的聚合时长；未登记说明建图逻辑漏登记，必须报错。"""
        service = self._services.get(key)
        if service is None:
            raise CycleCapacityUnsupported(
                "service-not-registered",
                f"等待边引用了未登记的服务 {key}，事件图不完整。",
            )
        return service.seconds

    def services(self) -> Tuple[CycleServiceLifetime, ...]:
        """按登记顺序返回全部服务。"""
        return tuple(self._services[key] for key in self._order)


class _EquipmentTiming:
    """设备派生的聚合服务计时器：只读取编译后的 CapacityInput 与 DeviceDurations。"""

    def __init__(self, problem: CapacityInput, book: _ServiceBook) -> None:
        self._problem = problem
        self._durations = DeviceDurations(problem)
        self._book = book

    @property
    def book(self) -> _ServiceBook:
        """返回该计时器写入的服务登记簿。"""
        return self._book

    def action_parts(self, robot: str, station: str, action: str) -> Tuple[Tuple[str, float], ...]:
        """把一次取或放拆成开门准备、机器人动作、取放后关门三段。"""
        if action == "pick":
            return (
                (f"{station} 开门准备", self._durations.pick_pre(robot, station)),
                (f"{robot} 从 {station} 取片", self._durations.pick_t(robot, station)),
                (f"{station} 取后关门", self._durations.pick_post(robot, station)),
            )
        if action == "place":
            return (
                (f"{station} 开门准备", self._durations.place_pre(robot, station)),
                (f"{robot} 向 {station} 放片", self._durations.place_t(robot, station)),
                (f"{station} 放后关门", self._durations.place_post(robot, station)),
            )
        raise ValueError(f"未知取放动作：{action}")

    def register_action(
        self,
        key: str,
        robot: str,
        station: str,
        action: str,
        *,
        category: str,
        detail: str,
    ) -> CycleServiceLifetime:
        """登记一次取或放。"""
        return self._book.register(
            key, category=category, station=station, robot=robot,
            parts=self.action_parts(robot, station, action), detail=detail,
        )

    def register_swap(
        self,
        key: str,
        robot: str,
        station: str,
        *,
        category: str,
        detail: str,
    ) -> CycleServiceLifetime:
        """登记一次换片：同一站点的取 + 放。"""
        return self._book.register(
            key, category=category, station=station, robot=robot,
            parts=(
                *self.action_parts(robot, station, "pick"),
                *self.action_parts(robot, station, "place"),
            ),
            detail=detail,
        )

    def register_transfer(
        self,
        key: str,
        robot: str,
        source: str,
        destination: str,
        trans_type: int,
    ) -> CycleServiceLifetime:
        """登记一次转位；缺四元组由 ``DeviceDurations.move`` 明确报错。"""
        seconds = self._durations.move(robot, source, destination, trans_type)
        hand = "带片" if trans_type == TRANSFER_WITH_WAFER else "空手"
        return self._book.register(
            key, category=_SERVICE_CATEGORY_TRANSFER, station=destination, robot=robot,
            parts=((f"{robot} {hand}转位 {source}→{destination}", seconds),),
            detail=f"{robot} {hand}转位 {source}→{destination}",
        )

    def register_pressure(self, key: str, station: str, kind: str) -> CycleServiceLifetime:
        """登记 LoadLock 的抽气或充气。"""
        chamber = self._problem.chambers.get(station)
        if chamber is None:
            raise CycleCapacityUnsupported(
                "unknown-station", f"路线引用了不存在的站点 {station}。",
            )
        value = chamber.pump_time if kind == "pump" else chamber.vent_time
        seconds = 0.0 if value is None else _finite_seconds(value, f"{station} {kind}")
        label = "抽气" if kind == "pump" else "充气"
        return self._book.register(
            key, category=_SERVICE_CATEGORY_PRESSURE, station=station, robot="",
            parts=((f"{station} {label}", seconds),),
            detail=f"{station} {label}",
        )


@dataclass(frozen=True)
class _RoundVisits:
    """一轮内的锁分工与所用加工腔。"""

    in_lock: str
    out_lock: str
    next_in_lock: str
    chambers: Tuple[str, ...]


def describe_route_facts(
    problem: CapacityInput,
    topology: Any,
    *,
    process_seconds: Optional[Sequence[Mapping[str, float]]] = None,
    members_per_unit: int = SINGLE_CHAMBER_PHYSICAL_MEMBERS,
    loadlock_batch_size: int = DEFAULT_LOADLOCK_BATCH_SIZE,
) -> CycleCapacityFacts:
    """把 Cycle 路线识别结果整理成与产能建图有关的稳定事实。

    :param problem: 编译后的调度输入。
    :param topology: ``analyze_capacity_topology`` 的识别结果。
    :param process_seconds: 可选的按工序加工秒数覆盖；缺省读取参考晶圆。
    :param members_per_unit: 一个逻辑循环单元包含的物理晶圆数。
    :param loadlock_batch_size: 单周期压力批片数；仅支持 1。
    :return: 路线事实，含轮转轮数、每周期产出与假设清单。
    :raises CycleCapacityUnsupported: 形态不受支持、压力批不为 1 或输入不完整时。
    """
    if not bool(getattr(topology, "supported", False)):
        raise CycleCapacityUnsupported(
            str(getattr(topology, "reason", "unsupported-topology")),
            str(getattr(topology, "detail", "Cycle 模板不支持该路线。")),
        )
    lock_mode = str(getattr(topology, "lock_mode", LOCK_MODE_SHARED))
    route_kind = str(getattr(topology, "route_kind", ROUTE_KIND_SINGLE_NODE))
    if lock_mode not in ATM_STAGE_ORDER:
        raise CycleCapacityUnsupported(
            "unsupported-lock-mode",
            f"锁分工 {lock_mode} 不在本建图器覆盖范围内。",
        )
    if route_kind not in (
        ROUTE_KIND_SINGLE_NODE, ROUTE_KIND_PARALLEL, ROUTE_KIND_SERIAL,
        ROUTE_KIND_MIXED, ROUTE_KIND_REENTRANT,
    ):
        raise CycleCapacityUnsupported(
            "unsupported-route-shape",
            f"路线形态 {route_kind} 不在本建图器覆盖范围内。",
        )
    if int(members_per_unit) not in SUPPORTED_PHYSICAL_MEMBER_COUNTS:
        raise CycleCapacityUnsupported(
            "invalid-physical-members",
            f"每逻辑单元的物理成员数只能是 1 或 2，实际为 {members_per_unit}。",
        )
    batch_size = int(loadlock_batch_size)
    if batch_size != DEFAULT_LOADLOCK_BATCH_SIZE:
        raise CycleCapacityUnsupported(
            "loadlock-batch-not-modeled",
            "两片压力批需要重新建立锁内依赖与产出计数，本建图器只支持单片批。",
        )
    overrides = (
        tuple(process_seconds)
        if process_seconds is not None
        else reference_process_seconds(problem, topology)
    )
    if len(overrides) != len(topology.process_nodes):
        raise CycleCapacityUnsupported(
            "process-node-count-mismatch",
            f"加工秒数覆盖了 {len(overrides)} 道工序，"
            f"路线识别到 {len(topology.process_nodes)} 道。",
        )
    nodes: List[CycleProcessNode] = []
    for ordinal, (node, seconds) in enumerate(zip(topology.process_nodes, overrides), start=1):
        chambers = tuple(str(chamber) for chamber in node.chambers)
        if not chambers:
            raise CycleCapacityUnsupported(
                "empty-process-candidates",
                f"第 {ordinal} 道加工工序没有候选腔，无法建立固定轮转。",
            )
        by_chamber = {str(key): value for key, value in dict(seconds).items()}
        nodes.append(CycleProcessNode(
            ordinal=ordinal,
            alias=f"{_PROCESS_NODE_PREFIX}{ordinal}",
            chambers=chambers,
            process_seconds={
                chamber: _finite_seconds(
                    by_chamber.get(chamber, 0.0), f"第 {ordinal} 道加工时间",
                )
                for chamber in chambers
            },
        ))
    domains = tuple(str(domain) for domain in getattr(topology, "pressure_domains", ()))
    if not domains:
        raise CycleCapacityUnsupported(
            "empty-pressure-domains",
            "路线识别结果没有压力域，无法建立进出循环。",
        )
    rotation = [len(node.chambers) for node in nodes]
    if lock_mode == LOCK_MODE_SHARED:
        rounds = _least_common_multiple([SHARED_PRESSURE_DOMAIN_COUNT, *rotation])
    else:
        rounds = _least_common_multiple([batch_size, *rotation])
    source, sink, inbound, outbound = _relay_stages(problem, topology)
    return CycleCapacityFacts(
        route_name=str(getattr(topology, "route_name", "")) or source,
        lock_mode=lock_mode,
        route_kind=route_kind,
        pressure_domains=domains,
        vacuum_robot=str(getattr(topology, "vacuum_robot", "")),
        atmosphere_robot=str(getattr(topology, "atmosphere_robot", "")),
        process_nodes=tuple(nodes),
        rounds_per_cycle=rounds,
        physical_members_per_unit=int(members_per_unit),
        loadlock_batch_size=batch_size,
        source_station=source,
        sink_station=sink,
        inbound_relays=tuple(str(stage.chamber) for stage in inbound),
        outbound_relays=tuple(str(stage.chamber) for stage in outbound),
        assumptions=(
            "一个循环单元每轮完成一次加工链交接，物理片数按逻辑单元成员数折算。",
            "无限连续供片、固定服务时长、无清洁、无 Dummy、无驻留上限与 Q-time。",
            "无中转的非重入模板采用动作级门/转位并行；重入与中转接口保留聚合服务口径。",
            "ATM 为单臂固定回收补片顺序；不同站点与机器人的占用分别建模。",
            "真空段外中转站按编译后的零时长工序处理，未额外添加校准时长。",
            "固定源/汇端口、无限连续供片的周期解；有限批次与补片重算统计另行比较。",
        ),
    )


class _EventGraphBuilder:
    """按轮结构生成等待边、服务时长表与资源负荷。"""

    def __init__(self, problem: CapacityInput, facts: CycleCapacityFacts, topology: Any) -> None:
        self._problem = problem
        self._facts = facts
        self._rounds = facts.rounds_per_cycle
        self._book = _ServiceBook()
        self._timing = _EquipmentTiming(problem, self._book)
        self._edges: List[WaitingConstraint] = []
        self._loads: dict[str, float] = {}
        source, sink, inbound, outbound = _relay_stages(problem, topology)
        self._source = source
        self._sink = sink
        self._inbound = tuple(inbound)
        self._outbound = tuple(outbound)
        self._rounds_visits = self._plan_rounds()

    # ---- 轮结构与事件名 -------------------------------------------------

    def _plan_rounds(self) -> Tuple[_RoundVisits, ...]:
        """按固定轮转计算每一轮的锁分工与所用加工腔。"""
        visits: List[_RoundVisits] = []
        for round_index in range(self._rounds):
            visits.append(_RoundVisits(
                in_lock=self._in_lock_of_round(round_index),
                out_lock=self._out_lock_of_round(round_index),
                next_in_lock=self._in_lock_of_round(round_index + 1),
                chambers=tuple(
                    node.chambers[round_index % len(node.chambers)]
                    for node in self._facts.process_nodes
                ),
            ))
        return tuple(visits)

    def _in_lock_of_round(self, round_index: int) -> str:
        """返回第 ``round_index`` 轮（0 基）使用的入口压力域。"""
        domains = self._facts.pressure_domains
        if self._facts.lock_mode == LOCK_MODE_SHARED:
            return domains[round_index % SHARED_PRESSURE_DOMAIN_COUNT]
        return domains[0]

    def _out_lock_of_round(self, round_index: int) -> str:
        """返回第 ``round_index`` 轮使用的出口压力域。"""
        domains = self._facts.pressure_domains
        if self._facts.lock_mode == LOCK_MODE_DEDICATED and len(domains) > 1:
            return domains[1]
        return self._in_lock_of_round(round_index)

    def _in_event(self, round_index: int) -> str:
        """入口锁事件名；共享锁一轮只有一个交换事件。"""
        if self._facts.lock_mode == LOCK_MODE_SHARED:
            return f"{_LOCK_SHARED_PREFIX}{round_index}"
        return f"{_LOCK_IN_PREFIX}{round_index}"

    def _out_event(self, round_index: int) -> str:
        """出口锁事件名；共享锁与入口事件相同。"""
        if self._facts.lock_mode == LOCK_MODE_SHARED:
            return f"{_LOCK_SHARED_PREFIX}{round_index}"
        return f"{_LOCK_OUT_PREFIX}{round_index}"

    def _process_event(self, node_index: int, round_index: int) -> str:
        """加工工序事件名；工序序号从 1 开始，与 PM 别名一致。"""
        return f"{_PROCESS_NODE_PREFIX}{node_index + 1}_{round_index}"

    def _atmosphere_event(self, round_index: int, stage: str) -> str:
        """ATM 阶段事件名。"""
        return f"{_ATMOSPHERE_PREFIX}{stage.removeprefix('atm-')}{round_index}"

    def _fold(self, next_index: int) -> Tuple[int, int]:
        """把展开轮号折回一个完整周期，并返回真实跨期数。"""
        return next_index % self._rounds, next_index // self._rounds

    def _add_edge(
        self,
        source_event: str,
        target_event: str,
        delay: float,
        cycle_shift: int,
        service_key: str,
    ) -> None:
        """登记一条等待边；服务键必须已登记，避免事件图与时长表脱节。"""
        self._book.seconds(service_key)
        self._edges.append(WaitingConstraint(
            source_event, target_event, float(delay), int(cycle_shift), service_key,
        ))

    # ---- 服务键 ---------------------------------------------------------

    def _swap_key(self, station: str) -> str:
        """共享锁换片服务键。"""
        return f"vacuum-swap:{station}"

    def _pick_key(self, station: str) -> str:
        """真空取片服务键（分向锁与同锁不做交换）。"""
        return f"vacuum-pick:{station}"

    def _place_key(self, station: str) -> str:
        """真空放片服务键（分向锁与同锁不做交换）。"""
        return f"vacuum-place:{station}"

    def _process_swap_key(self, chamber: str) -> str:
        """加工腔换片服务键。"""
        return f"process-swap:{chamber}"

    def _process_cycle_key(self, chamber: str, ordinal: int) -> str:
        """加工腔“换片 + 加工”服务键；同腔不同工序的加工时间可能不同。"""
        return f"process-cycle:{chamber}:{ordinal}"

    def _pressure_key(self, station: str, kind: str) -> str:
        """压力转换服务键。"""
        return f"pressure:{station}:{kind}"

    def _atm_key(self, stage: str, station: str) -> str:
        """ATM 阶段服务键。"""
        return f"{stage}:{station}"

    def _transfer_key(self, prefix: str, source: str, destination: str) -> str:
        """通用转位服务键。"""
        return f"{prefix}:move:{source}->{destination}"

    # ---- 建图主流程 -----------------------------------------------------

    def build(self) -> Tuple[Tuple[WaitingConstraint, ...], Tuple[CycleServiceLifetime, ...], Mapping[str, float]]:
        """生成等待边、服务时长表与资源负荷。"""
        self._register_lock_services()
        self._register_process_services()
        self._register_atmosphere_services()
        if self._facts.route_kind == ROUTE_KIND_REENTRANT:
            # 重入模板同一物理腔在一轮内被访问两次，且真空侧只允许一片在制，
            # 因此按“放片 → 加工 → 取片”逐段展开，不能套用换片轮转。
            self._add_reentrant_chain()
        else:
            self._add_vacuum_chain()
            self._add_process_reuse_edges()
        self._add_atmosphere_chain()
        self._add_lock_recovery_edges()
        self._fill_chamber_and_lock_loads()
        return tuple(self._edges), self._book.services(), dict(self._loads)

    def _register_lock_services(self) -> None:
        """登记压力域上的取放、换片与抽充气服务。"""
        robot = self._facts.vacuum_robot
        for lock in self._facts.pressure_domains:
            if self._facts.lock_mode == LOCK_MODE_SHARED:
                self._timing.register_swap(
                    self._swap_key(lock), robot, lock,
                    category=_SERVICE_CATEGORY_VACUUM,
                    detail=f"VAC 在共享锁 {lock} 上换片：取生片、放回上一轮成品",
                )
            else:
                self._timing.register_action(
                    self._pick_key(lock), robot, lock, "pick",
                    category=_SERVICE_CATEGORY_VACUUM,
                    detail=f"VAC 从锁 {lock} 取生片（不做锁上交换）",
                )
                self._timing.register_action(
                    self._place_key(lock), robot, lock, "place",
                    category=_SERVICE_CATEGORY_VACUUM,
                    detail=f"VAC 把成品放入锁 {lock}（不做锁上交换）",
                )
            self._timing.register_pressure(self._pressure_key(lock, "vent"), lock, "vent")
            self._timing.register_pressure(self._pressure_key(lock, "pump"), lock, "pump")

    def _register_process_services(self) -> None:
        """登记每道加工工序各候选腔的真空侧服务。

        轮转模板每次访问是一整次换片；重入模板每次访问只有放片或取片之一，
        因此分别登记，避免在服务时长表里出现没有使用的换片项。
        """
        robot = self._facts.vacuum_robot
        reentrant = self._facts.route_kind == ROUTE_KIND_REENTRANT
        for node in self._facts.process_nodes:
            for chamber in node.chambers:
                if reentrant:
                    self._timing.register_action(
                        self._process_place_key(chamber), robot, chamber, "place",
                        category=_SERVICE_CATEGORY_PROCESS,
                        detail=f"VAC 把晶圆放入 {chamber}（重入模板逐段访问）",
                    )
                    self._timing.register_action(
                        self._process_pick_key(chamber), robot, chamber, "pick",
                        category=_SERVICE_CATEGORY_PROCESS,
                        detail=f"VAC 从 {chamber} 取走已完成加工的晶圆（重入模板逐段访问）",
                    )
                    continue
                self._timing.register_swap(
                    self._process_swap_key(chamber), robot, chamber,
                    category=_SERVICE_CATEGORY_PROCESS,
                    detail=f"VAC 在 {chamber} 换片：取上一片、放入手上这一片",
                )

    def _chamber_access_seconds(self, chamber: str) -> float:
        """返回一次加工访问占用的真空侧服务时长；重入模板为放片加取片。"""
        if self._facts.route_kind == ROUTE_KIND_REENTRANT:
            return (
                self._book.seconds(self._process_place_key(chamber))
                + self._book.seconds(self._process_pick_key(chamber))
            )
        return self._book.seconds(self._process_swap_key(chamber))

    def _register_atmosphere_services(self) -> None:
        """登记 ATM 四段式回补的端点取放与锁间空手转位。"""
        robot = self._facts.atmosphere_robot
        self._timing.register_action(
            self._atm_key(ATM_HOME, self._sink), robot, self._sink, "place",
            category=_SERVICE_CATEGORY_ATMOSPHERE,
            detail=f"ATM 把成品放回 {self._sink}",
        )
        self._timing.register_action(
            self._atm_key(ATM_RAW, self._source), robot, self._source, "pick",
            category=_SERVICE_CATEGORY_ATMOSPHERE,
            detail=f"ATM 从 {self._source} 取下一片生片",
        )
        self._timing.register_transfer(
            self._transfer_key("atm-empty", self._sink, self._source),
            robot, self._sink, self._source, TRANSFER_EMPTY_HAND,
        )
        for lock in self._facts.pressure_domains:
            self._timing.register_action(
                self._atm_key(ATM_RECEIVE, lock), robot, lock, "pick",
                category=_SERVICE_CATEGORY_ATMOSPHERE,
                detail=f"ATM 从锁 {lock} 收走成品",
            )
            self._timing.register_action(
                self._atm_key(ATM_FEED, lock), robot, lock, "place",
                category=_SERVICE_CATEGORY_ATMOSPHERE,
                detail=f"ATM 把生片补入锁 {lock}",
            )

    def _add_vacuum_chain(self) -> None:
        """登记真空机器人的换片主线与它的每周期工作量。"""
        work = 0.0
        for round_index, visits in enumerate(self._rounds_visits):
            robot = self._facts.vacuum_robot
            # 第一段：从入口压力域取生片（共享锁为交换）并转到首道加工腔。
            start_key = (
                self._swap_key(visits.in_lock)
                if self._facts.lock_mode == LOCK_MODE_SHARED
                else self._pick_key(visits.in_lock)
            )
            move_key = self._transfer_key("vacuum", visits.in_lock, visits.chambers[0])
            self._timing.register_transfer(
                move_key, robot, visits.in_lock, visits.chambers[0], TRANSFER_WITH_WAFER,
            )
            delay = self._book.seconds(start_key) + self._book.seconds(move_key)
            work += delay
            self._add_edge(
                self._in_event(round_index), self._process_event(0, round_index),
                delay, 0, start_key,
            )
            # 第二段：沿加工链自内向外逐腔换片并转位。
            for node_index in range(1, len(visits.chambers)):
                swap_key = self._process_swap_key(visits.chambers[node_index])
                move_key = self._transfer_key(
                    "vacuum", visits.chambers[node_index - 1], visits.chambers[node_index],
                )
                self._timing.register_transfer(
                    move_key, robot, visits.chambers[node_index - 1],
                    visits.chambers[node_index], TRANSFER_WITH_WAFER,
                )
                delay = self._book.seconds(swap_key) + self._book.seconds(move_key)
                work += delay
                self._add_edge(
                    self._process_event(node_index - 1, round_index),
                    self._process_event(node_index, round_index),
                    delay, 0, swap_key,
                )
            # 第三段：尾道成品转到出口压力域。
            last = len(visits.chambers) - 1
            swap_key = self._process_swap_key(visits.chambers[last])
            move_key = self._transfer_key("vacuum", visits.chambers[last], visits.out_lock)
            self._timing.register_transfer(
                move_key, robot, visits.chambers[last], visits.out_lock, TRANSFER_WITH_WAFER,
            )
            delay = self._book.seconds(swap_key) + self._book.seconds(move_key)
            work += delay
            if self._facts.lock_mode == LOCK_MODE_SHARED:
                target_index, shift = self._fold(round_index + 1)
                self._add_edge(
                    self._process_event(last, round_index), self._in_event(target_index),
                    delay, shift, swap_key,
                )
            else:
                self._add_edge(
                    self._process_event(last, round_index), self._out_event(round_index),
                    delay, 0, swap_key,
                )
                work += self._book.seconds(self._place_key(visits.out_lock))
        self._loads[f"真空机器人 {self._facts.vacuum_robot}"] = work

    def _register_relay(
        self,
        robot: str,
        stages: Sequence[RouteStage],
        *,
        start: str,
        end: str,
        prefix: str,
        detail: str,
    ) -> float:
        """登记“持片经过中转工序”的完整时长，并累加中转站负荷。

        返回从 ``start`` 动作开始到 ``end`` 动作开始的秒数，即等待边时长。
        """
        total = 0.0
        previous = str(start)
        for stage in stages:
            station = str(stage.chamber)
            move_key = self._transfer_key(prefix, previous, station)
            self._timing.register_transfer(move_key, robot, previous, station, TRANSFER_WITH_WAFER)
            total += self._book.seconds(move_key)
            place_key = f"{prefix}:place:{station}"
            self._timing.register_action(
                place_key, robot, station, "place",
                category=_SERVICE_CATEGORY_ATMOSPHERE,
                detail=f"{robot} 把晶圆放到中转站 {station}",
            )
            pick_key = f"{prefix}:pick:{station}"
            self._timing.register_action(
                pick_key, robot, station, "pick",
                category=_SERVICE_CATEGORY_ATMOSPHERE,
                detail=f"{robot} 从中转站 {station} 取回晶圆",
            )
            stay = _finite_seconds(stage.proc, f"中转站 {station} 停留时间")
            total += self._book.seconds(place_key) + self._book.seconds(pick_key) + stay
            key = f"中转站 {station}"
            self._loads[key] = self._loads.get(key, 0.0) + (
                self._book.seconds(place_key) + self._book.seconds(pick_key) + stay
            )
            previous = station
        tail_key = self._transfer_key(prefix, previous, end)
        self._timing.register_transfer(tail_key, robot, previous, end, TRANSFER_WITH_WAFER)
        return total + self._book.seconds(tail_key)

    def _atmosphere_leg(self, round_index: int, stage: str) -> Tuple[float, str]:
        """返回 ATM 从 ``stage`` 段到下一段之间的等待时长与服务键。"""
        robot = self._facts.atmosphere_robot
        visits = self._rounds_visits[round_index]
        if stage == ATM_RECEIVE:
            key = self._atm_key(ATM_RECEIVE, visits.out_lock)
            total = self._book.seconds(key) + self._register_relay(
                robot, self._outbound,
                start=visits.out_lock, end=self._sink,
                prefix=f"atm-outbound:{visits.out_lock}",
                detail=f"ATM 带成品从 {visits.out_lock} 沿返程路线回到 {self._sink}",
            )
            return total, key
        if stage == ATM_HOME:
            key = self._atm_key(ATM_HOME, self._sink)
            empty_key = self._transfer_key("atm-empty", self._sink, self._source)
            return self._book.seconds(key) + self._book.seconds(empty_key), key
        if stage == ATM_RAW:
            key = self._atm_key(ATM_RAW, self._source)
            total = self._book.seconds(key) + self._register_relay(
                robot, self._inbound,
                start=self._source, end=visits.in_lock,
                prefix=f"atm-inbound:{visits.in_lock}",
                detail=f"ATM 带生片从 {self._source} 沿进程路线送到 {visits.in_lock}",
            )
            return total, key
        if stage != ATM_FEED:
            raise CycleCapacityUnsupported(
                "unknown-atmosphere-stage", f"未知 ATM 阶段 {stage}。",
            )
        key = self._atm_key(ATM_FEED, visits.in_lock)
        target_lock = (
            visits.out_lock if self._facts.lock_mode == LOCK_MODE_DEDICATED
            else visits.next_in_lock
        )
        empty_key = self._transfer_key("atm-empty", visits.in_lock, target_lock)
        self._timing.register_transfer(
            empty_key, robot, visits.in_lock, target_lock, TRANSFER_EMPTY_HAND,
        )
        return self._book.seconds(key) + self._book.seconds(empty_key), key

    def _add_atmosphere_chain(self) -> None:
        """登记 ATM 四段式回补链与它的每周期工作量。"""
        work = 0.0
        order = ATM_STAGE_ORDER[self._facts.lock_mode]
        for round_index in range(self._rounds):
            for stage_index, stage in enumerate(order):
                following = order[(stage_index + 1) % len(order)]
                wraps = stage_index + 1 == len(order)
                target_round = round_index + 1 if wraps else round_index
                target_index, shift = self._fold(target_round)
                delay, key = self._atmosphere_leg(round_index, stage)
                work += delay
                self._add_edge(
                    self._atmosphere_event(round_index, stage),
                    self._atmosphere_event(target_index, following),
                    delay, shift, key,
                )
        self._loads[f"大气机器人 {self._facts.atmosphere_robot}"] = work

    def _add_process_reuse_edges(self) -> None:
        """登记同一物理加工腔下一次访问前的换片与加工等待。"""
        visits: List[Tuple[int, int, str]] = []
        for round_index, round_visits in enumerate(self._rounds_visits):
            for node_index, chamber in enumerate(round_visits.chambers):
                visits.append((round_index, node_index, chamber))
        positions_by_chamber: dict[str, List[int]] = {}
        for position, (_round, _node, chamber) in enumerate(visits):
            positions_by_chamber.setdefault(chamber, []).append(position)
        for chamber, positions in positions_by_chamber.items():
            for order, position in enumerate(positions):
                following = positions[(order + 1) % len(positions)]
                shift = 0 if order + 1 < len(positions) else 1
                round_index, node_index, _used = visits[position]
                next_round, next_node, _next_used = visits[following]
                node = self._facts.process_nodes[node_index]
                key = self._process_cycle_key(chamber, node.ordinal)
                self._book.register(
                    key,
                    category=_SERVICE_CATEGORY_PROCESS,
                    station=chamber,
                    robot=self._facts.vacuum_robot,
                    parts=(
                        (f"{chamber} 换片占用", self._book.seconds(self._process_swap_key(chamber))),
                        (f"{chamber} 加工时间（第 {node.ordinal} 道工序）", node.process_seconds_of(chamber)),
                    ),
                    detail=f"{chamber} 换片并加工第 {node.ordinal} 道工序后，才能再次换片",
                )
                self._add_edge(
                    self._process_event(node_index, round_index),
                    self._process_event(next_node, next_round),
                    self._book.seconds(key), shift, key,
                )

    def _add_reentrant_chain(self) -> None:
        """登记独立重入模板的逐段“放片 → 加工 → 取片”链。

        重入路线里同一物理腔在一轮内被访问两次，且真空侧只允许一片在制；因此
        每次加工访问必须拆成放片与取片两个事件，中间等待该道工序的加工时间。
        整条链从本轮锁事件走到下一轮锁事件，给出串行的片间隔下界；资源负荷只
        累加真空侧真正占用的放片、取片与转位，不含晶圆在腔内的加工时间。
        """
        robot = self._facts.vacuum_robot
        work = 0.0
        for round_index, visits in enumerate(self._rounds_visits):
            # 起点：从入口压力域取生片（共享锁为交换），再转到首道加工腔。
            start_key = (
                self._swap_key(visits.in_lock)
                if self._facts.lock_mode == LOCK_MODE_SHARED
                else self._pick_key(visits.in_lock)
            )
            move_key = self._transfer_key("vacuum", visits.in_lock, visits.chambers[0])
            self._timing.register_transfer(
                move_key, robot, visits.in_lock, visits.chambers[0], TRANSFER_WITH_WAFER,
            )
            delay = self._book.seconds(start_key) + self._book.seconds(move_key)
            work += delay
            self._add_edge(
                self._in_event(round_index),
                self._reentrant_event(0, round_index, "place"),
                delay, 0, start_key,
            )
            for node_index, chamber in enumerate(visits.chambers):
                node = self._facts.process_nodes[node_index]
                place_key = self._process_place_key(chamber)
                pick_key = self._process_pick_key(chamber)
                # 放片后必须等本道工序加工完成，才能再次取片。
                hold_key = self._process_hold_key(chamber, node.ordinal)
                self._book.register(
                    hold_key,
                    category=_SERVICE_CATEGORY_PROCESS,
                    station=chamber,
                    robot=robot,
                    parts=(
                        (f"{chamber} 放片占用", self._book.seconds(place_key)),
                        (f"{chamber} 加工时间（第 {node.ordinal} 道工序）", node.process_seconds_of(chamber)),
                    ),
                    detail=f"{chamber} 放片并加工第 {node.ordinal} 道工序后，才能再次取片",
                )
                self._add_edge(
                    self._reentrant_event(node_index, round_index, "place"),
                    self._reentrant_event(node_index, round_index, "pick"),
                    self._book.seconds(hold_key), 0, hold_key,
                )
                work += self._book.seconds(place_key) + self._book.seconds(pick_key)
                last = node_index + 1 == len(visits.chambers)
                destination = visits.out_lock if last else visits.chambers[node_index + 1]
                move_key = self._transfer_key("vacuum", chamber, destination)
                self._timing.register_transfer(
                    move_key, robot, chamber, destination, TRANSFER_WITH_WAFER,
                )
                delay = self._book.seconds(pick_key) + self._book.seconds(move_key)
                # 取片已在本次访问中计入工作量，这里只累加转位时间。
                work += self._book.seconds(move_key)
                if not last:
                    self._add_edge(
                        self._reentrant_event(node_index, round_index, "pick"),
                        self._reentrant_event(node_index + 1, round_index, "place"),
                        delay, 0, pick_key,
                    )
                    continue
                # 链尾：取走成品并交给出口压力域。
                if self._facts.lock_mode == LOCK_MODE_SHARED:
                    target_index, shift = self._fold(round_index + 1)
                    self._add_edge(
                        self._reentrant_event(node_index, round_index, "pick"),
                        self._in_event(target_index), delay, shift, pick_key,
                    )
                else:
                    self._add_edge(
                        self._reentrant_event(node_index, round_index, "pick"),
                        self._out_event(round_index), delay, 0, pick_key,
                    )
                    work += self._book.seconds(self._place_key(visits.out_lock))
        self._loads[f"真空机器人 {self._facts.vacuum_robot}"] = work

    def _process_place_key(self, chamber: str) -> str:
        """重入模板的加工腔放片服务键。"""
        return f"process-place:{chamber}"

    def _process_pick_key(self, chamber: str) -> str:
        """重入模板的加工腔取片服务键。"""
        return f"process-pick:{chamber}"

    def _process_hold_key(self, chamber: str, ordinal: int) -> str:
        """重入模板“放片 + 加工”服务键，同腔不同工序加工时间可能不同。"""
        return f"process-hold:{chamber}:{ordinal}"

    def _reentrant_event(self, node_index: int, round_index: int, action: str) -> str:
        """重入模板的加工事件名：放片与取片是两个独立事件。"""
        return f"{_PROCESS_NODE_PREFIX}{node_index + 1}_{action.upper()}{round_index}"

    def _add_lock_recovery_edges(self) -> None:
        """登记锁的恢复环：换片或取放、充气、ATM 收补、抽气。"""
        for round_index, visits in enumerate(self._rounds_visits):
            if self._facts.lock_mode == LOCK_MODE_SHARED:
                self._add_shared_lock_recovery(round_index, visits)
            elif self._facts.lock_mode == LOCK_MODE_DEDICATED:
                self._add_dedicated_lock_recovery(round_index, visits)
            else:
                self._add_single_lock_recovery(round_index, visits)

    def _add_shared_lock_recovery(self, round_index: int, visits: _RoundVisits) -> None:
        """共享锁：换片后充气供 ATM 收补，补片抽气后两轮再访问。"""
        vent_key = self._pressure_key(visits.in_lock, "vent")
        self._add_edge(
            self._out_event(round_index), self._atmosphere_event(round_index, ATM_RECEIVE),
            self._book.seconds(self._swap_key(visits.in_lock)) + self._book.seconds(vent_key),
            0, vent_key,
        )
        pump_key = self._pressure_key(visits.in_lock, "pump")
        feed_key = self._atm_key(ATM_FEED, visits.in_lock)
        target_index, shift = self._fold(round_index + SHARED_PRESSURE_DOMAIN_COUNT)
        self._add_edge(
            self._atmosphere_event(round_index, ATM_FEED), self._in_event(target_index),
            self._book.seconds(feed_key) + self._book.seconds(pump_key), shift, pump_key,
        )

    def _add_dedicated_lock_recovery(self, round_index: int, visits: _RoundVisits) -> None:
        """分向锁：入口取空充气后补片抽气；出口收片充气后回收抽气。"""
        in_vent_key = self._pressure_key(visits.in_lock, "vent")
        self._add_edge(
            self._in_event(round_index), self._atmosphere_event(round_index, ATM_FEED),
            self._book.seconds(self._pick_key(visits.in_lock))
            + self._book.seconds(in_vent_key),
            0, in_vent_key,
        )
        in_pump_key = self._pressure_key(visits.in_lock, "pump")
        feed_key = self._atm_key(ATM_FEED, visits.in_lock)
        target_index, shift = self._fold(round_index + 1)
        self._add_edge(
            self._atmosphere_event(round_index, ATM_FEED), self._in_event(target_index),
            self._book.seconds(feed_key) + self._book.seconds(in_pump_key), shift, in_pump_key,
        )
        out_vent_key = self._pressure_key(visits.out_lock, "vent")
        self._add_edge(
            self._out_event(round_index), self._atmosphere_event(round_index, ATM_RECEIVE),
            self._book.seconds(self._place_key(visits.out_lock))
            + self._book.seconds(out_vent_key),
            0, out_vent_key,
        )
        out_pump_key = self._pressure_key(visits.out_lock, "pump")
        receive_key = self._atm_key(ATM_RECEIVE, visits.out_lock)
        target_index, shift = self._fold(round_index + 1)
        self._add_edge(
            self._atmosphere_event(round_index, ATM_RECEIVE), self._out_event(target_index),
            self._book.seconds(receive_key) + self._book.seconds(out_pump_key),
            shift, out_pump_key,
        )

    def _add_single_lock_recovery(self, round_index: int, visits: _RoundVisits) -> None:
        """唯一同锁：放成品、充气、ATM 回收补片、抽气后再取下一片。"""
        vent_key = self._pressure_key(visits.in_lock, "vent")
        self._add_edge(
            self._out_event(round_index), self._atmosphere_event(round_index, ATM_RECEIVE),
            self._book.seconds(self._place_key(visits.in_lock)) + self._book.seconds(vent_key),
            0, vent_key,
        )
        pump_key = self._pressure_key(visits.in_lock, "pump")
        feed_key = self._atm_key(ATM_FEED, visits.in_lock)
        target_index, shift = self._fold(round_index + 1)
        self._add_edge(
            self._atmosphere_event(round_index, ATM_FEED), self._in_event(target_index),
            self._book.seconds(feed_key) + self._book.seconds(pump_key), shift, pump_key,
        )

    def _fill_chamber_and_lock_loads(self) -> None:
        """汇总加工腔、压力域与大气侧站点的每周期独占工作量。"""
        for round_visits in self._rounds_visits:
            for node_index, chamber in enumerate(round_visits.chambers):
                node = self._facts.process_nodes[node_index]
                key = f"加工腔 {chamber}"
                self._loads[key] = self._loads.get(key, 0.0) + (
                    self._chamber_access_seconds(chamber)
                    + node.process_seconds_of(chamber)
                )
        for round_visits in self._rounds_visits:
            for role, lock in (("in", round_visits.in_lock), ("out", round_visits.out_lock)):
                if role == "out" and round_visits.out_lock == round_visits.in_lock:
                    # 共享锁与唯一同锁的入口、出口是同一把锁，只能计入一次。
                    continue
                key = f"LoadLock {lock}"
                self._loads[key] = self._loads.get(key, 0.0) + self._lock_work(lock, role)
        per_round: dict[str, float] = {}
        home_key = f"{ATM_HOME}:{self._sink}"
        if self._book.find(home_key) is not None:
            per_round[self._sink] = per_round.get(self._sink, 0.0) + self._book.seconds(home_key)
        raw_key = f"{ATM_RAW}:{self._source}"
        if self._book.find(raw_key) is not None:
            per_round[self._source] = per_round.get(self._source, 0.0) + self._book.seconds(raw_key)
        for station, seconds in per_round.items():
            key = f"LoadPort {station}"
            self._loads[key] = self._loads.get(key, 0.0) + seconds * self._rounds

    def _lock_work(self, lock: str, role: str) -> float:
        """返回一把锁在一轮内、担当 ``role`` 角色时的独占工作量。"""
        work = (
            self._book.seconds(self._pressure_key(lock, "vent"))
            + self._book.seconds(self._pressure_key(lock, "pump"))
        )
        if self._facts.lock_mode == LOCK_MODE_SHARED:
            return work + (
                self._book.seconds(self._swap_key(lock))
                + self._book.seconds(self._atm_key(ATM_FEED, lock))
                + self._book.seconds(self._atm_key(ATM_RECEIVE, lock))
            )
        if self._facts.lock_mode == LOCK_MODE_SINGLE:
            return work + (
                self._book.seconds(self._pick_key(lock))
                + self._book.seconds(self._place_key(lock))
                + self._book.seconds(self._atm_key(ATM_FEED, lock))
                + self._book.seconds(self._atm_key(ATM_RECEIVE, lock))
            )
        if role == "in":
            return work + (
                self._book.seconds(self._pick_key(lock))
                + self._book.seconds(self._atm_key(ATM_FEED, lock))
            )
        return work + (
            self._book.seconds(self._place_key(lock))
            + self._book.seconds(self._atm_key(ATM_RECEIVE, lock))
        )


def build_cycle_capacity_model(
    problem: CapacityInput,
    topology: Any,
    *,
    process_seconds: Optional[Sequence[Mapping[str, float]]] = None,
    members_per_unit: int = SINGLE_CHAMBER_PHYSICAL_MEMBERS,
    loadlock_batch_size: int = DEFAULT_LOADLOCK_BATCH_SIZE,
) -> CycleCapacityModel:
    """把编译结果与 Cycle 路线事实转换为固定周期事件图。

    :param problem: 生产编译器输出的调度输入，提供站点、机器人与参考晶圆。
    :param topology: ``analyze_capacity_topology`` 的识别结果。
    :param process_seconds: 可选的按工序加工秒数覆盖；缺省读取参考晶圆。
    :param members_per_unit: 一个逻辑循环单元包含的物理晶圆数。
    :param loadlock_batch_size: 单周期压力批片数；仅支持 1。
    :return: 事件图、路线事实与设备派生服务时长。
    :raises CycleCapacityUnsupported: 输入不属于本建图器覆盖范围时。
    """
    facts = describe_route_facts(
        problem,
        topology,
        process_seconds=process_seconds,
        members_per_unit=members_per_unit,
        loadlock_batch_size=loadlock_batch_size,
    )
    if facts.route_kind != ROUTE_KIND_REENTRANT and not facts.inbound_relays and not facts.outbound_relays:
        from app.backend.theory_capacity.timing_graph import CycleActionTimingGraph
        edges, services, loads, events = CycleActionTimingGraph(problem, facts).build()
    else:
        builder = _EventGraphBuilder(problem, facts, topology)
        edges, services, loads = builder.build()
        events = (
            *_lock_events(facts),
            *_process_events(facts),
            *(
                f"{_ATMOSPHERE_PREFIX}{stage.removeprefix('atm-')}{index}"
                for index in range(facts.rounds_per_cycle)
                for stage in ATM_STAGE_ORDER[facts.lock_mode]
            ),
        )
    model = CapacityModel(
        name=f"设备派生 {facts.lock_mode} {facts.route_kind} 固定周期（无清洁）",
        events=events,
        constraints=edges,
        units_per_cycle=facts.units_per_cycle,
        resource_work_seconds=loads,
        assumptions=facts.assumptions,
    )
    return CycleCapacityModel(facts=facts, model=model, services=services)


def _lock_events(facts: CycleCapacityFacts) -> Tuple[str, ...]:
    """按锁分工返回锁事件名序列。"""
    if facts.lock_mode == LOCK_MODE_SHARED:
        return tuple(f"{_LOCK_SHARED_PREFIX}{index}" for index in range(facts.rounds_per_cycle))
    events: List[str] = []
    for index in range(facts.rounds_per_cycle):
        events.append(f"{_LOCK_IN_PREFIX}{index}")
        events.append(f"{_LOCK_OUT_PREFIX}{index}")
    return tuple(events)


def _process_events(facts: CycleCapacityFacts) -> Tuple[str, ...]:
    """按路线形态返回加工事件名序列。

    轮转模板每次访问只有一个换片事件；重入模板的放片与取片是两个独立事件。
    """
    if facts.route_kind == ROUTE_KIND_REENTRANT:
        return tuple(
            f"{_PROCESS_NODE_PREFIX}{node.ordinal}_{action}{index}"
            for node in facts.process_nodes
            for index in range(facts.rounds_per_cycle)
            for action in ("PLACE", "PICK")
        )
    return tuple(
        f"{_PROCESS_NODE_PREFIX}{node.ordinal}_{index}"
        for node in facts.process_nodes
        for index in range(facts.rounds_per_cycle)
    )


def _limiting_constraint(
    report: CapacityReport,
    model: CapacityModel,
    services: Mapping[str, CycleServiceLifetime],
) -> Tuple[str, str]:
    """判断限制理论周期的是资源负荷还是闭合等待链。

    求解器返回的是误差不超过 ``tolerance_seconds`` 的可行上端点，因此当最大资源
    负荷与求解周期之差不超过该误差时，资源负荷就是限制项，而不是等待链。
    """
    floor_resource = ""
    floor_seconds = 0.0
    for name, seconds in model.resource_work_seconds.items():
        if float(seconds) > floor_seconds:
            floor_resource, floor_seconds = str(name), float(seconds)
    slack = max(float(report.tolerance_seconds), 1e-12)
    if floor_seconds > 0.0 and report.period_seconds - floor_seconds <= slack:
        return _LIMITING_RESOURCE, f"{floor_resource}（{floor_seconds:.3f} 秒/周期）"
    labels: List[str] = []
    for edge in report.critical_chain:
        service = services.get(edge.label)
        text = service.detail if service is not None and service.detail else edge.label
        if text and text not in labels:
            labels.append(text)
    return _LIMITING_WAITING_CHAIN, " → ".join(labels) if labels else "未找到闭合等待链"


def analyze_cycle_capacity_model(
    problem: CapacityInput,
    topology: Any,
    *,
    process_seconds: Optional[Sequence[Mapping[str, float]]] = None,
    members_per_unit: int = SINGLE_CHAMBER_PHYSICAL_MEMBERS,
    loadlock_batch_size: int = DEFAULT_LOADLOCK_BATCH_SIZE,
    tolerance_seconds: float = 1e-7,
) -> CycleCapacityAnalysis:
    """求解一条设备路线的理论产能，并保留完整推导证据。

    :param problem: 编译后的调度输入。
    :param topology: ``analyze_capacity_topology`` 的识别结果。
    :param process_seconds: 可选的按工序加工秒数覆盖。
    :param members_per_unit: 一个逻辑循环单元包含的物理晶圆数。
    :param loadlock_batch_size: 单周期压力批片数；仅支持 1。
    :param tolerance_seconds: 周期求解误差。
    :return: 含事件图、服务时长、资源负荷与关键链的完整推导。
    """
    built = build_cycle_capacity_model(
        problem,
        topology,
        process_seconds=process_seconds,
        members_per_unit=members_per_unit,
        loadlock_batch_size=loadlock_batch_size,
    )
    report = analyze_capacity(built.model, tolerance_seconds=tolerance_seconds)
    kind, label = _limiting_constraint(report, built.model, built.service_index())
    return CycleCapacityAnalysis(model=built, report=report, limiting_kind=kind, limiting_label=label)


def scan_cycle_capacity(
    problem: CapacityInput,
    topology: Any,
    *,
    first_seconds: float,
    last_seconds: float,
    step_seconds: float,
    members_per_unit: int = SINGLE_CHAMBER_PHYSICAL_MEMBERS,
    loadlock_batch_size: int = DEFAULT_LOADLOCK_BATCH_SIZE,
    tolerance_seconds: float = SCAN_TOLERANCE_SECONDS,
) -> CycleCapacityScan:
    """扫描统一加工时间下的理论产能，返回完整曲线。

    扫描把每道加工工序的每个候选腔都设为同一个加工时间，其它设备服务时长保持
    不变，用于比较路线之间对加工时间的敏感程度。

    :param problem: 编译后的调度输入。
    :param topology: ``analyze_capacity_topology`` 的识别结果。
    :param first_seconds: 扫描起点加工时间，包含。
    :param last_seconds: 扫描终点加工时间，包含。
    :param step_seconds: 扫描步长，必须为正。
    :param members_per_unit: 一个逻辑循环单元包含的物理晶圆数。
    :param loadlock_batch_size: 单周期压力批片数；仅支持 1。
    :param tolerance_seconds: 周期求解误差。
    :return: 路线事实与逐点结果。
    :raises ValueError: 扫描区间非法时。
    """
    first = _finite_seconds(first_seconds, "扫描起点")
    last = _finite_seconds(last_seconds, "扫描终点")
    step = _finite_seconds(step_seconds, "扫描步长")
    if step <= 0.0:
        raise ValueError("扫描步长必须为正数")
    if last < first:
        raise ValueError("扫描终点不能小于起点")
    # 先校验路线是否可建图，再用已验证的工序结构生成逐点覆盖；这样不受支持的
    # 形态会得到稳定的 CycleCapacityUnsupported，而不是属性错误。
    facts = describe_route_facts(
        problem,
        topology,
        members_per_unit=members_per_unit,
        loadlock_batch_size=loadlock_batch_size,
    )
    points: List[CycleCapacityPoint] = []
    count = int(round((last - first) / step)) + 1
    for index in range(count):
        process_seconds = first + index * step
        overridden = tuple(
            {chamber: process_seconds for chamber in node.chambers}
            for node in facts.process_nodes
        )
        analysis = analyze_cycle_capacity_model(
            problem,
            topology,
            process_seconds=overridden,
            members_per_unit=members_per_unit,
            loadlock_batch_size=loadlock_batch_size,
            tolerance_seconds=tolerance_seconds,
        )
        points.append(CycleCapacityPoint(
            process_seconds=process_seconds,
            period_seconds=analysis.report.period_seconds,
            seconds_per_unit=analysis.report.seconds_per_unit,
            physical_wafers_per_hour=analysis.report.physical_wafers_per_hour,
            limiting_kind=analysis.limiting_kind,
            limiting_label=analysis.limiting_label,
        ))
    return CycleCapacityScan(facts=facts, points=tuple(points))
