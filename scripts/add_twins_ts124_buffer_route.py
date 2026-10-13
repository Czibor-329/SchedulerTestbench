"""为 TWINS 的 TS-124 增加 Buffer In 专用路径模板并改写该测试的 PJob 引用。

TS-124 的 PJob 都声明 ``bufferOption=2``（强制 buffer in），但它引用的共享路径模板
``PM1(80s) → PM2(40s) · Post:PostClean · 31s · 驻留 25s`` 没有 Buffer 工步，标准算法
会以 ``BUFFER_ROUTE_STEP_REQUIRED`` 拒绝该测试。本脚本按 PSE300 数据集的既有约定
复制该模板，在首个 LoadLock 之前插入 Buffer 工步（Buffer1~Buffer4 候选）与配套 ATM
机械手工步，然后只把 TS-124 的 PJob 指向新模板。共享模板与其它测试保持不变。

脚本幂等：TS-124 已指向含 Buffer 工步的模板时不再改写文件。改写前把原始文件备份到
工作区 ``.tmp/dataset-backup-<时间戳>/``；数据结构、``schemaVersion`` 与目录布局均
不改变。
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "app" / "data" / "datasets" / "0c32dbfb6fb748eb99365fec835c4646"
BACKUP_ROOT = PROJECT_ROOT / ".tmp"
TEST_ID = "3c8330abba584dd9a46b14a0a7346cb1"
BUFFER_ROUTE_SUFFIX = " → Buffer In"
BUFFER_STATION_TYPE = "buffer"
BUFFER_STEP_SLOT_IDS = "1"


def _load_json(path: Path) -> Any:
    """读取 UTF-8 JSON 文档。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    """按工作区仓库的持久化格式写回 JSON。"""
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )


def _backup(paths: Sequence[Path]) -> Path:
    """把待改写文件复制到独立备份目录，返回备份目录。"""
    directory = BACKUP_ROOT / f"dataset-backup-{datetime.now():%Y%m%d-%H%M%S}"
    directory.mkdir(parents=True, exist_ok=True)
    for path in paths:
        (directory / path.name).write_bytes(path.read_bytes())
    return directory


def _pjob_route_refs(test_case: Mapping[str, Any]) -> set[str]:
    """返回测试内全部 PJob 引用的路径模板名称。"""
    return {
        str(pjob.get("routeRef") or "").strip()
        for round_row in test_case.get("rounds") or []
        for cjob in round_row.get("cjobs") or []
        for pjob in cjob.get("pjobs") or []
        if str(pjob.get("routeRef") or "").strip()
    }


def _single_route_ref(test_case: Mapping[str, Any]) -> str:
    """返回测试内 PJob 引用的唯一路径模板名称。"""
    names = _pjob_route_refs(test_case)
    if len(names) != 1:
        raise ValueError(f"TS-124 应只引用一个路径模板，实际为 {sorted(names)}")
    return names.pop()


def _pjob_buffer_option(test_case: Mapping[str, Any], route_name: str) -> int:
    """返回测试内引用该模板的 PJob 一致的 BufferOption；不一致时返回 0。"""
    options = {
        int((pjob.get("routeConfig") or {}).get("bufferOption") or 0)
        for round_row in test_case.get("rounds") or []
        for cjob in round_row.get("cjobs") or []
        for pjob in cjob.get("pjobs") or []
        if str(pjob.get("routeRef") or "") == route_name
    }
    return options.pop() if len(options) == 1 else 0


