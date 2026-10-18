"""PyCharm 可直接右键 Debug 的理论产能教学入口。

固定 PM1、共享 LA/LB 双锁、无清洁、加工 120 秒，使用内存中的合成标准载荷。
计时为开关门各 1 秒、取放各 2 秒、转位 3 秒、Pump/Vent 各 20 秒；
不是公司设备标定数据，不读写工作空间，不运行排程，不生成 MoveList。

在 main 的 compile_capacity_input 调用处下断点，按 F7 进入正式解析器，
随后跟踪 build_cycle_capacity_model → CycleActionTimingGraph.build →
access / robot_chain / station_chain / edge → analyze_capacity / _check_period。
main 保留 device_init、schedule_update、problem、compact_result、facts、waiting_edges、
edge_details 和 report；先进入精简核，再用原实现核对同一个加工点，不扫描曲线。
结果应约为 258 秒/完整周期、2 片/周期、129 秒/片、27.907 片/小时。

依据：D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md 与
pages/platform/13-interface-equipment.md。直接运行不依赖 IDE 工作目录。
"""

from __future__ import annotations

from pathlib import Path
import sys

# 直接右键运行脚本时，将父仓库作为唯一平台包根目录。
PLATFORM_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM_ROOT))

from app.backend.theory_capacity.device_input import compile_capacity_input
from app.backend.theory_capacity.device_graph import build_cycle_capacity_model
from app.backend.theory_capacity.solver import analyze_capacity
from app.backend.theory_capacity.topology import analyze_capacity_topology
from app.backend.theory_capacity.compact_period import calculate_period
from app.backend.theory_capacity.input_model import DeviceDurations

DOOR_SECONDS = 1.0
PICK_SECONDS = 2.0
PLACE_SECONDS = 2.0
TRANSFER_SECONDS = 3.0
PUMP_SECONDS = 20.0
VENT_SECONDS = 20.0
PROCESS_SECONDS = 120.0
EMPTY_HAND = 0
WITH_WAFER = 1
REFERENCE_MATERIAL_ID = 1000
PROCESS_RECIPE_NAME = "PM1-120s"


def build_debug_payloads() -> tuple[dict, dict]:
    """生成独立的最小标准设备与参考路线载荷，返回 (init, update)。

    ATR 单持片位，VTR 双持片位可 Swap，LA/LB 各两槽，PM1 单槽。
    所有可达转位（含站内转位）均显式登记，不依赖缺失计时的默认值。
    每次调用创建新的字典；输入仅描述无限连续供片的参考路线，没有清洁义务。
    """
    # 第一阶段：站点持有门与压力时间，机器人持有取放与转位时间。
    station_definitions = (
        ("LP1", "LoadPort", 1, ("ATR",)),
        ("LA", "LoadLock", 2, ("ATR", "VTR")),
        ("LB", "LoadLock", 2, ("ATR", "VTR")),
        ("PM1", "ProcessChamber", 1, ("VTR",)),
    )
    stations = {}
    for name, station_type, capacity, accessible_robots in station_definitions:
        station = {
            "Name": name,
            "Type": station_type,
            "Capacity": capacity,
            "Slots": list(range(1, capacity + 1)),
            "PickPrepareTime": {robot: DOOR_SECONDS for robot in accessible_robots},
            "PickCompleteTime": {robot: DOOR_SECONDS for robot in accessible_robots},
            "PlacePrepareTime": {robot: DOOR_SECONDS for robot in accessible_robots},
            "PlaceCompleteTime": {robot: DOOR_SECONDS for robot in accessible_robots},
        }
        if station_type == "LoadLock":
            station["PrePrepareTime"] = [
                {"LastItem": "ATR", "CurrentItem": "VTR",
                 "PrePrepareType": "PumpTime", "Time": PUMP_SECONDS},
                {"LastItem": "VTR", "CurrentItem": "ATR",
                 "PrePrepareType": "VentTime", "Time": VENT_SECONDS},
            ]
        stations[name] = station

    robots = {}
    for name, robot_type, capacity, scope in (
        ("ATR", "ATMRobot", 1, ("LP1", "LA", "LB")),
        ("VTR", "VTMRobot", 2, ("LA", "LB", "PM1")),
    ):
        robots[name] = {
            "Name": name,
            "Type": robot_type,
            "Capacity": capacity,
            "CanMultiTrans": robot_type == "VTMRobot",
            "PickTime": {station: PICK_SECONDS for station in scope},
            "PlaceTime": {station: PLACE_SECONDS for station in scope},
            "PrepTransTime": [
                {"SrcStation": source, "DestStation": target,
                 "TransType": carried, "Time": TRANSFER_SECONDS}
                for source in scope for target in scope
                for carried in (EMPTY_HAND, WITH_WAFER)
            ],
            "ArmInfo": {
                f"Arm{slot}": {
                    "Name": f"Arm{slot}", "IsEnable": True, "SlotIDs": [slot],
                    "AccessibleStations": list(scope), "SlotAtStation": scope[0],
                    "UseCount": 0, "SlotsStationMap": {},
                }
                for slot in range(1, capacity + 1)
            },
        }
    device_init = {"Stations": stations, "Robots": robots}

    # 第二阶段：标准 Route 交替排列站点与机器人，进出口使用相同锁候选。
    route_sequence = (
        ("LP1",), ("ATR",), ("LA", "LB"), ("VTR",), ("PM1",),
        ("VTR",), ("LA", "LB"), ("ATR",), ("LP1",),
    )
    route_steps = []
    for index, candidates in enumerate(route_sequence):
        visits = [{"StationName": name} for name in candidates]
        is_process = candidates == ("PM1",)
        if is_process:
            visits[0]["ProcessRecipe"] = PROCESS_RECIPE_NAME
        route_steps.append({
            "StepID": index,
            "PostStepID": [index + 1] if index + 1 < len(route_sequence) else [],
            "NeedProcess": is_process,
            "Visits": visits,
        })
    schedule_update = {
        "ProcessRecipes": [
            {"ModuleName": "PM1", "Name": PROCESS_RECIPE_NAME, "Time": PROCESS_SECONDS},
        ],
        "Materials": [{
            "ID": REFERENCE_MATERIAL_ID,
            "Route": {"Name": "PM1 共享双锁无清洁", "RouteSteps": route_steps},
        }],
    }
    return device_init, schedule_update


