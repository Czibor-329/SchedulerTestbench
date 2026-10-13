"""理论产能页面服务：按筛选条件编译路线、求解扫描曲线并标注测试数据点。

本模块是“结果诊断 → 理论产能”页签的数据边界，负责：

- 读取设备概览与测试，按与真实运行完全相同的编译链路
  （``build_workspace_batch_plan`` → ``build_task_alg_init`` →
  ``build_round_update`` → ``compile_problem``）得到 ``Problem``；
- 用一次“路线分类”缓存（只编译、不求解）得到每条路线的路径类型、LoadLock
  类型与可建图状态，供筛选器选择；
- 只对**当前选中**的路径类型与 LoadLock 类型组合扫描曲线，不下发全设备求解；
- 把设备里**满足筛选条件**的测试换算成曲线上的数据点：横轴是该测试关键工序的
  加工时间，纵轴是它按真实加工时间推导出的理论产能。

模型口径固定为**无清洁**：测试中的 Clean 引用与 Clean 定义都会被剥离，清洁类型
只用于挑选测试点，不改变曲线。驻留上限与 Q-time 不会被静默放宽（由 Cycle 拓扑
识别明确拒绝）。统一加工时间扫描只改真空段加工工序的加工时间，其它设备服务时长
取自设备 init。

服务不带缓存以外的状态，也不修改设备或测试数据；缓存键包含设备更新时间，设备
或测试被编辑后不会继续返回旧结果。
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.backend.execution.batch_service import build_workspace_batch_plan
from app.backend.execution.plan_builder import BuildState, build_round_update, build_task_alg_init
from app.backend.workspace.repository import get_workspace_device_overview, get_workspace_test

# 导入即装配批量服务依赖，保证与运行接口使用同一套计划构造边界。
import app.backend.wiring  # noqa: F401

# 扫描区间默认值与上限；点数上限用于防止接口被构造出超大响应。
SCAN_DEFAULT_FIRST_SECONDS = 0.0
SCAN_DEFAULT_LAST_SECONDS = 300.0
SCAN_DEFAULT_STEP_SECONDS = 10.0
SCAN_MAXIMUM_POINTS = 201

# 新建测试时平台为每个加工工序使用的默认加工时间，与前端模板默认值一致。
DEFAULT_PROCESS_SECONDS = 20.0

# 建图只需要少量晶圆即可确定路线形态；多片会让编译和展开变慢而不增加信息。
PROBE_WAFER_COUNT = 4

# 每次响应最多换算多少个测试数据点；超出时截断并显式告知调用方。
POINT_LIMIT = 150

# 分组键的截断长度；等价路线共用同一个键，用于把 PM1/PM3 这类等价腔合并成一张卡片。
GROUP_KEY_LENGTH = 12

# 三种锁分工与五种路径形态的稳定取值；与 Cycle 拓扑识别结果一致。
LOCK_MODE_VALUES = ("shared-bidirectional", "dedicated-entry-exit", "single-bidirectional")
ROUTE_KIND_VALUES = ("single-node", "parallel", "serial", "mixed", "reentrant")

# 清洁类型筛选取值；``none`` 表示不引用任何清洁的测试。
CLEAN_TYPE_VALUES = ("preclean", "postclean", "wacclean", "dummy", "dummywac")
NO_CLEAN_VALUE = "none"
CLEAN_FILTER_VALUES = (*CLEAN_TYPE_VALUES, NO_CLEAN_VALUE)

# 设备扫描、路线分类与测试索引的缓存条数；键都含设备更新时间。
SCAN_CACHE_LIMIT = 8
INDEX_CACHE_LIMIT = 6
_SCAN_CACHE: "Dict[Tuple[Any, ...], Dict[str, Any]]" = {}
_ROUTE_INDEX_CACHE: "Dict[Tuple[str, str], Tuple['_RouteIndexEntry', ...]]" = {}
_TEST_INDEX_CACHE: "Dict[Tuple[str, str], Tuple['_TestIndexEntry', ...]]" = {}


@dataclass(frozen=True)
class _AlgorithmRuntime:
    """按需加载的算法侧能力集合。

    算法仓库是可选依赖：平台在它缺失时仍必须能启动并给出可读错误，因此这里不用
    模块级导入，而是在首次调用时加载。
    """

    analyze_cycle_capacity_model: Callable[..., Any]
    build_cycle_capacity_model: Callable[..., Any]
    physical_members_per_unit: Callable[..., int]
    reference_process_seconds: Callable[..., Sequence[Mapping[str, float]]]
    scan_cycle_capacity: Callable[..., Any]
    compile_problem: Callable[..., Any]
    analyze_cycle_topology: Callable[..., Any]


@dataclass(frozen=True)
class _RouteIndexEntry:
    """一条路线的分类结果：路径类型、LoadLock 类型与可建图状态。"""

    route_name: str
    group: str
    route_kind: str
    lock_mode: str
    status: str
    reason: str
    detail: str
    load_port: str


@dataclass(frozen=True)
class _TestIndexEntry:
    """一个测试的筛选与换算所需信息。"""

    test_id: str
    name: str
    group: str
    clean_types: Tuple[str, ...]
    route_configs: Mapping[str, Mapping[str, Any]]


_ALGORITHM_RUNTIME: Optional[_AlgorithmRuntime] = None


def _algorithm() -> _AlgorithmRuntime:
    """返回算法侧能力；算法仓库缺失或导入失败时给出可读错误。"""
    global _ALGORITHM_RUNTIME
    if _ALGORITHM_RUNTIME is not None:
        return _ALGORITHM_RUNTIME
    try:
        from src.analysis.cycle_capacity_graph import (
            analyze_cycle_capacity_model,
            build_cycle_capacity_model,
            physical_members_per_unit,
            reference_process_seconds,
            scan_cycle_capacity,
        )
        from src.compiler import compile_problem
        from src.schedule.strategies.cycle.topology import analyze_cycle_topology
    except Exception as error:  # noqa: BLE001 - 算法仓库是可选依赖
        raise RuntimeError(
            f"本地算法仓库未加载，无法计算理论产能：{type(error).__name__}: {error}"
        ) from error
    _ALGORITHM_RUNTIME = _AlgorithmRuntime(
        analyze_cycle_capacity_model=analyze_cycle_capacity_model,
        build_cycle_capacity_model=build_cycle_capacity_model,
        physical_members_per_unit=physical_members_per_unit,
        reference_process_seconds=reference_process_seconds,
        scan_cycle_capacity=scan_cycle_capacity,
        compile_problem=compile_problem,
        analyze_cycle_topology=analyze_cycle_topology,
    )
    return _ALGORITHM_RUNTIME


def _finite_number(value: Any, default: float) -> float:
    """读取有限数字；缺失或非法时使用默认值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return float(default)
    return number


