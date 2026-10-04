"""平台 Move 时间线回放、依赖与时间约束校验；使用独立状态和动作模块。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple
from collections.abc import Mapping, Sequence
from app.backend.payload_snapshot import copy_payload

from .move_state import (
    CLEAN_VALIDATION_TYPES,
    MOVE_STATE_RUNNING,
    MOVE_STATE_DONE,
    MOVE_STATE_ABORTED,
    MULTI_PICK_MOVE,
    MachineState,
    PICK_MOVE,
    PLACE_MOVE,
    PRE_TRANS_MOVE,
    PROCESS_MOVE,
    SlotPhase,
    TIME_TOLERANCE,
    ValidationErrorCode,
    _ScheduledCompletion,
)
from .move_validation_helpers import (
    _IndexedMoves,
    _final_clean_obligation_issue,
    _finish_until,
    _first_text,
    _global_issue,
    _initial_payload,
    _integer_values,
    _issue,
    _mapping,
    _notification_state,
    _number,
    _set_slot,
    _sort_key,
    _station_name,
    _values,
)
from .move_actions import (
    _pretrans_is_linked_empty_pick,
    _start_move,
    _supplement_state_from_moves,
)


@dataclass
class MovePlanValidation:
    """一代完整校验的结论、补齐后的初态与终态；不跨计划代次共享。"""

    issues: List[str]
    moves: Optional[_IndexedMoves] = None
    initial_state: Optional[MachineState] = None
    completed_state: Optional[MachineState] = None


class MoveStateReplay:
    """维护外部 Move 开始/结束通知对应的平台物理状态。"""

    RUNNING = MOVE_STATE_RUNNING
    DONE = MOVE_STATE_DONE
    ABORTED = MOVE_STATE_ABORTED

    def __init__(
        self,
        task: Any,
        moves: Sequence[Mapping[str, Any]],
        init_data: "Optional[Mapping[str, Any] | MachineState]" = None,
    ) -> None:
        """用计划和初始快照创建实时状态记录器。"""
        self.task = task
        self.moves = _IndexedMoves(dict(move) for move in sorted(moves, key=_sort_key))
        self.state = MachineState.from_sources(task, init_data)
        _supplement_state_from_moves(self.state, self.moves)
        self.current_time = 0.0
        self._moves_by_id = {
            int(move["MoveID"]): move
            for move in self.moves
            if isinstance(move.get("MoveID"), int)
        }
        self._scheduled: List[_ScheduledCompletion] = []
        self._running: Dict[int, Dict[str, Any]] = {}
        self._executed: Dict[int, Dict[str, Any]] = {}
        self._validated_completed_state: Optional[MachineState] = None

    @classmethod
    def from_validation(cls, validation: MovePlanValidation, *, take_ownership: bool = False) -> "MoveStateReplay":
        """从成功校验建立记录器，复用本代索引和终态，避免重复扫描动作。

        参数 validation 必须包含成功的完整校验及初终态。记录器复制可变状态与
        Move 数据，调用方后续修改校验结果不会影响正在运行的记录器。
        take_ownership 仅供已经隔离 Move 输入的运行时使用：转移本次校验的
        索引和状态，并清空原结论中的可变数据，避免同一代再次复制。
        """
        if validation.issues or validation.initial_state is None or validation.completed_state is None:
            raise ValueError("只有完整校验通过的计划才能建立回放记录器")
        replay = cls.__new__(cls)
        replay.task = None
        replay.moves = validation.moves if take_ownership else _IndexedMoves(copy_payload(list(validation.moves or [])))
        replay.state = validation.initial_state if take_ownership else validation.initial_state.clone()
        replay.current_time = 0.0
        replay._moves_by_id = {int(move["MoveID"]): move for move in replay.moves}
        replay._scheduled = []
        replay._running = {}
        replay._executed = {}
        replay._validated_completed_state = validation.completed_state if take_ownership else validation.completed_state.clone()
        replay._last_start = max((float(move["StartTime"]) for move in replay.moves), default=-math.inf)
        replay._last_end = max((float(move["EndTime"]) for move in replay.moves), default=-math.inf)
        if take_ownership:
            validation.moves = None
            validation.initial_state = None
            validation.completed_state = None
        return replay

    def finish_validated_plan(self, cutoff: float) -> bool:
        """到达完整计划结束后应用已校验终态；中途切点仍逐事件物理推进。

        所有动作必须在 cutoff 前严格启动、且已结束。人工通知改变计划时间或
        转位源站后，终态缓存失效，返回 False，调用方继续普通回放。
        """
        if self._validated_completed_state is None:
            return False
        if self._last_start >= cutoff - TIME_TOLERANCE or self._last_end > cutoff + TIME_TOLERANCE:
            return False
        self.state = self._validated_completed_state
        self._validated_completed_state = None
        self._scheduled.clear()
        self._running.clear()
        self._executed = {int(move["MoveID"]): dict(move) for move in self.moves}
        self.current_time = max(self.current_time, cutoff)
        return True

    @property
    def running_move_ids(self) -> frozenset[int]:
        """返回已经开始但尚未完成的 MoveID。"""
        return frozenset(self._running)

    @property
    def executed_moves(self) -> List[dict]:
        """按实际时间返回已完成动作副本。"""
        return [dict(move) for move in sorted(self._executed.values(), key=_sort_key)]

    @property
    def materialized_plan(self) -> List[dict]:
        """用实际执行记录覆盖原计划并返回当前代次。"""
        return [
            dict(self._executed.get(int(move.get("MoveID", -1)), move))
            for move in self.moves
        ]

    def update_move_state(
        self,
        notification: Mapping[str, Any],
        *,
        snapshot: bool = True,
        track_reservations: bool = True,
    ) -> Optional[MachineState]:
        """应用一条 Running/Done 通知；返回值按需复制当前快照。"""
        del track_reservations  # 平台状态按实际完成回调落地，不依赖算法资源回滚。
        move_id = notification.get("MoveID")
        if not isinstance(move_id, int) or move_id not in self._moves_by_id:
            raise ValueError(f"未知 MoveID={move_id}")
        move_state = _notification_state(notification)
        if move_state == self.RUNNING:
            if move_id in self._running or move_id in self._executed:
                raise ValueError(f"MoveID={move_id} 收到重复开始通知")
            planned = self._moves_by_id[move_id]
            move = dict(planned)
            planned_start = _number(planned.get("StartTime")) or 0.0
            planned_end = _number(planned.get("EndTime"))
            actual_start = _number(notification.get("StartTime"))
            actual_start = planned_start if actual_start is None else actual_start
            if actual_start != planned_start or (
                isinstance(notification.get("SrcStationList"), list)
                and notification["SrcStationList"] != planned.get("SrcStationList")
            ):
                self._validated_completed_state = None
            if isinstance(notification.get("SrcStationList"), list):
                move["SrcStationList"] = list(notification["SrcStationList"])
            move["StartTime"] = actual_start
            move["EndTime"] = actual_start + max(0.0, (planned_end or planned_start) - planned_start)
            error = _start_move(self.state, move, float(move["EndTime"]), self.moves, self._scheduled)
            if error:
                raise ValueError(error)
            self._running[move_id] = move
            self.current_time = max(self.current_time, actual_start)
        elif move_state == self.DONE:
            move = self._running.get(move_id)
            if move is None:
                raise ValueError(f"MoveID={move_id} 尚未开始，不能结束")
            actual_end = _number(notification.get("EndTime"))
            actual_end = float(move["EndTime"]) if actual_end is None else actual_end
            if actual_end != self._moves_by_id[move_id].get("EndTime"):
                self._validated_completed_state = None
            if actual_end + TIME_TOLERANCE < float(move["StartTime"]):
                raise ValueError(f"MoveID={move_id} 的 EndTime 早于 StartTime")
            completion = next((item for item in self._scheduled if item.move_id == move_id), None)
            if completion is None:
                raise ValueError(f"MoveID={move_id} 缺少待落地状态")
            completion.complete()
            self._scheduled.remove(completion)
            move["EndTime"] = actual_end
            self._executed[move_id] = dict(move)
            del self._running[move_id]
            self.current_time = max(self.current_time, actual_end)
        else:
            raise ValueError(f"MoveID={move_id} 已中止；请先完成设备恢复，再从稳定状态重算")
        return self.state.clone() if snapshot else None


def materialize_module_parallel_moves(
    moves: Sequence[Mapping[str, Any]],
    clock_floor: float = 0.0,
    duration_resolver: Optional[Callable[[Mapping[str, Any]], float]] = None,
) -> List[dict]:
    """按 HongYe ``module-parallel`` 规则计算 Move 的实际时间。

    每个 ``ModuleName`` 是一条独立串行时间线；不同模块并行推进。同一模块的
    Move 按计划开始时刻和 MoveID 排序，实际开始时刻不得早于模块上一条 Move
    的结束时刻。当前 Move 引用的本代 ``PreMoveID`` 也必须全部结束，并把最晚
    前驱结束时刻作为开始下界。跨代前驱不在本 MoveList 中，其完成事实已经包含
    在本代初始快照里，因此不会阻塞。已有本代前驱或模块前项的 Move 按实际完成
    时刻推进，保留原计划在前驱之后的等待间隔；独立首项保留起点，现场时刻始终是下界。

    参数:
        moves: 当前代算法输出的 MoveList。
        clock_floor: 当前代现场时刻，所有 Move 的实际开始时间不得早于该值。
        duration_resolver: 可选的设备实际时长解析器；缺失时使用算法理论时长。

    返回:
        深拷贝后的 MoveList，其中 StartTime/EndTime 已替换为实际执行时间。
    """
    floor = _number(clock_floor)
    normalized_floor = max(0.0, floor if floor is not None else 0.0)
    copied = [deepcopy(dict(move)) for move in moves]
    known_ids = {
        int(move["MoveID"])
        for move in copied
        if isinstance(move.get("MoveID"), int)
    }
    queues: Dict[str, List[dict]] = {}
    for move in copied:
        module_name = str(move.get("ModuleName") or "").strip() or "__GLOBAL__"
        queues.setdefault(module_name, []).append(move)
    for queue in queues.values():
        queue.sort(key=_sort_key)

    module_available: Dict[str, float] = {
        module_name: normalized_floor for module_name in queues
    }
    actual_end_by_id: Dict[int, float] = {}
    ended_ids: Set[int] = set()
    materialized: List[dict] = []
    planned_end_by_id = {
        move["MoveID"]: (_number(move.get("EndTime")) if _number(move.get("EndTime")) is not None
                         else (_number(move.get("StartTime")) or 0.0))
        for move in copied if isinstance(move.get("MoveID"), int)
    }
    planned_module_end: Dict[str, float] = {}

    while queues:
        candidates: List[Tuple[float, str, int, dict]] = []
        blocked_heads: List[Tuple[float, str, int, dict]] = []
        for module_name, queue in queues.items():
            move = queue[0]
            move_id = int(move.get("MoveID")) if isinstance(move.get("MoveID"), int) else 0
            predecessors = {
                int(value)
                for value in (move.get("PreMoveID") or [])
                if isinstance(value, int) and int(value) in known_ids
            }
            planned_start = _number(move.get("StartTime")) or 0.0
            # 原计划可能含驻留、释放或工艺等待。只传播前驱完成时刻的变化，
            # 不把这些等待误当成可删除的空白；零间隔依赖可随前驱完成直接提前。
            planned_bounds = [planned_end_by_id[value] for value in predecessors]
            if module_name in planned_module_end:
                planned_bounds.append(planned_module_end[module_name])
            wait_after_predecessors = max(0.0, planned_start - max(planned_bounds)) if planned_bounds else 0.0
            actual_bounds = [actual_end_by_id[value] for value in predecessors if value in actual_end_by_id]
            if module_name in planned_module_end:
                actual_bounds.append(module_available[module_name])
            earliest_start = max(
                normalized_floor,
                (max(actual_bounds) + wait_after_predecessors) if actual_bounds else planned_start,
                module_available[module_name],
                *(actual_end_by_id[value] for value in predecessors if value in actual_end_by_id),
            )
            candidate = (earliest_start, module_name, move_id, move)
            blocked_heads.append(candidate)
            if predecessors <= ended_ids:
                candidates.append(candidate)

        # 依赖环属于后续结构校验的职责。这里沿用 HongYe 的容错行为继续生成
        # 确定性时间线，使调用方仍能得到带稳定错误码的依赖环诊断。
        selected = min(candidates or blocked_heads, key=lambda item: item[:3])
        actual_start, module_name, move_id, move = selected
        planned_start = _number(move.get("StartTime")) or 0.0
        planned_end = _number(move.get("EndTime"))
        planned_duration = max(0.0, (planned_end if planned_end is not None else planned_start) - planned_start)
        duration = (
            max(0.0, float(duration_resolver(move)))
            if duration_resolver is not None
            else planned_duration
        )
        actual_end = actual_start + duration
        move["StartTime"] = actual_start
        move["EndTime"] = actual_end
        materialized.append(move)
        planned_module_end[module_name] = planned_end if planned_end is not None else planned_start
        module_available[module_name] = actual_end
        if isinstance(move.get("MoveID"), int):
            actual_end_by_id[move_id] = actual_end
            ended_ids.add(move_id)
        queues[module_name].pop(0)
        if not queues[module_name]:
            del queues[module_name]

    return materialized


def validate_move_list(
    task: Any,
    moves: List[dict],
    init_data: "Optional[Mapping[str, Any] | MachineState]" = None,
    *,
    check_residency: bool = True,
    external_predecessors: "Optional[Mapping[int, Mapping[str, Any]]]" = None,
    skipped_clean_validation_types: "Optional[Iterable[str]]" = None,
    initial_state: "Optional[MachineState]" = None,
) -> List[str]:
    """保持公共校验契约：返回稳定错误列表，空列表表示所有规则通过。

    参数含义与 validate_move_plan 一致；现场及静态配置均不会被修改。
    需要继续推进本代计划的调用方直接保留 validate_move_plan 的初态与终态。
    """
    return validate_move_plan(
        task, moves, init_data, check_residency=check_residency,
        external_predecessors=external_predecessors,
        skipped_clean_validation_types=skipped_clean_validation_types,
        initial_state=initial_state,
    ).issues


def validate_move_plan(
    task: Any,
    moves: List[dict],
    init_data: "Optional[Mapping[str, Any] | MachineState]" = None,
    *,
    check_residency: bool = True,
    external_predecessors: "Optional[Mapping[int, Mapping[str, Any]]]" = None,
    skipped_clean_validation_types: "Optional[Iterable[str]]" = None,
    initial_state: "Optional[MachineState]" = None,
) -> MovePlanValidation:
    """按时间线校验 MoveList；覆盖依赖 DAG、Route 时限与物理状态。

    ``external_predecessors`` 提供上一代已提交或正在执行的 Move（按 MoveID
    索引），供重算增量输出引用：其 MoveID 不属于本代 ``moves``，但可被本代
    ``PreMoveID`` 合法引用为已完成的前驱；首排校验不传该参数。
    ``initial_state`` 可提供重算现场（含门、持片和占用窗口）；静态时长校验
    仍读取 ``init_data`` 的设备配置，现场快照只用于物理回放且不会被修改。
    """
    for index, move in enumerate(moves):
        if not isinstance(move, Mapping):
            return MovePlanValidation([
                _global_issue(
                    ValidationErrorCode.MOVE_ITEM_INVALID,
                    f"MoveList[{index}] 必须是 JSON 对象",
                )
            ])
    field_error = _validate_move_field_shapes(moves)
    if field_error:
        return MovePlanValidation([field_error])
    dependency_error = _validate_move_dependencies(moves, external_predecessors)
    if dependency_error:
        return MovePlanValidation([dependency_error])
    duration_error = _validate_configured_durations(task, moves, init_data)
    if duration_error:
        return MovePlanValidation([duration_error])
    if check_residency:
        route_time_error = _validate_route_time_limits(task, moves)
        if route_time_error:
            return MovePlanValidation([route_time_error])
    try:
        state = MachineState.from_sources(
            task, initial_state if initial_state is not None else init_data,
        )
    except ValueError as error:
        return MovePlanValidation([str(error)])
    state.skipped_clean_validation_types = {
        str(value).strip().lower()
        for value in (skipped_clean_validation_types or ())
        if str(value).strip().lower() in CLEAN_VALIDATION_TYPES
    }
    scheduled: List[_ScheduledCompletion] = []
    ordered_moves = _IndexedMoves(sorted(moves, key=_sort_key))
    _supplement_state_from_moves(state, ordered_moves)
    replay_initial_state = state.clone()
    for move in ordered_moves:
        start_time = _number(move.get("StartTime"))
        end_time = _number(move.get("EndTime"))
        if start_time is None or end_time is None:
            return MovePlanValidation([_issue(move, ValidationErrorCode.TIME_INVALID, "StartTime 和 EndTime 必须是有限数字")])
        if end_time + TIME_TOLERANCE < start_time:
            return MovePlanValidation([_issue(move, ValidationErrorCode.TIME_REVERSED, "EndTime 不能早于 StartTime")])
        _finish_until(scheduled, start_time)
        error = _start_move(state, move, end_time, ordered_moves, scheduled)
        if error:
            return MovePlanValidation([error])
    _finish_until(scheduled, float("inf"))
    clean_issue = _final_clean_obligation_issue(state)
    if clean_issue:
        return MovePlanValidation([clean_issue])
    return MovePlanValidation([], ordered_moves, replay_initial_state, state)


def _validate_move_field_shapes(moves: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """校验 MoveType 与专属槽位字段的协议边界。"""
    for move in moves:
        if move.get("MoveType") in {PICK_MOVE, MULTI_PICK_MOVE} and "SlotList" in move:
            return _issue(
                move,
                ValidationErrorCode.MOVE_FIELD_INVALID,
                "PickMove 不允许携带 SlotList，请使用 SrcSlotList",
            )
    return None


def _validate_move_dependencies(
    moves: Sequence[Mapping[str, Any]],
    external_predecessors: "Optional[Mapping[int, Mapping[str, Any]]]" = None,
) -> Optional[str]:
    """校验 MoveID 唯一性、PreMoveID 引用、拓扑无环和时间先后。

    ``external_predecessors`` 提供上一代已提交或正在执行的 Move 索引（按
    MoveID）。重算增量输出的 ``PreMoveID`` 可引用这些旧代 MoveID 作为已完成
    前驱：仍要求其 EndTime 不晚于本动作 StartTime，但不参与本代拓扑环检测。
    """
    by_id: Dict[int, Mapping[str, Any]] = {}
    for move in moves:
        raw_move_id = move.get("MoveID")
        if isinstance(raw_move_id, bool) or not isinstance(raw_move_id, int):
            return _issue(move, ValidationErrorCode.MOVE_ID_INVALID, "MoveID 必须是整数")
        move_id = int(raw_move_id)
        if move_id in by_id:
            return _issue(move, ValidationErrorCode.MOVE_ID_DUPLICATE, f"MoveID={move_id} 重复")
        by_id[move_id] = move

    # 每条边只登记一次；用入度计数代替为每个节点长期保留两份集合。
    # 重复 PreMoveID 仍逐项检查协议与时间，但不能重复增加拓扑入度。
    predecessor_counts: Dict[int, int] = {}
    successors: Dict[int, List[int]] = {move_id: [] for move_id in by_id}
    for move_id, move in by_id.items():
        raw_values = move.get("PreMoveID") or []
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
            return _issue(move, ValidationErrorCode.PREDECESSOR_FORMAT_INVALID, "PreMoveID 必须是整数数组")
        parsed: Set[int] = set()
        current_start = _number(move.get("StartTime"))
        for raw_value in raw_values:
            if isinstance(raw_value, bool) or not isinstance(raw_value, int):
                return _issue(move, ValidationErrorCode.PREDECESSOR_VALUE_INVALID, f"PreMoveID 包含非整数引用: {raw_value}")
            predecessor_id = int(raw_value)
            if predecessor_id == move_id:
                return _issue(move, ValidationErrorCode.PREDECESSOR_SELF_REFERENCE, "PreMoveID 不能引用自身")
            predecessor = by_id.get(predecessor_id)
            external = (
                external_predecessors.get(predecessor_id)
                if predecessor is None and external_predecessors is not None
                else None
            )
            if predecessor is None and external is None:
                return _issue(move, ValidationErrorCode.PREDECESSOR_MISSING, f"PreMoveID 引用了不存在的 MoveID={predecessor_id}")
            predecessor_end = _number((predecessor or external).get("EndTime"))
            if (
                predecessor_end is not None
                and current_start is not None
                and predecessor_end > current_start + TIME_TOLERANCE
            ):
                return _issue(
                    move,
                    ValidationErrorCode.PREDECESSOR_TIME_CONFLICT,
                    f"前驱 MoveID={predecessor_id} 尚未结束（EndTime={predecessor_end}）",
                )
            if predecessor is not None and predecessor_id not in parsed:
                parsed.add(predecessor_id)
                successors[predecessor_id].append(move_id)
        predecessor_counts[move_id] = len(parsed)

    ready = [move_id for move_id, count in predecessor_counts.items() if not count]
    visited = 0
    while ready:
        current = ready.pop()
        visited += 1
        for successor in successors[current]:
            predecessor_counts[successor] -= 1
            if not predecessor_counts[successor]:
                ready.append(successor)
    if visited != len(by_id):
        cyclic = sorted(move_id for move_id, count in predecessor_counts.items() if count)
        return _global_issue(
            ValidationErrorCode.DEPENDENCY_CYCLE,
            f"MoveList 的 PreMoveID 存在依赖环: {cyclic}",
        )
    return None


def _validate_configured_durations(
    task: Any,
    moves: Sequence[Mapping[str, Any]],
    init_data: "Optional[Mapping[str, Any] | MachineState]",
) -> Optional[str]:
    """按设备四元组和 Route Visit 校验算法输出的原子动作时长。"""
    if isinstance(init_data, MachineState):
        payload: Mapping[str, Any] = {}
    else:
        payload = _initial_payload(init_data)
    robots = _mapping(payload.get("Robots"))
    if not robots and task is None:
        # 只有物理快照时不含静态时长配置，也没有 Task 工艺时长；原逐动作
        # 分支全部无可比对的 expected，直接返回同一结论。字段与物理校验仍执行。
        return None

    for move in moves:
        move_type = move.get("MoveType")
        start_time = _number(move.get("StartTime"))
        end_time = _number(move.get("EndTime"))
        if start_time is None or end_time is None:
            continue
        actual = end_time - start_time
        robot_name = str(move.get("Robot") or move.get("ModuleName") or "")
        robot = robots.get(robot_name)
        expected: Optional[float] = None
        if move_type in {PICK_MOVE, PLACE_MOVE} and isinstance(robot, Mapping):
            station_field = "SrcStationList" if move_type == PICK_MOVE else "DestStationList"
            station_name = _first_text(move, station_field)
            timing_field = "PickTime" if move_type == PICK_MOVE else "PlaceTime"
            raw_timing = robot.get(timing_field)
            if isinstance(raw_timing, Mapping):
                if station_name not in raw_timing:
                    return _issue(
                        move,
                        ValidationErrorCode.TIMING_CONFIG_MISSING,
                        f"{robot_name} 缺少 {timing_field}[{station_name}]",
                    )
                expected = _number(raw_timing[station_name])
                if expected is None or expected < 0.0:
                    return _issue(
                        move,
                        ValidationErrorCode.TIMING_CONFIG_INVALID,
                        f"{robot_name} 的 {timing_field}[{station_name}] 必须是非负有限数字",
                    )
        elif move_type == PRE_TRANS_MOVE and isinstance(robot, Mapping):
            entries = robot.get("PrepTransTime")
            if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)) and entries:
                source = _first_text(move, "SrcStationList")
                destination = _first_text(move, "DestStationList")
                material_ids = _values(move, "MatIDList")
                robot_slots = _integer_values(move, "RobotSlotList")
                is_linked_empty = bool(material_ids) and all(
                    index < len(robot_slots)
                    and _pretrans_is_linked_empty_pick(
                        move,
                        moves,
                        index,
                        robot_slots[index],
                        material_id,
                    )
                    for index, material_id in enumerate(material_ids)
                )
                trans_type = 0 if not material_ids or is_linked_empty else 1
                transfer_index: Dict[Tuple[str, str, int], float] = {}
                for item in entries:
                    if not isinstance(item, Mapping) or "TransType" not in item:
                        return _issue(move, ValidationErrorCode.TIMING_CONFIG_INVALID, f"{robot_name} 的 PrepTransTime 缺少 TransType")
                    raw_transfer_type = item.get("TransType")
                    if isinstance(raw_transfer_type, bool) or not isinstance(raw_transfer_type, int):
                        return _issue(move, ValidationErrorCode.TIMING_CONFIG_INVALID, f"{robot_name} 的 PrepTransTime.TransType 必须是整数")
                    transfer_time = _number(item.get("Time"))
                    if transfer_time is None or transfer_time < 0.0:
                        return _issue(move, ValidationErrorCode.TIMING_CONFIG_INVALID, f"{robot_name} 的 PrepTransTime.Time 必须是非负有限数字")
                    key = (
                        str(item.get("SrcStation") or ""),
                        str(item.get("DestStation") or ""),
                        raw_transfer_type,
                    )
                    if key in transfer_index:
                        return _issue(move, ValidationErrorCode.TIMING_CONFIG_INVALID, f"{robot_name} 的 PrepTransTime 重复四元组 {key}")
                    transfer_index[key] = transfer_time
                lookup = (source, destination, trans_type)
                if lookup not in transfer_index:
                    return _issue(move, ValidationErrorCode.TIMING_CONFIG_MISSING, f"{robot_name} 缺少 PrepTransTime 四元组 {lookup}")
                expected = transfer_index[lookup]
        elif move_type == PROCESS_MOVE and task is not None:
            expected = _process_move_expected_duration(task, move)

        if expected is not None and abs(actual - expected) > TIME_TOLERANCE:
            return _issue(
                move,
                ValidationErrorCode.DURATION_MISMATCH,
                f"动作时长 {actual:.6f}s 与配置 {expected:.6f}s 不一致",
            )
    return None


def _process_move_expected_duration(task: Any, move: Mapping[str, Any]) -> Optional[float]:
    """按 MatID、原始 StepID 和模块定位 ProcessMove 的配置时长。"""
    material_ids = _values(move, "MatIDList")
    if not material_ids:
        return None
    material_key = str(material_ids[0])
    wafer = next(
        (
            item
            for item in getattr(task, "wafers", ()) or ()
            if str(getattr(item, "mat_id", "")) == material_key
        ),
        None,
    )
    if wafer is None:
        return None
    step_ids = _integer_values(move, "StepIDList")
    step_id = step_ids[0] if step_ids else None
    station_name = _station_name(move)
    stage = next(
        (
            item
            for item in getattr(wafer, "stages", ()) or ()
            if str(getattr(item, "stage_type", "")) == "process"
            and (
                step_id is None
                or int(getattr(item, "step_id", getattr(item, "j", -1))) == step_id
            )
            and (
                str(getattr(item, "chamber", "")) == station_name
                or station_name in {str(value) for value in getattr(item, "cands", ()) or ()}
            )
        ),
        None,
    )
    if stage is None:
        return None
    by_chamber = dict(getattr(stage, "process_time_by_chamber", {}) or {})
    return float(by_chamber.get(station_name, getattr(stage, "proc", 0.0)))


def _validate_route_time_limits(
    task: Any,
    moves: Sequence[Mapping[str, Any]],
) -> Optional[str]:
    """按 Problem 中保留的原始 StepID 校验 Residency 与相邻加工 Q-time。"""
    wafers = list(getattr(task, "wafers", ()) or ()) if task is not None else []
    if not wafers:
        return None
    wafer_by_material = {
        str(getattr(wafer, "mat_id", "")): wafer
        for wafer in wafers
    }
    ordered = sorted(moves, key=_sort_key)
    process_events: Dict[str, List[Tuple[Mapping[str, Any], Any]]] = {}
    for move in ordered:
        if move.get("MoveType") != PROCESS_MOVE:
            continue
        material_ids = _values(move, "MatIDList")
        step_ids = _integer_values(move, "StepIDList")
        station_name = _station_name(move)
        for index, material_id in enumerate(material_ids):
            material_key = str(material_id)
            wafer = wafer_by_material.get(material_key)
            if wafer is None:
                continue
            step_id = step_ids[index] if index < len(step_ids) else None
            stages = [
                stage
                for stage in getattr(wafer, "stages", ()) or ()
                if str(getattr(stage, "stage_type", "")) == "process"
                and (
                    step_id is None
                    or int(getattr(stage, "step_id", getattr(stage, "j", -1))) == step_id
                )
                and (
                    str(getattr(stage, "chamber", "")) == station_name
                    or station_name in {
                        str(value) for value in getattr(stage, "cands", ()) or ()
                    }
                )
            ]
            occurrence = len(process_events.get(material_key, ()))
            if not stages:
                continue
            stage = stages[min(occurrence, len(stages) - 1)]
            process_events.setdefault(material_key, []).append((move, stage))

    for material_key, events in process_events.items():
        for index, (process_move, stage) in enumerate(events):
            process_end = _number(process_move.get("EndTime"))
            if process_end is None:
                continue
            residency = float(getattr(stage, "residency", -1.0))
            if residency >= 0.0:
                source_station = _station_name(process_move)
                departure = next(
                    (
                        candidate
                        for candidate in ordered
                        if candidate.get("MoveType") == PICK_MOVE
                        and material_key in {
                            str(value) for value in _values(candidate, "MatIDList")
                        }
                        and source_station in {
                            str(value) for value in _values(candidate, "SrcStationList")
                        }
                        and (_number(candidate.get("StartTime")) or 0.0)
                        >= process_end - TIME_TOLERANCE
                    ),
                    None,
                )
                if departure is None:
                    return _issue(
                        process_move,
                        ValidationErrorCode.POST_PROCESS_PICK_MISSING,
                        f"加工完成后缺少物料 {material_key} 的取片动作",
                    )
                elapsed = (_number(departure.get("StartTime")) or process_end) - process_end
                if elapsed > residency + TIME_TOLERANCE:
                    return _issue(
                        departure,
                        ValidationErrorCode.RESIDENCY_EXCEEDED,
                        f"物料 {material_key} 驻留 {elapsed:.3f}s 超过上限 {residency:.3f}s",
                    )
            qtime = float(getattr(stage, "qtime", -1.0))
            if qtime >= 0.0 and index + 1 < len(events):
                next_move = events[index + 1][0]
                elapsed = (_number(next_move.get("StartTime")) or process_end) - process_end
                if elapsed > qtime + TIME_TOLERANCE:
                    return _issue(
                        next_move,
                        ValidationErrorCode.QTIME_EXCEEDED,
                        f"物料 {material_key} 相邻加工间隔 {elapsed:.3f}s 超过 Q-time {qtime:.3f}s",
                    )
    return None


def release_completed_load_port_materials(
    task: Any,
    state: MachineState,
    load_port_names: Sequence[str],
) -> Tuple[set[Any], set[str]]:
    """从平台快照卸载已经到达 Route 终点的晶圆。"""
    wafer_by_material = {
        getattr(wafer, "mat_id", None): wafer
        for wafer in getattr(task, "wafers", ()) or ()
    }
    released_ids: set[Any] = set()
    empty_ports: set[str] = set()
    for load_port_name in {str(name) for name in load_port_names if str(name)}:
        station = state.stations.get(load_port_name)
        if station is None:
            continue
        for slot in station.slots.values():
            material = slot.material
            if material is None:
                continue
            wafer = wafer_by_material.get(material.material_id)
            stages = list(getattr(wafer, "stages", ()) or ()) if wafer is not None else []
            if not stages:
                continue
            final_stage_index = len(stages) - 1
            final_stage = stages[final_stage_index]
            accepts_load_port = (
                str(getattr(final_stage, "chamber", "")) == load_port_name
                or load_port_name in {str(name) for name in getattr(final_stage, "cands", ()) or ()}
            )
            if (
                str(getattr(final_stage, "stage_type", "")) != "sink"
                or not accepts_load_port
                or material.step_id != final_stage_index
            ):
                continue
            released_ids.add(material.material_id)
            _set_slot(slot, SlotPhase.EMPTY, None)
        if all(slot.material is None for slot in station.slots.values()):
            empty_ports.add(load_port_name)
    return released_ids, empty_ports
