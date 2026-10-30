"""调度请求数据建模。

本模块负责设备初始化数据归一化、企业格式 AlgInit 透传、Route/Recipe 构造以及
各轮 CJob/PJob 请求展开。命名与结构对齐企业平台协议（Material ID 从 0 起、
PJobName 形如 ``{TaskID}.PJOB_{CJob全局序号}``、Dummy ID 从 1000000 起）。
它不执行调度算法，也不处理 HTTP 或工作区持久化。
"""

from __future__ import annotations
from collections.abc import Mapping, Sequence

from app.backend.payload_snapshot import copy_payload

import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.backend.execution.runtime_snapshot import (
    compact_robot_snapshots, compact_station_snapshots,
)


FIRST_SLOT_ID = 1
MAX_WAFERS_PER_JOB = 25
DEFAULT_TRIGGER_UPPER = 9999.0
DEFAULT_IDLE_TIME_CLEAN_UPPER = 999999.0
DEFAULT_PRE_CLEAN_IDLE_TIME_UPPER = 99999.0
# 旧 Clean 未提供库存配置时使用的 DummyPort wafer 数量。
DEFAULT_DUMMY_WAFER_COUNT = 8
# 企业协议：Dummy 晶圆 ID 从 1000000 起编号，寿命上限默认 99999。
DUMMY_MATERIAL_ID_START = 1000000
DUMMY_MATERIAL_LIMIT_LEVEL = 99999
DUMMY_MATERIAL_USAGE_MIX = 2
CJOB_TYPE_VALUES = {"NormalLot": 0, "HighestLot": 2, "HigherLot": 3}
TASK_MODE_VALUES = {"Smart": 0, "Pipeline": 1, "Sequential": 2, "Concurrent": 3}
# Aligner RouteStep 必须通过 NeedProcess 触发 AlignMove 规划。
ALIGNER_NAME_MARKER = "aligner"
# 需要按任务请求补齐全量站点的标准 Station.Type 名称。
DUMMY_PORT_STATION_TYPE = "dummyport"
BUFFER_STATION_TYPE = "buffer"


@dataclass
class BuildState:
    """跨轮生成 Job 接口对象时使用的全局编号和 LoadPort 槽位状态。

    企业协议：产品 Material ID 从 0 起连续编号；CJob 全局序号从 1 起递增，
    用于 ``PJOB_{序号}`` 命名，CJobCycle 补片沿用原始 CJob 的序号。
    """

    next_material_id: int = 0
    next_cjob_index: int = 1
    next_slot_by_port: Dict[str, int] = field(default_factory=dict)
    job_names: set[str] = field(default_factory=set)
    task_ids: set[str] = field(default_factory=set)
    dummy_material_count: int = 0


def _is_aligner_station(station_name: str) -> bool:
    """判断 Route Visit 是否指向需要通过 NeedProcess 下发的 Aligner。"""
    return ALIGNER_NAME_MARKER in station_name.casefold()


def extract_init_data(raw: Any) -> Dict[str, Any]:
    """兼容原始 init_data、AlgInit 录制数组和一层 Info 包装。

    标准 ``IToolTopo`` 不包含顶层 Route；旧录制或导入文件即使携带
    ``Route/Routes`` 兼容字段，也不能继续透传给算法 ``init``。
    """
    value = raw
    if isinstance(value, list):
        entry = next(
            (
                item for item in value
                if isinstance(item, Mapping) and str(item.get("Describe") or "").lower() == "alginit"
            ),
            None,
        )
        if entry is None:
            raise ValueError("设备文件数组中找不到 Describe=AlgInit")
        value = entry.get("Info")
    if isinstance(value, Mapping) and isinstance(value.get("InitData"), Mapping):
        value = value["InitData"]
    if isinstance(value, Mapping) and isinstance(value.get("Info"), Mapping):
        value = value["Info"]
    if not isinstance(value, Mapping):
        raise ValueError("设备文件不是有效 JSON 对象")
    if not isinstance(value.get("Stations"), Mapping) or not isinstance(value.get("Robots"), Mapping):
        raise ValueError("设备文件必须包含 Stations 和 Robots")
    return {
        str(key): copy_payload(item)
        for key, item in value.items()
        if str(key).casefold() not in {"route", "routes"}
    }


def _station_type_name(station: Mapping[str, Any]) -> str:
    """返回标准接口 Station.Type 的归一化名称。

    保留给运行时按类型定位站点（例如 DummyPort 库存查找）使用。
    """
    return str(station.get("Type") or "").strip().casefold()


def build_task_alg_init(
    device: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]] = (),
    rounds: Sequence[Mapping[str, Any]] = (),
    cleans: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    """构造发送给算法 ``init`` 的完整设备拓扑。

    企业协议要求 AlgInit 携带设备声明的全部 Station/Robot，包括未被当前任务
    Route 引用的模块、完整时序字典、PrepTransTime 与 ArmInfo；不做任务级拓扑
    裁剪，也不改写机器人指向。任务级裁剪曾把 18 站设备裁成 9 站并过滤各类时序
    键，导致本地与企业在候选生成前就已分叉。

    参数 routes/rounds/cleans 仅保留调用方兼容；本函数不再依据任务内容修改
    拓扑。任务级裁剪如需恢复，必须作为显式标注的独立实验入口，不允许在主链路
    隐式生效。
    """
    result = extract_init_data(device)
    if not isinstance(result.get("Stations"), Mapping) or not isinstance(
        result.get("Robots"), Mapping
    ):
        raise ValueError("设备文件必须包含 Stations 和 Robots")
    # 企业 AlgInit 顶层键序：Robots → Stations（设备文件的字段顺序不参与协议）。
    ordered_result = {key: result[key] for key in ("Robots", "Stations") if key in result}
    ordered_result.update(
        {key: value for key, value in result.items() if key not in ordered_result}
    )
    return ordered_result


def _name_index(rows: Sequence[Mapping[str, Any]], label: str) -> Dict[str, Dict[str, Any]]:
    """按唯一 Name 建立通用配置索引。"""
    result: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        name = str(row.get("name") or "").strip()
        if not name:
            raise ValueError(f"{label} 第 {index} 项缺少名称")
        if name in result:
            raise ValueError(f"{label} 名称重复：{name}")
        result[name] = copy_payload(dict(row))
    return result