def _query_number(value: Any, default: float, field: str) -> Optional[float]:
    """把查询参数转换为数字；缺省返回默认值，非法值抛出可读错误。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return float(default)
    if isinstance(value, bool):
        raise ValueError(f"参数 {field} 必须是数字")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            raise ValueError(f"参数 {field} 必须是数字") from None
    else:
        raise ValueError(f"参数 {field} 必须是数字")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"参数 {field} 必须是有限数字")
    return number


def _scan_window(
    first_seconds: Any,
    last_seconds: Any,
    step_seconds: Any,
) -> Tuple[float, float, float]:
    """校验并规范化扫描区间，返回起点、终点与步长。

    :raises ValueError: 区间非法、步长为零或点数超过上限时。
    """
    first = _query_number(first_seconds, SCAN_DEFAULT_FIRST_SECONDS, "first")
    last = _query_number(last_seconds, SCAN_DEFAULT_LAST_SECONDS, "last")
    step = _query_number(step_seconds, SCAN_DEFAULT_STEP_SECONDS, "step")
    if step is None or step <= 0.0:
        raise ValueError("扫描步长必须是大于零的数字")
    if first is None or last is None:
        raise ValueError("扫描区间必须是数字")
    first = max(0.0, first)
    if last <= first:
        raise ValueError("扫描终点必须大于起点")
    points = int(round((last - first) / step)) + 1
    if points > SCAN_MAXIMUM_POINTS:
        raise ValueError(f"扫描点数不能超过 {SCAN_MAXIMUM_POINTS}，请增大步长或缩小区间")
    return first, last, step


def _filter_values(raw: Any, allowed: Sequence[str], field: str) -> Tuple[str, ...]:
    """把逗号分隔的筛选参数收敛为稳定取值序列；未知取值明确报错。"""
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        items = [str(item) for item in raw]
    else:
        items = str(raw).replace("，", ",").split(",")
    values: List[str] = []
    for item in items:
        value = item.strip()
        if not value:
            continue
        if value not in allowed:
            raise ValueError(f"参数 {field} 不支持取值 {value}")
        if value not in values:
            values.append(value)
    return tuple(values)


def _first_load_port(device_init: Mapping[str, Any]) -> str:
    """返回设备中第一个 LoadPort 站点名。"""
    for name, station in (device_init.get("Stations") or {}).items():
        if str((station or {}).get("Type") or "").casefold() == "loadport":
            return str(name)
    raise ValueError("设备未配置 LoadPort，无法建立理论产能输入")


def _route_by_name(device: Mapping[str, Any], route_name: str) -> Mapping[str, Any]:
    """按名称查找共享路径模板。"""
    for route in device.get("routes") or []:
        if isinstance(route, Mapping) and str(route.get("name") or "") == route_name:
            return route
    raise ValueError(f"设备中不存在路径模板：{route_name}")


def _route_load_port(route: Mapping[str, Any], device_init: Mapping[str, Any]) -> str:
    """返回路线起点站点；缺少起点时退回设备第一个 LoadPort。"""
    try:
        candidate = str(route["stages"][0]["visits"][0].get("stationName") or "")
    except (KeyError, IndexError, TypeError):
        candidate = ""
    return candidate or _first_load_port(device_init)


def _default_route_config(route: Mapping[str, Any]) -> Dict[str, Any]:
    """按共享模板生成默认测试侧参数，等价于新建测试时的初始配置。"""
    recipe_prefix = str(route.get("group") or route.get("name") or "Route").strip()
    stages: Dict[str, Any] = {}
    for index, stage in enumerate(route.get("stages") or []):
        if not isinstance(stage, Mapping):
            continue
        step_id = str(int(_finite_number(stage.get("stepId"), index)))
        visits = [visit for visit in (stage.get("visits") or []) if isinstance(visit, Mapping)]
        visit = visits[0] if visits else {}
        process_time = _finite_number(
            visit.get("processTime", visit.get("recipeTime")), DEFAULT_PROCESS_SECONDS,
        )
        stages[step_id] = {
            "processTime": process_time,
            "recipeTime": process_time,
            "qTimeLimit": _finite_number(visit.get("qTimeLimit"), -1),
            "residencyConstraint": _finite_number(visit.get("residencyConstraint"), -1),
            "beforeCleanRefs": [],
            "afterCleanRefs": [],
            "processRecipe": str(
                visit.get("processRecipe")
                or (f"{recipe_prefix}_Step{step_id}" if stage.get("needProcess") else "")
            ),
            "processType": str(visit.get("processType") or ""),
            "weight": deepcopy(visit.get("weight") or {}),
            "moveTimeOffset": deepcopy(visit.get("moveTimeOffset") or {}),
            "slotIds": str(visit.get("slotIds") or "1"),
        }
    return {
        "bufferOption": int(_finite_number(route.get("bufferOption"), 0)),
        "prePJobCleanRefs": [],
        "postPJobCleanRefs": [],
        "postCJobCleanRefs": [],
        "stages": stages,
    }


def _clean_route_config(route_config: Mapping[str, Any]) -> Dict[str, Any]:
    """剥离测试配置中的 Clean 引用，得到无清洁的理论产能输入。"""
    cleaned = deepcopy(dict(route_config))
    for key in ("prePJobCleanRefs", "postPJobCleanRefs", "postCJobCleanRefs"):
        cleaned[key] = []
    stages = cleaned.get("stages")
    if isinstance(stages, Mapping):
        for stage in stages.values():
            if not isinstance(stage, dict):
                continue
            stage["beforeCleanRefs"] = []
            stage["afterCleanRefs"] = []
    return cleaned


def _synthetic_test_case(
    route_name: str,
    route_config: Mapping[str, Any],
    load_port: str,
) -> Dict[str, Any]:
    """构造只含一个 PJob 的测试用例，供计划构造器产出该路线的编译输入。"""
    return {
        "id": "theory-capacity-probe",
        "name": "理论产能探针",
        "group": "",
        "strategy": "cycle",
        "roundCount": 1,
        "times": [0.0],
        "options": {},
        "cleans": [],
        "routeConfigs": {route_name: _clean_route_config(route_config)},
        "rounds": [{
            "currentTime": 0.0,
            "cjobs": [{
                "taskId": "TheoryProbe",
                "loadPort": load_port,
                "cjobCycle": 1,
                "jobType": 0,
                "priority": 1,
                "taskMode": 0,
                "pJobNameList": ["P1"],
                "pjobs": [{
                    "jobName": "P1",
                    "taskId": "1",
                    "waferCount": PROBE_WAFER_COUNT,
                    "matList": [],
                    "routeRef": route_name,
                    "loadPort": load_port,
                    "priority": 1,
                }],
            }],
        }],
        "schemaVersion": 9,
    }


def _compile_route(
    device: Mapping[str, Any],
    route_name: str,
    route_config: Mapping[str, Any],
    load_port: str,
) -> Tuple[Any, Any]:
    """编译单条路线并识别 Cycle 拓扑。

    :return: ``(problem, topology)``；``topology`` 可能是拒绝结果。
    :raises ValueError: 计划构造或编译失败时，消息可直接展示给用户。
    """
    runtime = _algorithm()
    test_case = _synthetic_test_case(route_name, route_config, load_port)
    plan = build_workspace_batch_plan(device, test_case, "cycle", {})
    plan["device"] = build_task_alg_init(
        plan["device"], plan["routes"], plan["rounds"], plan["cleans"],
    )
    build_state = BuildState()
    update = build_round_update(plan, plan["rounds"][0], 0.0, build_state)
    problem = runtime.compile_problem(plan["device"], update)
    return problem, runtime.analyze_cycle_topology(problem)


def _failure_fields(error: Exception) -> Tuple[str, str, str]:
    """把编译或建图异常映射为 (status, reason, detail)。

    Cycle 拓扑拒绝与建图器拒绝都带 ``reason``/``detail`` 稳定字段，按码分类即可
    区分“模板不适用”和真正的编译失败，不需要导入算法侧异常类型。
    """
    reason = str(getattr(error, "reason", "") or "")
    detail = str(getattr(error, "detail", "") or "")
    if reason and detail:
        return "unsupported", reason, detail
    return "error", "compile-failed", f"{type(error).__name__}: {error}"


def _unsupported_entry(
    route_name: str,
    group: str,
    reason: str,
    detail: str,
    *,
    status: str = "unsupported",
) -> Dict[str, Any]:
    """生成一条不可建图路线的结果条目。"""
    return {
        "route_name": route_name,
        "group": group,
        "status": status,
        "reason": reason,
        "detail": detail,
        "facts": None,
        "points": {},
        "group_key": "",
    }


def _structure_signature(runtime: _AlgorithmRuntime, problem: Any, topology: Any, members: int) -> str:
    """返回忽略站点名的设备计时结构签名。

    签名只用服务类别、服务键的角色前缀与秒数，因此 ``PM1`` 与 ``PM3`` 这类计时完全
    相同的腔室得到同一个签名，而取片准备时间不同的 ``PM2`` 会单独成组。加工时间在这里
    固定为 0，保证签名与扫描区间、扫描步长、测试加工参数都无关。

    这里只建图不求解，因此比完整推导便宜得多。

    :param runtime: 算法侧能力集合。
    :param problem: 该路线的编译结果。
    :param topology: 该路线的 Cycle 拓扑识别结果。
    :param members: 每逻辑单元的物理成员数。
    :return: 稳定的结构签名字符串。
    """
    overrides = [
        {str(chamber): 0.0 for chamber in by_chamber}
        for by_chamber in runtime.reference_process_seconds(problem, topology)
    ]
    model = runtime.build_cycle_capacity_model(
        problem, topology, process_seconds=overrides, members_per_unit=members,
    )
    parts = [
        f"{service.category}:{str(service.key).split(':', 1)[0]}:{float(service.seconds):.6f}"
        for service in model.services
    ]
    return "|".join(sorted(parts))


def _route_group_key(row: Mapping[str, Any]) -> str:
    """返回一条可建图路线的等价分组键。

    路线名里的加工腔只是设备站点名：同一路径类型、同一 LoadLock 分工、同样的候选腔数、
    同样的压力域、同样的周期轮数与片数，并且设备计时结构相同的路线，对页面而言完全等价
    （例如 PSE300 上 ``PM1`` 与 ``PM3`` 的取片准备时间相同）。这类路线合并成一张卡片，
    避免用腔室名重复列出同一份结论；只要服务结构不同就保留为不同分组。

    分组键只用形态与设备计时，不含扫描区间、步长或测试加工参数，因此调整扫描设置不会
    让卡片重新洗牌。

    :param row: 单条路线的扫描结果条目（含 ``facts`` 与 ``structure_key``）。
    :return: 稳定的短分组键。
    """
    facts = row.get("facts") or {}
    signature = "|".join((
        str(facts.get("route_kind", "")),
        str(facts.get("lock_mode", "")),
        str(facts.get("rounds_per_cycle", "")),
        str(facts.get("units_per_cycle", "")),
        ",".join(str(count) for count in facts.get("chamber_counts") or []),
        ",".join(str(domain) for domain in facts.get("pressure_domains") or []),
        str(row.get("structure_key", "")),
    ))
    return hashlib.sha1(signature.encode("utf-8")).hexdigest()[:GROUP_KEY_LENGTH]


def _route_groups(routes: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """把等价路线合并为展示分组，保持首次出现顺序。

    :param routes: 本次筛选命中的全部路线条目（可含不可建图项）。
    :return: 每个分组带稳定键、结构化形态字段、等价路线名列表与代表路线。
    """
    groups: Dict[str, Dict[str, Any]] = {}
    for row in routes:
        if row.get("status") != "supported":
            continue
        key = str(row.get("group_key") or "")
        facts = row.get("facts") or {}
        columns = row.get("points") or {}
        capacities = [float(value) for value in columns.get("physical_wafers_per_hour") or []]
        if key not in groups:
            groups[key] = {
                "group_key": key,
                "route_kind": str(facts.get("route_kind", "")),
                "lock_mode": str(facts.get("lock_mode", "")),
                "rounds_per_cycle": facts.get("rounds_per_cycle"),
                "units_per_cycle": facts.get("units_per_cycle"),
                "chamber_counts": list(facts.get("chamber_counts") or []),
                "pressure_domains": list(facts.get("pressure_domains") or []),
                "route_names": [],
                "representative": str(row.get("route_name", "")),
                "start_wafers_per_hour": capacities[0] if capacities else None,
                "end_wafers_per_hour": capacities[-1] if capacities else None,
                "best_wafers_per_hour": max(capacities) if capacities else None,
                "matched_tests": 0,
            }
        groups[key]["route_names"].append(str(row.get("route_name", "")))
    return list(groups.values())


def _attach_group_counts(
    groups: Sequence[Dict[str, Any]],
    points: Sequence[Mapping[str, Any]],
) -> None:
    """就地写入每个分组命中的测试数量。"""
    counts: Dict[str, int] = {}
    for point in points:
        key = str(point.get("group_key") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    for group in groups:
        group["matched_tests"] = counts.get(str(group["group_key"]), 0)


def _route_failure(route_name: str, group: str, error: Exception) -> Dict[str, Any]:
    """把编译或建图异常转换为可在页面展示的条目。"""
    status, reason, detail = _failure_fields(error)
    return _unsupported_entry(route_name, group, reason, detail, status=status)


def _round_floats(value: Any, digits: int = 3) -> Any:
    """递归把响应中的浮点数收敛到展示精度，避免响应体积被浮点尾数放大。"""
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, Mapping):
        return {str(key): _round_floats(item, digits) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_round_floats(item, digits) for item in value]
    return value


def _scan_points_payload(scan: Any) -> Dict[str, Any]:
    """把扫描点转成列式载荷：图表按列读取，体积远小于逐点对象数组。"""
    return {
        "process_seconds": [round(point.process_seconds, 3) for point in scan.points],
        "period_seconds": [round(point.period_seconds, 3) for point in scan.points],
        "seconds_per_unit": [round(point.seconds_per_unit, 3) for point in scan.points],
        "physical_wafers_per_hour": [
            round(point.physical_wafers_per_hour, 2) for point in scan.points
        ],
        "limiting_kind": [point.limiting_kind for point in scan.points],
    }


def _compact_facts(facts: Mapping[str, Any], route_name: str) -> Dict[str, Any]:
    """裁剪路线事实：扫描图的每一点加工时间相同，无需逐腔重复携带。"""
    return {
        "route_name": route_name,
        "lock_mode": facts.get("lock_mode", ""),
        "route_kind": facts.get("route_kind", ""),
        "pressure_domains": list(facts.get("pressure_domains") or []),
        "vacuum_robot": facts.get("vacuum_robot", ""),
        "atmosphere_robot": facts.get("atmosphere_robot", ""),
        "process_nodes": [
            {
                "ordinal": node.get("ordinal"),
                "alias": node.get("alias"),
                "chambers": list(node.get("chambers") or []),
            }
            for node in facts.get("process_nodes") or []
        ],
        "rounds_per_cycle": facts.get("rounds_per_cycle"),
        "units_per_cycle": facts.get("units_per_cycle"),
        "physical_members_per_unit": facts.get("physical_members_per_unit"),
        "loadlock_batch_size": facts.get("loadlock_batch_size"),
        "chamber_counts": list(facts.get("chamber_counts") or []),
        "source_station": facts.get("source_station", ""),
        "sink_station": facts.get("sink_station", ""),
        "inbound_relays": list(facts.get("inbound_relays") or []),
        "outbound_relays": list(facts.get("outbound_relays") or []),
    }


# --------------------------------------------------------------------------- #
# 路线分类索引
# --------------------------------------------------------------------------- #
def _route_index(
    device_id: str,
    device: Mapping[str, Any],
) -> Tuple[_RouteIndexEntry, ...]:
    """按设备更新时间缓存每条路线的路径类型、锁分工与可建图状态。

    只做“编译 + 拓扑识别”，不求解曲线，因此筛选器可以显示可选值与数量，而不会
    预先计算任何扫描结果。
    """
    cache_key = (str(device_id), str(device.get("updatedAt") or ""))
    cached = _ROUTE_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    device_init = device.get("device") or {}
    entries: List[_RouteIndexEntry] = []
    for route in device.get("routes") or []:
        if not isinstance(route, Mapping):
            continue
        route_name = str(route.get("name") or "")
        group = str(route.get("group") or "")
        load_port = _route_load_port(route, device_init)
        try:
            _problem, topology = _compile_route(
                device, route_name, _default_route_config(route), load_port,
            )
        except Exception as error:  # noqa: BLE001 - 单条路线失败不影响其它路线
            status, reason, detail = _failure_fields(error)
            entries.append(_RouteIndexEntry(
                route_name, group, "", "", status, reason, detail, load_port,
            ))
            continue
        if not bool(getattr(topology, "supported", False)):
            entries.append(_RouteIndexEntry(
                route_name, group, "", "",
                "unsupported",
                str(getattr(topology, "reason", "unsupported-topology")),
                str(getattr(topology, "detail", "")),
                load_port,
            ))
            continue
        entries.append(_RouteIndexEntry(
            route_name,
            group,
            str(getattr(topology, "route_kind", "")),
            str(getattr(topology, "lock_mode", "")),
            "supported",
            "",
            "",
            load_port,
        ))
    result = tuple(entries)
    if len(_ROUTE_INDEX_CACHE) >= INDEX_CACHE_LIMIT:
        _ROUTE_INDEX_CACHE.pop(next(iter(_ROUTE_INDEX_CACHE)))
    _ROUTE_INDEX_CACHE[cache_key] = result
    return result


def _route_index_payload(entries: Sequence[_RouteIndexEntry]) -> Dict[str, Any]:
    """汇总筛选器需要的可选值与数量。"""

    def counts(values: Sequence[str], field: str) -> List[Dict[str, Any]]:
        """按稳定顺序统计取值数量，只统计可建图路线。"""
        result = []
        for value in values:
            total = sum(
                1 for entry in entries
                if entry.status == "supported"
                and str(getattr(entry, field)) == value
            )
            result.append({"value": value, "count": total})
        return result

    return {
        "route_kind": counts(ROUTE_KIND_VALUES, "route_kind"),
        "lock_mode": counts(LOCK_MODE_VALUES, "lock_mode"),
        "route_total": len(entries),
        "route_supported": sum(1 for entry in entries if entry.status == "supported"),
    }


# --------------------------------------------------------------------------- #
# 测试索引
# --------------------------------------------------------------------------- #
def _referenced_clean_names(route_config: Mapping[str, Any]) -> Tuple[str, ...]:
    """收集测试配置中引用的清洁定义名称。"""
    names: List[str] = []
    for key in ("prePJobCleanRefs", "postPJobCleanRefs", "postCJobCleanRefs"):
        for name in route_config.get(key) or []:
            text = str(name or "").strip()
            if text and text not in names:
                names.append(text)
    stages = route_config.get("stages")
    if isinstance(stages, Mapping):
        for stage in stages.values():
            if not isinstance(stage, Mapping):
                continue
            for key in ("beforeCleanRefs", "afterCleanRefs"):
                for name in stage.get(key) or []:
                    text = str(name or "").strip()
                    if text and text not in names:
                        names.append(text)
    return tuple(names)


def _test_index(
    device_id: str,
    device: Mapping[str, Any],
) -> Tuple[_TestIndexEntry, ...]:
    """按设备更新时间缓存测试的筛选信息与已剥离清洁的路线参数。

    只读取每个测试一次并保留真正被引用的路线参数，避免每次筛选都重新解析测试文件。
    """
    cache_key = (str(device_id), str(device.get("updatedAt") or ""))
    cached = _TEST_INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached
    library = {
        str(clean.get("name") or ""): str(clean.get("cleanType") or "")
        for clean in (device.get("cleans") or [])
        if isinstance(clean, Mapping)
    }
    entries: List[_TestIndexEntry] = []
    for summary in device.get("tests") or []:
        if not isinstance(summary, Mapping):
            continue
        test_id = str(summary.get("id") or "")
        if not test_id:
            continue
        try:
            test_case = get_workspace_test(device_id, test_id)
        except Exception:  # noqa: BLE001 - 单个测试读取失败时跳过该测试
            continue
        own = {
            str(clean.get("name") or ""): str(clean.get("cleanType") or "")
            for clean in (test_case.get("cleans") or [])
            if isinstance(clean, Mapping)
        }
        route_configs = test_case.get("routeConfigs")
        configs = route_configs if isinstance(route_configs, Mapping) else {}
        referenced_routes = _test_route_names(test_case)
        clean_types: List[str] = []
        kept: Dict[str, Mapping[str, Any]] = {}
        for route_name in referenced_routes:
            config = configs.get(route_name)
            if not isinstance(config, Mapping):
                continue
            for name in _referenced_clean_names(config):
                clean_type = own.get(name) or library.get(name)
                if clean_type and clean_type not in clean_types:
                    clean_types.append(clean_type)
            kept[route_name] = _clean_route_config(config)
        entries.append(_TestIndexEntry(
            test_id=test_id,
            name=str(summary.get("name") or test_case.get("name") or ""),
            group=str(summary.get("group") or test_case.get("group") or ""),
            clean_types=tuple(clean_types),
            route_configs=kept,
        ))
    result = tuple(entries)
    if len(_TEST_INDEX_CACHE) >= INDEX_CACHE_LIMIT:
        _TEST_INDEX_CACHE.pop(next(iter(_TEST_INDEX_CACHE)))
    _TEST_INDEX_CACHE[cache_key] = result
    return result


def _test_route_names(test_case: Mapping[str, Any]) -> Tuple[str, ...]:
    """按首次出现顺序返回测试引用的路线名。"""
    names: List[str] = []
    for round_row in test_case.get("rounds") or []:
        if not isinstance(round_row, Mapping):
            continue
        for cjob in round_row.get("cjobs") or []:
            if not isinstance(cjob, Mapping):
                continue
            for pjob in cjob.get("pjobs") or []:
                if not isinstance(pjob, Mapping):
                    continue
                route_name = str(pjob.get("routeRef") or "").strip()
                if route_name and route_name not in names:
                    names.append(route_name)
    return tuple(names)


def _clean_filter_matches(entry: _TestIndexEntry, cleans: Sequence[str]) -> bool:
    """判断测试是否满足清洁筛选；空筛选表示全部测试。"""
    if not cleans:
        return True
    if NO_CLEAN_VALUE in cleans and not entry.clean_types:
        return True
    return any(value in entry.clean_types for value in cleans if value != NO_CLEAN_VALUE)


def _test_signature(route_config: Mapping[str, Any]) -> Tuple[Any, ...]:
    """返回测试加工参数的稳定签名，用于复用完全相同的推导结果。"""
    stages = route_config.get("stages")
    if not isinstance(stages, Mapping):
        return ()
    return tuple(sorted(
        (
            str(step_id),
            str(stage.get("processRecipe") or ""),
            round(float(_finite_number(stage.get("processTime"), 0.0)), 6),
            round(float(_finite_number(stage.get("qTimeLimit"), -1)), 6),
            round(float(_finite_number(stage.get("residencyConstraint"), -1)), 6),
        )
        for step_id, stage in (
            (str(key), value) for key, value in stages.items() if isinstance(value, Mapping)
        )
    ))


def _point_payload(
    entry: _TestIndexEntry,
    route_name: str,
    *,
    status: str,
    reason: str = "",
    detail: str = "",
    group_key: str = "",
    process_seconds: Sequence[float] = (),
    key_process_seconds: Optional[float] = None,
    period_seconds: Optional[float] = None,
    seconds_per_unit: Optional[float] = None,
    wafers_per_hour: Optional[float] = None,
    limiting_kind: str = "",
    limiting_label: str = "",
) -> Dict[str, Any]:
    """组装一个测试数据点；不可推导时保留原因，便于页面说明为什么没有点。"""
    return {
        "id": entry.test_id,
        "name": entry.name,
        "group": entry.group,
        "clean_types": list(entry.clean_types),
        "route_name": route_name,
        "group_key": group_key,
        "status": status,
        "reason": reason,
        "detail": detail,
        "process_seconds": [round(float(value), 3) for value in process_seconds],
        "key_process_seconds": (
            None if key_process_seconds is None else round(float(key_process_seconds), 3)
        ),
        "period_seconds": None if period_seconds is None else round(float(period_seconds), 3),
        "seconds_per_unit": None if seconds_per_unit is None else round(float(seconds_per_unit), 3),
        "physical_wafers_per_hour": (
            None if wafers_per_hour is None else round(float(wafers_per_hour), 2)
        ),
        "limiting_kind": limiting_kind,
        "limiting_label": limiting_label,
    }


def _matched_points(
    device: Mapping[str, Any],
    members: int,
    tests: Sequence[_TestIndexEntry],
    route_names: Sequence[str],
    route_groups: Mapping[str, str],
    cleans: Sequence[str],
) -> Tuple[List[Dict[str, Any]], bool]:
    """把满足筛选条件的测试换算成曲线数据点。

    :param route_groups: 路线名到等价分组键的映射，用于把数据点挂到合并后的卡片上。
    :return: ``(数据点列表, 是否被数量上限截断)``。
    """
    allowed = set(route_names)
    candidates = [
        entry for entry in tests
        if any(name in allowed for name in entry.route_configs)
        and _clean_filter_matches(entry, cleans)
    ]
    candidates.sort(key=lambda item: (item.group, item.name, item.test_id))
    truncated = len(candidates) > POINT_LIMIT
    runtime = _algorithm()
    cache: Dict[Tuple[str, Tuple[Any, ...]], Optional[Dict[str, Any]]] = {}
    points: List[Dict[str, Any]] = []
    for entry in candidates[:POINT_LIMIT]:
        for route_name in route_names:
            route_config = entry.route_configs.get(route_name)
            if route_config is None:
                continue
            group_key = str(route_groups.get(route_name, ""))
            signature = (route_name, _test_signature(route_config))
            if signature not in cache:
                cache[signature] = _derive_point(
                    runtime, device, route_name, route_config, members,
                )
            derived = cache[signature]
            if derived is None:
                points.append(_point_payload(
                    entry, route_name, group_key=group_key,
                    status="error", reason="compile-failed",
                    detail="该测试的路径参数无法编译，无法换算理论产能数据点。",
                ))
                continue
            if derived["status"] != "supported":
                points.append(_point_payload(
                    entry, route_name, group_key=group_key,
                    status=derived["status"], reason=derived["reason"], detail=derived["detail"],
                ))
                continue
            points.append(_point_payload(
                entry, route_name, group_key=group_key,
                status="supported",
                process_seconds=derived["process_seconds"],
                key_process_seconds=derived["key_process_seconds"],
                period_seconds=derived["period_seconds"],
                seconds_per_unit=derived["seconds_per_unit"],
                wafers_per_hour=derived["wafers_per_hour"],
                limiting_kind=derived["limiting_kind"],
                limiting_label=derived["limiting_label"],
            ))
    return points, truncated


def _derive_point(
    runtime: _AlgorithmRuntime,
    device: Mapping[str, Any],
    route_name: str,
    route_config: Mapping[str, Any],
    members: int,
) -> Optional[Dict[str, Any]]:
    """按测试的实际加工时间推导一个数据点；失败时返回带原因的结果。"""
    try:
        load_port = _route_load_port(_route_by_name(device, route_name), device.get("device") or {})
    except ValueError:
        load_port = _first_load_port(device.get("device") or {})
    try:
        problem, topology = _compile_route(device, route_name, route_config, load_port)
    except Exception as error:  # noqa: BLE001 - 单个测试失败不能影响其它点
        status, reason, detail = _failure_fields(error)
        return {"status": status, "reason": reason, "detail": detail}
    try:
        analysis = runtime.analyze_cycle_capacity_model(
            problem, topology, members_per_unit=members,
        )
    except Exception as error:  # noqa: BLE001 - 建图器拒绝也按不可推导处理
        status, reason, detail = _failure_fields(error)
        return {"status": status, "reason": reason, "detail": detail}
    process_seconds = _analysis_process_seconds(runtime, problem, topology)
    return {
        "status": "supported",
        "process_seconds": process_seconds,
        "key_process_seconds": max(process_seconds) if process_seconds else 0.0,
        "period_seconds": analysis.report.period_seconds,
        "seconds_per_unit": analysis.report.seconds_per_unit,
        "wafers_per_hour": analysis.report.physical_wafers_per_hour,
        "limiting_kind": analysis.limiting_kind,
        "limiting_label": analysis.limiting_label,
    }


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def read_theory_capacity_route_index(device_id: str) -> Dict[str, Any]:
    """只返回设备的路线分类索引，供筛选器显示可选值与数量。

    该接口只做编译与拓扑识别（不求解任何曲线），因此比完整扫描快得多。

    :param device_id: 设备稳定 ID。
    :return: 含设备信息与各筛选取值数量的响应对象。
    :raises ValueError: 设备不存在时。
    """
    device = get_workspace_device_overview(device_id)
    device_init = device.get("device") or {}
    members = _algorithm().physical_members_per_unit(device_init.get("Stations") or {})
    return {
        "ok": True,
        "device": {
            "id": str(device.get("id") or device_id),
            "name": str(device.get("name") or ""),
            "physical_members_per_unit": members,
        },
        "route_index": _route_index_payload(_route_index(device_id, device)),
        "routes": [
            {
                "route_name": entry.route_name,
                "group": entry.group,
                "route_kind": entry.route_kind,
                "lock_mode": entry.lock_mode,
                "status": entry.status,
                "reason": entry.reason,
                "detail": entry.detail,
            }
            for entry in _route_index(device_id, device)
        ],
    }


def read_theory_capacity_scan(
    device_id: str,
    *,
    kinds: Any = "",
    locks: Any = "",
    cleans: Any = "",
    first_seconds: Any = SCAN_DEFAULT_FIRST_SECONDS,
    last_seconds: Any = SCAN_DEFAULT_LAST_SECONDS,
    step_seconds: Any = SCAN_DEFAULT_STEP_SECONDS,
) -> Dict[str, Any]:
    """按筛选条件读取理论产能扫描与测试数据点。

    只对当前选中的路径类型与 LoadLock 类型组合求解曲线；满足筛选条件的测试会被
    换算成曲线上的数据点（横轴为关键工序加工时间，纵轴为其理论产能）。

    :param device_id: 设备稳定 ID。
    :param kinds: 逗号分隔的路径类型；空表示全部。
    :param locks: 逗号分隔的 LoadLock 类型；空表示全部。
    :param cleans: 逗号分隔的清洁类型；空表示全部，``none`` 表示无清洁测试。
    :param first_seconds: 扫描起点加工时间，秒。
    :param last_seconds: 扫描终点加工时间，秒。
    :param step_seconds: 扫描步长，秒。
    :return: 含设备信息、筛选回显、逐路线曲线与测试数据点的响应对象。
    :raises ValueError: 设备不存在、筛选取值不支持或扫描区间非法时。
    """
    window = _scan_window(first_seconds, last_seconds, step_seconds)
    kind_filter = _filter_values(kinds, ROUTE_KIND_VALUES, "kinds")
    lock_filter = _filter_values(locks, LOCK_MODE_VALUES, "locks")
    clean_filter = _filter_values(cleans, CLEAN_FILTER_VALUES, "cleans")
    device = get_workspace_device_overview(device_id)
    cache_key = (
        str(device_id),
        str(device.get("updatedAt") or ""),
        kind_filter,
        lock_filter,
        clean_filter,
        window,
    )
    cached = _SCAN_CACHE.get(cache_key)
    if cached is not None:
        return cached
    device_init = device.get("device") or {}
    members = _algorithm().physical_members_per_unit(device_init.get("Stations") or {})
    index = _route_index(device_id, device)
    selected = [
        entry for entry in index
        if (not kind_filter or entry.route_kind in kind_filter)
        and (not lock_filter or entry.lock_mode in lock_filter)
    ]
    routes = [_scan_selected_route(device, members, entry, window) for entry in selected]
    # 先给可建图路线标注等价分组键，再把数据点挂到分组上：PM1/PM3 这类等价腔只出一张卡片。
    for row in routes:
        row["group_key"] = _route_group_key(row) if row["status"] == "supported" else ""
    route_groups = {str(row["route_name"]): str(row["group_key"]) for row in routes}
    supported_names = [
        entry.route_name for entry in selected if entry.status == "supported"
    ]
    points, truncated = _matched_points(
        device,
        members,
        _test_index(device_id, device),
        supported_names,
        route_groups,
        clean_filter,
    )
    groups = _route_groups(routes)
    _attach_group_counts(groups, points)
    payload = {
        "ok": True,
        "device": {
            "id": str(device.get("id") or device_id),
            "name": str(device.get("name") or ""),
            "physical_members_per_unit": members,
        },
        "scan": {
            "first_seconds": window[0],
            "last_seconds": window[1],
            "step_seconds": window[2],
        },
        "filters": {
            "kinds": list(kind_filter),
            "locks": list(lock_filter),
            "cleans": list(clean_filter),
        },
        "route_index": _route_index_payload(index),
        "routes": routes,
        "groups": groups,
        "tests": points,
        "test_total": len(points),
        "test_truncated": truncated,
    }
    if len(_SCAN_CACHE) >= SCAN_CACHE_LIMIT:
        _SCAN_CACHE.pop(next(iter(_SCAN_CACHE)))
    _SCAN_CACHE[cache_key] = payload
    return payload


def _scan_selected_route(
    device: Mapping[str, Any],
    members: int,
    entry: _RouteIndexEntry,
    window: Tuple[float, float, float],
) -> Dict[str, Any]:
    """按筛选命中的路线求解扫描曲线；不可建图时只返回状态与原因。"""
    if entry.status != "supported":
        return _unsupported_entry(
            entry.route_name, entry.group, entry.reason, entry.detail, status=entry.status,
        )
    try:
        route = _route_by_name(device, entry.route_name)
        problem, topology = _compile_route(
            device, entry.route_name, _default_route_config(route), entry.load_port,
        )
        scan = _algorithm().scan_cycle_capacity(
            problem,
            topology,
            first_seconds=window[0],
            last_seconds=window[1],
            step_seconds=window[2],
            members_per_unit=members,
        )
        structure_key = _structure_signature(_algorithm(), problem, topology, members)
    except Exception as error:  # noqa: BLE001 - 单条路线失败不能影响整台设备
        return _route_failure(entry.route_name, entry.group, error)
    return {
        "route_name": entry.route_name,
        "group": entry.group,
        "status": "supported",
        "reason": "",
        "detail": "",
        "structure_key": structure_key,
        "facts": _compact_facts(scan.facts.to_dict(), entry.route_name),
        "points": _scan_points_payload(scan),
    }

def read_theory_capacity_derivation(
    device_id: str,
    *,
    test_id: str = "",
    route_name: str = "",
) -> Dict[str, Any]:
    """读取一个测试或一条路线的理论产能完整推导。

    测试口径使用该测试的实际加工时间；路线口径使用模板默认参数（与新建测试时的
    初始配置一致）。两者都会剥离 Clean 引用，模型口径固定为无清洁。

    :param device_id: 设备稳定 ID。
    :param test_id: 测试稳定 ID；与 ``route_name`` 二选一。
    :param route_name: 路径模板名称；与 ``test_id`` 二选一。
    :return: 含设备信息、目标信息与逐路线推导的响应对象。
    :raises ValueError: 设备/测试/路线不存在或目标为空时。
    """
    if not test_id and not route_name:
        raise ValueError("必须指定测试或路径模板")
    device = get_workspace_device_overview(device_id)
    device_init = device.get("device") or {}
    members = _algorithm().physical_members_per_unit(device_init.get("Stations") or {})
    if test_id:
        test_case = get_workspace_test(device_id, test_id)
        entries = _test_route_entries(test_case)
        target = {
            "kind": "test",
            "id": str(test_case.get("id") or test_id),
            "name": str(test_case.get("name") or ""),
            "group": str(test_case.get("group") or ""),
        }
        if not entries:
            raise ValueError("该测试没有引用任何路径模板，无法计算理论产能")
    else:
        route = _route_by_name(device, route_name)
        entries = [{
            "route_name": route_name,
            "pjob_name": "",
            "load_port": _route_load_port(route, device_init),
            "route_config": _default_route_config(route),
        }]
        target = {
            "kind": "route",
            "id": route_name,
            "name": route_name,
            "group": str(route.get("group") or ""),
        }
    derivations: List[Dict[str, Any]] = []
    for entry in entries:
        current = str(entry["route_name"])
        group = ""
        try:
            group = str(_route_by_name(device, current).get("group") or "")
        except ValueError:
            group = ""
        route_config = entry["route_config"] if entry["route_config"] else None
        if not route_config:
            derivations.append(_unsupported_entry(
                current, group, "missing-route-config",
                "该测试没有为这条路径保存加工时间参数，无法计算理论产能。",
            ))
            continue
        load_port = str(entry["load_port"] or "") or _first_load_port(device_init)
        try:
            problem, topology = _compile_route(
                device, current, _clean_route_config(route_config), load_port,
            )
            analysis = _algorithm().analyze_cycle_capacity_model(
                problem, topology, members_per_unit=members,
            )
        except Exception as error:  # noqa: BLE001 - 逐条路线隔离失败
            derivations.append(_route_failure(current, group, error))
            continue
        payload = analysis.to_dict()
        payload.pop("tolerance_seconds", None)
        payload = _round_floats(payload)
        payload.update({
            "route_name": current,
            "group": group,
            "pjob_name": str(entry["pjob_name"] or ""),
            "status": "supported",
            "reason": "",
            "detail": "",
            "process_seconds": [
                round(float(seconds), 3)
                for seconds in _analysis_process_seconds(_algorithm(), problem, topology)
            ],
        })
        derivations.append(payload)
    return {
        "ok": True,
        "device": {
            "id": str(device.get("id") or device_id),
            "name": str(device.get("name") or ""),
            "physical_members_per_unit": members,
        },
        "target": target,
        "derivations": derivations,
    }


def _test_route_entries(test_case: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """收集测试引用的路线、LoadPort 与测试侧参数，保持首次出现顺序。"""
    route_configs = test_case.get("routeConfigs")
    configs = route_configs if isinstance(route_configs, Mapping) else {}
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for round_row in test_case.get("rounds") or []:
        if not isinstance(round_row, Mapping):
            continue
        for cjob in round_row.get("cjobs") or []:
            if not isinstance(cjob, Mapping):
                continue
            for pjob in cjob.get("pjobs") or []:
                if not isinstance(pjob, Mapping):
                    continue
                route_name = str(pjob.get("routeRef") or "").strip()
                if not route_name or route_name in seen:
                    continue
                seen.add(route_name)
                config = configs.get(route_name)
                entries.append({
                    "route_name": route_name,
                    "pjob_name": str(pjob.get("jobName") or ""),
                    "load_port": str(pjob.get("loadPort") or cjob.get("loadPort") or ""),
                    "route_config": config if isinstance(config, Mapping) else {},
                })
    return entries


def _analysis_process_seconds(
    runtime: _AlgorithmRuntime,
    problem: Any,
    topology: Any,
) -> Sequence[float]:
    """返回参考晶圆每道加工工序的加工时间，供页面在曲线图上标注数据点。"""
    overrides = runtime.reference_process_seconds(problem, topology)
    values: List[float] = []
    for node, seconds in zip(topology.process_nodes, overrides):
        by_chamber = dict(seconds)
        values.append(float(by_chamber.get(str(node.chambers[0]), 0.0)))
    return values


__all__ = (
    "CLEAN_FILTER_VALUES",
    "CLEAN_TYPE_VALUES",
    "LOCK_MODE_VALUES",
    "NO_CLEAN_VALUE",
    "ROUTE_KIND_VALUES",
    "SCAN_DEFAULT_FIRST_SECONDS",
    "SCAN_DEFAULT_LAST_SECONDS",
    "SCAN_DEFAULT_STEP_SECONDS",
    "SCAN_MAXIMUM_POINTS",
    "read_theory_capacity_derivation",
    "read_theory_capacity_route_index",
    "read_theory_capacity_scan",
)
