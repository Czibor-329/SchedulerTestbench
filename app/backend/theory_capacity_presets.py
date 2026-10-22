"""理论产能固定 PM 组合：只取设备计时构造临时输入，不匹配持久路线和测试。

由平台静态输入解析与精简周期核求解；临时模板只在内存存在，不写入工作空间。
"""
from copy import deepcopy
from app.backend import theory_capacity_service as capacity
from app.backend.theory_capacity.compact_presentation import calculate_preset_payload

PRESETS = {
    "single-node": (("PM1",),),
    "serial": (("PM1",), ("PM2",)),
    "parallel": (("PM1", "PM2", "PM3"),),
    "mixed": (("PM1",), ("PM2", "PM3")),
}


def build_preset_device(device, kind, lock):
    """由设备和形态构造临时模板；缺少指定 PM、锁或机器人时明确拒绝。"""
    if kind not in PRESETS or lock not in capacity.LOCK_MODE_VALUES:
        raise ValueError("不支持的 PM 组合或 LoadLock 类型")
    init = device["device"]
    stations = init.get("Stations", {})
    nodes = PRESETS[kind]
    for name in {name for node in nodes for name in node}:
        if name not in stations:
            raise ValueError(f"设备缺少指定加工腔 {name}")
    locks = [name for name, value in stations.items() if value.get("Type") == "LoadLock"]
    # 双腔按平台的成对锁视图选择通道，LA/LB 是一个通道而非两把独立锁。
    from app.backend.theory_capacity.device_input import detect_dual_chamber, paired_loadlock_groups
    groups = paired_loadlock_groups(init.get("Robots", {}), stations) if detect_dual_chamber(stations) else {}
    paired = {name for names in groups.values() for name in names}
    lock_channels = list(groups.values()) + [[name] for name in locks if name not in paired]
    required = 1 if lock == "single-bidirectional" else 2
    if len(lock_channels) < required:
        raise ValueError(f"该锁分工需要 {required} 个 LoadLock")
    robots = init.get("Robots", {})
    atmosphere = next((name for name, value in robots.items() if value.get("Type") == "ATMRobot"), None)
    vacuum = next((name for name, value in robots.items() if value.get("Type") == "VTMRobot"), None)
    if not atmosphere or not vacuum:
        raise ValueError("设备缺少 ATMRobot 或 VTMRobot")
    load_port = capacity._first_load_port(init)
    entry = [name for channel in lock_channels[:required] for name in channel] if lock != "dedicated-entry-exit" else lock_channels[0]
    exit_locks = lock_channels[1] if lock == "dedicated-entry-exit" else entry
    sequence = [("station", [load_port], False), ("robot", [atmosphere], False),
                ("station", entry, False), ("robot", [vacuum], False)]
    for node in nodes:
        sequence.extend([("station", node, True), ("robot", [vacuum], False)])
    sequence.extend([("station", exit_locks, False), ("robot", [atmosphere], False),
                     ("station", [load_port], False)])
    name = " → ".join("/".join(node) for node in nodes)
    route = {"name": name, "group": "固定 PM 组合", "stages": [
        {"stepId": index, "postStepIds": [index + 1] if index + 1 < len(sequence) else [],
         "kind": stage_kind, "needProcess": process,
         "visits": [{"stationName": station} for station in candidates]}
        for index, (stage_kind, candidates, process) in enumerate(sequence)
    ]}
    result = deepcopy(device)
    result.update(routes=[route], tests=[], cleans=[], routeAliases={})
    return result, route, load_port


def read_theory_capacity_preset(device_id, *, kind="single-node", lock="shared-bidirectional",
                               first_seconds=None, last_seconds=None, step_seconds=None,
                               process_seconds=None, cleaning_values=None):
    """返回固定组合扫描及一个加工时间的推导；参数单位秒，输出不含测试匹配。"""
    if cleaning_values and (cleaning_values.get("clean") or cleaning_values.get("mode", "steady") != "steady"):
        raise ValueError("独立理论产能当前仅支持无清洁稳态计算；清洁及有限批次计算暂不支持")
    window = capacity._scan_window(first_seconds, last_seconds, step_seconds)
    process = capacity._query_number(process_seconds, capacity.DEFAULT_PROCESS_SECONDS, "process")
    if process < 0:
        raise ValueError("加工时间不能小于零")
    device, route, load_port = build_preset_device(
        capacity.get_workspace_device_overview(device_id), kind, lock)
    config = capacity._default_route_config(route)
    for stage in config["stages"].values():
        stage.update(processTime=process, recipeTime=process)
    problem, topology = capacity._compile_route(device, route["name"], config, load_port)
    runtime = capacity._capacity_runtime()
    members = runtime.physical_members_per_unit(device["device"]["Stations"])
    points, derivation = calculate_preset_payload(problem, topology, members, window)
    derivation.update(route_name=route["name"], status="supported", process_seconds=[process] * len(PRESETS[kind]))
    return {"ok": True, "name": route["name"], "points": points,
            "derivation": capacity._round_floats(derivation)}