def main() -> None:
    """单次解析、建图与求解；保留中间变量供调试，并打印边分项与产能。

    参数由模块常量固定，输出到控制台，无文件、服务或数据集写入。
    """
    device_init, schedule_update = build_debug_payloads()

    # 断点 1：F7 进入解析器，查看标准字段如何变为 StationTiming/RobotTiming。
    problem = compile_capacity_input(device_init, schedule_update)

    # 精简核入口：设备数字就绪后，F7 可在单个模块内跟完模板、负荷、约束和周期。
    compact_result = calculate_period(
        DeviceDurations(problem), ({"PM1": PROCESS_SECONDS},), ("LA", "LB"),
        "shared-bidirectional", "VTR", "ATR", "LP1",
    )
    topology = analyze_capacity_topology(problem)
    if not topology.supported:
        raise ValueError(f"{topology.reason}: {topology.detail}")

    # 断点 2：F7 进入建图器，跟踪 access、robot_chain、station_chain 与 edge。
    built_model = build_cycle_capacity_model(problem, topology)
    facts = built_model.facts
    waiting_edges = built_model.model.constraints
    resource_work_seconds = built_model.model.resource_work_seconds
    services_by_key = built_model.service_index()
    edge_details = [
        {"edge": edge, "parts": services_by_key[edge.label].parts,
         "detail": services_by_key[edge.label].detail}
        for edge in waiting_edges
    ]

    # 断点 3：F7 进入求解器，查看 _check_period 与二分的 lower/middle/upper。
    report = analyze_capacity(built_model.model)
    difference = abs(compact_result["period_seconds"] - report.period_seconds)
    if difference > report.tolerance_seconds:
        raise RuntimeError(f"精简核与原实现的周期差异超出容差：{difference}")

    print(f"合成设备：PM1 / {facts.lock_mode} / 无清洁 / 加工 {PROCESS_SECONDS:g} 秒")
    print("等待边：target_offset >= source_offset + delay - shift * T")
    for item in edge_details:
        edge = item["edge"]
        parts = " + ".join(f"{part.label}={part.seconds:g}" for part in item["parts"])
        print(f"  {edge.source} -> {edge.target}: delay={edge.minimum_delay_seconds:g}, "
              f"shift={edge.cycle_shift} [{parts}]")
    print(f"资源负荷（秒/完整周期）：{resource_work_seconds}")
    print(f"完整周期：{report.period_seconds:.6f} 秒；产出：{facts.units_per_cycle:g} 片")
    print(f"平均片间隔：{report.seconds_per_unit:.6f} 秒/片")
    print(f"理论产能：{report.physical_wafers_per_hour:.6f} 片/小时")
    print(f"精简核周期：{compact_result['period_seconds']:.6f} 秒；与原实现差异：{difference:.3g} 秒")


if __name__ == "__main__":
    main()
