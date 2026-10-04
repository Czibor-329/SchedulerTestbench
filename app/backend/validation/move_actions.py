"""平台物理动作的使能检查、资源占用和完成落地；只依赖状态与配置解析。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple
from collections.abc import Mapping, Sequence

from .move_state import (
    _is_product_process_chamber,
    ALIGN_MOVE,
    ATMOSPHERE,
    COMPLETE_MOVE,
    DoorState,
    LoadLockState,
    MULTI_PICK_MOVE,
    MachineState,
    MaterialState,
    PICK_MOVE,
    PLACE_MOVE,
    PREPARE_MOVE,
    PRE_PREPARE_MOVE,
    PRE_TRANS_MOVE,
    PROCESS_MOVE,
    RobotState,
    SWAP_MOVE,
    SlotPhase,
    SlotState,
    StationState,
    TIME_TOLERANCE,
    TWIN_LOAD_LOCK_PAIRS,
    VACUUM,
    ValidationErrorCode,
    _ScheduledCompletion,
)
from .move_validation_helpers import (
    _available,
    _clean_validation_type,
    _environment_label,
    _environment_state,
    _first_text,
    _has_active_process,
    _integer_values,
    _issue,
    _material_matches,
    _material_with_metadata,
    _number,
    _related_move,
    _required_environment,
    _reserve_slot,
    _robot,
    _schedule,
    _set_slot,
    _slot_map_group_stations,
    _start_time,
    _station_access_error,
    _station_name,
    _transport_rows,
    _validate_clean_start,
    _validate_distinct_transport_rows,
    _values,
)


def _robot_target_stations(robot: RobotState) -> Set[str]:
    """机器人当前可对准的站集合（槽位级模式）。"""
    stations: Set[str] = set()
    for options in robot.slot_options.values():
        stations.update(station for station, _ in options)
    return stations


def _robot_derived_position(robot: RobotState) -> Optional[str]:
    """从槽位候选派生站级 position（兼容外部 API 与快照回写）。"""
    stations = _robot_target_stations(robot)
    return min(stations) if stations else robot.position


def _robot_alignment_issue(
    robot: RobotState,
    move: Mapping[str, Any],
    station_refs: Sequence[Tuple[str, int, int]],
) -> Optional[str]:
    """校验机器人能否在取放/换片动作的目标站槽位上作业。

    ``station_refs`` 为逐行 ``(站名, 站槽位号, 手槽号)``。配置了 ``SlotsStationMap``
    的机器人按手槽候选的**站级可达**逐行校验（双臂可跨站）；具体槽位号信任算法
    动作声明，不构成硬约束。未配置的退回站级 position 校验。
    """
    if robot.slot_map:
        for station_name, station_slot_id, robot_slot_id in station_refs:
            options = robot.slot_options.get(robot_slot_id)
            if options is None:
                # 该手槽没有槽位级拓扑配置（如算法按 capacity 补全的逻辑槽位），
                # 不参与候选校验，退回调用方的宽松判定。
                continue
            if station_name not in {candidate[0] for candidate in options}:
                reachable = "、".join(sorted({candidate[0] for candidate in options}))
                return _issue(
                    move,
                    ValidationErrorCode.ROBOT_ALIGNMENT_INVALID,
                    f"{robot.name}#{robot_slot_id} 无法对准 {station_name}#{station_slot_id}（当前手槽可及站：{reachable or '无'}）",
                )
        return None
    station_names = {station_name for station_name, _, _ in station_refs}
    if robot.position is not None and robot.position not in station_names:
        return _issue(move, ValidationErrorCode.ROBOT_ALIGNMENT_INVALID, f"{robot.name} 当前指向 {robot.position}，不在组合站点 {sorted(station_names)}")
    return None


def _parallel_arrays_alignment_issue(move: Mapping[str, Any], fields: Sequence[str]) -> Optional[str]:
    """校验一组并行数组的非空长度一致（空字段忽略），对齐 MOVE.ARRAY_ALIGNMENT。"""
    lengths = {field: len(_values(move, field)) for field in fields}
    nonzero = {length for length in lengths.values() if length > 0}
    if len(nonzero) > 1:
        detail = ",".join(f"{field}={lengths[field]}" for field in fields)
        return _issue(move, ValidationErrorCode.PARALLEL_ARRAY_INVALID, f"Move 对应数组长度不一致: {detail}")
    return None


def _pretrans_source_issue(robot: RobotState, move: Mapping[str, Any]) -> Optional[str]:
    """校验转位起点：槽位级模式按涉及手槽是否跨站分两种语义比对。

    真空手双槽臂在孪生站组（LALB）下，本次转位涉及的不同手槽会指向不同站
    （槽1→LA、槽2→LB）：此时 SrcStationList 必须按槽与该手槽精确指向一致，对齐
    MoveStateSim 的 RequirePreTransSource 逐槽检查。其余情况（大气手整臂单站、
    单槽转位）退回候选站集合检查；未配置槽位级拓扑时退回站级 position。
    """
    sources = [str(value) for value in _values(move, "SrcStationList") if value]
    robot_slots = _integer_values(move, "RobotSlotList")
    if robot.slot_map:
        involved_stations = {
            robot.slot_targets[slot][0]
            for slot in robot_slots
            if slot in robot.slot_targets and robot.slot_targets[slot] and robot.slot_targets[slot][0]
        }
        if len(involved_stations) >= 2:
            n = max(len(sources), max(1, len(robot_slots)))
            for index in range(n):
                source = sources[index] if index < len(sources) else (sources[0] if len(sources) == 1 else "")
                slot = robot_slots[index] if index < len(robot_slots) else (robot_slots[0] if len(robot_slots) == 1 else None)
                if not source or slot is None:
                    continue
                target = robot.slot_targets.get(slot)
                if target is not None and target[0]:
                    if source != target[0]:
                        return _issue(
                            move,
                            ValidationErrorCode.PRETRANS_STATE_INVALID,
                            f"{robot.name}#{slot} 无法从 {source} 转位（当前指向 {target[0]}）",
                        )
                elif source not in _robot_target_stations(robot):
                    return _issue(
                        move,
                        ValidationErrorCode.PRETRANS_STATE_INVALID,
                        f"{robot.name} 无法从 {source} 转位（当前手槽指向站：{sorted(_robot_target_stations(robot))}）",
                    )
            return None
        source = sources[0] if sources else ""
        if source and source not in _robot_target_stations(robot):
            return _issue(
                move,
                ValidationErrorCode.PRETRANS_STATE_INVALID,
                f"{robot.name} 无法从 {source} 转位（当前手槽指向站：{sorted(_robot_target_stations(robot))}）",
            )
        return None
    source = sources[0] if sources else ""
    if robot.position is not None and source and robot.position != source:
        return _issue(move, ValidationErrorCode.PRETRANS_STATE_INVALID, f"{robot.name} 当前指向 {robot.position}，不是 {source}")
    return None


def _pretrans_target_group(robot: RobotState, dest_stations: Sequence[str]) -> Optional[str]:
    """从 DestStationList 反查覆盖全部目标站的 SlotsStationMap 站组名。"""
    target_stations = {str(value) for value in dest_stations if value}
    if not target_stations:
        return None
    if robot.station_groups is None:
        robot.station_groups = _slot_map_group_stations(robot.slot_map)
    for group_name, stations in robot.station_groups.items():
        if target_stations.issubset(stations):
            return group_name
    return None


def _apply_pretrans_landing(robot: RobotState, move: Mapping[str, Any]) -> None:
    """转位落地：整条物理 Arm 先切到目标站组，再刷新动作声明的精确指向。

    ``SlotsStationMap`` 描述的是共享 Arm 的站组姿态。即使一条 PreTrans 只列出
    一个 ``RobotSlot``，Arm 上未参与本次取放的其他手槽也会随 Arm 一起转动；
    若只刷新声明槽位，后续双片转位会把其余手槽误判为仍指向旧站。
    """
    if robot.slot_map:
        dest_stations = [str(value) for value in _values(move, "DestStationList")]
        group = _pretrans_target_group(robot, dest_stations)
        if group is not None:
            for slot_id, groups in robot.slot_map.items():
                candidates = set(groups.get(group) or ())
                if not candidates:
                    continue
                robot.slot_options[slot_id] = candidates
                robot.slot_targets[slot_id] = min(candidates)
    robot_slots = _integer_values(move, "RobotSlotList")
    dest_stations = [str(value) for value in _values(move, "DestStationList")]
    dest_slots = _integer_values(move, "DestSlotList")
    for index, robot_slot in enumerate(robot_slots):
        station = dest_stations[index] if index < len(dest_stations) else (dest_stations[0] if dest_stations else None)
        station_slot = dest_slots[index] if index < len(dest_slots) else None
        if station:
            if station_slot is not None:
                robot.slot_targets[robot_slot] = (station, station_slot)
            else:
                robot.slot_targets[robot_slot] = min(
                    (candidate for candidate in robot.slot_options.get(robot_slot, ()) if candidate[0] == station),
                    default=None,
                )
    robot.position = _robot_derived_position(robot)


def _start_move(
    state: MachineState,
    move: Mapping[str, Any],
    end_time: float,
    all_moves: Sequence[Mapping[str, Any]],
    scheduled: List[_ScheduledCompletion],
) -> Optional[str]:
    """分派一条动作并登记其完成状态。"""
    handlers = {
        PICK_MOVE: _start_pick,
        PLACE_MOVE: _start_place,
        SWAP_MOVE: _start_swap,
        PRE_TRANS_MOVE: _start_pretrans,
        PREPARE_MOVE: _start_prepare,
        COMPLETE_MOVE: _start_complete,
        PROCESS_MOVE: _start_process,
        PRE_PREPARE_MOVE: _start_preprepare,
        ALIGN_MOVE: _start_align,
    }
    handler = handlers.get(move.get("MoveType"))
    if handler is None:
        return _issue(move, ValidationErrorCode.MOVE_TYPE_UNSUPPORTED, f"不支持 MoveType={move.get('MoveType')}")
    return handler(state, move, end_time, all_moves, scheduled)


def _supplement_state_from_moves(
    state: MachineState,
    moves: Sequence[Mapping[str, Any]],
) -> None:
    """为旧协议中省略的拓扑补齐动作已明确引用的资源与槽位。"""
    transport_move_types = {PICK_MOVE, PLACE_MOVE, SWAP_MOVE, PRE_TRANS_MOVE}
    controlled_station_names = {
        _station_name(move)
        for move in moves
        if move.get("MoveType") in {PREPARE_MOVE, COMPLETE_MOVE}
        and _station_name(move)
    }
    station_slot_references: Dict[str, Set[int]] = {}
    robot_slot_references: Dict[str, Set[int]] = {}
    swapping_robots: Set[str] = set()
    for move in moves:
        move_type = move.get("MoveType")
        for station_key, slot_key in (
            ("SrcStationList", "SrcSlotList"),
            ("DestStationList", "DestSlotList"),
        ):
            if move.get(station_key) is None:
                continue
            station_names = _values(move, station_key)
            slot_ids = _integer_values(move, slot_key)
            for index, station_name in enumerate(station_names):
                if station_name in {None, ""}:
                    continue
                slot_id = slot_ids[index] if index < len(slot_ids) else 1
                station_slot_references.setdefault(str(station_name), set()).add(slot_id)
        if move_type not in transport_move_types:
            continue
        robot_name = str(move.get("Robot") or move.get("ModuleName") or "")
        if not robot_name:
            continue
        robot_slot_ids = {
            *_integer_values(move, "RobotSlotList"),
            *_integer_values(move, "RecvRobotSlotList"),
            *_integer_values(move, "SendRobotSlotList"),
        }
        if not robot_slot_ids:
            robot_slot_ids.add(1)
        robot_slot_references.setdefault(robot_name, set()).update(robot_slot_ids)
        if move_type == SWAP_MOVE:
            swapping_robots.add(robot_name)

    # 拓扑补齐按唯一资源执行，避免每条运输 Move 重复写入同一个槽位。
    for station_name, slot_ids in station_slot_references.items():
        for slot_id in slot_ids:
            station = state.ensure_station(station_name, slot_id)
            if not station.station_type and station.name not in controlled_station_names:
                station.door = DoorState.OPEN
    for robot_name, robot_slot_ids in robot_slot_references.items():
        robot = state.resolve_robot(robot_name)
        if robot is None:
            robot = RobotState(
                name=robot_name,
                hands={slot_id: None for slot_id in sorted(robot_slot_ids)},
                can_swap=robot_name in swapping_robots,
            )
            state.robots[robot_name] = robot
            state.robot_aliases[robot_name] = robot_name
        else:
            for slot_id in robot_slot_ids:
                robot.hands.setdefault(slot_id, None)
            if robot_name in swapping_robots:
                robot.can_swap = True


def _is_omitted_zero_duration_process(
    state: MachineState,
    station_name: str,
    material: Optional[MaterialState],
) -> bool:
    """判断未输出 ProcessMove 的 PM 物料能否按零时长工艺完成。

    优先匹配物料当前 Step 与 PM 的零时长三元组。双腔取放可能未回写 StepID，
    此时仅当该物料在本 PM 的全部 NeedProcess 访问都是零时长才允许补齐，避免
    把同腔重入的非零工序误判为已完成。
    """
    if material is None:
        return False
    material_id = str(material.material_id)
    step_id = str(material.step_id) if material.step_id is not None else ""
    if (material_id, step_id, station_name) in state.zero_duration_process_steps:
        return True
    process_steps = state.process_steps_by_material_station.get(
        (material_id, station_name),
        set(),
    )
    if not process_steps:
        return False
    if step_id in process_steps:
        return (material_id, step_id, station_name) in state.zero_duration_process_steps
    return all(
        (material_id, process_step, station_name) in state.zero_duration_process_steps
        for process_step in process_steps
    )


def _finish_omitted_zero_duration_slot(
    state: MachineState,
    station_name: str,
    slot: SlotState,
) -> None:
    """把已证明的零时长产品工序补齐为完成态，并登记产品进腔。"""
    _set_slot(slot, SlotPhase.COMPLETED, slot.material)
    slot.material_process_count += 1
    if slot.material is not None and slot.material.pjob_name:
        state.product_clean_entries.add(
            (str(slot.material.pjob_name), station_name)
        )


def _apply_omitted_zero_duration_process(
    state: MachineState,
    station: StationState,
) -> None:
    """为当前腔室内可证明的零时长驻片补齐已加工状态。

    单腔和双腔 PM 都允许省略逻辑层的零时长 ProcessMove；双腔必须两槽同时
    证明为零时长后一起补齐。Aligner 即使逻辑 Route 时长为零，也必须保留
    物理解包生成的显式校准动作。
    """
    if not _is_product_process_chamber(station):
        return
    unprocessed = [
        slot
        for slot in station.slots.values()
        if slot.material is not None and slot.phase is SlotPhase.UNPROCESSED
    ]
    if not unprocessed:
        return
    if not all(
        _is_omitted_zero_duration_process(state, station.name, slot.material)
        for slot in unprocessed
    ):
        return
    for slot in unprocessed:
        _finish_omitted_zero_duration_slot(state, station.name, slot)


def _complete_omitted_zero_duration_preprepare(
    station: LoadLockState,
    target_environment: str,
) -> bool:
    """在可证明的零时长转换被省略时同步压力态和待加工晶圆。

    返回 ``True`` 表示已经补齐一次省略的转换。真实 PrePrepare 的完成语义会
    把当前 LoadLock 内的 ``UNPROCESSED`` 晶圆更新为可 Pick；省略时必须保留
    相同效果，否则级联 DBR/UBR 会留下无法取出的晶圆。
    """
    transition = (station.environment, target_environment)
    if transition not in station.zero_duration_environment_transitions:
        return False
    station.environment = target_environment
    station.last_environment_transition_was_empty = not any(
        slot.material is not None for slot in station.slots.values()
    )
    for slot in station.slots.values():
        if slot.material is not None and slot.phase is SlotPhase.UNPROCESSED:
            _set_slot(slot, SlotPhase.COMPLETED, slot.material)
    return True


def _complete_ready_loadlock_outbound_slots(
    station: LoadLockState,
    prepare_move: Mapping[str, Any],
    related_move: Optional[Mapping[str, Any]],
) -> None:
    """在已处于出片压力侧时完成无需 Pump/Vent 的 LoadLock 出片槽位。

    LoadLock 没有独立产品加工。晶圆放入后若该锁已经处于后续出片所需
    压力侧，算法会省略零时长 Process/PrePrepare；本函数只在 Prepare 已完成
    压力态校验且关联动作为 Pick/Swap 时补齐同一状态语义，绝不放宽非
    LoadLock 或压力不匹配的访问。
    """
    related_action = prepare_move.get("RelatedActionType")
    is_pick = related_action == 1 or (
        related_move is not None
        and related_move.get("MoveType") in {PICK_MOVE, MULTI_PICK_MOVE}
    )
    is_swap = related_action == 2 or (
        related_move is not None and related_move.get("MoveType") == SWAP_MOVE
    )
    if not is_pick and not is_swap:
        return
    slot_ids = (
        _integer_values(related_move, "StnSendSlotList")
        if is_swap and related_move is not None
        else _integer_values(prepare_move, "SlotList")
    )
    if is_pick and not slot_ids and related_move is not None:
        slot_ids = _integer_values(related_move, "SrcSlotList")
    for slot_id in slot_ids:
        slot = station.slots.get(slot_id)
        if slot is not None and slot.phase is SlotPhase.UNPROCESSED:
            _set_slot(slot, SlotPhase.COMPLETED, slot.material)


def _start_pick(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验并执行可包含多片晶圆的原子 Pick。"""
    robot = _robot(state, move)
    if isinstance(robot, str):
        return robot
    rows_or_error = _transport_rows(move, "SrcStationList", "SrcSlotList", "RobotSlotList", "MatIDList")
    if isinstance(rows_or_error, str):
        return rows_or_error
    rows = rows_or_error
    error = _validate_distinct_transport_rows(move, rows)
    if error:
        return error
    start_time = _start_time(move)
    if not _available(robot.busy_until, start_time):
        return _issue(move, ValidationErrorCode.ROBOT_BUSY, f"{robot.name} 正在执行其他动作")
    transfers: List[Tuple[StationState, SlotState, int, int, MaterialState]] = []
    for station_name, station_slot_id, robot_slot_id, material_id, index in rows:
        station = state.stations.get(station_name)
        if station is None:
            return _issue(move, ValidationErrorCode.STATION_UNKNOWN, f"未知站点 {station_name}")
        error = _station_access_error(robot, station, start_time, move)
        if error:
            return error
        slot = station.slots.get(station_slot_id)
        if slot is None:
            return _issue(move, ValidationErrorCode.STATION_SLOT_UNKNOWN, f"{station_name} 不存在槽位 {station_slot_id}")
        if robot_slot_id not in robot.hands:
            return _issue(move, ValidationErrorCode.ROBOT_SLOT_DISABLED, f"{robot.name} 未启用手槽 {robot_slot_id}")
        if robot.hands[robot_slot_id] is not None:
            return _issue(move, ValidationErrorCode.ROBOT_HAND_STATE_INVALID, f"{robot.name}#{robot_slot_id} 不是空手")
        if not _available(slot.busy_until, start_time):
            return _issue(move, ValidationErrorCode.STATION_SLOT_BUSY, f"{station_name}#{station_slot_id} 正在{slot.busy_action}")
        _apply_omitted_zero_duration_process(state, station)
        if slot.phase is not SlotPhase.COMPLETED or not _material_matches(slot.material, material_id):
            return _issue(move, ValidationErrorCode.PICK_SOURCE_INVALID, f"{station_name}#{station_slot_id} 没有匹配的已完成物料")
        transfers.append((station, slot, station_slot_id, robot_slot_id, _material_with_metadata(slot.material, move, index)))
    alignment_error = _robot_alignment_issue(robot, move, [(row[0], row[1], row[2]) for row in rows])
    if alignment_error:
        return alignment_error
    robot.busy_until = end_time
    for station in {row[0].name: row[0] for row in transfers}.values():
        station.transfer_busy_until = end_time
    for _, slot, _, _, _ in transfers:
        _reserve_slot(slot, end_time, "取片")

    def complete() -> None:
        """在 Pick 完成时一次性把全部晶圆移入对应手槽，并落地槽位级指向。"""
        for station, slot, station_slot_id, robot_slot_id, material in transfers:
            _set_slot(slot, SlotPhase.EMPTY, None)
            robot.hands[robot_slot_id] = material
            robot.slot_targets[robot_slot_id] = (station.name, station_slot_id)
        robot.position = _robot_derived_position(robot)

    _schedule(scheduled, move, end_time, complete)
    return None