def _recipe_index(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """按 Recipe 名称建立引用索引，允许同名 Recipe 为不同模块定义不同参数。"""
    result: Dict[str, Dict[str, Any]] = {}
    modules_by_name: Dict[str, set[str]] = {}
    for index, row in enumerate(rows, start=1):
        name = str(row.get("name") or "").strip()
        if not name:
            raise ValueError(f"Recipe 第 {index} 项缺少名称")
        module_list = _string_list(row.get("modules"))
        modules = set(module_list)
        if name not in result:
            result[name] = copy_payload(dict(row))
            result[name]["modules"] = module_list
            modules_by_name[name] = modules
            continue
        occupied_modules = modules_by_name[name]
        duplicate_modules = sorted(occupied_modules & modules)
        if duplicate_modules or not occupied_modules or not modules:
            detail = f"（模块：{','.join(duplicate_modules)}）" if duplicate_modules else ""
            raise ValueError(f"Recipe 名称和模块重复：{name}{detail}")
        occupied_modules.update(modules)
        result[name]["modules"] = [
            *result[name]["modules"],
            *(module for module in module_list if module not in result[name]["modules"]),
        ]
    return result


def _string_list(value: Any) -> List[str]:
    """把数组或逗号分隔文本收敛为去重字符串列表。"""
    if isinstance(value, str):
        items = value.replace("，", ",").split(",")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = value
    else:
        items = []
    result: List[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _slot_list(value: Any) -> List[int]:
    """解析 Route Visit 使用的一基槽位列表。"""
    slots: List[int] = []
    for item in _string_list(value):
        try:
            slot = int(item)
        except ValueError:
            continue
        if slot >= FIRST_SLOT_ID and slot not in slots:
            slots.append(slot)
    return slots or [FIRST_SLOT_ID]


def _station_slot_ids(station: Mapping[str, Any]) -> List[int]:
    """从站点配置读取真实物理槽位编号。"""
    raw_slots = station.get("Slots")
    if isinstance(raw_slots, Sequence) and not isinstance(raw_slots, (str, bytes)):
        return [int(item) for item in raw_slots]
    capacity = _finite_number(station.get("Capacity"), 0)
    if capacity >= 1:
        return list(range(1, int(capacity) + 1))
    return [FIRST_SLOT_ID]


def _robot_slot_ids(robot: Mapping[str, Any]) -> List[int]:
    """从启用 Arm 收集机器人全部物理手槽，缺失时按 Capacity 补齐。"""
    slots: set[int] = set()
    arm_info = robot.get("ArmInfo")
    if isinstance(arm_info, Mapping):
        for arm in arm_info.values():
            if not isinstance(arm, Mapping) or arm.get("IsEnable") is False:
                continue
            for value in arm.get("SlotIDs") or []:
                try:
                    slot_id = int(value)
                except (TypeError, ValueError):
                    continue
                if slot_id >= FIRST_SLOT_ID:
                    slots.add(slot_id)
    if slots:
        return sorted(slots)
    capacity = int(_finite_number(robot.get("Capacity"), 0))
    return list(range(FIRST_SLOT_ID, capacity + FIRST_SLOT_ID)) if capacity >= 1 else [FIRST_SLOT_ID]


def _finite_number(value: Any, default: float) -> float:
    """读取有限浮点数，非法值回退默认值。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clean_type(clean: Mapping[str, Any]) -> str:
    """识别精简 Clean 类别，并兼容旧版任务字段。"""
    explicit = "".join(
        char
        for char in str(clean.get("cleanType") or clean.get("category") or "").lower()
        if char not in "-_ "
    )
    aliases = {
        "preclean": "preclean",
        "postclean": "postclean",
        "wacclean": "wacclean",
        "dummy": "dummy",
        "dummyclean": "dummy",
        "dummywac": "dummywac",
        "dummywacclean": "dummywac",
    }
    if explicit in aliases:
        return aliases[explicit]
    signature = (
        f"{clean.get('taskName') or ''} {clean.get('name') or ''}"
    ).lower()
    if (
        ("dummy" in signature and "wac" in signature)
        or "prewac" in signature
        or clean.get("emptyRecipeRef")
    ):
        return "dummywac"
    if int(_finite_number(clean.get("materialCount"), 0)) > 0 or "dummy" in signature:
        return "dummy"
    if (
        str(clean.get("stateVariable") or "").lower() == "processcount"
        or "wac" in signature
    ):
        return "wacclean"
    if "post" in signature:
        return "postclean"
    return "preclean"


# 企业协议：CleanRecipe 按 cleanType 派生固定名称（"/清洗名首字母大写"）。
ENTERPRISE_CLEAN_RECIPES = {
    "dummy": "/PreJobDummy",
    "preclean": "/PreClean",
    "postclean": "/PostClean",
    "wacclean": "/Wacclean",
    "dummywac": "/PreJobDummy_wac",
}
# dummywac 的空腔（WAC）阶段清洁配方按 wacclean 语义命名。
ENTERPRISE_WAC_CLEAN_RECIPE = "/Wacclean"
# Dummy 系缺省触发窗口为 [DBL_MIN, 9999]；显式编辑器上下限优先。
# 下界取 double 最小正值（约等于“任意空闲即满足”）。
ENTERPRISE_DBL_MIN = 2.2250738585072014e-308


def _runtime_clean(clean: Mapping[str, Any]) -> Dict[str, Any]:
    """把精简编辑字段展开为标准 Clean 模板，同时保留显式适用腔室。

    企业协议：CleanRecipe 名称按 cleanType 派生固定值（如 ``/PreJobDummy``），
    不使用编辑器显示名拼接；编辑器自定义 recipeName 仅在持久化层保留。
    """
    value = copy_payload(dict(clean))
    name = str(value.get("name") or "").strip()
    clean_type = _clean_type(value)
    recipe_name = ENTERPRISE_CLEAN_RECIPES[clean_type]
    wac_recipe_name = ENTERPRISE_WAC_CLEAN_RECIPE
    trigger_count = max(
        1,
        int(_finite_number(
            value.get("triggerCount", value.get("lower")),
            5,
        )),
    )
    is_wac = clean_type == "wacclean"
    is_dummy = clean_type in {"dummy", "dummywac"}
    material_count = max(
        1,
        int(_finite_number(value.get("materialCount", value.get("triggerCount")), 2)),
    ) if is_dummy else 0
    task_names = {
        "preclean": "PreClean",
        "postclean": "PostClean",
        "wacclean": "WacClean",
        "dummy": "PreDummyClean",
        "dummywac": "PreWacClean",
    }
    # 前置清洁保留编辑器配置的 IdleTime；Max 只传递，不代表触发截止时间。
    idle_lower, idle_upper = 0.0, DEFAULT_IDLE_TIME_CLEAN_UPPER
    if clean_type in {"preclean", "dummy", "dummywac"}:
        idle_lower = value.get("lower")
        idle_upper = value.get("upper")
        idle_lower = (ENTERPRISE_DBL_MIN if is_dummy else 0.0) if idle_lower is None else idle_lower
        idle_upper = (
            DEFAULT_TRIGGER_UPPER if is_dummy else DEFAULT_PRE_CLEAN_IDLE_TIME_UPPER
        ) if idle_upper is None else idle_upper
        try:
            if isinstance(idle_lower, bool) or isinstance(idle_upper, bool):
                raise ValueError("布尔值不是时间")
            idle_lower, idle_upper = float(idle_lower), float(idle_upper)
        except (ValueError, TypeError) as error:
            raise ValueError("IdleTime 必须是非负有限数值") from error
        if not all(math.isfinite(item) and item >= 0 for item in (idle_lower, idle_upper)):
            raise ValueError("IdleTime 必须是非负有限数值")
        if idle_upper < idle_lower:
            raise ValueError("IdleTime 最大值不能小于最小值")
    value.update({
        "name": name,
        "cleanType": clean_type,
        "recipeName": recipe_name,
        "recipeRef": recipe_name,
        "recipeTime": max(0.0, _finite_number(value.get("recipeTime"), 0.0)),
        "modules": _string_list(value.get("modules")),
        "taskName": task_names[clean_type],
        "stateVariable": "ProcessCount" if is_wac else "IdleTime",
        "lower": trigger_count if is_wac else idle_lower,
        "upper": DEFAULT_TRIGGER_UPPER if is_wac else idle_upper,
        "triggerCount": material_count if is_dummy else trigger_count,
        "updateStateVariables": (
            ["ProcessCount"]
            if is_wac
            # 企业协议：Dummy 带片清洁更新 IdleTime/ProcessCount/DummyCount
            # 三个计数器，产品 Recipe 的 Weight 据此补齐 ProcessCount 与 DummyCount。
            else ["IdleTime", "ProcessCount", "DummyCount"]
            if is_dummy
            else ["IdleTime"]
            if clean_type == "preclean"
            else []
        ),
        "materialCount": material_count,
        "dummyWaferCount": (
            max(1, int(_finite_number(
                value.get("dummyWaferCount"),
                DEFAULT_DUMMY_WAFER_COUNT,
            )))
            if is_dummy
            else 0
        ),
        "preJudge": False,
        "emptyRecipeRef": (wac_recipe_name if clean_type == "dummywac" else ""),
        "wacRecipeTime": max(
            0.0,
            _finite_number(
                value.get("wacRecipeTime", value.get("emptyRecipeTime")),
                20.0,
            ),
        ),
    })
    return value


def _clean_condition(clean: Mapping[str, Any]) -> Dict[str, Any]:
    """把一个通用 Clean 模板展开成标准 ICleanCondition。

    Dummy 系缺省窗口为 [DBL_MIN, 9999]，编辑器显式上下限仍是权威输入。
    ExecuteOrder 键序为 ThresholdValueList → PreJuge → StateVariableName → Alias。
    """
    clean = _runtime_clean(clean)
    alias = str(clean.get("name") or "").strip()
    state_variable = str(clean.get("stateVariable") or "IdleTime").strip() or "IdleTime"
    lower = _finite_number(clean.get("lower"), 0.0)
    upper = _finite_number(clean.get("upper"), DEFAULT_TRIGGER_UPPER)
    if upper < lower:
        raise ValueError(f"Clean {alias} 的触发上限小于下限")
    task = {
        "TaskName": str(clean.get("taskName") or alias).strip() or alias,
        "CleanRecipe": str(clean.get("recipeRef") or "").strip(),
        "UpdateStateVariables": _string_list(clean.get("updateStateVariables")),
        "MaterialCount": max(0, int(_finite_number(clean.get("materialCount"), 0))),
        "EmptyCleanRecipeAfterMaterial": str(clean.get("emptyRecipeRef") or "").strip(),
    }
    return {
        "CheckConditions": {alias: [task]},
        "ExecuteOrder": [{
            "ThresholdValueList": [lower, upper],
            "PreJuge": bool(clean.get("preJudge", False)),
            "StateVariableName": state_variable,
            "Alias": alias,
        }],
    }


def _clean_conditions(names: Any, clean_by_name: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """按引用名称复制 Clean 条件，引用不存在时立即报错。"""
    conditions: List[Dict[str, Any]] = []
    for name in _string_list(names):
        clean = clean_by_name.get(name)
        if clean is None:
            raise ValueError(f"Route 引用了不存在的 Clean：{name}")
        conditions.append(_clean_condition(clean))
    return conditions


def _clean_names_for_types(
    names: Any,
    clean_by_name: Mapping[str, Mapping[str, Any]],
    allowed_types: set[str],
) -> List[str]:
    """按清洁类别筛选引用，防止挂到不兼容的 Route 位置。"""
    result: List[str] = []
    for name in _string_list(names):
        clean = clean_by_name.get(name)
        if clean is None:
            raise ValueError(f"Route 引用了不存在的 Clean：{name}")
        if _clean_type(clean) in allowed_types:
            result.append(name)
    return result


def _clean_names_for_module(
    names: Any,
    clean_by_name: Mapping[str, Mapping[str, Any]],
    module: str,
) -> List[str]:
    """按 Clean 显式配置的适用腔室筛选引用。"""
    result: List[str] = []
    for name in _string_list(names):
        clean = clean_by_name.get(name)
        if clean is None:
            raise ValueError(f"Route 引用了不存在的 Clean：{name}")
        if module in _string_list(clean.get("modules")):
            result.append(name)
    return result


def _append_module_clean_conditions(
    table: Dict[str, List[Dict[str, Any]]],
    module: str,
    clean_names: Any,
    clean_by_name: Mapping[str, Mapping[str, Any]],
) -> None:
    """向指定腔室追加 Clean 条件并去重。"""
    if not module:
        return
    module_clean_names = _clean_names_for_module(
        clean_names,
        clean_by_name,
        module,
    )
    conditions = _clean_conditions(module_clean_names, clean_by_name)
    if not conditions:
        return
    target = table.setdefault(module, [])
    for condition in conditions:
        if condition not in target:
            target.append(condition)


def _dummy_accessible_pms(route: Mapping[str, Any]) -> List[str]:
    """按 Route 的带片清洁条件收集 Dummy 晶圆允许进入的腔室。"""
    modules: List[str] = []
    for module, conditions in (route.get("PrePJob") or {}).items():
        has_dummy_clean = any(
            int(_finite_number(task.get("MaterialCount"), 0)) > 0
            for condition in conditions or []
            if isinstance(condition, Mapping)
            for tasks in (condition.get("CheckConditions") or {}).values()
            for task in tasks or []
            if isinstance(task, Mapping)
        )
        module_name = str(module)
        if has_dummy_clean and module_name not in modules:
            modules.append(module_name)
    return modules


def _dummy_wafer_count(
    route: Mapping[str, Any],
    clean_by_name: Mapping[str, Mapping[str, Any]],
) -> int:
    """返回 Route 引用的 Dummy Clean 所配置的最大 DummyPort 库存数。"""
    counts = [
        max(1, int(_finite_number(
            clean_by_name[name].get("dummyWaferCount"),
            DEFAULT_DUMMY_WAFER_COUNT,
        )))
        for name in _string_list(route.get("prePJobCleanRefs"))
        if name in clean_by_name
        and _clean_type(clean_by_name[name]) in {"dummy", "dummywac"}
    ]
    return max(counts, default=0)


def _dummy_material(
    slot_id: int,
    dummy_port: str,
    accessible_pms: Sequence[str],
) -> Dict[str, Any]:
    """使用企业接口模板创建一片可复用的 DummyPort 库存晶圆。

    企业协议：Dummy ID 从 1000000 起连续编号，Name 为 ``dummy{序号}``（0 起），
    寿命上限默认 99999。
    """
    material_id = DUMMY_MATERIAL_ID_START + slot_id - FIRST_SLOT_ID
    return {
        "Name": f"dummy{slot_id - FIRST_SLOT_ID}",
        "AccessiblePM": list(accessible_pms),
        "TaskID": "",
        "FoupID": "",
        "ID": material_id,
        "LimitLevel1": DUMMY_MATERIAL_LIMIT_LEVEL,
        "LimitLevel2": DUMMY_MATERIAL_LIMIT_LEVEL,
        "Priority": -1,
        "StepID": 0,
        "LotID": "",
        "SlotID": slot_id,
        "NeedSchedule": True,
        "CurrentModuleName": dummy_port,
        "PJobName": "",
        "SrcPortName": dummy_port,
        "Usage": DUMMY_MATERIAL_USAGE_MIX,
        "Count": 0,
        "Route": {
            "Name": "",
            "RouteSteps": [],
            "BufferOption": -1,
            "BoundedStepIDs": [],
            "Group": "",
            "PrePJob": {},
            "PostPJob": {},
            "PostCJob": {},
        },
    }


def _integer_list(value: Any) -> List[int]:
    """把数组或逗号文本解析为去重整数列表。"""
    result: List[int] = []
    for item in _string_list(value):
        try:
            number = int(item)
        except ValueError:
            raise ValueError(f"无法解析整数列表项：{item}") from None
        if number not in result:
            result.append(number)
    return result


def _stage_visit_rows(stage: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """读取新 IVisit 编辑结构，并兼容旧版 Step 聚合字段。"""
    raw_visits = stage.get("visits")
    if isinstance(raw_visits, Sequence) and not isinstance(raw_visits, (str, bytes)):
        return [copy_payload(dict(visit)) for visit in raw_visits if isinstance(visit, Mapping)]
    return [{
        "stationName": station,
        "slotIds": stage.get("slots"),
        "processRecipe": stage.get("recipeRef"),
        "processTime": stage.get("processTime", stage.get("recipeTime", 0.0)),
        "recipeTime": stage.get("recipeTime", stage.get("processTime", 0.0)),
        "moveTimeOffset": {},
        "qTimeLimit": stage.get("qTime"),
        "residencyConstraint": stage.get("residency"),
        "afterCleanRefs": stage.get("afterCleanRefs"),
        "beforeCleanRefs": stage.get("beforeCleanRefs"),
    } for station in _string_list(stage.get("stations"))]


def _clean_target_modules(
    clean_names: Any,
    clean_by_name: Mapping[str, Mapping[str, Any]],
    recipe_by_name: Mapping[str, Mapping[str, Any]],
) -> List[str]:
    """读取 Clean 显式配置的 Route 顶层清洁目标。"""
    targets: List[str] = []
    for clean_name in _string_list(clean_names):
        clean = clean_by_name.get(clean_name)
        if clean is None:
            raise ValueError(f"Route 引用了不存在的 Clean：{clean_name}")
        recipe_name = str(clean.get("recipeRef") or "").strip()
        recipe = recipe_by_name.get(recipe_name)
        modules = _string_list(clean.get("modules"))
        if not modules and recipe:
            modules = _string_list(recipe.get("modules"))
        for module in modules:
            if module not in targets:
                targets.append(module)
    return targets


def _route_clean_dict(
    clean_names: Any,
    clean_by_name: Mapping[str, Mapping[str, Any]],
    recipe_by_name: Mapping[str, Mapping[str, Any]],
    allowed_types: Optional[set[str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """把 Route 顶层 Clean 引用展开为 PM 到条件列表的标准字典。"""
    if allowed_types is not None:
        clean_names = _clean_names_for_types(
            clean_names,
            clean_by_name,
            allowed_types,
        )
    conditions = _clean_conditions(clean_names, clean_by_name)
    return {
        module: copy_payload(conditions)
        for module in _clean_target_modules(clean_names, clean_by_name, recipe_by_name)
    }


def build_route(
    route: Mapping[str, Any],
    recipe_by_name: Mapping[str, Mapping[str, Any]],
    clean_by_name: Mapping[str, Mapping[str, Any]],
    robot_names: Optional[set[str]] = None,
    station_slots: Optional[Mapping[str, List[int]]] = None,
) -> Dict[str, Any]:
    """把控制台 Route 展开成解析器接受的标准 Route。"""
    route_name = str(route.get("name") or "").strip()
    stages = [stage for stage in (route.get("stages") or []) if isinstance(stage, Mapping)]
    if len(stages) < 2:
        raise ValueError(f"Route {route_name} 至少需要源和汇两个 Step")
    if len(stages) < 3 or len(stages) % 2 == 0:
        raise ValueError(f"Route {route_name} 的 Step 数必须是大于等于 3 的奇数")
    pre_pjob = _route_clean_dict(
        route.get("prePJobCleanRefs"),
        clean_by_name,
        recipe_by_name,
        {"preclean", "dummy", "dummywac"},
    )
    post_pjob = _route_clean_dict(
        route.get("postPJobCleanRefs"),
        clean_by_name,
        recipe_by_name,
        {"postclean"},
    )
    post_cjob = _route_clean_dict(
        route.get("postCJobCleanRefs"),
        clean_by_name,
        recipe_by_name,
        {"postclean"},
    )
    route_steps: List[Dict[str, Any]] = []
    for index, stage in enumerate(stages):
        has_product_recipe = False
        visit_rows = _stage_visit_rows(stage)
        stations = [
            str(visit.get("stationName") or visit.get("StationName") or "").strip()
            for visit in visit_rows
        ]
        if not stations or any(not station for station in stations):
            raise ValueError(f"Route {route_name} 的 Step {index} 没有完整 Visit StationName")
        if robot_names is not None:
            contains_robot = [station in robot_names for station in stations]
            expected_robot = index % 2 == 1
            if any(contains_robot) != all(contains_robot):
                raise ValueError(f"Route {route_name} 的 Step {index} 混用了 Robot 和 Station")
            if bool(contains_robot[0]) != expected_robot:
                expected = "Robot" if expected_robot else "Station"
                raise ValueError(f"Route {route_name} 的 Step {index} 必须填写 {expected}")
        visits: List[Dict[str, Any]] = []
        # 提前计算 StepID，供 Visit 的企业格式 ProcessRecipe 派生使用。
        step_id = int(_finite_number(stage.get("stepId", stage.get("StepID")), index))
        for visit_index, (station, visit) in enumerate(zip(stations, visit_rows)):
            recipe_name = str(visit.get("processRecipe") or visit.get("ProcessRecipe") or "").strip()
            if index % 2 == 1 and recipe_name:
                raise ValueError(f"Route {route_name} 的 Robot Step {index} Visit 不能引用 Recipe")
            if recipe_name and recipe_name not in recipe_by_name:
                raise ValueError(f"Route {route_name} Visit 引用了不存在的 Recipe：{recipe_name}")
            has_visit_recipe = bool(recipe_name)
            has_product_recipe = has_product_recipe or has_visit_recipe
            # 零时长产品工艺不需要下发 Recipe；保留 NeedProcess 以表达该 Route
            # Step 仍是一个 PM 工艺步骤，并让算法按即时加工处理。
            if (
                recipe_name
                and _finite_number(recipe_by_name[recipe_name].get("time"), 0.0)
                <= 0.0
            ):
                recipe_name = ""
            move_offsets = visit.get("moveTimeOffset") or visit.get("MoveTimeOffset") or {}
            if isinstance(move_offsets, str):
                try:
                    move_offsets = json.loads(move_offsets or "{}")
                except json.JSONDecodeError:
                    raise ValueError(
                        f"Route {route_name} Step {index} Visit {visit_index} 的 MoveTimeOffset 不是有效 JSON"
                    ) from None
            if not isinstance(move_offsets, Mapping):
                raise ValueError(f"Route {route_name} Step {index} Visit {visit_index} 的 MoveTimeOffset 必须是对象")
            before_names = visit.get("beforeCleanRefs") or visit.get("BeforeInPM")
            after_names = visit.get("afterCleanRefs") or visit.get("AfterOutPM")
            before_pjob_names = _clean_names_for_types(
                before_names,
                clean_by_name,
                {"preclean", "dummy", "dummywac"},
            )
            after_pjob_names = _clean_names_for_types(
                after_names,
                clean_by_name,
                {"postclean"},
            )
            after_wac_names = _clean_names_for_types(
                after_names,
                clean_by_name,
                {"wacclean"},
            )
            after_wac_names = _clean_names_for_module(
                after_wac_names,
                clean_by_name,
                station,
            )
            if has_visit_recipe:
                _append_module_clean_conditions(
                    pre_pjob,
                    station,
                    before_pjob_names,
                    clean_by_name,
                )
                _append_module_clean_conditions(
                    post_pjob,
                    station,
                    after_pjob_names,
                    clean_by_name,
                )
            slot_id = _slot_list(visit.get("slotIds") or visit.get("SlotID"))
            if station_slots and str(station) in station_slots and sorted(slot_id) == [FIRST_SLOT_ID]:
                slot_id = list(station_slots[str(station)])
            # 企业协议：加工 Visit 的 ProcessRecipe 按 ``/Process/{Route名}_{StepID}``
            # 派生命名（同一 Step 的多 PM Visit 共用同一 Recipe 名），编辑器自定义
            # recipe 名仅用于本地校验与时长匹配。
            enterprise_recipe = (
                f"/Process/{route_name}_{step_id}" if recipe_name else ""
            )
            visits.append({
                "StationName": station,
                "MoveTimeOffset": copy_payload(dict(move_offsets)),
                "SlotID": slot_id,
                "ProcessRecipe": enterprise_recipe,
                "AfterOutPM": _clean_conditions(after_wac_names, clean_by_name),
                "BeforeInPM": [],
                "ResidencyConstraint": _integer_or_float(
                    _finite_number(visit.get("residencyConstraint", visit.get("ResidencyConstraint")), -1)
                ),
                "QTimeLimit": _integer_or_float(
                    _finite_number(visit.get("qTimeLimit", visit.get("QTimeLimit")), -1)
                ),
            })
        explicit_post = stage.get("postStepIds", stage.get("PostStepID"))
        post_step_ids = (
            _integer_list(explicit_post)
            if explicit_post is not None
            else ([index + 1] if index + 1 < len(stages) else [])
        )
        declared_need_process = stage.get(
            "needProcess",
            stage.get("NeedProcess"),
        )
        need_process = (
            bool(declared_need_process)
            if declared_need_process is not None
            else any(visit["ProcessRecipe"] for visit in visits)
            or has_product_recipe
        )
        # Aligner 必须以 NeedProcess 标识，供标准算法和 HongYe 按 AlignMove
        # 而非普通中转站处理。该规则优先于编辑器中可能遗留的 false。
        if any(_is_aligner_station(visit["StationName"]) for visit in visits):
            need_process = True
        route_steps.append({
            "StepID": step_id,
            "PostStepID": post_step_ids,
            "NeedProcess": need_process,
            "Visits": visits,
        })
    step_ids = [step["StepID"] for step in route_steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError(f"Route {route_name} 的 StepID 重复")
    unknown_posts = sorted({post for step in route_steps for post in step["PostStepID"] if post not in step_ids})
    if unknown_posts:
        raise ValueError(f"Route {route_name} 的 PostStepID 不存在：{unknown_posts}")
    raw_buffer_option = route.get("bufferOption", 0)
    try:
        numeric_buffer_option = float(raw_buffer_option)
    except (TypeError, ValueError):
        raise ValueError(
            f"Route {route_name} 的 BufferOption 必须是 0~4 的整数"
        ) from None
    buffer_option = int(numeric_buffer_option)
    if (
        not math.isfinite(numeric_buffer_option)
        or numeric_buffer_option != buffer_option
        or not 0 <= buffer_option <= 4
    ):
        raise ValueError(f"Route {route_name} 的 BufferOption 必须是 0~4 的整数")
    return {
        "Name": route_name,
        "RouteSteps": route_steps,
        "BufferOption": buffer_option,
        "BoundedStepIDs": [],
        "Group": str(route.get("group") or route_name).strip() or route_name,
        "PrePJob": pre_pjob,
        "PostPJob": post_pjob,
        "PostCJob": post_cjob,
    }


# 企业产品行 Weight 中清洁计数器的固定键序（企业日志实证：ProcessCount →
# DummyCount）；未列出的变量按名称序追加。
_WEIGHT_COUNTER_ORDER = ("ProcessCount", "DummyCount")


def build_process_recipes(
    recipes: Sequence[Mapping[str, Any]],
    routes: Sequence[Mapping[str, Any]],
    cleans: Sequence[Mapping[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """展开标准 IProcessRecipe 列表（企业格式）。

    企业协议：
      - 产品 Recipe 名由 Route 名与 StepID 派生（``/Process/{Route名}_{StepID}``，
        与 Visit.ProcessRecipe 一致），时长取 Visit 合并后的 recipeTime/processTime；
      - Clean Recipe 名按 cleanType 派生（``/PreJobDummy`` 等），dummywac 额外派生
        空腔阶段的 WAC 行；
      - Weight 中清洁引用的计数器（ProcessCount/DummyCount）补齐为 1.0，编辑器
        显式权重优先，绝不覆盖；计数器键序 ProcessCount → DummyCount（企业实证）；
      - 行序按模块交错：同一模块的产品行后紧跟其清洗行，模块顺序按产品行
        （Route 加工 Visit）首现序，仅出现在清洗里的模块按清洗序追加。
    """
    # 同名 Recipe 允许为不同模块定义不同参数：按 (名称, 模块) 精确匹配，
    # 无命中时回退到该名称的首条配置（与 _recipe_index 的首行语义一致）。
    recipe_rows_by_name: Dict[str, List[Dict[str, Any]]] = {}
    for recipe in recipes:
        name = str(recipe.get("name") or "").strip()
        if name:
            recipe_rows_by_name.setdefault(name, []).append(copy_payload(dict(recipe)))

    def recipe_config(name: str, module: str) -> Mapping[str, Any]:
        """返回模块命中的同名 Recipe 配置，未命中时回退首条。"""
        rows = recipe_rows_by_name.get(name) or []
        for row in rows:
            if module in _string_list(row.get("modules")):
                return row
        return rows[0] if rows else {}

    clean_by_name = {
        str(clean.get("name") or "").strip(): _runtime_clean(clean)
        for clean in cleans
        if str(clean.get("name") or "").strip()
    }
    # (ModuleName, Name) 是协议唯一键；同内容可合并，冲突内容不能静默取首行。
    product_rows: List[Dict[str, Any]] = []
    clean_rows: List[Dict[str, Any]] = []
    collected: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def emit(
        target: List[Dict[str, Any]],
        module_name: str,
        name: str,
        time: float,
        process_type: str,
        weight: Mapping[str, Any],
    ) -> None:
        """按企业键序输出配方；同键异义时拒绝，避免悄悄改变清洗时长或权重。"""
        key = (module_name, name)
        if not module_name or not name:
            return
        module_weight = {
            str(variable): float(value)
            for variable, value in dict(weight or {}).items()
        }
        row = {
            "ModuleName": module_name,
            "Name": name,
            "ProcessType": str(process_type or ""),
            "Time": max(0.0, _finite_number(time, 0.0)),
            "Weight": module_weight,
        }
        if key in collected:
            if collected[key] != row:
                raise ValueError(f"同一模块的配方定义冲突：ModuleName={module_name},Name={name}")
            return
        collected[key] = row
        target.append(row)

    # 产品 Recipe：按 Route 加工 Visit 派生，编辑器 Recipe 配置仅提供显式
    # processType/weight 与缺失时长的兜底。
    for route in routes:
        route_name = str(route.get("name") or "").strip()
        for index, stage in enumerate(route.get("stages") or []):
            if not isinstance(stage, Mapping):
                continue
            step_id = int(_finite_number(stage.get("stepId", stage.get("StepID")), index))
            visit_rows = _stage_visit_rows(stage)
            for visit in visit_rows:
                editor_recipe = str(
                    visit.get("processRecipe") or visit.get("ProcessRecipe") or ""
                ).strip()
                module = str(
                    visit.get("stationName") or visit.get("StationName") or ""
                ).strip()
                if not editor_recipe or not module:
                    continue
                config = recipe_config(editor_recipe, module)
                # 时长优先级：新版 Visit 显式时长 → 旧版 Step 聚合时长 →
                # Recipe 配置兜底。旧版聚合行会补 0.0 默认值，必须回读
                # 原始 Step 才能区分“未配置”与“显式 0 秒”。
                stage_visits = stage.get("visits")
                explicit_visits = isinstance(
                    stage_visits, Sequence
                ) and not isinstance(stage_visits, (str, bytes))
                declared_time = (
                    visit.get("recipeTime", visit.get("processTime"))
                    if explicit_visits
                    else stage.get("recipeTime", stage.get("processTime"))
                )
                time = _finite_number(declared_time, config.get("time", 0.0))
                weight = visit.get("weight")
                if weight is None or (isinstance(weight, Mapping) and not weight):
                    weight = config.get("weight") or {}
                if isinstance(weight, str):
                    try:
                        weight = json.loads(weight or "{}")
                    except json.JSONDecodeError:
                        raise ValueError(
                            f"Route {route_name} Step {step_id} 的 Weight 不是有效 JSON"
                        ) from None
                if not isinstance(weight, Mapping):
                    raise ValueError(
                        f"Route {route_name} Step {step_id} 的 Weight 必须是对象"
                    )
                # 清洁引用的计数器补齐：按该模块挂载的清洁更新变量推导
                # （含 Visit 级 before/after 与 Route 级 Pre/PostPJob 引用）。
                required_variables = _recipe_required_weight_variables(
                    module, visit, route, clean_by_name,
                )
                module_weight = dict(weight)
                # 企业协议：补齐计数器按固定键序（ProcessCount → DummyCount），
                # 其余未知变量按名称序追加，保证行间键序稳定。
                for variable_name in sorted(
                    required_variables,
                    key=lambda name: (
                        _WEIGHT_COUNTER_ORDER.index(name)
                        if name in _WEIGHT_COUNTER_ORDER
                        else len(_WEIGHT_COUNTER_ORDER),
                        name,
                    ),
                ):
                    module_weight.setdefault(variable_name, 1)
                emit(
                    product_rows,
                    module,
                    f"/Process/{route_name}_{step_id}",
                    time,
                    visit.get("processType") or config.get("processType") or "",
                    module_weight,
                )

    # Clean Recipe：按 cleanType 派生固定名；dummywac 额外输出空腔 WAC 行。
    for clean in clean_by_name.values():
        clean_type = str(clean.get("cleanType") or "").strip()
        modules = _string_list(clean.get("modules"))
        if not modules:
            continue
        clean_weight: Dict[str, Any] = {}
        for module in modules:
            emit(
                clean_rows,
                module,
                str(clean.get("recipeRef") or ""),
                clean.get("recipeTime"),
                "",
                clean_weight,
            )
        if clean_type == "dummywac":
            wac_time = _finite_number(clean.get("wacRecipeTime"), 0.0)
            for module in modules:
                emit(clean_rows, module, str(clean.get("emptyRecipeRef") or ""), wac_time, "", {})
    # 企业协议：行序按模块交错——同一模块的产品行后紧跟其清洗行（含
    # dummywac 空腔 WAC 行）；模块顺序取产品行（Route 加工 Visit）首现序，
    # 仅出现在清洗里的模块按清洗行序追加。
    module_order: List[str] = []
    for row in product_rows:
        if row["ModuleName"] not in module_order:
            module_order.append(row["ModuleName"])
    for row in clean_rows:
        if row["ModuleName"] not in module_order:
            module_order.append(row["ModuleName"])
    merged: List[Dict[str, Any]] = []
    for module in module_order:
        merged.extend(row for row in product_rows if row["ModuleName"] == module)
        merged.extend(row for row in clean_rows if row["ModuleName"] == module)
    return merged


def _recipe_required_weight_variables(
    module: str,
    visit: Mapping[str, Any],
    route: Mapping[str, Any],
    clean_by_name: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """返回产品 Recipe 需要在 Weight 中补齐的清洁计数器变量集合。

    IdleTime 由设备空闲时钟维护，不属于产品 Recipe 的增量；其余更新变量
    （ProcessCount/DummyCount 等）必须进入 Weight 才能在工艺完成时推进
    CleanCondition 引用的计数器。挂载来源同时覆盖 Visit 级 before/after 与
    Route 级 Pre/PostPJob 引用（Dummy 带片清洁挂在 Route 级）。
    """
    required: set[str] = set()
    clean_names = [
        *_string_list(visit.get("beforeCleanRefs") or visit.get("BeforeInPM")),
        *_string_list(visit.get("afterCleanRefs") or visit.get("AfterOutPM")),
        *_string_list(route.get("prePJobCleanRefs")),
        *_string_list(route.get("postPJobCleanRefs")),
    ]
    for clean_name in clean_names:
        clean = clean_by_name.get(clean_name)
        if clean is None or module not in _string_list(clean.get("modules")):
            continue
        for variable_name in _string_list(clean.get("updateStateVariables")):
            if variable_name and variable_name != "IdleTime":
                required.add(variable_name)
    return required


def _load_port_capacity(tool_topo: Mapping[str, Any], name: str) -> int:
    """读取所选 LoadPort 容量，接口缺失时使用 25。"""
    station = (tool_topo.get("Stations") or {}).get(name) or {}
    try:
        return max(1, int(station.get("Capacity") or MAX_WAFERS_PER_JOB))
    except (TypeError, ValueError):
        return MAX_WAFERS_PER_JOB


def _integer_or_float(value: float) -> float:
    """整数数值归一化为 int（企业协议的 -1 等默认值以整数下发）。"""
    if float(value).is_integer():
        return int(value)
    return float(value)


def _enum_value(raw: Any, values: Mapping[str, int], field_name: str, default: str) -> int:
    """把页面枚举名称或兼容的整数值转换成后端枚举。"""
    if raw is None or raw == "":
        return values[default]
    if isinstance(raw, str) and raw in values:
        return values[raw]
    try:
        numeric = int(raw)
    except (TypeError, ValueError):
        numeric = -999
    if numeric in values.values():
        return numeric
    raise ValueError(f"{field_name} 不支持：{raw}")


def _round_cjob_rows(round_config: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """读取新 CJob/PJob 层级，并把旧版扁平 Job 兼容为一 Job 一 CJob。

    兼容 CJob 的命名与新版一致走 ``{TaskID}.PJOB_{全局序号}`` 企业协议，
    仅保留单 PJob 结构（jobName 固定 P1）。
    """
    raw_cjobs = round_config.get("cjobs")
    if isinstance(raw_cjobs, Sequence) and not isinstance(raw_cjobs, (str, bytes)):
        return [copy_payload(dict(row)) for row in raw_cjobs if isinstance(row, Mapping)]
    rows: List[Dict[str, Any]] = []
    for index, job in enumerate(round_config.get("jobs") or [], start=1):
        if not isinstance(job, Mapping):
            continue
        name = str(job.get("name") or f"Job{index}").strip()
        pjob = copy_payload(dict(job))
        pjob["jobName"] = "P1"
        rows.append({
            "taskId": name,
            "jobType": job.get("jobType", 0),
            "priority": job.get("priority", 1),
            "taskMode": job.get("taskMode", 0),
            "pjobs": [pjob],
        })
    return rows


def _round_pjob_count(round_config: Mapping[str, Any]) -> int:
    """返回一轮内的 PJob 数量，兼容旧版 jobs。"""
    cjobs = round_config.get("cjobs")
    if isinstance(cjobs, Sequence) and not isinstance(cjobs, (str, bytes)):
        return sum(len(cjob.get("pjobs") or []) for cjob in cjobs if isinstance(cjob, Mapping))
    return len([job for job in (round_config.get("jobs") or []) if isinstance(job, Mapping)])


def _instantiate_load_port_slots(route: Mapping[str, Any], slot_id: int) -> None:
    """把 Material 内嵌 Route 的首/末 LoadPort 步骤槽位实例化为晶圆所在槽位。

    标准 update 中 ``Material.Route`` 是实例化 Route：首站/末站（LoadPort）的
    ``Visits.SlotID`` 必须等于该晶圆当前所在槽位，而不是 Route 模板展开的站点
    全部槽位。``ProcessJob.OriginRoute`` 仍是模板，保持全量槽位不变；只有随
    Material 下发的实例化 Route 需要逐片改写。
    """
    route_steps = route.get("RouteSteps") or []
    for step_index in (0, len(route_steps) - 1):
        if 0 <= step_index < len(route_steps):
            for visit in route_steps[step_index].get("Visits") or []:
                if isinstance(visit, dict):
                    visit["SlotID"] = [int(slot_id)]


def _rekey_pjob_clean_conditions(
    route: Dict[str, Any],
    task_id: str,
    pjob_name: str,
) -> None:
    """按企业协议把实例化 Route 的清洗条件键与 Alias 绑定到当前 PJob。

    企业条件名为 ``{TaskID}_{PJobName}_{模块}_{TaskName}_CounterCondition``；
    同一 Route 模板被多个 PJob 引用时，每个 PJob 的实例化 Route（Material.Route
    与 ProcessJob.OriginRoute）携带各自的条件键。TaskName 取条件首个任务的
    任务名；同一 PJob 内同键（同模块同 TaskName 的多条清洁）追加序号去重。
    """
    used: set[str] = set()

    def condition_task_name(condition: Mapping[str, Any]) -> str:
        for tasks in (condition.get("CheckConditions") or {}).values():
            for task in tasks or []:
                task_name = str((task or {}).get("TaskName") or "").strip()
                if task_name:
                    return task_name
        return ""

    def rename_condition(condition: Mapping[str, Any], module: str) -> Dict[str, Any]:
        task_name = condition_task_name(condition)
        base = (
            f"{task_id}_{pjob_name}_{module}_{task_name}_CounterCondition"
            if task_name
            else f"{task_id}_{pjob_name}_{module}_CounterCondition"
        )
        final = base
        serial = 2
        while final in used:
            final = f"{base}_{serial}"
            serial += 1
        used.add(final)
        # 企业条件键下是任务列表：合并原 CheckConditions 各键的任务行。
        task_rows: List[Any] = []
        for tasks in (condition.get("CheckConditions") or {}).values():
            for task in tasks or []:
                task_rows.append(copy_payload(dict(task)) if isinstance(task, Mapping) else task)
        return {
            "CheckConditions": {final: task_rows},
            "ExecuteOrder": [
                {**copy_payload(dict(order)), "Alias": final}
                for order in (condition.get("ExecuteOrder") or [])
                if isinstance(order, Mapping)
            ],
        }

    def rename_conditions(conditions: Any, module: str) -> Any:
        if not isinstance(conditions, list):
            return conditions
        return [
            rename_condition(condition, module)
            if isinstance(condition, Mapping) else condition
            for condition in conditions
        ]

    for field in ("PrePJob", "PostPJob", "PostCJob"):
        table = route.get(field)
        if not isinstance(table, dict):
            continue
        for module, conditions in list(table.items()):
            table[module] = rename_conditions(conditions, str(module))
    for step in route.get("RouteSteps") or []:
        if not isinstance(step, dict):
            continue
        for visit in step.get("Visits") or []:
            if isinstance(visit, dict):
                visit["AfterOutPM"] = rename_conditions(
                    visit.get("AfterOutPM"),
                    str(visit.get("StationName") or ""),
                )


def build_round_update(
    plan: Mapping[str, Any],
    round_config: Mapping[str, Any],
    current_time: float,
    build_state: BuildState,
) -> Dict[str, Any]:
    """把一轮 ``CJob → PJob`` 展开成标准 IUpdateParams。"""
    recipes = [row for row in (plan.get("recipes") or []) if isinstance(row, Mapping)]
    cleans = [
        _runtime_clean(row)
        for row in (plan.get("cleans") or [])
        if isinstance(row, Mapping)
    ]
    routes = [row for row in (plan.get("routes") or []) if isinstance(row, Mapping)]
    recipe_by_name = _recipe_index(recipes)
    clean_by_name = _name_index(cleans, "Clean")
    route_by_name = _name_index(routes, "Route")
    tool_topo = plan["device"]
    robot_names = {str(name) for name in (tool_topo.get("Robots") or {})}
    station_slots_map = {
        str(station_name): _station_slot_ids(station_data)
        for station_name, station_data in (tool_topo.get("Stations") or {}).items()
        if isinstance(station_data, Mapping)
    }
    station_slots_map.update({
        str(robot_name): _robot_slot_ids(robot_data)
        for robot_name, robot_data in (tool_topo.get("Robots") or {}).items()
        if isinstance(robot_data, Mapping)
    })
    built_routes = {
        name: build_route(route, recipe_by_name, clean_by_name, robot_names, station_slots_map)
        for name, route in route_by_name.items()
    }
    dummy_wafer_count_by_route = {
        name: _dummy_wafer_count(route, clean_by_name)
        for name, route in route_by_name.items()
    }
    dummy_accessible_pms: List[str] = []
    for route in built_routes.values():
        for module in _dummy_accessible_pms(route):
            if module not in dummy_accessible_pms:
                dummy_accessible_pms.append(module)
    cjobs = _round_cjob_rows(round_config)
    if not cjobs:
        raise ValueError("每一轮至少需要一个 CJob")
    if len(cjobs) > 1 and any(
        _enum_value(
            cjob.get("taskMode", cjob.get("TaskMode")),
            TASK_MODE_VALUES,
            "TaskMode",
            "Smart",
        ) in {TASK_MODE_VALUES["Pipeline"], TASK_MODE_VALUES["Sequential"]}
        for cjob in cjobs
    ):
        raise ValueError("Pipeline/Sequential 每轮只能配置一个 ControlJob")

    materials: List[Dict[str, Any]] = []
    process_jobs: List[Dict[str, Any]] = []
    control_jobs: List[Dict[str, Any]] = []
    round_uses_dummy_material = False
    round_dummy_wafer_count = 0
    used_control_load_ports: Dict[str, str] = {}
    for cjob_index, cjob in enumerate(cjobs, start=1):
        task_id = str(cjob.get("taskId") or cjob.get("TaskID") or cjob_index).strip()
        if task_id in build_state.task_ids:
            raise ValueError(f"ControlJob TaskID 跨轮或同轮重复：{task_id}")
        build_state.task_ids.add(task_id)
        pjobs = [row for row in (cjob.get("pjobs") or []) if isinstance(row, Mapping)]
        if not pjobs:
            raise ValueError(f"CJob {cjob_index} 至少需要一个 PJob")
        job_type = _enum_value(cjob.get("jobType", cjob.get("JobType")), CJOB_TYPE_VALUES, "JobType", "NormalLot")
        task_mode = _enum_value(cjob.get("taskMode", cjob.get("TaskMode")), TASK_MODE_VALUES, "TaskMode", "Smart")
        cjob_priority = max(1, int(_finite_number(cjob.get("priority"), 1))) if job_type == 0 else -1
        runtime_pjob_names: List[str] = []
        cjob_load_port = str(
            cjob.get("loadPort")
            or cjob.get("LoadPort")
            or ""
        ).strip()
        if cjob_load_port:
            load_port_definition = (tool_topo.get("Stations") or {}).get(cjob_load_port)
            if not isinstance(load_port_definition, Mapping):
                raise ValueError(f"CJob {cjob_index} 的 LoadPort 不存在：{cjob_load_port}")
            if str(load_port_definition.get("Type") or "").strip().lower() != "loadport":
                raise ValueError(f"CJob {cjob_index} 选择的站点不是 LoadPort：{cjob_load_port}")
        # 企业命名协议：CJob 全局序号 n 的第 k 个 PJob 为 ``{TaskID}.PJOB_n``
        # （k=1）或 ``{TaskID}.PJOB_n_{k}``（k≥2）；CJobCycle 补片 PJob 由运行
        # 侧携带原始基础名 ``_basePJobName``，在末尾追加补片 TaskID。
        cjob_serial = build_state.next_cjob_index
        build_state.next_cjob_index += 1
        for pjob_index, pjob in enumerate(pjobs, start=1):
            display_name = str(pjob.get("jobName") or pjob.get("name") or f"P{pjob_index}").strip()
            base_pjob_name = str(pjob.get("_basePJobName") or "").strip()
            if base_pjob_name:
                pjob_name = f"{base_pjob_name}_{task_id}"
            else:
                pjob_name = (
                    f"{task_id}.PJOB_{cjob_serial}"
                    if pjob_index == 1
                    else f"{task_id}.PJOB_{cjob_serial}_{pjob_index}"
                )
            if pjob_name in build_state.job_names:
                raise ValueError(f"PJob 名称跨轮重复：{pjob_name}")
            build_state.job_names.add(pjob_name)
            runtime_pjob_names.append(pjob_name)
            origin_route = pjob.get("originRoute")
            if isinstance(origin_route, Mapping):
                origin_route = origin_route.get("name") or origin_route.get("Name")
            route_name = str(pjob.get("routeRef") or origin_route or "").strip()
            route = built_routes.get(route_name)
            if route is None:
                raise ValueError(f"PJob {display_name} 引用了不存在的 Route：{route_name}")
            pjob_load_port = str(
                pjob.get("loadPort")
                or pjob.get("LoadPort")
                or ""
            ).strip()
            load_port = cjob_load_port or pjob_load_port
            if load_port not in (tool_topo.get("Stations") or {}):
                raise ValueError(f"PJob {display_name} 的 LoadPort 不存在：{load_port}")
            if cjob_load_port and pjob_load_port and pjob_load_port != cjob_load_port:
                raise ValueError(
                    f"ControlJob TaskID={task_id} 的 PJob 必须使用同一个 LoadPort："
                    f"{cjob_load_port}、{pjob_load_port}"
                )
            cjob_load_port = load_port
            wafer_count = int(_finite_number(pjob.get("waferCount"), len(pjob.get("matList") or []) or 1))
            if wafer_count < 1 or wafer_count > MAX_WAFERS_PER_JOB:
                raise ValueError(f"PJob {display_name} 晶圆数必须为 1~{MAX_WAFERS_PER_JOB}")
            priority = max(1, int(_finite_number(pjob.get("priority"), 1)))
            # 企业协议：Material.Route.Name 与 ProcessJob.OriginRoute.Name 保持
            # Route 配置名；不再追加 PJob 实例后缀（企业多 PJob 共享同名 Route）。
            runtime_route = copy_payload(route)
            round_uses_dummy_material = (
                round_uses_dummy_material
                or bool(_dummy_accessible_pms(runtime_route))
            )
            round_dummy_wafer_count = max(
                round_dummy_wafer_count,
                dummy_wafer_count_by_route.get(route_name, 0),
            )
            runtime_route["Name"] = route_name
            # 首次实例化时将清洗条件键/Alias 绑定到对应 PJob。
            # CJobCycle 复制原工艺条件定义；实例 Job/Task 更新，不重新命名原条件。
            # 这样 Alias 仍可对应企业保留的同一份清洗模板。
            _rekey_pjob_clean_conditions(
                runtime_route,
                base_pjob_name.split(".", 1)[0] if base_pjob_name else task_id,
                base_pjob_name or pjob_name,
            )
            route_steps = runtime_route.get("RouteSteps") or []
            for step_index in (0, len(route_steps) - 1):
                if 0 <= step_index < len(route_steps):
                    for visit in route_steps[step_index].get("Visits") or []:
                        visit["StationName"] = load_port
            material_ids: List[int] = []
            next_slot = build_state.next_slot_by_port.get(load_port, 0)
            capacity = _load_port_capacity(tool_topo, load_port)
            if next_slot + wafer_count > capacity:
                raise ValueError(f"{load_port} 总片数超过容量 {capacity}")
            for _ in range(wafer_count):
                next_slot += 1
                material_id = build_state.next_material_id
                build_state.next_material_id += 1
                material_ids.append(material_id)
                material_route = copy_payload(runtime_route)
                # Material 内嵌 Route 是实例化 Route：首/末 LoadPort 步骤的
                # Visit 槽位必须指向该晶圆所在槽位，而不是模板展开的全部槽位。
                _instantiate_load_port_slots(material_route, next_slot)
                # 企业协议（企业日志 schedule/schedule2 共 12 个 PJob 实证）：
                # FoupID 取 PJobName 第一段原始 CJob 序号（补片轮沿用原始基础名，
                # 与当前 TaskID 无关），LotID 为该序号串重复 PJob 序号次
                # （P1→"1"、P2→"11"）。
                origin_cjob_serial = pjob_name.split(".", 1)[0]
                materials.append({
                    "Name": (
                        f"{next_slot - FIRST_SLOT_ID}_{pjob_name}"
                        if base_pjob_name else str(material_id)
                    ), "ID": material_id, "TaskID": task_id,
                    "LotID": origin_cjob_serial * pjob_index,
                    "FoupID": origin_cjob_serial,
                    "Priority": priority, "StepID": 0, "CurrentModuleName": load_port,
                    "SlotID": next_slot, "NeedSchedule": True, "PJobName": pjob_name,
                    "SrcPortName": load_port, "Route": material_route,
                })
            build_state.next_slot_by_port[load_port] = next_slot
            # 企业键序：JobName → OriginRoute → TaskID → Priority → MatList → State。
            process_jobs.append({
                "JobName": pjob_name, "OriginRoute": copy_payload(runtime_route),
                "TaskID": task_id, "Priority": priority,
                "MatList": material_ids, "State": 0,
            })

        if cjob_load_port in used_control_load_ports:
            raise ValueError(
                f"不同 ControlJob 不能使用同一个 LoadPort：{cjob_load_port} "
                f"(TaskID={used_control_load_ports[cjob_load_port]}、{task_id})"
            )
        used_control_load_ports[cjob_load_port] = task_id
        # 企业协议：ControlJob 不携带 MaterialCount（算法侧按 PJob MatList 重算），
        # 键序为 TaskID → JobType → TaskMode → PJobNameList → Priority。
        control_jobs.append({
            "TaskID": task_id, "JobType": job_type, "TaskMode": task_mode,
            "PJobNameList": runtime_pjob_names, "Priority": cjob_priority,
        })
    # DummyPort 库存与 Clean 的 MaterialCount 独立，采用当前轮所引用的页面配置。
    dummy_material_count = round_dummy_wafer_count
    if round_uses_dummy_material and build_state.dummy_material_count < dummy_material_count:
        dummy_port = next(
            (
                str(name)
                for name, station in (tool_topo.get("Stations") or {}).items()
                if str((station or {}).get("Type") or "").lower() == "dummyport"
            ),
            "",
        )
        if not dummy_port:
            raise ValueError("Dummy / Dummy WAC 清洁需要设备配置 DummyPort")
        capacity = _load_port_capacity(tool_topo, dummy_port)
        if dummy_material_count > capacity:
            raise ValueError(
                f"DummyPort 容量不足：需要 {dummy_material_count} 片，容量 {capacity}"
            )
        for slot_id in range(
            build_state.dummy_material_count + FIRST_SLOT_ID,
            dummy_material_count + FIRST_SLOT_ID,
        ):
            materials.append(_dummy_material(slot_id, dummy_port, dummy_accessible_pms))
        build_state.dummy_material_count = dummy_material_count
    # 企业协议 update 键序：Robots → Stations → Materials → ProcessJobs →
    # ControlJobs → RemoveList → MoveStates → ProcessRecipes → CurrentTime；
    # 不携带 Scenario 与 InitialMoveID。标准 update 是当前设备快照而不只是
    # 新增 Job。首排时动态状态与 init 相同；重算调用方必须在发送前以真实
    # 执行现场覆盖这些字段。
    update = {
        "Robots": compact_robot_snapshots(tool_topo.get("Robots") or {}),
        "Stations": compact_station_snapshots(tool_topo.get("Stations") or {}),
        "Materials": materials, "ProcessJobs": process_jobs, "ControlJobs": control_jobs,
        "RemoveList": [], "MoveStates": [],
        "ProcessRecipes": build_process_recipes(recipes, routes, cleans),
        "CurrentTime": _integer_or_float(float(current_time)),
    }
    return update
