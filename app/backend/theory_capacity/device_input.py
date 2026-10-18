"""把平台标准设备与路线载荷读为独立理论产能输入。

只读取静态无清洁时间和完整线性路线，不调用算法编译器。双腔按设备的
SlotsStationMap 合并压力通道，ATM 成对取放累计成员动作与内部转位；VAC 同步取放。
原始载荷不被修改。字段依据文档仓库 pages/platform/13-interface-equipment.md。
"""
from math import isfinite

from app.backend.theory_capacity.input_model import (
    CapacityInput, ReferenceProduct, RobotTiming, RouteStage, StationTiming,
)


def seconds(value, label):
    """读取有限非负秒数，非法输入明确拒绝。"""
    result = float(value)
    if not isfinite(result) or result < 0:
        raise ValueError(f'{label} 必须是有限非负秒数')
    return result


def detect_dual_chamber(stations):
    """MultiProcessChamber 表示按一对物理晶圆组成一个逻辑产出单元。"""
    return any(str(row.get('Type', '')).lower() == 'multiprocesschamber' for row in stations.values())


def paired_loadlock_groups(robots, stations):
    """从启用臂的槽位映射提取成对 LoadLock，拒绝重叠和非两成员压力通道。"""
    groups, assigned = {}, {}
    for robot in robots.values():
        for arm in (robot.get('ArmInfo') or {}).values():
            if arm.get('IsEnable') is False:
                continue
            for name, slots in (arm.get('SlotsStationMap') or {}).items():
                if name in stations:
                    continue
                members = list(dict.fromkeys(str(row['Key']) for slot in sorted(slots, key=str)
                                             for row in slots[slot] if row.get('Key')))
                if not members or not all(stations.get(member, {}).get('Type') == 'LoadLock' for member in members):
                    continue
                if len(members) != 2 or name in groups and groups[name] != members:
                    raise ValueError(f'压力通道 {name} 的成对映射不一致')
                for member in members:
                    if member in assigned and assigned[member] != name:
                        raise ValueError(f'LoadLock {member} 被多个压力通道使用')
                    assigned[member] = name
                groups[name] = members
    return groups