def _start_place(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验并执行可包含多片晶圆的原子 Place。"""
    robot = _robot(state, move)
    if isinstance(robot, str):
        return robot
    rows_or_error = _transport_rows(move, "DestStationList", "DestSlotList", "RobotSlotList", "MatIDList")
    if isinstance(rows_or_error, str):
        return rows_or_error
    rows = rows_or_error
    error = _validate_distinct_transport_rows(move, rows)
    if error:
        return error
    start_time = _start_time(move)
    if not _available(robot.busy_until, start_time):
        return _issue(move, ValidationErrorCode.ROBOT_BUSY, f"{robot.name} 正在执行其他动作")
    transfers: List[Tuple[StationState, SlotState, int, int, MaterialState]] = []
    for station_name, station_slot_id, robot_slot_id, material_id, index in rows:
        station = state.stations.get(station_name)
        if station is None:
            return _issue(move, ValidationErrorCode.STATION_UNKNOWN, f"未知站点 {station_name}")
        error = _station_access_error(robot, station, start_time, move)
        if error:
            return error
        slot = station.slots.get(station_slot_id)
        if slot is None:
            return _issue(move, ValidationErrorCode.STATION_SLOT_UNKNOWN, f"{station_name} 不存在槽位 {station_slot_id}")
        if robot_slot_id not in robot.hands:
            return _issue(move, ValidationErrorCode.ROBOT_SLOT_DISABLED, f"{robot.name} 未启用手槽 {robot_slot_id}")
        material = robot.hands.get(robot_slot_id)
        if material is None or not _material_matches(material, material_id):
            return _issue(move, ValidationErrorCode.ROBOT_HAND_STATE_INVALID, f"{robot.name}#{robot_slot_id} 没有匹配物料")
        if slot.phase not in {SlotPhase.EMPTY, SlotPhase.CLEANED}:
            return _issue(move, ValidationErrorCode.PLACE_TARGET_INVALID, f"{station_name}#{station_slot_id} 不是可放片空槽")
        if not _available(slot.busy_until, start_time):
            return _issue(move, ValidationErrorCode.STATION_SLOT_BUSY, f"{station_name}#{station_slot_id} 正在{slot.busy_action}")
        transfers.append((station, slot, station_slot_id, robot_slot_id, _material_with_metadata(material, move, index)))
    alignment_error = _robot_alignment_issue(robot, move, [(row[0], row[1], row[2]) for row in rows])
    if alignment_error:
        return alignment_error
    robot.busy_until = end_time
    for station in {row[0].name: row[0] for row in transfers}.values():
        station.transfer_busy_until = end_time
    for _, slot, _, _, _ in transfers:
        _reserve_slot(slot, end_time, "放片")

    def complete() -> None:
        """在 Place 完成时一次性把全部晶圆放入目标槽位，并落地槽位级指向。"""
        for station, slot, station_slot_id, robot_slot_id, material in transfers:
            phase = (
                SlotPhase.COMPLETED
                if station.completes_material_on_place
                else SlotPhase.UNPROCESSED
            )
            _set_slot(slot, phase, material)
            robot.hands[robot_slot_id] = None
            robot.slot_targets[robot_slot_id] = (station.name, station_slot_id)
        robot.position = _robot_derived_position(robot)

    _schedule(scheduled, move, end_time, complete)
    return None


def _start_pretrans(state: MachineState, move: Mapping[str, Any], end_time: float, all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验机器人转位；兼容以未来晶圆 ID 标注的 Pick 前空载转位。"""
    robot = _robot(state, move)
    if isinstance(robot, str):
        return robot
    sources = [str(value) for value in _values(move, "SrcStationList") if value]
    destination = _first_text(move, "DestStationList")
    if not destination:
        return _issue(move, ValidationErrorCode.PRETRANS_STATE_INVALID, "转位缺少 DestStationList")
    if not _available(robot.busy_until, _start_time(move)):
        return _issue(move, ValidationErrorCode.ROBOT_BUSY, f"{robot.name} 正在执行其他动作")
    source_error = _pretrans_source_issue(robot, move)
    if source_error:
        return source_error
    for station_name in (*sources, destination):
        if station_name and robot.scope and station_name not in robot.scope:
            return _issue(move, ValidationErrorCode.ROBOT_UNREACHABLE, f"{robot.name} 无法访问 {station_name}")
    robot_slots = _integer_values(move, "RobotSlotList")
    material_ids = _values(move, "MatIDList")
    if material_ids and len(robot_slots) != len(material_ids):
        return _issue(move, ValidationErrorCode.PARALLEL_ARRAY_INVALID, "MatIDList 与 RobotSlotList 数量不一致")
    for index, material_id in enumerate(material_ids):
        material = robot.hands.get(robot_slots[index])
        if material is None and _pretrans_is_linked_empty_pick(
            move,
            all_moves,
            index,
            robot_slots[index],
            material_id,
        ):
            continue
        if not _material_matches(material, material_id):
            return _issue(move, ValidationErrorCode.ROBOT_HAND_STATE_INVALID, f"{robot.name}#{robot_slots[index]} 持有物料与 Move 不匹配")
    robot.busy_until = end_time
    _schedule(scheduled, move, end_time, lambda: _apply_pretrans_landing(robot, move))
    return None


def _pretrans_is_linked_empty_pick(
    pretrans: Mapping[str, Any],
    all_moves: Sequence[Mapping[str, Any]],
    index: int,
    robot_slot_id: int,
    material_id: Any,
) -> bool:
    """判断带物料标注的 PreTrans 是否是同片后继 Pick 的空载前置转位。"""
    pretrans_id = pretrans.get("MoveID")
    robot_name = str(pretrans.get("Robot") or pretrans.get("ModuleName") or "")
    destinations = [str(value) for value in _values(pretrans, "DestStationList")]
    destination_slots = _integer_values(pretrans, "DestSlotList")
    if pretrans_id is None or not robot_name or not destinations:
        return False
    destination = destinations[index] if index < len(destinations) else destinations[0]
    destination_slot = (
        destination_slots[index]
        if index < len(destination_slots)
        else destination_slots[0] if len(destination_slots) == 1 else None
    )
    pretrans_end = _number(pretrans.get("EndTime"))
    if pretrans_end is None:
        return False

    for candidate in all_moves:
        if candidate.get("MoveType") != PICK_MOVE:
            continue
        if str(candidate.get("Robot") or candidate.get("ModuleName") or "") != robot_name:
            continue
        if not any(str(value) == str(pretrans_id) for value in _values(candidate, "PreMoveID")):
            continue
        candidate_start = _number(candidate.get("StartTime"))
        if candidate_start is None or candidate_start + TIME_TOLERANCE < pretrans_end:
            continue
        rows = _transport_rows(
            candidate,
            "SrcStationList",
            "SrcSlotList",
            "RobotSlotList",
            "MatIDList",
        )
        if isinstance(rows, str):
            continue
        for station_name, station_slot_id, candidate_robot_slot, candidate_material_id, _ in rows:
            if (
                station_name == destination
                and candidate_robot_slot == robot_slot_id
                and str(candidate_material_id) == str(material_id)
                and (destination_slot is None or station_slot_id == destination_slot)
            ):
                return True
    return False


def _start_prepare(state: MachineState, move: Mapping[str, Any], end_time: float, all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验开门动作及 LoadLock 当前压力态；重复开门按幂等动作处理。"""
    alignment = _parallel_arrays_alignment_issue(move, ("MatIDList", "StepIDList", "SlotList"))
    if alignment:
        return alignment
    station_name = _station_name(move)
    station = state.stations.get(station_name)
    if station is None:
        return _issue(move, ValidationErrorCode.STATION_UNKNOWN, f"未知站点 {station_name or '<empty>'}")
    start_time = _start_time(move)
    if not _available(station.door_busy_until, start_time) or not _available(station.transfer_busy_until, start_time):
        return _issue(move, ValidationErrorCode.STATION_TRANSFER_BUSY, f"{station.name} 门机构或取放资源正在忙")
    if not _available(station.environment_busy_until, start_time):
        return _issue(move, ValidationErrorCode.STATION_ENVIRONMENT_BUSY, f"{station.name} 正在切换环境")
    if _has_active_process(station, start_time):
        return _issue(move, ValidationErrorCode.STATION_PROCESS_BUSY, f"{station.name} 存在尚未完成的加工或清洁")
    if isinstance(station, LoadLockState):
        related = _related_move(move, all_moves)
        expected = _required_environment(state, station, move, related)
        if expected is not None and station.environment != expected:
            _complete_omitted_zero_duration_preprepare(station, expected)
        if expected is not None and station.environment != expected:
            return _issue(
                move,
                ValidationErrorCode.LOADLOCK_ENVIRONMENT_INVALID,
                f"{station.name}.CurState为{_environment_label(station, station.environment)}，不是期望的{_environment_label(station, expected)}",
            )
        station.last_environment_transition_was_empty = False
        _complete_ready_loadlock_outbound_slots(station, move, related)
    else:
        # PM 0s 产品工艺省略 ProcessMove 后，开门取片前补成完成态。
        _apply_omitted_zero_duration_process(state, station)
    station.door_busy_until = end_time
    _schedule(scheduled, move, end_time, lambda: setattr(station, "door", DoorState.OPEN))
    return None


def _start_complete(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验关门动作并登记完成状态；重复关门按幂等动作处理。"""
    alignment = _parallel_arrays_alignment_issue(move, ("MatIDList", "StepIDList", "SlotList"))
    if alignment:
        return alignment
    station_name = _station_name(move)
    station = state.stations.get(station_name)
    if station is None:
        return _issue(move, ValidationErrorCode.STATION_UNKNOWN, f"未知站点 {station_name or '<empty>'}")
    start_time = _start_time(move)
    if not _available(station.door_busy_until, start_time) or not _available(station.transfer_busy_until, start_time):
        return _issue(move, ValidationErrorCode.STATION_TRANSFER_BUSY, f"{station.name} 门机构或取放资源正在忙")
    station.door_busy_until = end_time

    def complete() -> None:
        """关门完成后，为零时长 PM 驻片补齐加工结束态。"""
        station.door = DoorState.CLOSED
        _apply_omitted_zero_duration_process(state, station)

    _schedule(scheduled, move, end_time, complete)
    return None


def _start_process(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验多槽同步加工或无片清洁，并在结束时更新全部槽位。"""
    station_name = _station_name(move)
    material_ids = _values(move, "MatIDList")
    slot_ids = _integer_values(move, "SlotList")
    if not station_name and not slot_ids:
        # 旧版协议可能只用 ProcessMove 和 MatIDList 表示不带资源明细的时间窗。
        _schedule(scheduled, move, end_time, lambda: None)
        return None
    station = state.stations.get(station_name)
    if station is None:
        return _issue(move, ValidationErrorCode.STATION_UNKNOWN, f"未知站点 {station_name or '<empty>'}")
    start_time = _start_time(move)
    clean_task_name = str(move.get("CleanTaskName") or "").strip()
    station_clean_obligations = [
        ((pjob_name, required_station, task_name), requirement)
        for (pjob_name, required_station, task_name), requirement in state.clean_obligations.items()
        if required_station == station_name
        and (not _values(move, "PJobName") or pjob_name in {str(value) for value in _values(move, "PJobName")})
    ]
    matched_clean_obligations = [
        (clean_key, requirement)
        for clean_key, requirement in station_clean_obligations
        if clean_key[2] == clean_task_name
    ]
    # 调度器为 Dummy WAC 空腔尾段使用通用 ``WacClean`` 任务名。平台不校验
    # 清洗配方，因此依据空腔形态和待完成的 DummyWAC 义务识别尾段。
    dummy_wac_tail_obligations = [
        (clean_key, requirement)
        for clean_key, requirement in station_clean_obligations
        if requirement[0] == "pre"
        and requirement[1] > 0
        and len(requirement[2]) >= 2
        and "wac" in clean_task_name.casefold()
    ]
    clean_material_count = max(
        (requirement[1] for _key, requirement in matched_clean_obligations),
        default=0,
    )
    clean_type = (
        _clean_validation_type(clean_task_name, material_count=clean_material_count)
        if clean_task_name
        else ""
    )
    if not material_ids and dummy_wac_tail_obligations:
        clean_type = "dummywac"
        matched_clean_obligations = dummy_wac_tail_obligations
    dummy_wac_tail_key: Optional[Tuple[str, str, str]] = None
    if clean_type in {"dummy", "dummywac"}:
        if material_ids:
            pending_wac_key = next((
                clean_key
                for clean_key, _requirement in matched_clean_obligations
                if clean_type == "dummywac"
                and state.completed_clean_counts.get(clean_key, 0)
                > state.completed_dummy_wac_counts.get(clean_key, 0)
            ), None)
            if pending_wac_key is not None:
                return _issue(
                    move,
                    ValidationErrorCode.PROCESS_STATE_INVALID,
                    f"{clean_task_name} 上一片 Dummy 离腔后必须先完成空腔 WAC",
                )
        else:
            if clean_type == "dummy":
                return _issue(
                    move,
                    ValidationErrorCode.PROCESS_STATE_INVALID,
                    f"{clean_task_name} 必须先完成足量 Dummy 带片清洁",
                )
            dummy_wac_tail_key = next((
                clean_key
                for clean_key, requirement in matched_clean_obligations
                if clean_type == "dummywac"
                and len(requirement[2]) >= 2
                and state.completed_clean_counts.get(clean_key, 0)
                > state.completed_dummy_wac_counts.get(clean_key, 0)
            ), None)
            if dummy_wac_tail_key is None:
                return _issue(
                    move,
                    ValidationErrorCode.PROCESS_STATE_INVALID,
                    f"{clean_task_name} 空腔 WAC 必须紧跟一片尚未完成尾段的 Dummy 清洁",
                )
    if clean_type in {"preclean", "postclean", "wacclean"} and material_ids:
        return _issue(
            move,
            ValidationErrorCode.PROCESS_STATE_INVALID,
            f"{clean_task_name} 是空腔 Clean，不能携带物料",
        )
    clean_issue = _validate_clean_start(state, station, move, material_ids, clean_task_name)
    if clean_issue:
        return clean_issue
    if station.door is not DoorState.CLOSED:
        return _issue(move, ValidationErrorCode.STATION_DOOR_STATE_INVALID, f"{station.name} 加工或清洁时必须关门")
    if not _available(station.door_busy_until, start_time) or not _available(station.transfer_busy_until, start_time):
        return _issue(move, ValidationErrorCode.STATION_TRANSFER_BUSY, f"{station.name} 正在执行开关门或取放动作")
    if material_ids and len(slot_ids) != len(material_ids):
        return _issue(move, ValidationErrorCode.PARALLEL_ARRAY_INVALID, "MatIDList 与 SlotList 数量不一致")
    if not slot_ids:
        # 兼容只声明腔室占用窗口的旧版 ProcessMove；没有物料和槽位时无法产生
        # 逐槽状态变化，但仍可验证门与共享资源，供跨代计划保留该物理时间窗。
        if material_ids:
            return _issue(move, ValidationErrorCode.PROCESS_STATE_INVALID, "加工动作携带物料时必须提供 SlotList")
        _schedule(scheduled, move, end_time, lambda: None)
        return None
    if len(set(slot_ids)) != len(slot_ids):
        return _issue(move, ValidationErrorCode.DUPLICATE_RESOURCE_REFERENCE, "加工或清洁不能重复引用同一槽位")
    targets: List[Tuple[SlotState, Optional[MaterialState], int]] = []
    for index, slot_id in enumerate(slot_ids):
        slot = station.slots.get(slot_id)
        if slot is None:
            return _issue(move, ValidationErrorCode.STATION_SLOT_UNKNOWN, f"{station.name} 不存在槽位 {slot_id}")
        if not _available(slot.busy_until, start_time):
            return _issue(move, ValidationErrorCode.STATION_SLOT_BUSY, f"{station.name}#{slot_id} 正在{slot.busy_action}")
        material_id = material_ids[index] if material_ids else None
        if material_id is None:
            if slot.phase not in {SlotPhase.EMPTY, SlotPhase.CLEANED}:
                return _issue(move, ValidationErrorCode.PROCESS_STATE_INVALID, f"{station.name}#{slot_id} 有物料，不能执行无片清洁")
            material = None
        else:
            if slot.phase is not SlotPhase.UNPROCESSED or not _material_matches(slot.material, material_id):
                return _issue(move, ValidationErrorCode.PROCESS_STATE_INVALID, f"{station.name}#{slot_id} 没有待加工的匹配物料")
            material = slot.material
        targets.append((slot, material, slot_id))
    for slot, material, _ in targets:
        _reserve_slot(slot, end_time, "清洁" if material is None else "加工")

    def complete() -> None:
        """同时完成槽位，并按 ProcessRecipe.Weight 累计站点状态变量。"""
        for slot, material, _ in targets:
            _set_slot(slot, SlotPhase.CLEANED if material is None else SlotPhase.COMPLETED, material)
            if material is not None:
                slot.material_process_count += 1
                pjob_name = str(material.pjob_name or "").strip()
                if pjob_name:
                    state.product_clean_entries.add((pjob_name, station.name))
        recipe_name = str(
            move.get("ProcessRecipe")
            or move.get("RecipeName")
            or move.get("CleanRecipe")
            or ""
        )
        product_pjob_names = {
            str(material.pjob_name or "").strip()
            for _slot, material, _slot_id in targets
            if material is not None and str(material.pjob_name or "").strip()
        }
        if not product_pjob_names:
            product_pjob_names = {
                str(pjob_name).strip()
                for pjob_name in _values(move, "PJobName")
                if str(pjob_name).strip()
            }
        for variable_name, increment in state.process_recipe_weights.get(
            (station.name, recipe_name),
            {},
        ).items():
            if (
                state.uses_pjob_scoped_wac(station)
                and state.is_wac_counter_variable(station.name, variable_name)
                and product_pjob_names
            ):
                for pjob_name in sorted(product_pjob_names):
                    state.add_wac_counter(
                        station,
                        pjob_name,
                        variable_name,
                        increment,
                    )
            else:
                state.add_wac_counter(station, "", variable_name, increment)
        # Process 完成后立即把“哪一个 Route Visit 触发了 WAC”固化到状态中。
        # 后续无片 Clean Move 没有产品 StepID，不能再从整条 Route 取首个同名任务。
        process_steps_by_pjob: Dict[str, Set[str]] = {}
        for _slot, material, _slot_id in targets:
            if material is None or not str(material.pjob_name or "").strip():
                continue
            process_steps_by_pjob.setdefault(
                str(material.pjob_name).strip(), set()
            ).add(str(material.step_id))
        for (
            rule_pjob_name,
            variable_name,
            lower,
            rule_task_name,
            source_step_id,
        ) in state.clean_wac_trigger_rules.get((station.name, recipe_name), ()):
            matching_pjobs = (
                {rule_pjob_name}
                if rule_pjob_name
                else set(process_steps_by_pjob)
            )
            for matching_pjob in matching_pjobs:
                if matching_pjob not in process_steps_by_pjob:
                    continue
                if (
                    source_step_id is not None
                    and source_step_id not in process_steps_by_pjob[matching_pjob]
                ):
                    continue
                value = state.wac_counter_value(
                    station, matching_pjob, variable_name
                )
                if value + TIME_TOLERANCE >= lower:
                    state.pending_wac_obligations.add((
                        station.name,
                        matching_pjob,
                        variable_name,
                        rule_task_name,
                    ))
        clean_task_name = str(move.get("CleanTaskName") or "")
        if clean_task_name:
            for pjob_name in _values(move, "PJobName"):
                clean_key = (str(pjob_name).strip(), station.name, clean_task_name)
                obligation = state.clean_obligations.get(clean_key)
                if not clean_key[0] or obligation is None:
                    continue
                _phase, required_count, _recipes = obligation
                increment = (
                    len(material_ids)
                    if required_count > 0
                    else int(move.get("IsLastCleanTaskMove") is True)
                )
                if increment:
                    state.completed_clean_counts[clean_key] = (
                        state.completed_clean_counts.get(clean_key, 0) + increment
                    )
        if dummy_wac_tail_key is not None:
            state.completed_dummy_wac_counts[dummy_wac_tail_key] = (
                state.completed_dummy_wac_counts.get(dummy_wac_tail_key, 0) + 1
            )
        if clean_task_name and move.get("IsLastCleanTaskMove") is True:
            selected_pjobs = {
                str(name).strip()
                for name in _values(move, "PJobName")
                if str(name).strip()
            }
            pjob_scoped_wac = state.uses_pjob_scoped_wac(station)
            for pending_key in list(state.pending_wac_obligations):
                if (
                    pending_key[0] == station.name
                    and pending_key[3] == clean_task_name
                    # 单腔 WAC 周期按 PJob 隔离；双腔/多槽 PM 使用物理腔室
                    # 全局周期，一次合法清洁必须清除同任务的全部待办，不能因
                    # Clean Move 携带了另一个 PJobName 留下幽灵义务。
                    and (
                        not pjob_scoped_wac
                        or not selected_pjobs
                        or pending_key[1] in selected_pjobs
                    )
                ):
                    state.pending_wac_obligations.discard(pending_key)
            for variable_name in state.clean_task_state_variables.get(
                clean_task_name,
                set(),
            ):
                state.reset_wac_counter(
                    station,
                    [str(name).strip() for name in _values(move, "PJobName")],
                    variable_name,
                )

    _schedule(scheduled, move, end_time, complete)
    return None


def _start_align(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验合法 AlignMove，并在完成时把待对准物料标记为可取。

    Align 是站点服务动作：物料在动作前后仍位于同一 Route Step，平台只检查
    站点、槽位、物料和占用窗口，不修改位置或 StepID；对准结束会把 Place
    产生的 ``UNPROCESSED`` 槽位推进为 ``COMPLETED``，供后续 Pick 校验。
    """
    alignment = _parallel_arrays_alignment_issue(
        move,
        ("MatIDList", "StepIDList", "SlotList"),
    )
    if alignment:
        return alignment
    station_name = _station_name(move)
    station = state.stations.get(station_name)
    if station is None:
        return _issue(
            move,
            ValidationErrorCode.STATION_UNKNOWN,
            f"未知站点 {station_name or '<empty>'}",
        )
    start_time = _start_time(move)
    if station.door is not DoorState.CLOSED:
        return _issue(
            move,
            ValidationErrorCode.STATION_DOOR_STATE_INVALID,
            f"{station.name} 对准时必须关门",
        )
    if not _available(station.door_busy_until, start_time) or not _available(
        station.transfer_busy_until,
        start_time,
    ):
        return _issue(
            move,
            ValidationErrorCode.STATION_TRANSFER_BUSY,
            f"{station.name} 正在执行开关门或取放动作",
        )
    if _has_active_process(station, start_time):
        return _issue(
            move,
            ValidationErrorCode.STATION_PROCESS_BUSY,
            f"{station.name} 正在执行其他站点服务",
        )

    material_ids = _values(move, "MatIDList")
    slot_ids = _integer_values(move, "SlotList")
    if material_ids and not slot_ids:
        slot_ids = [
            slot_id
            for material_id in material_ids
            for slot_id, slot in station.slots.items()
            if _material_matches(slot.material, material_id)
        ]
    if material_ids and len(slot_ids) != len(material_ids):
        return _issue(
            move,
            ValidationErrorCode.PARALLEL_ARRAY_INVALID,
            "AlignMove 的 MatIDList 与 SlotList 数量不一致",
        )
    if not material_ids and not slot_ids:
        # 无片对准仍占用整台 Aligner；按全部物理槽位登记服务窗口。
        slot_ids = sorted(station.slots)

    targets: List[SlotState] = []
    for index, slot_id in enumerate(slot_ids):
        slot = station.slots.get(slot_id)
        if slot is None:
            return _issue(
                move,
                ValidationErrorCode.STATION_SLOT_UNKNOWN,
                f"{station.name} 不存在槽位 {slot_id}",
            )
        if not _available(slot.busy_until, start_time):
            return _issue(
                move,
                ValidationErrorCode.STATION_SLOT_BUSY,
                f"{station.name}#{slot_id} 正在{slot.busy_action}",
            )
        if material_ids and not _material_matches(slot.material, material_ids[index]):
            return _issue(
                move,
                ValidationErrorCode.PROCESS_STATE_INVALID,
                f"{station.name}#{slot_id} 没有待对准的匹配物料",
            )
        targets.append(slot)
    for slot in targets:
        _reserve_slot(slot, end_time, "对准")

    def complete() -> None:
        """结束对准占用并完成待对准物料，但保持物料 Route Step。"""
        for slot in targets:
            if slot.material is not None and slot.phase is SlotPhase.UNPROCESSED:
                _set_slot(slot, SlotPhase.COMPLETED, slot.material)
            else:
                slot.busy_action = ""

    _schedule(scheduled, move, end_time, complete)
    return None


def _start_preprepare(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验 LoadLock 压力切换；满载到声明容量也属于物理合法状态。"""
    station_name = _station_name(move)
    station = state.stations.get(station_name)
    if not isinstance(station, LoadLockState):
        return _issue(move, ValidationErrorCode.LOADLOCK_REQUIRED, f"{station_name or '<empty>'} 不是 LoadLock，不能切换环境")
    start_time = _start_time(move)
    if station.door is not DoorState.CLOSED:
        return _issue(move, ValidationErrorCode.STATION_DOOR_STATE_INVALID, f"{station.name} 切换环境时必须关门")
    if not _available(station.door_busy_until, start_time) or not _available(station.transfer_busy_until, start_time):
        return _issue(move, ValidationErrorCode.STATION_TRANSFER_BUSY, f"{station.name} 正在开关门或取放物料")
    if not _available(station.environment_busy_until, start_time):
        return _issue(move, ValidationErrorCode.STATION_ENVIRONMENT_BUSY, f"{station.name} 正在切换环境")
    last_state = _environment_state(station, move.get("LastState"))
    current_state = _environment_state(station, move.get("CurState"))
    if (
        last_state in {ATMOSPHERE, VACUUM}
        and station.environment != last_state
    ):
        _complete_omitted_zero_duration_preprepare(station, last_state)
    raw_last = str(move.get("LastState") or "").strip().upper()
    raw_current = str(move.get("CurState") or "").strip().upper()
    # 严格状态空间：配置了 PrePrepareTime 时，LastState/CurState 原始标签必须在该 LoadLock 声明内。
    state_space_violation = bool(
        station.environment_state_space
        and (
            raw_last not in station.environment_state_space
            or raw_current not in station.environment_state_space
        )
    )
    violation: Optional[str] = None
    if state_space_violation:
        violation = _issue(
            move,
            ValidationErrorCode.LOADLOCK_ENVIRONMENT_INVALID,
            f"{station.name} 的 LastState/CurState 不在其 PrePrepareTime 状态空间内：({raw_last}, {raw_current})",
        )
    elif last_state not in {ATMOSPHERE, VACUUM} or current_state not in {ATMOSPHERE, VACUUM}:
        violation = _issue(move, ValidationErrorCode.LOADLOCK_ENVIRONMENT_INVALID, "LastState 和 CurState 必须是有效压力态")
    elif station.environment != last_state:
        violation = _issue(
            move,
            ValidationErrorCode.LOADLOCK_ENVIRONMENT_INVALID,
            f"{station.name}.CurState为{_environment_label(station, station.environment)}，不是动作声明的{_environment_label(station, last_state)}",
        )
    if violation is not None:
        if not station.environment_exemption_used:
            # 豁免：每个 LoadLock 仅放行首条环境不匹配的切换——无论 LastState/CurState
            # 越出 PrePrepareTime 状态空间，还是 LastState 与 LoadLock 当前压力态不符——
            # 但照常执行该 move，把 LoadLock 状态更新为对应的 CurState；后续违例照常报错。
            station.environment_exemption_used = True
            # CurState 必须可解析为标准压力态才能安全落地环境；陌生标签不豁免。
            if current_state in {ATMOSPHERE, VACUUM}:
                violation = None
        if violation is not None:
            return violation
    material_ids = _values(move, "MatIDList")
    slot_ids = _integer_values(move, "SlotList")
    if material_ids and slot_ids and len(material_ids) != len(slot_ids):
        return _issue(move, ValidationErrorCode.PARALLEL_ARRAY_INVALID, "MatIDList 与 SlotList 数量不一致")
    if material_ids and not slot_ids:
        inferred_slots = []
        for material_id in material_ids:
            inferred = next((slot_id for slot_id, slot in station.slots.items() if _material_matches(slot.material, material_id)), None)
            if inferred is None:
                return _issue(move, ValidationErrorCode.LOADLOCK_CONTENT_INVALID, f"{station.name} 中找不到物料 {material_id}")
            inferred_slots.append(inferred)
        slot_ids = inferred_slots
    targets = [station.slots[slot_id] for slot_id in slot_ids if slot_id in station.slots]
    if len(targets) != len(slot_ids):
        return _issue(move, ValidationErrorCode.LOADLOCK_CONTENT_INVALID, f"{station.name} 的 SlotList 包含无效槽位")
    for index, slot in enumerate(targets):
        material_id = material_ids[index] if material_ids else None
        if material_id is not None and not _material_matches(slot.material, material_id):
            return _issue(move, ValidationErrorCode.LOADLOCK_CONTENT_INVALID, f"{station.name}#{slot_ids[index]} 没有匹配物料")
        if not _available(slot.busy_until, start_time):
            return _issue(move, ValidationErrorCode.STATION_SLOT_BUSY, f"{station.name}#{slot_ids[index]} 正在{slot.busy_action}")
        _reserve_slot(slot, end_time, "抽充气")
    station.environment_busy_until = end_time

    def complete() -> None:
        """压力切换作用于整个 LoadLock，并完成其中所有待转换晶圆。"""
        station.environment = current_state
        station.last_environment_transition_was_empty = not any(slot.material for slot in station.slots.values())
        for slot in station.slots.values():
            if slot.material is not None and slot.phase is SlotPhase.UNPROCESSED:
                _set_slot(slot, SlotPhase.COMPLETED, slot.material)

    _schedule(scheduled, move, end_time, complete)
    return None


def _start_swap(state: MachineState, move: Mapping[str, Any], end_time: float, _all_moves: Sequence[Mapping[str, Any]], scheduled: List[_ScheduledCompletion]) -> Optional[str]:
    """校验同站或孪生 LoadLock 原子 Swap，并按站点逐组落地物料。"""
    robot = _robot(state, move)
    if isinstance(robot, str):
        return robot
    stations = [str(value) for value in _values(move, "StationList")]
    if not stations:
        return _issue(move, ValidationErrorCode.SWAP_INPUT_INVALID, "SwapMove 缺少 StationList")
    distinct_station_names = set(stations)
    is_twin_load_lock_swap = (
        len(distinct_station_names) == 2
        and frozenset(name.upper() for name in distinct_station_names)
        in TWIN_LOAD_LOCK_PAIRS
    )
    if len(distinct_station_names) != 1 and not is_twin_load_lock_swap:
        return _issue(
            move,
            ValidationErrorCode.SWAP_INPUT_INVALID,
            "SwapMove 必须引用同一个站点或一组孪生 LoadLock（LA/LB、LC/LD）",
        )
    receive_materials = _values(move, "RecvMatList")
    send_materials = _values(move, "SendMatList")
    station_send_slots = _integer_values(move, "StnSendSlotList")
    station_receive_slots = _integer_values(move, "StnRecvSlotList")
    robot_receive_slots = _integer_values(move, "RecvSlotList")
    robot_send_slots = _integer_values(move, "SendSlotList")
    send_count = len(send_materials)
    recv_count = len(receive_materials)
    if not send_count and not recv_count:
        return _issue(move, ValidationErrorCode.SWAP_INPUT_INVALID, "SwapMove 必须声明至少一个 Send 或 Recv 晶圆")
    # Send 组：机器人送出晶圆进入腔室，站侧使用 StnRecvSlotList（进入槽位）。
    if len(robot_send_slots) != send_count or len(station_receive_slots) != send_count:
        lengths = (send_count, len(robot_send_slots), len(station_receive_slots))
        return _issue(move, ValidationErrorCode.SWAP_INPUT_INVALID, f"SwapMove 的 Send 组数组数量不一致：SendMatList={lengths[0]} SendSlotList={lengths[1]} StnRecvSlotList={lengths[2]}")
    # Recv 组：机器人拿回晶圆离开腔室，站侧使用 StnSendSlotList（离开槽位）。
    if len(robot_receive_slots) != recv_count or len(station_send_slots) != recv_count:
        lengths = (recv_count, len(robot_receive_slots), len(station_send_slots))
        return _issue(move, ValidationErrorCode.SWAP_INPUT_INVALID, f"SwapMove 的 Recv 组数组数量不一致：RecvMatList={lengths[0]} RecvSlotList={lengths[1]} StnSendSlotList={lengths[2]}")
    if is_twin_load_lock_swap:
        # 孪生 Swap 允许 Send/Recv 组不对称（如 LA 换入换出、LB 仅换入），
        # 组内第 i 项按下标对齐 StationList 的第 i 个站点，因此任一组数量
        # 都不能超过站点数。
        for group_name, group_count in (("Send", send_count), ("Recv", recv_count)):
            if group_count and group_count > len(stations):
                return _issue(
                    move,
                    ValidationErrorCode.SWAP_INPUT_INVALID,
                    f"孪生 LoadLock Swap 的 {group_name} 组数量不能超过 StationList",
                )
        for field_name, slot_ids in (
            ("StnRecvSlotList", station_receive_slots),
            ("StnSendSlotList", station_send_slots),
        ):
            if slot_ids and len(set(slot_ids)) != 1:
                return _issue(
                    move,
                    ValidationErrorCode.SWAP_INPUT_INVALID,
                    f"孪生 LoadLock Swap 的 {field_name} 必须使用同一层槽位",
                )
    start_time = _start_time(move)
    physical_stations: List[StationState] = []
    for station_name in stations:
        station = state.stations.get(station_name)
        if station is None:
            return _issue(move, ValidationErrorCode.STATION_UNKNOWN, f"未知站点 {station_name}")
        if is_twin_load_lock_swap and not isinstance(station, LoadLockState):
            return _issue(
                move,
                ValidationErrorCode.SWAP_INPUT_INVALID,
                f"孪生站点 {station_name} 不是 LoadLock",
            )
        error = _station_access_error(robot, station, start_time, move)
        if error:
            return error
        physical_stations.append(station)
    if not _available(robot.busy_until, start_time):
        return _issue(move, ValidationErrorCode.ROBOT_BUSY, f"{robot.name} 正在执行其他动作")
    send_stations = (
        physical_stations
        if is_twin_load_lock_swap
        else [physical_stations[0]] * send_count
    )
    recv_stations = (
        physical_stations
        if is_twin_load_lock_swap
        else [physical_stations[0]] * recv_count
    )
    send_rows = [
        (send_stations[i], send_materials[i], robot_send_slots[i], station_receive_slots[i], i)
        for i in range(send_count)
    ]
    recv_rows = [
        (recv_stations[j], receive_materials[j], robot_receive_slots[j], station_send_slots[j], j)
        for j in range(recv_count)
    ]
    send_robot_slots = {row[2] for row in send_rows}
    recv_robot_slots = {row[2] for row in recv_rows}
    if len(send_robot_slots) != send_count or len(recv_robot_slots) != recv_count:
        return _issue(move, ValidationErrorCode.SWAP_INPUT_INVALID, "SwapMove 的 Send/Recv 手槽不能重复")
    shared_robot_slots = send_robot_slots & recv_robot_slots
    swap_mode = int(move.get("SwapMode") or 0)
    place_first_shared_slots = {
        robot_slot_id
        for robot_slot_id in shared_robot_slots
        if swap_mode == 1
        and any(
            send_station.name == recv_station.name
            and len(send_station.slots) > 1
            and send_station_slot != recv_station_slot
            for send_station, _, send_slot, send_station_slot, _ in send_rows
            for recv_station, _, recv_slot, recv_station_slot, _ in recv_rows
            if send_slot == recv_slot == robot_slot_id
        )
    }
    if shared_robot_slots - place_first_shared_slots:
        return _issue(
            move,
            ValidationErrorCode.SWAP_INPUT_INVALID,
            "SwapMove 仅允许多槽目标腔室的 place-first 动作共用 Send/Recv 手槽",
        )
    if not place_first_shared_slots and (not robot.can_swap or len(robot.hands) < 2):
        return _issue(move, ValidationErrorCode.ROBOT_SWAP_UNSUPPORTED, f"{robot.name} 不支持双臂换片")
    for slot_id in send_robot_slots | recv_robot_slots:
        if slot_id not in robot.hands:
            return _issue(move, ValidationErrorCode.ROBOT_SLOT_DISABLED, f"{robot.name} 未启用手槽 {slot_id}")
    send_station_slots = {(row[0].name, row[3]) for row in send_rows}
    recv_station_slots = {(row[0].name, row[3]) for row in recv_rows}
    if len(send_station_slots) != send_count or len(recv_station_slots) != recv_count:
        return _issue(move, ValidationErrorCode.SWAP_INPUT_INVALID, "SwapMove 的站槽位不能重复使用")
    # Recv 组校验：站槽位有匹配的已完成物料、目标手槽为空。
    for station, material_id, robot_slot_id, station_slot_id, _ in recv_rows:
        _apply_omitted_zero_duration_process(state, station)
        slot = station.slots.get(station_slot_id)
        if slot is None:
            return _issue(move, ValidationErrorCode.STATION_SLOT_UNKNOWN, f"{station.name} 不存在槽位 {station_slot_id}")
        if slot.phase is not SlotPhase.COMPLETED or not _material_matches(slot.material, material_id):
            return _issue(move, ValidationErrorCode.SWAP_STATE_INVALID, f"{station.name}#{station_slot_id} 没有可换出的物料")
        if robot_slot_id not in place_first_shared_slots and robot.hands.get(robot_slot_id) is not None:
            return _issue(move, ValidationErrorCode.SWAP_STATE_INVALID, f"{robot.name}#{robot_slot_id} 不是空手")
        if not _available(slot.busy_until, start_time):
            return _issue(move, ValidationErrorCode.STATION_SLOT_BUSY, f"{station.name}#{station_slot_id} 正在{slot.busy_action}")
    # Send 组校验：手上有匹配物料；目标槽位可放（换片槽位由 Recv 组腾空，跳过空槽检查）。
    for station, material_id, robot_slot_id, station_slot_id, _ in send_rows:
        slot = station.slots.get(station_slot_id)
        if slot is None:
            return _issue(move, ValidationErrorCode.STATION_SLOT_UNKNOWN, f"{station.name} 不存在槽位 {station_slot_id}")
        material = robot.hands.get(robot_slot_id)
        if material is None or not _material_matches(material, material_id):
            return _issue(move, ValidationErrorCode.SWAP_STATE_INVALID, f"{robot.name}#{robot_slot_id} 没有可换入的物料")
        if (station.name, station_slot_id) not in recv_station_slots and slot.phase not in {SlotPhase.EMPTY, SlotPhase.CLEANED}:
            return _issue(move, ValidationErrorCode.SWAP_STATE_INVALID, f"{station.name}#{station_slot_id} 不是可直接放片的空槽")
        if not _available(slot.busy_until, start_time):
            return _issue(move, ValidationErrorCode.STATION_SLOT_BUSY, f"{station.name}#{station_slot_id} 正在{slot.busy_action}")
    station_refs = [
        (row[0].name, row[3], row[2]) for row in send_rows
    ] + [
        (row[0].name, row[3], row[2]) for row in recv_rows
    ]
    alignment_error = _robot_alignment_issue(robot, move, station_refs)
    if alignment_error:
        return alignment_error
    robot.busy_until = end_time
    for station_item in {value.name: value for value in physical_stations}.values():
        station_item.transfer_busy_until = end_time
    for station, _, _, station_slot_id, _ in recv_rows:
        _reserve_slot(station.slots[station_slot_id], end_time, "换片")
    for station, _, _, station_slot_id, _ in send_rows:
        _reserve_slot(station.slots[station_slot_id], end_time, "换片")

    received_materials = [
        station.slots[station_slot_id].material
        for station, _, _, station_slot_id, _ in recv_rows
    ]
    sent_materials = [
        robot.hands[robot_slot_id]
        for _, _, robot_slot_id, _, _ in send_rows
    ]

    def complete() -> None:
        """同时落地 Swap 中所有进出晶圆，并落地槽位级指向。

        开始时已分别保存进出物料，因此统一按 Send 后 Recv 落地；这既兼容
        普通双臂换片，也支持 place-first 共用手槽。纯 Recv 槽位最后清空。
        """
        for row_index, (station, _, robot_slot_id, station_slot_id, index) in enumerate(send_rows):
            _set_slot(station.slots[station_slot_id], SlotPhase.UNPROCESSED, _material_with_metadata(sent_materials[row_index], move, index, "SendMatStepIDList"))
            robot.hands[robot_slot_id] = None
            robot.slot_targets[robot_slot_id] = (station.name, station_slot_id)
        for row_index, (station, _, robot_slot_id, station_slot_id, index) in enumerate(recv_rows):
            robot.hands[robot_slot_id] = _material_with_metadata(received_materials[row_index], move, index, "RecvMatStepIDList")
            robot.slot_targets[robot_slot_id] = (station.name, station_slot_id)
        for station, _, _, station_slot_id, _ in recv_rows:
            if (station.name, station_slot_id) not in send_station_slots:
                _set_slot(station.slots[station_slot_id], SlotPhase.EMPTY, None)
        robot.position = _robot_derived_position(robot)

    _schedule(scheduled, move, end_time, complete)
    return None
