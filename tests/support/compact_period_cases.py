"""精简周期核的确定性合成输入；仅转换输入，不借用现有建图器的模板事实。

按九个有效组合构造独立对象；非对称变体使用调用方给定种子，分别改变门、取放、
方向转位、压力与逐腔加工时间。无工作空间或真实数据集读写。
"""
import random

from app.backend.theory_capacity.input_model import DeviceDurations
from tests.support.capacity_problem_builders import cycle_problem

ROUTES = {
    "single": (("PM1",),),
    "serial": (("PM1",), ("PM2",)),
    "parallel": (("PM1", "PM2", "PM3"),),
    "mixed": (("PM1",), ("PM2", "PM3")),
}
COMBINATIONS = [
    (mode, route)
    for mode, routes in (
        ("shared-bidirectional", tuple(ROUTES)),
        ("dedicated-entry-exit", ("single", "serial", "parallel")),
        ("single-bidirectional", ("single", "parallel")),
    )
    for route in routes
]


def period_problem(mode, route, process_seconds=120.0):
    """构造一个参考产品；改锁授权以选择共享、分向或唯一同锁语义。"""
    nodes = ROUTES[route]
    problem = cycle_problem(process_candidates=nodes, wafer_count=1,
                            process_seconds=[process_seconds] * len(nodes))
    for stage in problem.wafers[0].stages:
        if stage.stage_type == "loadlock" and mode != "shared-bidirectional":
            name = "LB" if mode == "dedicated-entry-exit" and stage.ll_type == "exit" else "LA"
            stage.chamber, stage.cands = name, [name]
    return problem


def period_arguments(problem, mode):
    """从参考路线提取名称和数字；不计算轮数、动作、约束或负荷。"""
    stages = problem.wafers[0].stages
    nodes = tuple({name: stage.process_time_by_chamber.get(name, stage.proc)
                   for name in stage.cands or [stage.chamber]}
                  for stage in stages if stage.stage_type == "process")
    return dict(timing=DeviceDurations(problem), process_nodes=nodes,
                loadlocks=("LA",) if mode == "single-bidirectional" else ("LA", "LB"),
                lock_mode=mode, vacuum_robot="VTR", atmosphere_robot="ATR", load_port="LP1")


def randomize_asymmetric_timing(problem, seed):
    """原地设置固定种子的非对称计时，含零秒、不同手臂门时间及全部精确转位。"""
    generator = random.Random(seed)
    def seconds():
        """生成毫秒精度且可能为零的时长，避免只测试正数或对称路径。"""
        return 0.0 if generator.randrange(7) == 0 else generator.randint(1, 100000) / 1000
    for chamber in problem.chambers.values():
        for field in ("pick_prepare_time", "place_prepare_time", "pick_complete_time", "place_complete_time"):
            setattr(chamber, field, {robot: seconds() for robot in getattr(chamber, field)})
        if chamber.type == "LoadLock":
            chamber.pump_time, chamber.vent_time = seconds(), seconds()
    for robot in problem.robots.values():
        robot.strict_timing = True
        robot.pick_time = {name: seconds() for name in robot.scope}
        robot.place_time = {name: seconds() for name in robot.scope}
        robot.transfer_time = {(source, target, carried): seconds()
                               for source in robot.scope for target in robot.scope for carried in (0, 1)}
    for stage in problem.wafers[0].stages:
        if stage.stage_type == "process":
            stage.process_time_by_chamber = {name: seconds() for name in stage.cands}
