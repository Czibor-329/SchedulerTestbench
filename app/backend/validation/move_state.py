"""平台物理快照、协议常量及初始状态建模。

状态从拓扑与任务快照恢复；配置解析按需导入，不依赖回放或动作分派。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from collections.abc import Mapping, Sequence

TIME_TOLERANCE = 1e-6
DEFAULT_SLOT_ID = 1
MOVE_STATE_RUNNING = 0
MOVE_STATE_DONE = 1
MOVE_STATE_ABORTED = 2
PICK_MOVE = 0
MULTI_PICK_MOVE = 2
PLACE_MOVE = 1
SWAP_MOVE = 4
PRE_TRANS_MOVE = 5
PREPARE_MOVE = 6
COMPLETE_MOVE = 7
PROCESS_MOVE = 9
PRE_PREPARE_MOVE = 10
ALIGN_MOVE = 11
ATMOSPHERE = "ATM"
VACUUM = "VAC"
LOAD_LOCK_TYPE = "loadlock"
LOAD_PORT_TYPE = "loadport"
DUMMY_PORT_TYPE = "dummyport"
BUFFER_TYPE = "buffer"
COMPLETED_ON_PLACE_STATION_TYPES = frozenset({
    LOAD_PORT_TYPE,
    DUMMY_PORT_TYPE,
    BUFFER_TYPE,
})
MULTI_PROCESS_CHAMBER_TYPE = "multiprocesschamber"
PROCESS_CHAMBER_TYPE = "processchamber"
DOORLESS_STATION_NAMES = frozenset({"Cooler", "Cool"})
TWIN_LOAD_LOCK_PAIRS = frozenset({
    frozenset({"LA", "LB"}),
    frozenset({"LC", "LD"}),
})
# 运行设置和标准 Clean 定义共用的稳定类型。所有类型默认检查触发策略；调用者
# 只能显式跳过某一类的触发时机与次数义务，动作合法性和状态推进始终生效。
CLEAN_VALIDATION_TYPES = frozenset({
    "preclean", "postclean", "wacclean", "dummy", "dummywac",
})


class ValidationErrorCode(str, Enum):
    """MoveList 输出校验的稳定错误码。"""

    MOVE_ITEM_INVALID = "MVL-FMT-001"
    MOVE_ID_INVALID = "MVL-FMT-002"
    MOVE_ID_DUPLICATE = "MVL-FMT-003"
    MOVE_FIELD_INVALID = "MVL-FMT-004"
    PREDECESSOR_FORMAT_INVALID = "MVL-DEP-001"
    PREDECESSOR_VALUE_INVALID = "MVL-DEP-002"
    PREDECESSOR_SELF_REFERENCE = "MVL-DEP-003"
    PREDECESSOR_MISSING = "MVL-DEP-004"
    PREDECESSOR_TIME_CONFLICT = "MVL-DEP-005"
    DEPENDENCY_CYCLE = "MVL-DEP-006"
    TIME_INVALID = "MVL-TIME-001"
    TIME_REVERSED = "MVL-TIME-002"
    TIMING_CONFIG_MISSING = "MVL-TIME-003"
    TIMING_CONFIG_INVALID = "MVL-TIME-004"
    DURATION_MISMATCH = "MVL-TIME-005"
    POST_PROCESS_PICK_MISSING = "MVL-ROUTE-001"
    RESIDENCY_EXCEEDED = "MVL-ROUTE-002"
    QTIME_EXCEEDED = "MVL-ROUTE-003"
    INITIAL_MATERIAL_CONFLICT = "MVL-STATE-001"
    MOVE_TYPE_UNSUPPORTED = "MVL-MOVE-001"
    PARALLEL_ARRAY_INVALID = "MVL-MOVE-002"
    DUPLICATE_RESOURCE_REFERENCE = "MVL-MOVE-003"
    ROBOT_UNKNOWN = "MVL-RES-001"
    STATION_UNKNOWN = "MVL-RES-002"
    STATION_SLOT_UNKNOWN = "MVL-RES-003"
    ROBOT_BUSY = "MVL-ROBOT-001"
    ROBOT_SLOT_DISABLED = "MVL-ROBOT-002"
    ROBOT_HAND_STATE_INVALID = "MVL-ROBOT-003"
    ROBOT_SWAP_UNSUPPORTED = "MVL-ROBOT-004"
    ROBOT_UNREACHABLE = "MVL-ROBOT-005"
    ROBOT_ALIGNMENT_INVALID = "MVL-ROBOT-006"
    STATION_DOOR_STATE_INVALID = "MVL-STATION-001"
    STATION_TRANSFER_BUSY = "MVL-STATION-002"
    STATION_ENVIRONMENT_BUSY = "MVL-STATION-003"
    STATION_PROCESS_BUSY = "MVL-STATION-004"
    STATION_SLOT_BUSY = "MVL-STATION-005"
    PICK_SOURCE_INVALID = "MVL-PICK-001"
    PLACE_TARGET_INVALID = "MVL-PLACE-001"
    PRETRANS_STATE_INVALID = "MVL-PRETRANS-001"
    PROCESS_STATE_INVALID = "MVL-PROCESS-001"
    LOADLOCK_REQUIRED = "MVL-LL-001"
    LOADLOCK_ENVIRONMENT_INVALID = "MVL-LL-002"
    LOADLOCK_CONTENT_INVALID = "MVL-LL-003"
    SWAP_INPUT_INVALID = "MVL-SWAP-001"
    SWAP_STATE_INVALID = "MVL-SWAP-002"
    CLEAN_WAC_MISSING = "MVL-CLEAN-WAC-MISSING"
    CLEAN_WAC_EARLY = "MVL-CLEAN-WAC-EARLY"
    CLEAN_PRE_MISSING = "MVL-CLEAN-PRE-MISSING"
    CLEAN_PRE_DUPLICATE = "MVL-CLEAN-PRE-DUPLICATE"
    CLEAN_DUMMY_MISSING = "MVL-CLEAN-DUMMY-MISSING"
    CLEAN_POST_MISSING = "MVL-CLEAN-POST-MISSING"
    CLEAN_RECIPE_INVALID = "MVL-CLEAN-RECIPE-INVALID"


class DoorState(str, Enum):
    """设备门的稳定开闭状态。"""

    CLOSED = "closed"
    OPEN = "open"


class SlotPhase(str, Enum):
    """槽位中物料在稳定时刻的加工状态。"""

    EMPTY = "empty"
    CLEANED = "cleaned"
    UNPROCESSED = "unprocessed"
    COMPLETED = "completed"


@dataclass
class MaterialState:
    """记录物料标识及其当前 PJob/Step 元数据。"""

    material_id: Any
    pjob_name: str = ""
    step_id: Any = None


@dataclass
class SlotState:
    """记录一个物理槽位的物料、占用窗口及累计加工次数。"""

    phase: SlotPhase = SlotPhase.EMPTY
    material: Optional[MaterialState] = None
    busy_until: float = 0.0
    busy_action: str = ""
    material_process_count: int = 0


@dataclass
class StationState:
    """记录腔室的门、槽位及共享取放资源。"""

    name: str
    station_type: str
    slots: Dict[int, SlotState] = field(default_factory=dict)
    door: DoorState = DoorState.CLOSED
    door_busy_until: float = 0.0
    transfer_busy_until: float = 0.0
    environment_busy_until: float = 0.0
    state_variables: Dict[str, float] = field(default_factory=dict)

    @property
    def is_load_lock(self) -> bool:
        """返回站点是否为 LoadLock。"""
        return self.station_type.lower() == LOAD_LOCK_TYPE

    @property
    def completes_material_on_place(self) -> bool:
        """返回物料放入后无需额外服务即可再次取出的库存或 Buffer 站点。"""
        return self.station_type.lower() in COMPLETED_ON_PLACE_STATION_TYPES


@dataclass
class LoadLockState(StationState):
    """记录 LoadLock 的压力态和设备标签映射。"""

    environment: str = ATMOSPHERE
    last_environment_transition_was_empty: bool = False
    environment_aliases: Dict[str, str] = field(default_factory=dict)
    #: ``PrePrepareTime`` 声明的合法状态标签集合（归一化大写）；空集表示未声明，退回宽松解析。
    environment_state_space: frozenset[str] = frozenset()
    #: 是否已行使“首条环境不匹配切换”豁免：外部算法的首条环境切换其 LastState
    #: 可与其初始压力态不符（如初始大气却先发 VentTime，或级联 LL 从 ATR_1 起始），
    #: 豁免放行并照常执行、落地到 CurState；后续违例照常报错。
    environment_exemption_used: bool = False
    #: 设备明确声明、允许由算法省略 Move 的零时长压力转换（标准 ATM/VAC 态对）。
    zero_duration_environment_transitions: Set[Tuple[str, str]] = field(
        default_factory=set
    )


@dataclass
class RobotState:
    """记录机器人全部手槽、可达范围、指向与占用窗口。

    ``position`` 是站级兼容字段（外部 API/快照回写仍消费）；槽位级拓扑启用后
    各手槽的精确指向与候选集才是权威状态：双臂设备的一个臂可横跨两个站
    （如 ``SlotsStationMap`` 的 ``LALB`` 站组：槽位 1 伸 LA、槽位 2 伸 LB）。
    """

    name: str
    hands: Dict[int, Optional[MaterialState]] = field(default_factory=dict)
    scope: Set[str] = field(default_factory=set)
    position: Optional[str] = None
    #: 手槽当前精确指向的站槽位 ``(站名, 站槽位号)``；未对准时为 None。
    slot_targets: Dict[int, Optional[Tuple[str, int]]] = field(default_factory=dict)
    #: 手槽在当前站组下可对准的站槽位候选集；机器人配置了 ``SlotsStationMap``
    #: 时按候选集校验取放，空集表示该手槽当前够不到任何槽位。
    slot_options: Dict[int, Set[Tuple[str, int]]] = field(default_factory=dict)
    #: 静态拓扑：手槽 → 站组名 → 该站组下可对准的站槽位候选集（来自 ArmInfo.SlotsStationMap）。
    slot_map: Dict[int, Dict[str, Set[Tuple[str, int]]]] = field(default_factory=dict)
    #: 静态拓扑：Arm 名 → 该 Arm 的物理手槽（用于按臂回写 SlotAtStation）。
    arm_slots: Dict[str, Set[int]] = field(default_factory=dict)
    busy_until: float = 0.0
    can_swap: bool = False
    #: 静态站组的站点集合；只从 slot_map 构建，不随转位位置变化。
    station_groups: Optional[Dict[str, Set[str]]] = None

    def swap_slot_error(self, receive_slot: int, send_slot: int) -> Optional[str]:
        """校验原子换片使用的两个机器人手槽。"""
        from .move_validation_helpers import (
            _global_issue,
        )
        if not self.can_swap or len(self.hands) < 2:
            return _global_issue(ValidationErrorCode.ROBOT_SWAP_UNSUPPORTED, f"{self.name} 不支持双臂换片")
        if receive_slot == send_slot:
            return _global_issue(ValidationErrorCode.SWAP_INPUT_INVALID, f"{self.name} 换片的接收手槽和发送手槽必须不同")
        if receive_slot not in self.hands:
            return _global_issue(ValidationErrorCode.ROBOT_SLOT_DISABLED, f"{self.name} 未启用手槽 {receive_slot}")
        if send_slot not in self.hands:
            return _global_issue(ValidationErrorCode.ROBOT_SLOT_DISABLED, f"{self.name} 未启用手槽 {send_slot}")
        return None


@dataclass
class MachineState:
    """平台回放期间的独立整机物理快照。"""

    stations: Dict[str, StationState] = field(default_factory=dict)
    robots: Dict[str, RobotState] = field(default_factory=dict)
    robot_aliases: Dict[str, str] = field(default_factory=dict)
    process_recipe_weights: Dict[Tuple[str, str], Dict[str, float]] = field(default_factory=dict)
    clean_task_state_variables: Dict[str, Set[str]] = field(default_factory=dict)
    # ``(PM, 产品Recipe) -> (PJob, 状态变量, 阈值, CleanTask, 来源StepID)``。
    # 同一 PJob 可能在同一 PM 重入，必须按来源工步识别实际触发 WAC 的 Visit。
    clean_wac_trigger_rules: Dict[
        Tuple[str, str],
        Tuple[Tuple[str, str, float, str, Optional[str]], ...],
    ] = field(default_factory=dict)
    #: 单腔 PM 的 WAC 计数按 ``(PM, PJob, 状态变量)`` 隔离。双腔仍使用
    #: ``StationState.state_variables`` 的腔室全局计数，以保持双片同步加工语义。
    pjob_wac_counters: Dict[Tuple[str, str, str], float] = field(default_factory=dict)
    clean_obligations: Dict[Tuple[str, str, str], Tuple[str, int, Tuple[str, ...]]] = field(default_factory=dict)
    #: 产品 Process 越过阈值时形成的待执行 WAC，成员为
    #: ``(PM, PJob, 状态变量, CleanTask)``。
    pending_wac_obligations: Set[Tuple[str, str, str, str]] = field(
        default_factory=set
    )
    #: 本次运行跳过的 Clean 触发/次数规则；物理状态回放与计数仍照常执行。
    skipped_clean_validation_types: Set[str] = field(default_factory=set)
    #: Dummy WAC 中已经完成尾随空腔 WAC 的片数。该计数与带片清洁数分离，
    #: 用于强制每片 Dummy 按“带片清洁 → 出片 → 空腔 WAC”成对完成。
    completed_dummy_wac_counts: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    completed_clean_counts: Dict[Tuple[str, str, str], int] = field(default_factory=dict)
    product_clean_entries: Set[Tuple[str, str]] = field(default_factory=set)
    #: 外部算法可省略的零时长产品 ProcessMove；键为（物料、Route Step、PM）。
    zero_duration_process_steps: Set[Tuple[str, str, str]] = field(default_factory=set)
    #: 产品在各 PM 上全部 NeedProcess Step；用于区分“该站只有 0s 工序”
    #: 与重入混合工时，避免仅凭站点名误补非零加工。
    process_steps_by_material_station: Dict[Tuple[str, str], Set[str]] = field(
        default_factory=dict
    )

    @classmethod
    def from_sources(
        cls,
        task: Any,
        init_data: "Optional[Mapping[str, Any] | MachineState]",
    ) -> "MachineState":
        """合并标准 update 与解析任务，构造容量感知的初始快照。"""
        from .move_validation_helpers import (
            _clean_obligation_specs,
            _clean_task_state_variables,
            _clean_wac_trigger_rules,
            _environment_aliases,
            _environment_from_last_item,
            _environment_state_space,
            _global_issue,
            _initial_materials,
            _initial_payload,
            _mapping,
            _process_recipe_weights,
            _robot_from_config,
            _robot_from_task,
            _station_from_task,
            _station_slot_ids,
            _station_slot_material_count,
            _station_state_variables,
        )
        if isinstance(init_data, cls):
            return init_data.clone()
        payload = _initial_payload(init_data)
        state = cls()
        station_configs = _mapping(payload.get("Stations"))
        robot_configs = _mapping(payload.get("Robots"))
        task_stations = getattr(task, "chambers", {}) or {}
        task_robots = getattr(task, "robots", {}) or {}
        state.process_recipe_weights = _process_recipe_weights(payload)
        state.clean_task_state_variables = _clean_task_state_variables(payload)
        state.clean_wac_trigger_rules = _clean_wac_trigger_rules(payload)
        state.clean_obligations = _clean_obligation_specs(payload)

        for name, config in station_configs.items():
            task_station = task_stations.get(name)
            station_type = str(config.get("Type") or getattr(task_station, "type", ""))
            slots = {
                slot_id: SlotState(
                    material_process_count=_station_slot_material_count(config, slot_id),
                )
                for slot_id in _station_slot_ids(config, task_station)
            }
            if station_type.lower() == LOAD_LOCK_TYPE:
                aliases = _environment_aliases(config)
                aliases.update({
                    str(alias).strip().upper(): str(environment)
                    for alias, environment in dict(
                        getattr(task_station, "environment_by_robot", {}) or {}
                    ).items()
                    if alias
                })
                state.stations[name] = LoadLockState(
                    name=name,
                    station_type=station_type,
                    slots=slots,
                    environment=_environment_from_last_item(str(config.get("LastItem") or ""), aliases),
                    environment_aliases=aliases,
                    environment_state_space=_environment_state_space(config),
                    zero_duration_environment_transitions=(
                        _zero_duration_environment_transitions(config)
                    ),
                    state_variables=_station_state_variables(config),
                )
            else:
                state.stations[name] = StationState(
                    name,
                    station_type,
                    slots,
                    state_variables=_station_state_variables(config),
                )

        state.seed_initial_single_chamber_wac_counters(station_configs)

        for name, task_station in task_stations.items():
            station_name = str(name)
            if station_name not in state.stations:
                state.stations[station_name] = _station_from_task(station_name, task_station)

        for name, config in robot_configs.items():
            robot = _robot_from_config(name, config, task_robots.get(name))
            state.robots[name] = robot
            state.robot_aliases[name] = name
            if config.get("Name"):
                state.robot_aliases[str(config["Name"])] = name
        for name, task_robot in task_robots.items():
            robot_name = str(name)
            if robot_name not in state.robots:
                state.robots[robot_name] = _robot_from_task(robot_name, task_robot)
            state.robot_aliases.setdefault(robot_name, robot_name)

        # Route 可引用配置中遗漏的逻辑槽位；只扩容，不缩小显式物理容量。
        for wafer in getattr(task, "wafers", ()) or ():
            for stage in getattr(wafer, "stages", ()) or ():
                station_name = str(getattr(stage, "chamber", "") or "")
                if station_name:
                    state.ensure_station(station_name, int(getattr(stage, "slot", 0) or 0) + 1)

        # 标准输出可能省略零时长产品 ProcessMove。ProcessRecipes 是本轮运行计划
        # 的最终时长来源，优先于 Route 编译期默认值；保留可严格证明的三元组，
        # 后续 Pick 时仅为该类 PM 物料补齐已加工状态。
        recipe_durations = {
            (str(recipe.get("Name") or ""), str(recipe.get("ModuleName") or "")): recipe.get("Time")
            for recipe in payload.get("ProcessRecipes", []) or []
            if isinstance(recipe, Mapping)
        }
        (
            state.zero_duration_process_steps,
            state.process_steps_by_material_station,
        ) = _product_process_step_indexes(payload, recipe_durations)
        for wafer in getattr(task, "wafers", ()) or ():
            material_id = str(getattr(wafer, "mat_id", ""))
            for stage in getattr(wafer, "stages", ()) or ():
                if str(getattr(stage, "stage_type", "")) != "process":
                    continue
                step_id = str(getattr(stage, "step_id", getattr(stage, "j", "")))
                process_times = dict(
                    getattr(stage, "process_time_by_chamber", {}) or {}
                )
                candidates = {
                    str(getattr(stage, "chamber", "") or ""),
                    *(
                        str(candidate)
                        for candidate in getattr(stage, "cands", ()) or ()
                    ),
                }
                for candidate in candidates - {""}:
                    try:
                        recipe_name = str(
                            dict(
                                getattr(stage, "process_recipe_by_chamber", {})
                                or {}
                            ).get(candidate, getattr(stage, "process_recipe", ""))
                            or ""
                        )
                        duration = float(
                            recipe_durations.get(
                                (recipe_name, candidate),
                                process_times.get(
                                    candidate,
                                    getattr(stage, "proc", 0.0),
                                ),
                            )
                        )
                    except (TypeError, ValueError):
                        continue
                    state.process_steps_by_material_station.setdefault(
                        (material_id, candidate),
                        set(),
                    ).add(step_id)
                    if math.isfinite(duration) and abs(duration) <= TIME_TOLERANCE:
                        state.zero_duration_process_steps.add(
                            (material_id, step_id, candidate)
                        )

        for station_name, slot_id, material in _initial_materials(task, payload):
            station = state.ensure_station(station_name, slot_id)
            if station.slots[slot_id].material is not None:
                raise ValueError(
                    _global_issue(
                        ValidationErrorCode.INITIAL_MATERIAL_CONFLICT,
                        f"初始物料在 {station_name}#{slot_id} 发生冲突",
                    )
                )
            station.slots[slot_id] = SlotState(
                SlotPhase.COMPLETED,
                material,
                material_process_count=station.slots[slot_id].material_process_count,
            )
        return state

    def ensure_station(self, name: str, slot_id: int) -> StationState:
        """返回站点，并为输入遗漏的合法引用补建槽位。"""
        station = self.stations.get(name)
        if station is None:
            station = StationState(name, "", {slot_id: SlotState()})
            self.stations[name] = station
        station.slots.setdefault(slot_id, SlotState())
        return station

    def resolve_robot(self, raw_name: str) -> Optional[RobotState]:
        """按标准名称或设备别名查找机器人。"""
        return self.robots.get(self.robot_aliases.get(raw_name, raw_name))

    def clone(self) -> "MachineState":
        """返回不共享可变状态的整机快照。"""
        return deepcopy(self)

    def seed_initial_single_chamber_wac_counters(
        self,
        station_configs: Mapping[str, Mapping[str, Any]],
    ) -> None:
        """把标准快照中可明确归属的单腔初始 WAC 值写入 PJob 账本。

        标准 ``StateVariables`` 本身不携带 PJob 维度。站点声明 ``PJobName`` 时按
        声明归属；未声明但某个变量只被一个 PJob 的 WAC 规则引用时也可无歧义
        继承。多个 PJob 共用且未声明归属时从零开始，避免旧全局值再次混入任一
        PJob 的独立周期。
        """
        for station_name, station in self.stations.items():
            if not self.uses_pjob_scoped_wac(station):
                continue
            config = station_configs.get(station_name, {})
            raw_names = config.get("PJobName") if isinstance(config, Mapping) else None
            if isinstance(raw_names, str):
                declared_pjobs = {raw_names.strip()} - {""}
            elif isinstance(raw_names, Sequence) and not isinstance(raw_names, (str, bytes)):
                declared_pjobs = {str(name).strip() for name in raw_names} - {""}
            else:
                declared_pjobs = set()
            pjobs_by_variable: Dict[str, Set[str]] = {}
            for (rule_station_name, _recipe_name), rules in self.clean_wac_trigger_rules.items():
                if rule_station_name != station_name:
                    continue
                for pjob_name, variable_name, _lower, _task_name, _step_id in rules:
                    if pjob_name:
                        pjobs_by_variable.setdefault(variable_name, set()).add(pjob_name)
            for variable_name, pjob_names in pjobs_by_variable.items():
                target_pjobs = declared_pjobs & pjob_names
                if not target_pjobs and len(pjob_names) == 1:
                    target_pjobs = set(pjob_names)
                for pjob_name in target_pjobs:
                    self.pjob_wac_counters[(station_name, pjob_name, variable_name)] = float(
                        station.state_variables.get(variable_name, 0.0)
                    )

    def uses_pjob_scoped_wac(self, station: StationState) -> bool:
        """判断 PM 的 WAC 是否应按 PJob 独立累计。

        单腔 ``Process`` / ``ProcessChamber`` 的产品依次进入同一物理槽位，WAC
        周期属于各 PJob；双腔/多槽 PM 的两片工艺共享同一次腔室周期，继续使用
        原有的腔室全局计数。
        """
        station_type = station.station_type.casefold()
        return (
            "process" in station_type
            and station_type != LOAD_LOCK_TYPE
            and not _is_paired_process_chamber(station)
        )

    def is_wac_counter_variable(self, station_name: str, variable_name: str) -> bool:
        """返回状态变量是否被当前代任一 WAC 规则引用。"""
        return any(
            rule_variable_name == variable_name
            for (rule_station_name, _recipe_name), rules in self.clean_wac_trigger_rules.items()
            if rule_station_name == station_name
            for _pjob_name, rule_variable_name, _lower, _task_name, _step_id in rules
        )

    def wac_counter_value(
        self,
        station: StationState,
        pjob_name: str,
        variable_name: str,
    ) -> float:
        """读取当前 PJob 或腔室全局的 WAC 计数。"""
        if not (
            pjob_name
            and self.uses_pjob_scoped_wac(station)
            and self.is_wac_counter_variable(station.name, variable_name)
        ):
            return float(station.state_variables.get(variable_name, 0.0))
        return self.pjob_wac_counters.get((station.name, pjob_name, variable_name), 0.0)

    def add_wac_counter(
        self,
        station: StationState,
        pjob_name: str,
        variable_name: str,
        increment: float,
    ) -> None:
        """增加 WAC 计数，并把单腔最近 PJob 值投影到兼容状态变量。"""
        if not (
            pjob_name
            and self.uses_pjob_scoped_wac(station)
            and self.is_wac_counter_variable(station.name, variable_name)
        ):
            station.state_variables[variable_name] = (
                station.state_variables.get(variable_name, 0.0) + increment
            )
            return
        key = (station.name, pjob_name, variable_name)
        value = self.pjob_wac_counters.get(key, 0.0) + increment
        self.pjob_wac_counters[key] = value
        # 标准实时快照没有按 PJob 表达 Counter 的字段，保留最近使用 PJob 的值，
        # 同时由独立账本保证平台校验和跨代回放不混计。
        station.state_variables[variable_name] = value

    def reset_wac_counter(
        self,
        station: StationState,
        pjob_names: Sequence[str],
        variable_name: str,
    ) -> None:
        """归零 WAC 清洁关联的 PJob 计数或双腔全局计数。"""
        names = [name for name in pjob_names if name]
        if (
            names
            and self.uses_pjob_scoped_wac(station)
            and self.is_wac_counter_variable(station.name, variable_name)
        ):
            for pjob_name in names:
                self.pjob_wac_counters[(station.name, pjob_name, variable_name)] = 0.0
            station.state_variables[variable_name] = 0.0
            return
        station.state_variables[variable_name] = 0.0

    def refresh_validation_metadata(
        self,
        update_params: Mapping[str, Any],
    ) -> None:
        """按下一代标准 update 刷新校验元数据，但保留持续物理状态。

        重算代际切换后，PM 的 ``StateVariables``、槽位占用和机器人状态是已经
        发生 Move 的事实，不能被新 update 中的初始化值重置；但 Route/Recipe
        可能新增 PJob，因此 WAC 触发条件、清洁完成后的计数重置规则和 Recipe
        权重必须从下一代 update 重新解析。
        """
        from .move_validation_helpers import (
            _clean_obligation_specs,
            _clean_task_state_variables,
            _clean_wac_trigger_rules,
            _initial_payload,
            _mapping,
            _process_recipe_weights,
            _station_state_variables,
        )
        payload = _initial_payload(update_params)
        self.process_recipe_weights = _process_recipe_weights(payload)
        self.clean_task_state_variables = _clean_task_state_variables(payload)
        self.clean_wac_trigger_rules = _clean_wac_trigger_rules(payload)
        self.clean_obligations = _clean_obligation_specs(payload)
        (
            self.zero_duration_process_steps,
            self.process_steps_by_material_station,
        ) = _product_process_step_indexes(
            payload,
            {
                (str(recipe.get("Name") or ""), str(recipe.get("ModuleName") or "")): recipe.get("Time")
                for recipe in payload.get("ProcessRecipes", []) or []
                if isinstance(recipe, Mapping)
            },
        )

        # 新一代可能首次声明某个状态变量。已有变量是已执行 Move 的累计事实，
        # 只能补缺，不能用 update 初值覆盖。
        for station_name, config in _mapping(payload.get("Stations")).items():
            station = self.stations.get(station_name)
            if station is None:
                continue
            for variable_name, value in _station_state_variables(config).items():
                station.state_variables.setdefault(variable_name, value)


def _route_need_process_visits(
    route: Mapping[str, Any],
    recipe_durations: Mapping[Tuple[str, str], Any],
) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """从一条 Route 提取 NeedProcess 访问，以及其中可证明的零时长访问。

    返回值为 ``((step_id, module_name) 全部加工访问, 零时长子集)``。空
    ``ProcessRecipe`` 按运行计划表示即时加工；有名称时以 ProcessRecipes 时长为准。
    """
    process_visits: Set[Tuple[str, str]] = set()
    zero_visits: Set[Tuple[str, str]] = set()
    for route_step in route.get("RouteSteps", []) or []:
        if not isinstance(route_step, Mapping) or not route_step.get("NeedProcess"):
            continue
        step_id = str(route_step.get("StepID", ""))
        for visit in route_step.get("Visits", []) or []:
            if not isinstance(visit, Mapping):
                continue
            module_name = str(visit.get("StationName") or "")
            if not module_name:
                continue
            process_visits.add((step_id, module_name))
            recipe_name = str(visit.get("ProcessRecipe") or "")
            if not recipe_name:
                # 运行计划约定：产品 NeedProcess Visit 的 Recipe 为空即表示
                # 零时长即时加工。清洗使用独立 CleanTaskName，不走本分支。
                zero_visits.add((step_id, module_name))
                continue
            try:
                duration = float(recipe_durations[(recipe_name, module_name)])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(duration) and abs(duration) <= TIME_TOLERANCE:
                zero_visits.add((step_id, module_name))
    return process_visits, zero_visits


def _record_material_process_visits(
    material_id: str,
    process_visits: Set[Tuple[str, str]],
    zero_visits: Set[Tuple[str, str]],
    process_steps_by_material_station: Dict[Tuple[str, str], Set[str]],
    zero_duration_steps: Set[Tuple[str, str, str]],
) -> None:
    """把一条物料的加工访问写入校验索引。"""
    if not material_id:
        return
    for step_id, module_name in process_visits:
        process_steps_by_material_station.setdefault(
            (material_id, module_name),
            set(),
        ).add(step_id)
    for step_id, module_name in zero_visits:
        zero_duration_steps.add((material_id, step_id, module_name))


def _product_process_step_indexes(
    payload: Mapping[str, Any],
    recipe_durations: Mapping[Tuple[str, str], Any],
) -> Tuple[Set[Tuple[str, str, str]], Dict[Tuple[str, str], Set[str]]]:
    """从 AlgSchedule 提取零时长产品工艺及各物料在各 PM 的加工 Step。

    同时扫描 ``Materials[].Route`` 与 ``ProcessJobs[].OriginRoute``，并把
    OriginRoute 上的零时长工序复制到该 PJob ``MatList`` 的每一片。双腔成对
    建模可能只在计划里留下 pair 头片，物理 MoveList 仍携带两片 ID；校验必须
    能证明两片都可以省略 ProcessMove。
    """
    zero_duration_steps: Set[Tuple[str, str, str]] = set()
    process_steps_by_material_station: Dict[Tuple[str, str], Set[str]] = {}
    materials_by_pjob: Dict[str, List[str]] = {}
    for material in payload.get("Materials", []) or []:
        if not isinstance(material, Mapping):
            continue
        material_id = str(material.get("ID", material.get("MatID", "")))
        pjob_name = str(material.get("PJobName") or "")
        if pjob_name:
            materials_by_pjob.setdefault(pjob_name, []).append(material_id)
        process_visits, zero_visits = _route_need_process_visits(
            material.get("Route") or {},
            recipe_durations,
        )
        _record_material_process_visits(
            material_id,
            process_visits,
            zero_visits,
            process_steps_by_material_station,
            zero_duration_steps,
        )
    for process_job in payload.get("ProcessJobs", []) or []:
        if not isinstance(process_job, Mapping):
            continue
        process_visits, zero_visits = _route_need_process_visits(
            process_job.get("OriginRoute") or {},
            recipe_durations,
        )
        if not process_visits and not zero_visits:
            continue
        job_name = str(process_job.get("JobName") or "")
        material_ids = [
            str(item)
            for item in (process_job.get("MatList") or [])
        ]
        if job_name:
            material_ids.extend(materials_by_pjob.get(job_name, []))
        seen_ids: Set[str] = set()
        for material_id in material_ids:
            if not material_id or material_id in seen_ids:
                continue
            seen_ids.add(material_id)
            _record_material_process_visits(
                material_id,
                process_visits,
                zero_visits,
                process_steps_by_material_station,
                zero_duration_steps,
            )
    return zero_duration_steps, process_steps_by_material_station


def _zero_duration_product_process_steps(
    payload: Mapping[str, Any],
    recipe_durations: Mapping[Tuple[str, str], Any],
) -> Set[Tuple[str, str, str]]:
    """从当前 AlgSchedule 精确提取可省略 ProcessMove 的零时长产品工艺。"""
    zero_duration_steps, _process_steps = _product_process_step_indexes(
        payload,
        recipe_durations,
    )
    return zero_duration_steps


@dataclass
class _ScheduledCompletion:
    """保存已经开始、等待在结束时落地的状态变更。"""

    end_time: float
    move_id: int
    complete: Callable[[], None]


def _zero_duration_environment_transitions(
    station_config: Mapping[str, Any],
) -> Set[Tuple[str, str]]:
    """提取设备明确配置为零时长、可省略的 LoadLock 压力转换。

    算法协议允许省略没有实际时长的 ``PrePrepareMove``。这里只接受
    ``PumpTime``、``VentTime`` 或 ``PrePrepareTime[].Time`` 的显式零值，
    缺失时长绝不推断为零，避免放宽普通的压力态转换校验。
    """
    from .move_validation_helpers import (
        _environment_aliases,
    )
    transitions: Set[Tuple[str, str]] = set()

    def add_if_zero(
        raw_duration: Any,
        source: str,
        target: str,
    ) -> None:
        """在时长是有限零值时登记一条标准压力态转换。"""
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            return
        if math.isfinite(duration) and abs(duration) <= TIME_TOLERANCE:
            transitions.add((source, target))

    if station_config.get("PumpTime") is not None:
        add_if_zero(station_config.get("PumpTime"), ATMOSPHERE, VACUUM)
    if station_config.get("VentTime") is not None:
        add_if_zero(station_config.get("VentTime"), VACUUM, ATMOSPHERE)
    aliases = _environment_aliases(station_config)
    for item in station_config.get("PrePrepareTime") or ():
        if not isinstance(item, Mapping):
            continue
        source = aliases.get(str(item.get("LastItem") or "").strip().upper())
        target = aliases.get(str(item.get("CurrentItem") or "").strip().upper())
        if source in {ATMOSPHERE, VACUUM} and target in {ATMOSPHERE, VACUUM}:
            add_if_zero(item.get("Time"), source, target)
            continue
        transition_type = str(item.get("PrePrepareType") or "").strip().lower()
        if transition_type.startswith("pump"):
            add_if_zero(item.get("Time"), ATMOSPHERE, VACUUM)
        elif transition_type.startswith("vent"):
            add_if_zero(item.get("Time"), VACUUM, ATMOSPHERE)
    return transitions


def _is_paired_process_chamber(station: StationState) -> bool:
    """判断站点是否按双腔/多槽同步加工语义处理驻片。"""
    station_type = station.station_type.lower()
    return station_type == MULTI_PROCESS_CHAMBER_TYPE or (
        "process" in station_type
        and station_type != LOAD_LOCK_TYPE
        and len(station.slots) > 1
    )


def _is_product_process_chamber(station: StationState) -> bool:
    """判断站点是否允许按产品工艺语义补齐省略的零时长 ProcessMove。"""
    return station.station_type.lower() in {
        PROCESS_CHAMBER_TYPE,
        MULTI_PROCESS_CHAMBER_TYPE,
    }
