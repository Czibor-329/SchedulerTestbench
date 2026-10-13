"""清洁产能的有限批次模型：复用生产编译器、Cycle 和物理校验，不修改设备主数据。

每个 Job 使用相同配方和片数；连续交接占用不同真实 LoadPort，排空模式逐批
重复同一空机任务。输出包含启动、产品回盒、Dummy 归库和尾部清洁，不宣称稳态极限。
文档：D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json

from app.backend import theory_capacity_service as capacity
from app.backend.execution.batch_service import build_workspace_batch_plan
from app.backend.execution.plan_builder import BuildState, build_round_update, build_task_alg_init

SECONDS_PER_HOUR = 3600
MAX_SCAN_POINTS = 41
MAX_JOB_COUNT = 8
MAX_WAFERS_PER_JOB = 25
MAX_DUMMY_VISITS = 10
MAX_CLEAN_SECONDS = 86400


@dataclass(frozen=True)
class CleaningScenario:
    """页面显式设置的清洁与 Job 边界；时长单位秒，片数为真实物理产品片数。"""

    types: tuple[str, ...]
    wafers: int
    jobs: int
    alternation: str
    pre_seconds: float
    post_seconds: float
    wac_seconds: float
    wac_interval: int
    dummy_seconds: float
    dummy_visits: int
    dummy_inventory: int


def parse_cleaning_scenario(values):
    """校验查询参数并返回不可变场景；不允许未知清洁类型或隐式截断小数计数。"""
    values = values or {}
    types = capacity._filter_values(values.get("clean"), capacity.CLEAN_TYPE_VALUES, "clean")
    before = set(types).intersection({"preclean", "dummy", "dummywac"})
    if len(before) > 1:
        raise ValueError("每个 Job 请选择一种前清洁：PreClean、Dummy 或 DummyWAC")
    def number(key, default, maximum, integer=False):
        """读取有界秒数或正整数，避免无界排程请求。"""
        value = capacity._query_number(values.get(key), default, key)
        if not (1 if integer else 0) <= value <= maximum or (integer and not value.is_integer()):
            raise ValueError(f"{key} 必须为 {'1' if integer else '0'}～{maximum} 的{'整数' if integer else '秒数'}")
        return int(value) if integer else value
    alternation = values.get("alternation", "continuous")
    if alternation not in ("continuous", "drain"):
        raise ValueError("Job 交替方式仅支持 continuous 或 drain")
    return CleaningScenario(types, number("wafers", 25, MAX_WAFERS_PER_JOB, True),
        number("jobs", 1, MAX_JOB_COUNT, True), alternation,
        number("pre_seconds", 30, MAX_CLEAN_SECONDS), number("post_seconds", 30, MAX_CLEAN_SECONDS),
        number("wac_seconds", 30, MAX_CLEAN_SECONDS), number("wac_interval", 5, MAX_WAFERS_PER_JOB, True),
        number("dummy_seconds", 30, MAX_CLEAN_SECONDS), number("dummy_visits", 2, MAX_DUMMY_VISITS, True),
        number("dummy_inventory", 6, MAX_WAFERS_PER_JOB, True))


def build_cleaning_plan(device, route, load_port, process_seconds, scenario, *, with_cleaning):
    """构造内存中的真实批次计划；每个 Job 独立计数，连续交接不虚增端口容量。"""
    config = capacity._default_route_config(route)
    chambers = sorted({visit["stationName"] for stage in route["stages"] if stage.get("needProcess")
                       for visit in stage["visits"]})
    cleans = []
    for kind in scenario.types if with_cleaning else ():
        seconds = {"preclean": scenario.pre_seconds, "postclean": scenario.post_seconds,
                   "wacclean": scenario.wac_seconds, "dummy": scenario.dummy_seconds,
                   "dummywac": scenario.dummy_seconds}[kind]
        cleans.append({"name": kind, "cleanType": kind, "recipeTime": seconds, "modules": chambers,
                       "triggerCount": scenario.wac_interval, "materialCount": scenario.dummy_visits,
                       "dummyWaferCount": scenario.dummy_inventory, "wacRecipeTime": scenario.wac_seconds})
        if kind == "postclean":
            config["postPJobCleanRefs"].append(kind)
        elif kind != "wacclean":
            config["prePJobCleanRefs"].append(kind)
    for stage in route["stages"]:
        if stage.get("needProcess"):
            parameters = config["stages"][str(stage["stepId"]) ]
            parameters.update(processTime=process_seconds, recipeTime=process_seconds)
            if with_cleaning and "wacclean" in scenario.types:
                parameters.update(afterCleanRefs=["wacclean"], weight={"ProcessCount": 1})
    test = capacity._synthetic_test_case(route["name"], config, load_port)
    # 理论基线入口会剥离 Clean；批次入口显式保留本次配置。
    test.update(routeConfigs={route["name"]: config}, cleans=cleans)
    stations = device["device"]["Stations"]
    ports = [name for name, station in stations.items() if station.get("Type") == "LoadPort"]
    job_count = scenario.jobs if scenario.alternation == "continuous" else 1
    if job_count > len(ports):
        raise ValueError(f"连续交接 {job_count} 个 Job 需要 {job_count} 个真实 LoadPort，设备只有 {len(ports)} 个；可选择逐批排空")
    prototype = test["rounds"][0]["cjobs"][0]
    jobs = []
    for index, port in enumerate(ports[:job_count]):
        available = int(stations[port].get("Capacity", 0))
        if scenario.wafers > available:
            raise ValueError(f"{port} 容量 {available}，无法装入 {scenario.wafers} 片产品")
        job = deepcopy(prototype)
        job.update(taskId=str(index + 1), loadPort=port)
        job["pjobs"][0].update(taskId=str(index + 1), loadPort=port, waferCount=scenario.wafers)
        jobs.append(job)
    test["rounds"][0]["cjobs"] = jobs
    return build_workspace_batch_plan(device, test, "cycle", {}, hongye_check=False)


def summarize_cleaning_moves(moves, products, dummy_ids, multiplier=1):
    """按物理物料核对最终回盒，汇总动作负荷及各腔清洁；不把 Dummy 算作产品。

    products 映射物料 ID 到原端口及原槽，multiplier 仅用于完全相同的排空批次。
    资源负荷取独占动作时间的区间并集，避免双腔展开重复累计机器人时间。
    """
    from src.schedule.core.move_fields import PLACE_MOVE, PROCESS_MOVE, STATION_MOVE_TYPES
    returned = {}
    inventory_returned = set(dummy_ids)
    intervals = {}
    cleaning = {}
    for move in moves:
        start, end = float(move["StartTime"]), float(move["EndTime"])
        if move.get("MoveType") == PLACE_MOVE:
            for material, station, slot in zip(move.get("MatIDList", []),
                    move.get("DestStationList", []), move.get("DestSlotList", [])):
                if material in products and (station, slot) == products[material]:
                    returned[material] = end
                if material in dummy_ids:
                    if (station, slot) == dummy_ids[material]:
                        inventory_returned.add(material)
                    else:
                        inventory_returned.discard(material)
        resource = str(move.get("Station") or "") if move.get("MoveType") in STATION_MOVE_TYPES else str(move.get("RobotName") or move.get("Robot") or "")
        if resource:
            intervals.setdefault(resource, []).append((start, end))
        task = str(move.get("CleanTaskName") or "")
        if move.get("MoveType") == PROCESS_MOVE and task:
            key = (str(move.get("Station") or ""), task)
            row = cleaning.setdefault(key, {"station": key[0], "task": task, "count": 0, "seconds": 0.0})
            row["count"] += multiplier
            row["seconds"] += (end - start) * multiplier
    if set(returned) != set(products) or set(dummy_ids) != inventory_returned:
        raise ValueError("Cycle 批次未完成全部产品回原槽或 Dummy 归库，不能计算产能")
    work = {}
    for resource, segments in intervals.items():
        boundary, total = 0.0, 0.0
        for start, end in sorted(segments):
            total += max(0.0, end - max(start, boundary))
            boundary = max(boundary, end)
        work[resource] = total * multiplier
    duration = max((float(move["EndTime"]) for move in moves), default=0.0) * multiplier
    if duration <= 0:
        raise ValueError("批次耗时为零，无法换算有限产能")
    count = len(products) * multiplier
    # 用真实回盒事件拆解批次耗时；同刻回盒的双腔成员保留零间隔，不虚增产出。
    completion_times = sorted(returned.values())
    gaps = {}
    for previous, current in zip(completion_times, completion_times[1:]):
        gap = round(current - previous, 6)
        gaps[gap] = gaps.get(gap, 0) + 1
    timing = {
        "first_return_seconds": completion_times[0],
        "remaining_returns_seconds": completion_times[-1] - completion_times[0],
        "tail_seconds": duration / multiplier - completion_times[-1],
        "repeat_count": multiplier,
        "return_intervals": [{"seconds": seconds, "count": occurrences} for seconds, occurrences in sorted(gaps.items())],
    }
    return {"period_seconds": duration, "seconds_per_unit": duration / count,
            "physical_wafers_per_hour": SECONDS_PER_HOUR * count / duration,
            "resource_work_seconds": work, "resource_period_lower_bound_seconds": max(work.values(), default=0),
            "batch_wafers": count, "cleaning_summary": list(cleaning.values()),
            "batch_timing": timing,
            "last_product_return_seconds": (duration / multiplier) * (multiplier - 1) + max(returned.values())}


@lru_cache(maxsize=128)
def _solve_batch(plan_json, multiplier, configuration):
    """缓存相同输入与 Cycle 配置的校验后批次；不保存 MoveList 或任何持久化结果。"""
    from src.schedule.strategies.cycle.scheduler import start_schedule_by_cycle
    from src.compiler.dual_chamber import expand_dual_moves
    plan = json.loads(plan_json)
    init = build_task_alg_init(plan["device"], plan["routes"], plan["rounds"], plan["cleans"])
    plan["device"] = init
    update = build_round_update(plan, plan["rounds"][0], 0.0, BuildState())
    problem = capacity._algorithm().compile_problem(init, update)
    result = start_schedule_by_cycle(problem)
    moves = result.machine_moves
    if problem.dual_view:
        moves = expand_dual_moves(moves, problem.dual_view)
    products = {int(row["ID"]): (row["CurrentModuleName"], int(row["SlotID"]))
                for row in update["Materials"] if row.get("PJobName")}
    dummy = {int(row["ID"]): (row["CurrentModuleName"], int(row["SlotID"]))
             for row in update["Materials"] if row["CurrentModuleName"] not in {port for port, _ in products.values()}}
    summary = summarize_cleaning_moves(moves, products, dummy, multiplier)
    summary["physical_validation_passed"] = result.cycle_diagnostics["physicalValidationPassed"]
    summary["loadlock_batch_size"] = result.cycle_diagnostics["loadLockBatchSize"]
    return summary


def analyze_cleaning_batch(device, route, load_port, process_seconds, scenario, *, with_cleaning=True):
    """求解一个加工时间下的完整有限批次，返回独立响应副本；失败时不返回乐观产能。"""
    from src.schedule.strategies.cycle.config import CONFIG_PATH
    plan = build_cleaning_plan(device, route, load_port, process_seconds, scenario, with_cleaning=with_cleaning)
    multiplier = scenario.jobs if scenario.alternation == "drain" else 1
    return deepcopy(_solve_batch(json.dumps(plan, sort_keys=True, ensure_ascii=False), multiplier,
                                 CONFIG_PATH.read_text(encoding="utf-8-sig")))


def read_cleaning_capacity(device, route, load_port, scan, theory, values, process):
    """返回带清洁与无清洁的同批次曲线、真实清洁统计及单片压力模板理论参照。"""
    scenario = parse_cleaning_scenario(values)
    theory_points = capacity._scan_points_payload(scan)
    times = theory_points["process_seconds"]
    if len(times) > MAX_SCAN_POINTS:
        raise ValueError(f"Cycle 批次扫描最多 {MAX_SCAN_POINTS} 个点，请增大步长或缩小区间")
    from src.schedule.strategies.cycle.config import CONFIG_PATH
    from app.backend.theory_capacity_workers import capacity_batch_workers
    configuration = CONFIG_PATH.read_text(encoding="utf-8-sig")
    multiplier = scenario.jobs if scenario.alternation == "drain" else 1
    coordinates, requests = [], []
    # 先完成全部输入校验，再并行提交；基线不含清洁参数，因此拖动时直接复用。
    for seconds in dict.fromkeys([process, *times]):
        for with_cleaning in (True, False) if scenario.types else (True,):
            plan = build_cleaning_plan(device, route, load_port, seconds, scenario, with_cleaning=with_cleaning)
            coordinates.append((seconds, with_cleaning))
            requests.append((json.dumps(plan, sort_keys=True, ensure_ascii=False), multiplier, configuration))
    solved = dict(zip(coordinates, capacity_batch_workers.solve(requests)))
    samples = {seconds: solved[seconds, True] for seconds in dict.fromkeys([process, *times])}
    baselines = {seconds: solved[seconds, False] if scenario.types else samples[seconds] for seconds in samples}
    selected, baseline = samples[process], baselines[process]
    derivation = {**selected, "route_name": route["name"], "status": "supported", "model_kind": "cycle-batch",
                  "process_seconds": [process] * len(theory["facts"]["process_nodes"]),
                  "job_count": scenario.jobs, "wafers_per_job": scenario.wafers, "alternation": scenario.alternation,
                  "baseline_wafers_per_hour": baseline["physical_wafers_per_hour"],
                  "baseline_seconds": baseline["period_seconds"],
                  "capacity_change_percent": 100 * (selected["physical_wafers_per_hour"] / baseline["physical_wafers_per_hour"] - 1),
                  "cleaning_types": list(scenario.types),
                  "scope_note": "Cycle 有限批次估算，含空机启动、产品回盒、Dummy 归库及尾部清洁；不是稳态理论极限或现场实测。"}
    return capacity._round_floats({"ok": True, "name": route["name"], "derivation": derivation,
        "points": {"process_seconds": times, "physical_wafers_per_hour": [samples[t]["physical_wafers_per_hour"] for t in times]},
        "baseline_points": {"process_seconds": times, "physical_wafers_per_hour": [baselines[t]["physical_wafers_per_hour"] for t in times]},
        "theory_points": theory_points, "theory_derivation": theory})