def _find_route(routes: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    """按名称取回唯一路径模板。"""
    matches = [route for route in routes if str(route.get("name") or "") == name]
    if len(matches) != 1:
        raise ValueError(f"路径模板 {name!r} 应唯一，实际找到 {len(matches)} 个")
    return matches[0]


def _has_buffer_step(
    template: Mapping[str, Any],
    buffer_station_names: Sequence[str],
) -> bool:
    """判断路径模板是否已经包含设备 Buffer 站点的候选工步。"""
    candidates = {str(name) for name in buffer_station_names}
    return any(
        str(visit.get("stationName") or "") in candidates
        for stage in template.get("stages") or []
        for visit in stage.get("visits") or []
    )


def _device_buffer_stations(device: Mapping[str, Any]) -> List[str]:
    """按设备拓扑读取 ``Type=Buffer`` 的 Station 名称。"""
    return sorted(
        str(name)
        for name, station in (device.get("Stations") or {}).items()
        if isinstance(station, Mapping)
        and str(station.get("Type") or "").strip().casefold() == BUFFER_STATION_TYPE
    )


def _buffer_in_template(
    template: Mapping[str, Any],
    new_name: str,
    buffer_station_names: Sequence[str],
) -> Dict[str, Any]:
    """在源模板的首个 LoadLock 之前插入 Buffer 工步，生成 Buffer In 副本。"""
    stages = [deepcopy(dict(stage)) for stage in template.get("stages") or []]
    if len(stages) < 3:
        raise ValueError("源路径模板至少需要三个 Step")
    if str(stages[1].get("kind") or "") != "robot":
        raise ValueError("源路径模板的第 2 个 Step 必须是机械手工步")
    if not buffer_station_names:
        raise ValueError("设备拓扑没有 Type=Buffer 站点")
    buffer_stage = {
        "needProcess": False,
        "kind": "station",
        "visits": [{"stationName": str(name)} for name in buffer_station_names],
    }
    # Buffer 之后仍需一次机械手工步，才能把缓存中的片送入 LoadLock。
    stages[2:2] = [buffer_stage, deepcopy(stages[1])]
    for index, stage in enumerate(stages):
        stage["stepId"] = index
        stage["postStepIds"] = [index + 1] if index + 1 < len(stages) else []
    return {**deepcopy(dict(template)), "name": new_name, "stages": stages}


def _buffer_step_config(source: Mapping[str, Any]) -> Dict[str, Any]:
    """用同一测试的非加工 Step 参数构造 Buffer 工步配置。"""
    config = deepcopy(dict(source))
    config.update(
        processTime=20,
        recipeTime=20,
        qTimeLimit=-1,
        residencyConstraint=-1,
        beforeCleanRefs=[],
        afterCleanRefs=[],
        processRecipe="",
        processType="",
        weight="{}",
        moveTimeOffset="{}",
        slotIds=BUFFER_STEP_SLOT_IDS,
    )
    return config


def _renumbered_recipe_name(recipe_name: str, step_id: int) -> str:
    """把 ``Route4_Step4`` 一类的配方名改写到新的 Step 编号。"""
    prefix, separator, _suffix = str(recipe_name or "").rpartition("_Step")
    return f"{prefix}{separator}{step_id}" if separator else str(recipe_name or "")


def _buffer_in_stage_configs(config: Mapping[str, Any] | None) -> Dict[str, Any]:
    """按新增两个 Step 的顺序重排测试侧 Stage 参数。"""
    stages = {
        int(key): deepcopy(value)
        for key, value in ((config or {}).get("stages") or {}).items()
        if str(key).isdigit() and isinstance(value, Mapping)
    }
    if len(stages) < 3:
        raise ValueError("测试侧路径配置的 Step 不足")
    last_stage_id = max(stages)
    remapped: Dict[str, Any] = {
        "0": stages[0],
        "1": stages[1],
        "2": _buffer_step_config(stages[2]),
        "3": deepcopy(stages[1]),
    }
    for old_step_id in range(2, last_stage_id + 1):
        new_step_id = old_step_id + 2
        stage_config = deepcopy(stages[old_step_id])
        recipe_name = str(stage_config.get("processRecipe") or "")
        if recipe_name:
            stage_config["processRecipe"] = _renumbered_recipe_name(
                recipe_name, new_step_id,
            )
        remapped[str(new_step_id)] = stage_config
    return remapped


def _remap_test_case(
    test_case: Dict[str, Any],
    old_name: str,
    new_name: str,
    buffer_option: int,
) -> None:
    """原地把测试内 PJob 与测试级配置从旧模板切到 Buffer In 模板。"""
    for round_row in test_case.get("rounds") or []:
        for cjob in round_row.get("cjobs") or []:
            for pjob in cjob.get("pjobs") or []:
                if str(pjob.get("routeRef") or "") != old_name:
                    continue
                pjob["routeRef"] = new_name
                config = pjob.get("routeConfig")
                if isinstance(config, dict):
                    config["stages"] = _buffer_in_stage_configs(config)
    configs = test_case.setdefault("routeConfigs", {})
    legacy = configs.get(old_name)
    if isinstance(legacy, Mapping):
        entry = deepcopy(dict(legacy))
        entry["bufferOption"] = buffer_option
        entry["stages"] = _buffer_in_stage_configs(legacy)
        configs[new_name] = entry


def main(argv: Sequence[str] | None = None) -> int:
    """执行幂等迁移并打印摘要。"""
    parser = argparse.ArgumentParser(description="为 TWINS TS-124 增加 Buffer In 路径模板")
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=DATASET_ROOT,
        help="TWINS 设备目录，默认使用仓库主数据",
    )
    args = parser.parse_args(argv)

    dataset_root: Path = args.dataset_root
    routes_path = dataset_root / "routes.json"
    device_path = dataset_root / "device.json"
    test_path = dataset_root / "tests" / TEST_ID / "test.json"
    routes_document = _load_json(routes_path)
    test_case = _load_json(test_path)
    routes: List[Dict[str, Any]] = routes_document["routes"]
    buffer_station_names = _device_buffer_stations(_load_json(device_path))

    current_name = _single_route_ref(test_case)
    current_template = _find_route(routes, current_name)
    if _has_buffer_step(current_template, buffer_station_names):
        print(f"TS-124 已指向含 Buffer 工步的模板 {current_name!r}，无需改写")
        return 0

    new_name = f"{current_name}{BUFFER_ROUTE_SUFFIX}"
    buffer_option = _pjob_buffer_option(test_case, current_name)
    if any(str(route.get("name") or "") == new_name for route in routes):
        raise ValueError(f"路径模板 {new_name!r} 已存在但不是 TS-124 引用的模板")
    routes.append(_buffer_in_template(current_template, new_name, buffer_station_names))
    _remap_test_case(test_case, current_name, new_name, buffer_option)

    backup_directory = _backup((routes_path, test_path))
    _write_json(routes_path, routes_document)
    _write_json(test_path, test_case)
    print(f"Buffer 站点：{', '.join(buffer_station_names)}")
    print(f"新模板：{new_name}")
    print(f"TS-124 PJob 引用已切换：{current_name} → {new_name}（BufferOption={buffer_option}）")
    print(f"备份目录：{backup_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
