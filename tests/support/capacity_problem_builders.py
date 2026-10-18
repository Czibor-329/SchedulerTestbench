"""平台理论产能测试的确定性领域 builder。

迁移自 Cycle 合成夹具，保持已有计时与拒绝分支前提；每次独立构造 CapacityInput，
不导入算法 tests 包，也不跨仓库共享可变实例。

本模块只提供合成输入，用于验证四种路线模板、边界批量与拒绝分支。时间参数
是为测试设计的合成值，不是真实设备测量值；公司数据集验收仍从父仓库统一
入口执行。

设备形态固定为：一个 LoadPort、两个可进可出的双槽 LoadLock、若干加工腔，
真空机器人有两个持片位并支持交换，大气机器人只有一个持片位且不支持交换。
这正是目标设备的真实能力，因此测试不必再隐藏这些关键前提，也不允许用双臂
ATM 夹具通过验收。
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.backend.theory_capacity.input_model import StationTiming, CleaningRequirement, CapacityInput, RobotTiming, RouteStage, ReferenceProduct

# 合成时长（秒）。命名常量避免在路线构造里散落裸数字。
DOOR_SECONDS = 1.0
ROBOT_PICK_SECONDS = 2.0
ROBOT_PLACE_SECONDS = 2.0
ROBOT_MOVE_SECONDS = 3.0
PUMP_SECONDS = 20.0
VENT_SECONDS = 20.0
DEFAULT_PROCESS_SECONDS = 120.0
ALIGN_SECONDS = 8.0

# 基准场景的资源名。``A``/``B`` 别名由策略按 Route 候选顺序建立。
LOAD_PORT = "LP1"
LOAD_LOCK_A = "LA"
LOAD_LOCK_B = "LB"
ATMOSPHERE_ROBOT = "ATR"
VACUUM_ROBOT = "VTR"
ALIGNER = "AL"

# 路线中 LoadLock 的槽位约定：入口占逻辑槽 0，出口占逻辑槽 1。单片语义由
# 策略保证瞬时占用不超过一片，而不是把物理容量钳成 1。
ENTRY_LOADLOCK_SLOT = 0
EXIT_LOADLOCK_SLOT = 1


def _chamber(
    name: str,
    station_type: str,
    *,
    capacity: int = 1,
    robots: Sequence[str] = (ATMOSPHERE_ROBOT, VACUUM_ROBOT),
) -> StationTiming:
    """构造带一秒门动作的测试站点；LoadLock 额外带抽/充气时长。"""
    is_loadlock = station_type == "LoadLock"
    return StationTiming(
        name,
        station_type,
        capacity,
        pump_time=PUMP_SECONDS if is_loadlock else None,
        vent_time=VENT_SECONDS if is_loadlock else None,
        pick_prepare_time={robot: DOOR_SECONDS for robot in robots},
        place_prepare_time={robot: DOOR_SECONDS for robot in robots},
        pick_complete_time={robot: DOOR_SECONDS for robot in robots},
        place_complete_time={robot: DOOR_SECONDS for robot in robots},
    )


# 机器人持片位数量：VAC 双臂可换片，ATM 单臂不可换片。
DUAL_HAND_COUNT = 2
SINGLE_HAND_COUNT = 1

# 默认 PJob 名；清洁义务按 (PJob, PM) 判定，Job 边界测试需显式覆盖。
DEFAULT_PJOB = "PJ1"

# 三种空腔清洁的任务名与配方名。任务名必须与平台协议一致，配方名只用于
# 可读性：平台校验器不比较清洁配方是否与 Route 配置一致。
PRE_CLEAN_TASK = "PreClean"
POST_CLEAN_TASK = "PostClean"
WAC_CLEAN_TASK = "WacClean"
PRE_CLEAN_RECIPE = "PreCleanRecipe"
POST_CLEAN_RECIPE = "PostCleanRecipe"
WAC_CLEAN_RECIPE = "WacCleanRecipe"


def _dual_hand_robot(name: str, scope: Sequence[str]) -> RobotTiming:
    """构造具有两个独立持片位并支持交换的机器人。"""
    return RobotTiming(
        name,
        list(scope),
        DUAL_HAND_COUNT,
        True,
        {station: ROBOT_PICK_SECONDS for station in scope},
        {station: ROBOT_PLACE_SECONDS for station in scope},
        [{"Time": ROBOT_MOVE_SECONDS}],
    )


def _single_hand_robot(name: str, scope: Sequence[str]) -> RobotTiming:
    """构造只有一个持片位、不支持交换的机器人。

    这是目标设备 ATM 的真实能力，也用于 VAC 缺失换片能力的拒绝分支测试。
    """
    return RobotTiming(
        name,
        list(scope),
        SINGLE_HAND_COUNT,
        False,
        {station: ROBOT_PICK_SECONDS for station in scope},
        {station: ROBOT_PLACE_SECONDS for station in scope},
        [{"Time": ROBOT_MOVE_SECONDS}],
    )


def cycle_route_stages(
    *,
    load_port: str,
    process_candidates: Sequence[Sequence[str]],
    load_port_slot: int,
    loadlock_candidates: Sequence[str] = (LOAD_LOCK_A, LOAD_LOCK_B),
    process_seconds: Sequence[float] | None = None,
    include_aligner: bool = False,
) -> tuple[list[RouteStage], list[str]]:
    """构造 ``LP → [Aligner] → LL → 加工链 → LL → LP`` 的单片循环路线。

    :param load_port: 原 LoadPort 站点名。
    :param process_candidates: 每道加工工序的候选腔列表，顺序即 ``PM`` 别名顺序。
    :param load_port_slot: 该片晶圆的原 LP 槽位（0-based）。
    :param loadlock_candidates: 进出口共享的 LoadLock 候选。
    :param process_seconds: 每道工序的加工时长；缺省全部使用默认值。
    :param include_aligner: 是否在大气段插入真实校准工序。
    :return: ``(stages, transports)``。
    """
    durations = list(process_seconds or [DEFAULT_PROCESS_SECONDS] * len(process_candidates))
    stages: list[RouteStage] = [
        RouteStage(0, load_port, "source", 0.0, "", ATMOSPHERE_ROBOT, -1.0, slot=load_port_slot),
    ]
    transports: list[str] = [ATMOSPHERE_ROBOT]
    if include_aligner:
        stages.append(RouteStage(len(stages), ALIGNER, "process", ALIGN_SECONDS,
                            ATMOSPHERE_ROBOT, ATMOSPHERE_ROBOT, -1.0))
        transports.append(ATMOSPHERE_ROBOT)
    stages.append(RouteStage(
        len(stages),
        loadlock_candidates[0],
        "loadlock",
        PUMP_SECONDS,
        ATMOSPHERE_ROBOT,
        VACUUM_ROBOT,
        -1.0,
        "entry",
        ENTRY_LOADLOCK_SLOT,
        cands=list(loadlock_candidates),
    ))
    transports.append(VACUUM_ROBOT)
    for position, candidates in enumerate(process_candidates):
        stages.append(RouteStage(
            len(stages),
            candidates[0],
            "process",
            durations[position],
            VACUUM_ROBOT,
            VACUUM_ROBOT,
            -1.0,
            cands=list(candidates),
        ))
        transports.append(VACUUM_ROBOT)
    stages.append(RouteStage(
        len(stages),
        loadlock_candidates[0],
        "loadlock",
        VENT_SECONDS,
        VACUUM_ROBOT,
        ATMOSPHERE_ROBOT,
        -1.0,
        "exit",
        EXIT_LOADLOCK_SLOT,
        cands=list(loadlock_candidates),
    ))
    transports.append(ATMOSPHERE_ROBOT)
    stages.append(RouteStage(len(stages), load_port, "sink", 0.0,
                        ATMOSPHERE_ROBOT, "", -1.0, slot=load_port_slot))
    # transports 长度必须为 stages 长度减一，最后一跳已在上面追加。
    return stages, transports


def dedicated_cycle_problem(wafer_count: int, *, process_candidates=(("PM1",),),
                            loadlock_capacity: int = 1, **options) -> CapacityInput:
    """构造 I=LA、O=LB 的分向锁输入，单槽也能工作，不赋予 LL 交换能力。

    加工候选可用于未开放形态的拒绝测试；清洁和校准参数沿用具名通用构造器。
    """
    problem = cycle_problem(wafer_count=wafer_count, process_candidates=process_candidates,
                            loadlock_capacity=loadlock_capacity, **options)
    for wafer in problem.wafers:
        for stage in wafer.stages:
            if stage.stage_type == "loadlock":
                station = LOAD_LOCK_A if stage.ll_type == "entry" else LOAD_LOCK_B
                stage.chamber, stage.cands, stage.slot = station, [station], 0
    return problem


def cycle_problem(
    *,
    process_candidates: Sequence[Sequence[str]],
    wafer_count: int,
    material_ids: Optional[Sequence[int]] = None,
    load_port_slots: Optional[Sequence[int]] = None,
    process_seconds: Sequence[float] | None = None,
    include_aligner: bool = False,
    vacuum_dual_hand: bool = True,
    atmosphere_dual_hand: bool = False,
    loadlock_capacity: int = 2,
    loadlock_candidates: Sequence[str] = (LOAD_LOCK_A, LOAD_LOCK_B),
    pjob_names: Optional[Sequence[str]] = None,
    pre_clean_seconds: float = 0.0,
    post_clean_seconds: float = 0.0,
    wac_trigger: int = 0,
    wac_seconds: float = 0.0,
) -> CapacityInput:
    """构造单片循环策略的测试 CapacityInput。

    :param process_candidates: 每道加工工序的候选腔列表，决定路线类型。
    :param wafer_count: 输入晶圆数量，可为 0。
    :param material_ids: 物料 ID；缺省使用非连续 ID 以防依赖连续整数假设。
    :param load_port_slots: 各片的原 LP 槽位；缺省按顺序占用不同槽位。
    :param process_seconds: 每道工序的加工时长。
    :param include_aligner: 是否插入真实校准工序。
    :param vacuum_dual_hand: 为假时 VAC 也退化成单臂不可交换，用于拒绝分支。
    :param atmosphere_dual_hand: 目标设备的 ATM 是单臂不可交换，默认即为此。
        仅反例测试可置真，用来构造物理上合法、但违反单臂服务协议的输出，
        证明审计不是靠物理校验兜底。正向用例不得使用双臂 ATM。
    :param loadlock_capacity: LoadLock 物理容量，可设为 1 触发拒绝分支。
    :param loadlock_candidates: 进出口共享的 LoadLock 候选。
    :param pjob_names: 逐片的 PJob 归属；缺省全部属于 ``PJ1``。清洁义务按
        ``(PJob, PM)`` 判定，因此 Job 边界测试必须显式给出两个 PJob。
    :param pre_clean_seconds: 大于 0 时为每个加工腔登记 PreClean 空腔清洁。
    :param post_clean_seconds: 大于 0 时为每个加工腔登记 PostClean 空腔清洁。
    :param wac_trigger: 大于 0 时为加工工序登记每 N 次离腔的周期清洁阈值。
    :param wac_seconds: 周期清洁时长；``wac_trigger`` 为 0 时忽略。
    """
    identifiers = list(material_ids or [1000 + index * 7 for index in range(wafer_count)])
    slots = list(load_port_slots or range(wafer_count))
    chambers = {
        LOAD_PORT: _chamber(LOAD_PORT, "LoadPort", capacity=max(wafer_count, 1)),
    }
    for name in loadlock_candidates:
        chambers[name] = _chamber(name, "LoadLock", capacity=loadlock_capacity)
    for candidates in process_candidates:
        for name in candidates:
            chambers.setdefault(name, _chamber(name, "ProcessChamber"))
    atmosphere_scope = [LOAD_PORT, *loadlock_candidates]
    if include_aligner:
        chambers[ALIGNER] = _chamber(ALIGNER, "Aligner")
        atmosphere_scope.append(ALIGNER)
    vacuum_scope = [
        *loadlock_candidates,
        *(name for candidates in process_candidates for name in candidates),
    ]
    build_vacuum = _dual_hand_robot if vacuum_dual_hand else _single_hand_robot
    build_atmosphere = _dual_hand_robot if atmosphere_dual_hand else _single_hand_robot
    owners = list(pjob_names or [DEFAULT_PJOB] * wafer_count)
    wafers: list[ReferenceProduct] = []
    for index in range(wafer_count):
        stages, transports = cycle_route_stages(
            load_port=LOAD_PORT,
            process_candidates=process_candidates,
            load_port_slot=slots[index],
            loadlock_candidates=loadlock_candidates,
            process_seconds=process_seconds,
            include_aligner=include_aligner,
        )
        # 周期清洁挂在加工工序上，与 Machine 的 (PM, PJob) 计数口径一致。
        if wac_trigger > 0:
            for stage in stages:
                if stage.stage_type != "process":
                    continue
                stage.clean_trigger = int(wac_trigger)
                stage.clean_time = float(wac_seconds)
                stage.clean_recipe = WAC_CLEAN_RECIPE
                stage.clean_task = WAC_CLEAN_TASK
        wafers.append(ReferenceProduct(
            index,
            identifiers[index],
            "cycle-route",
            index,
            stages,
            transports,
            owners[index],
        ))
    problem = CapacityInput(
        chambers,
        {
            ATMOSPHERE_ROBOT: build_atmosphere(ATMOSPHERE_ROBOT, atmosphere_scope),
            VACUUM_ROBOT: build_vacuum(VACUUM_ROBOT, vacuum_scope),
        },
        wafers,
    )
    # Pre/Post 清洁按 (PJob, PM 列表) 登记，每个出现过的 PJob 各一份。
    process_stations = [
        name for candidates in process_candidates for name in candidates
    ]
    for owner in dict.fromkeys(owners):
        if pre_clean_seconds > 0.0:
            problem.pre_clean.append(CleaningRequirement(
                list(process_stations),
                float(pre_clean_seconds),
                PRE_CLEAN_RECIPE,
                PRE_CLEAN_TASK,
                owner,
            ))
        if post_clean_seconds > 0.0:
            problem.post_clean.append(CleaningRequirement(
                list(process_stations),
                float(post_clean_seconds),
                POST_CLEAN_RECIPE,
                POST_CLEAN_TASK,
                owner,
            ))
    return problem


def single_node_problem(wafer_count: int, **options) -> CapacityInput:
    """构造 ``LL → PM1 → LL`` 的单节点循环输入。"""
    return cycle_problem(process_candidates=[["PM1"]], wafer_count=wafer_count, **options)


def parallel_problem(wafer_count: int, **options) -> CapacityInput:
    """构造 ``LL → PM1/PM2/PM3 → LL`` 的并行循环输入。"""
    return cycle_problem(
        process_candidates=[["PM1", "PM2", "PM3"]],
        wafer_count=wafer_count,
        **options,
    )


def serial_problem(wafer_count: int, **options) -> CapacityInput:
    """构造 ``LL → PM1 → PM2 → LL`` 的串行循环输入。"""
    return cycle_problem(
        process_candidates=[["PM1"], ["PM2"]],
        wafer_count=wafer_count,
        **options,
    )


def three_stage_serial_problem(wafer_count: int, **options) -> CapacityInput:
    """构造 ``LL → PM1 → PM2 → PM3 → LL`` 的三道串行循环输入。"""
    return cycle_problem(
        process_candidates=[["PM1"], ["PM2"], ["PM3"]],
        wafer_count=wafer_count,
        **options,
    )


def mixed_problem(wafer_count: int, **options) -> CapacityInput:
    """构造 ``LL → PM1 → PM2/PM3 → LL`` 的混合循环输入。"""
    return cycle_problem(
        process_candidates=[["PM1"], ["PM2", "PM3"]],
        wafer_count=wafer_count,
        **options,
    )


__all__ = [
    "ALIGNER",
    "ALIGN_SECONDS",
    "ATMOSPHERE_ROBOT",
    "DEFAULT_PROCESS_SECONDS",
    "ENTRY_LOADLOCK_SLOT",
    "EXIT_LOADLOCK_SLOT",
    "LOAD_LOCK_A",
    "LOAD_LOCK_B",
    "LOAD_PORT",
    "PUMP_SECONDS",
    "VACUUM_ROBOT",
    "VENT_SECONDS",
    "cycle_problem",
    "cycle_route_stages",
    "mixed_problem",
    "parallel_problem",
    "serial_problem",
    "single_node_problem",
    "three_stage_serial_problem",
]
