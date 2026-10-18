"""理论产能专用输入与计时读取，只依赖标准库。

设备计时、路线参考产品与模板事实由平台直接构造；不包含生产调度状态、
Machine、实时重算或算法编译器对象。单位为秒，转位按来源、目标、持片类型精确匹配。
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RobotTiming:
    """机器人的可达站点、持片能力和静态动作秒数。"""
    name: str
    scope: list[str]
    capacity: int
    can_swap: bool
    pick_time: dict[str, float] = field(default_factory=dict)
    place_time: dict[str, float] = field(default_factory=dict)
    prep_trans_time: list[dict] = field(default_factory=list)
    transfer_time: dict[tuple[str, str, int], float] = field(default_factory=dict)
    strict_timing: bool = False


@dataclass
class StationTiming:
    """站点门、容量与压力转换时间；双腔使用平台转换后的逻辑压力通道。"""
    name: str
    type: str
    capacity: int
    pump_time: float | None = None
    vent_time: float | None = None
    pick_prepare_time: dict[str, float] = field(default_factory=dict)
    place_prepare_time: dict[str, float] = field(default_factory=dict)
    pick_complete_time: dict[str, float] = field(default_factory=dict)
    place_complete_time: dict[str, float] = field(default_factory=dict)


@dataclass
class RouteStage:
    """一段站点访问；保留候选加工时间与限制，禁止静默放宽有限驻留和 Q-time。"""
    j: int
    chamber: str
    stage_type: str
    proc: float
    in_robot: str
    out_robot: str
    residency: float = -1.0
    ll_type: str = ''
    slot: int = 0
    clean_time: float = 0.0
    clean_trigger: int = 0
    clean_recipe: str = ''
    cands: list[str] = field(default_factory=list)
    step_id: int = -1
    qtime: float = -1.0
    process_time_by_chamber: dict[str, float] = field(default_factory=dict)
    residency_by_chamber: dict[str, float] = field(default_factory=dict)
    qtime_by_chamber: dict[str, float] = field(default_factory=dict)
    move_time_offsets: dict[int, float] = field(default_factory=dict)
    clean_task: str = ''


@dataclass
class ReferenceProduct:
    """描述一条重复产品路线；不是运行队列或可执行生产物料。"""
    wid: int
    mat_id: int
    route_name: str
    route_rank: int
    stages: list[RouteStage]
    transports: list[str]
    pjob_name: str = ''


@dataclass
class CleaningRequirement:
    """仅用于识别并拒绝超出当前无清洁范围的输入。"""
    visits: list[str]
    time: float
    recipe: str = ''
    task: str = ''
    pjob_name: str = ''


@dataclass
class CapacityInput:
    """平台拥有的静态设备和路线输入；各调用拥有独立的数据实例。"""
    chambers: dict[str, StationTiming]
    robots: dict[str, RobotTiming]
    wafers: list[ReferenceProduct]
    pre_clean: list[CleaningRequirement] = field(default_factory=list)
    post_clean: list[CleaningRequirement] = field(default_factory=list)


class DeviceDurations:
    """按图的动作边界读取静态时间，不修改输入。"""

    def __init__(self, problem: CapacityInput):
        """绑定只读计时表；不建立任何调度状态。"""
        self.robots, self.chambers = problem.robots, problem.chambers

    def _robot_seconds(self, robot, station, attribute):
        """严格设备输入缺少取放时间时拒绝，合成夹具允许显式宽松模式。"""
        row = self.robots[robot]
        times = getattr(row, attribute)
        if station not in times and row.strict_timing:
            raise ValueError(f'Robot {robot} 缺少 {attribute}[{station}]')
        return float(times.get(station, 0.0))

    def pick_t(self, robot, station):
        """返回机器手独占的取片时间。"""
        return self._robot_seconds(robot, station, 'pick_time')

    def place_t(self, robot, station):
        """返回机器手独占的放片时间。"""
        return self._robot_seconds(robot, station, 'place_time')

    def pick_pre(self, robot, station):
        """返回站点取片前开门时间。"""
        return float(self.chambers[station].pick_prepare_time.get(robot, 0.0))

    def pick_post(self, robot, station):
        """返回站点取片后关门时间。"""
        return float(self.chambers[station].pick_complete_time.get(robot, 0.0))

    def place_pre(self, robot, station):
        """返回站点放片前开门时间。"""
        return float(self.chambers[station].place_prepare_time.get(robot, 0.0))

    def place_post(self, robot, station):
        """返回站点放片后关门时间。"""
        return float(self.chambers[station].place_complete_time.get(robot, 0.0))

    def pick(self, robot, station):
        """返回含站点开关门的完整取片服务时间。"""
        return self.pick_pre(robot, station) + self.pick_t(robot, station) + self.pick_post(robot, station)

    def place(self, robot, station):
        """返回含站点开关门的完整放片服务时间。"""
        return self.place_pre(robot, station) + self.place_t(robot, station) + self.place_post(robot, station)

    def move(self, robot, source, destination, trans_type):
        """按转位四元组读取时长；严格输入不使用别的路径代替缺失项。"""
        row = self.robots[robot]
        key = (str(source), str(destination), int(trans_type))
        if key in row.transfer_time:
            return float(row.transfer_time[key])
        if row.strict_timing:
            raise ValueError(f'Robot {robot} 缺少 PrepTransTime 四元组 {key}')
        return float(row.prep_trans_time[0].get('Time', 0.0)) if row.prep_trans_time else 0.0