def _device_timing(init):
    """读取站点与机器人；双腔只转换建图所需计时和逻辑容量。"""
    raw_stations, raw_robots = init['Stations'], init['Robots']
    dual = detect_dual_chamber(raw_stations)
    groups = paired_loadlock_groups(raw_robots, raw_stations) if dual else {}
    merged = {member: name for name, members in groups.items() for member in members}
    stations = {}
    for name, row in raw_stations.items():
        logical = merged.get(name, name)
        if logical in stations:
            continue
        capacity = int(row.get('Capacity', 1))
        if row['Type'] == 'MultiProcessChamber':
            capacity = 1
        elif dual and row['Type'].lower() == 'cooler':
            capacity = max(1, capacity // 2)
        station = StationTiming(logical, row['Type'], capacity)
        for external, attribute in [('PickPrepareTime', 'pick_prepare_time'), ('PickCompleteTime', 'pick_complete_time'),
                                    ('PlacePrepareTime', 'place_prepare_time'), ('PlaceCompleteTime', 'place_complete_time')]:
            setattr(station, attribute, {robot: seconds(value, f'{name}.{external}')
                                        for robot, value in row.get(external, {}).items()})
        for external, attribute, prefix in [('PumpTime', 'pump_time', 'pump'), ('VentTime', 'vent_time', 'vent')]:
            value = row.get(external)
            if value is None:
                value = next((item['Time'] for item in row.get('PrePrepareTime', [])
                              if str(item.get('PrePrepareType', '')).lower().startswith(prefix)), None)
            if value is not None:
                setattr(station, attribute, seconds(value, f'{name}.{external}'))
        stations[logical] = station
    robots = {}
    for name, row in raw_robots.items():
        arms = [arm for arm in row.get('ArmInfo', {}).values() if arm.get('IsEnable') is not False]
        scope = list(dict.fromkeys(merged.get(station, station) for arm in arms
                                   for station in arm.get('AccessibleStations', [])))
        capacity = int(row.get('Capacity', 1))
        if row.get('Slot') is not None:
            capacity = len(row['Slot'])
        if dual:
            capacity = max(1, capacity // 2)
        robot = RobotTiming(name, scope, capacity, capacity >= 2 and not row.get('_DisableSwap', False), strict_timing=True)
        physical_transfers = {}
        for item in row.get('PrepTransTime', []):
            kind = int(item['TransType'])
            if kind not in (0, 1):
                raise ValueError(f'{name} 的 TransType 必须为 0/1')
            key = (str(item['SrcStation']), str(item['DestStation']), kind)
            value = seconds(item['Time'], f'{name}.PrepTransTime')
            if key in physical_transfers and physical_transfers[key] != value:
                raise ValueError(f'{name} 存在冲突的转位计时 {key}')
            physical_transfers[key] = value
            robot.transfer_time.setdefault((merged.get(key[0], key[0]), merged.get(key[1], key[1]), kind), value)
        atmosphere = any(stations[station].type == 'LoadPort' for station in scope if station in stations)
        for external, attribute in [('PickTime', 'pick_time'), ('PlaceTime', 'place_time')]:
            values = row.get(external, {})
            output = {}
            for station, value in values.items():
                logical = merged.get(station, station)
                if logical in output:
                    continue
                duration = seconds(value, f'{name}.{external}[{station}]')
                if dual and atmosphere and logical in groups:
                    left, right = groups[logical]
                    duration = seconds(values[left], external) + physical_transfers[(left, right, 1)] + seconds(values[right], external)
                elif dual and atmosphere and raw_stations.get(station, {}).get('Type', '').lower() == 'cooler':
                    duration = 2 * duration + physical_transfers[(station, station, 1)]
                output[logical] = round(duration, 4)
            setattr(robot, attribute, output)
        robots[name] = robot
    return stations, robots, merged


def _linear_steps(route):
    """按 StepID 链读取完整路线；分支、断链、环和游离工步均拒绝。"""
    rows = route.get('RouteSteps', [])
    by_id = {int(row['StepID']): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('Route 存在重复 StepID')
    targets = {int(target) for row in rows for target in row.get('PostStepID', [])}
    roots = set(by_id) - targets
    if len(roots) != 1 or not targets.issubset(by_id):
        raise ValueError('理论产能需要完整的单一线性 Route')
    result, current, seen = [], roots.pop(), set()
    while current is not None:
        if current in seen:
            raise ValueError('Route 存在循环连接')
        seen.add(current)
        row = by_id[current]
        result.append(row)
        successors = row.get('PostStepID', [])
        if len(successors) > 1:
            raise ValueError('理论产能暂不支持分支 Route')
        current = int(successors[0]) if successors else None
    if len(result) != len(rows):
        raise ValueError('Route 存在未连通工步')
    return result


def compile_capacity_input(init, update):
    """读取标准静态设备、配方和参考路线，返回平台 CapacityInput；不执行排程。

    Route 必须由站点和机器人交替组成。非加工中转站的停留时间沿用零秒口径；
    加工配方读取 ProcessRecipes 并加上 MoveType=9 偏移，其他动作偏移明确拒绝。
    """
    stations, robots, merged = _device_timing(init)
    recipes = {(row['ModuleName'], row['Name']): seconds(row['Time'], 'ProcessRecipes.Time')
               for row in update.get('ProcessRecipes', [])}
    products = []
    for index, material in enumerate(update.get('Materials', [])):
        route = material['Route']
        if any(route.get(field) for field in ('PrePJob', 'PostPJob', 'PostCJob')):
            raise ValueError('独立理论产能当前仅支持无清洁')
        rows = _linear_steps(route)
        stages, transports = [], []
        for position, row in enumerate(rows):
            visits = row.get('Visits', [])
            if not visits:
                raise ValueError('Route 工步缺少 Visits')
            names = list(dict.fromkeys(merged.get(visit['StationName'], visit['StationName']) for visit in visits))
            if position % 2:
                if len(names) != 1 or names[0] not in robots:
                    raise ValueError('Route 搬运工步需要唯一机器人')
                transports.append(names[0])
                continue
            if any(name not in stations for name in names):
                raise ValueError(f'Route 引用未知站点 {names}')
            incoming = rows[position - 1]['Visits'][0]['StationName'] if position else ''
            outgoing = rows[position + 1]['Visits'][0]['StationName'] if position + 1 < len(rows) else ''
            for robot in (incoming, outgoing):
                if robot and (robot not in robots or any(name not in robots[robot].scope for name in names)):
                    raise ValueError(f'Robot {robot} 无法访问站点 {names}')
            first = visits[0]
            role = ('source' if position == 0 else 'sink' if position == len(rows) - 1 else
                    'loadlock' if all(stations[name].type == 'LoadLock' for name in names) else
                    'process' if row.get('NeedProcess') else 'relay')
            by_chamber = {}
            for visit in visits:
                if visit.get('BeforeInPM') or visit.get('AfterOutPM'):
                    raise ValueError('独立理论产能当前仅支持无清洁')
                offsets = {int(key): float(value) for key, value in (visit.get('MoveTimeOffset') or {}).items()}
                if any(key != 9 and value != 0 for key, value in offsets.items()):
                    raise ValueError('独立理论产能暂不支持搬运动作时间偏移')
                if role == 'process':
                    key = (visit['StationName'], visit.get('ProcessRecipe', ''))
                    if key not in recipes:
                        raise ValueError(f'缺少加工配方 {key}')
                    by_chamber[merged.get(key[0], key[0])] = seconds(recipes[key] + offsets.get(9, 0.0), '加工时间')
            stage = RouteStage(len(stages), names[0], role, by_chamber.get(names[0], 0.0), incoming, outgoing,
                float(first.get('ResidencyConstraint', -1)), cands=names, step_id=int(row['StepID']),
                qtime=float(first.get('QTimeLimit', -1)), process_time_by_chamber=by_chamber,
                residency_by_chamber={merged.get(v['StationName'], v['StationName']): float(v.get('ResidencyConstraint', -1)) for v in visits},
                qtime_by_chamber={merged.get(v['StationName'], v['StationName']): float(v.get('QTimeLimit', -1)) for v in visits})
            stages.append(stage)
        if len(rows) % 2 != 1 or not stages or stations[stages[0].chamber].type != 'LoadPort' or stations[stages[-1].chamber].type != 'LoadPort':
            raise ValueError('完整理论路线必须以 LoadPort 开始并结束')
        for lock_index, stage in enumerate(stage for stage in stages if stage.stage_type == 'loadlock'):
            stage.ll_type = 'entry' if lock_index == 0 else 'exit'
            values = [getattr(stations[name], 'pump_time' if lock_index == 0 else 'vent_time') for name in stage.cands]
            if any(value is None for value in values):
                raise ValueError('LoadLock 缺少抽气或充气时间')
            stage.proc = float(values[0])
        products.append(ReferenceProduct(index, int(material['ID']), route.get('Name', ''), index, stages, transports))
    return CapacityInput(stations, robots, products)
