"""设备产能分析的数据契约：路线事实、服务分项、事件图及可序列化求解结果。

由设备建图器与动作定时建图器共用；不执行排程、不读取平台数据。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Tuple, Mapping
from app.backend.theory_capacity.solver import CapacityModel, CapacityReport

@dataclass(frozen=True)
class CycleServicePart:
    """聚合服务时长的一个组成项，用于页面上逐项核对。"""

    label: str
    seconds: float

    def to_dict(self) -> dict:
        """生成可直接序列化的组成项。"""
        return {"label": self.label, "seconds": self.seconds}


@dataclass(frozen=True)
class CycleServiceLifetime:
    """一段设备派生服务时长及其组成。

    :param key: 稳定键，例如 ``vacuum-swap:LA``；等待边的 label 与它一致。
    :param category: ``vacuum-swap``/``process-swap``/``pressure``/``transfer``
        /``atmosphere``，供页面分组。
    :param station: 该服务占用的站点；纯转位记目标站点。
    :param robot: 执行该服务的机器人。
    :param seconds: 聚合时长。
    :param parts: 组成项，顺序即物理先后。
    :param detail: 中文物理含义。
    """

    key: str
    category: str
    station: str
    robot: str
    seconds: float
    parts: Tuple[CycleServicePart, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict:
        """生成可直接序列化的服务时长。"""
        return {
            "key": self.key,
            "category": self.category,
            "station": self.station,
            "robot": self.robot,
            "seconds": self.seconds,
            "parts": [part.to_dict() for part in self.parts],
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CycleProcessNode:
    """一道加工工序：Cycle 别名、候选腔顺序与各候选腔的加工秒数。"""

    ordinal: int
    alias: str
    chambers: Tuple[str, ...]
    process_seconds: Mapping[str, float]

    def process_seconds_of(self, chamber: str) -> float:
        """返回指定候选腔的加工秒数；未登记时按 0 秒处理。"""
        return float(self.process_seconds.get(str(chamber), 0.0))

    def to_dict(self) -> dict:
        """生成可直接序列化的工序事实。"""
        return {
            "ordinal": self.ordinal,
            "alias": self.alias,
            "chambers": list(self.chambers),
            "process_seconds": dict(self.process_seconds),
        }


@dataclass(frozen=True)
class CycleCapacityFacts:
    """一条设备路线上与产能建图有关的稳定事实。"""

    route_name: str
    lock_mode: str
    route_kind: str
    pressure_domains: Tuple[str, ...]
    vacuum_robot: str
    atmosphere_robot: str
    process_nodes: Tuple[CycleProcessNode, ...]
    rounds_per_cycle: int
    physical_members_per_unit: int
    loadlock_batch_size: int
    source_station: str
    sink_station: str
    inbound_relays: Tuple[str, ...]
    outbound_relays: Tuple[str, ...]
    assumptions: Tuple[str, ...]

    @property
    def units_per_cycle(self) -> float:
        """一个完整周期完成的物理产品片数。"""
        return float(self.rounds_per_cycle * self.physical_members_per_unit)

    @property
    def chamber_counts(self) -> Tuple[int, ...]:
        """按工序顺序返回每道工序的候选腔数。"""
        return tuple(len(node.chambers) for node in self.process_nodes)

    def to_dict(self) -> dict:
        """生成可直接序列化的路线事实。"""
        return {
            "route_name": self.route_name,
            "lock_mode": self.lock_mode,
            "route_kind": self.route_kind,
            "pressure_domains": list(self.pressure_domains),
            "vacuum_robot": self.vacuum_robot,
            "atmosphere_robot": self.atmosphere_robot,
            "process_nodes": [node.to_dict() for node in self.process_nodes],
            "rounds_per_cycle": self.rounds_per_cycle,
            "physical_members_per_unit": self.physical_members_per_unit,
            "loadlock_batch_size": self.loadlock_batch_size,
            "units_per_cycle": self.units_per_cycle,
            "chamber_counts": list(self.chamber_counts),
            "source_station": self.source_station,
            "sink_station": self.sink_station,
            "inbound_relays": list(self.inbound_relays),
            "outbound_relays": list(self.outbound_relays),
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class CycleCapacityModel:
    """事件图、路线事实与设备派生服务时长三件套。"""

    facts: CycleCapacityFacts
    model: CapacityModel
    services: Tuple[CycleServiceLifetime, ...] = ()

    def service_index(self) -> Mapping[str, CycleServiceLifetime]:
        """按稳定键索引服务时长，供页面按等待边反查组成。"""
        return {service.key: service for service in self.services}


@dataclass(frozen=True)
class CycleCapacityAnalysis:
    """一条路线在一个加工时间下的完整理论产能推导。"""

    model: CycleCapacityModel
    report: CapacityReport
    limiting_kind: str
    limiting_label: str

    @property
    def facts(self) -> CycleCapacityFacts:
        """返回该推导使用的路线事实。"""
        return self.model.facts

    def to_dict(self) -> dict:
        """生成可直接序列化的完整推导，包含等待边与资源负荷明细。"""
        services = self.model.service_index()
        edges = [
            {
                "source": edge.source,
                "target": edge.target,
                "minimum_delay_seconds": edge.minimum_delay_seconds,
                "cycle_shift": edge.cycle_shift,
                "label": edge.label,
                "detail": services[edge.label].detail if edge.label in services else "",
                "parts": (
                    [part.to_dict() for part in services[edge.label].parts]
                    if edge.label in services else []
                ),
            }
            for edge in self.model.model.constraints
        ]
        return {
            "facts": self.facts.to_dict(),
            "model_name": self.model.model.name,
            "events": list(self.model.model.events),
            "edges": edges,
            "resource_work_seconds": dict(self.model.model.resource_work_seconds),
            "services": [service.to_dict() for service in self.model.services],
            "period_seconds": self.report.period_seconds,
            "seconds_per_unit": self.report.seconds_per_unit,
            "physical_wafers_per_hour": self.report.physical_wafers_per_hour,
            "resource_period_lower_bound_seconds": self.report.resource_period_lower_bound_seconds,
            "resource_upper_bound_wafers_per_hour": self.report.resource_upper_bound_wafers_per_hour,
            "critical_chain": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "minimum_delay_seconds": edge.minimum_delay_seconds,
                    "cycle_shift": edge.cycle_shift,
                    "label": edge.label,
                    "detail": services[edge.label].detail if edge.label in services else "",
                }
                for edge in self.report.critical_chain
            ],
            "limiting_kind": self.limiting_kind,
            "limiting_label": self.limiting_label,
            "tolerance_seconds": self.report.tolerance_seconds,
        }


@dataclass(frozen=True)
class CycleCapacityPoint:
    """扫描曲线上的一个加工时间采样点。"""

    process_seconds: float
    period_seconds: float
    seconds_per_unit: float
    physical_wafers_per_hour: float
    limiting_kind: str
    limiting_label: str

    def to_dict(self) -> dict:
        """生成可直接序列化的采样点。"""
        return {
            "process_seconds": self.process_seconds,
            "period_seconds": self.period_seconds,
            "seconds_per_unit": self.seconds_per_unit,
            "physical_wafers_per_hour": self.physical_wafers_per_hour,
            "limiting_kind": self.limiting_kind,
            "limiting_label": self.limiting_label,
        }


@dataclass(frozen=True)
class CycleCapacityScan:
    """一条路线的加工时间—理论产能扫描结果。"""

    facts: CycleCapacityFacts
    points: Tuple[CycleCapacityPoint, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        """生成可直接序列化的扫描结果。"""
        return {
            "facts": self.facts.to_dict(),
            "points": [point.to_dict() for point in self.points],
        }

