"""连续 Cycle 的重复周期分析，排除空机启动及停止供片后的收尾。

WAC 按计数与轮转相位选择候选周期，其它清洁按 Job 选择候选周期。
沿用论文无限连续供片边界，在内存中展开入口产品缓冲，要求中段连续三个窗口的动作时序重复；
缓冲仅用于理论模型，真实 LoadPort 的几何与计时、PM/LoadLock 和 Dummy 库存不变，
不写设备或测试数据，不将展开的缓冲容量宣称为真实设备容量。
这是生产 Cycle 的稳定排程周期，不冒充论文事件图求出的最小可行周期。
文档：D:/ct-scheduler-docs/pages/algorithm/26-cycle-capacity.md。
"""
from copy import deepcopy
from dataclasses import replace
import json
from math import ceil, gcd, lcm

from app.backend import theory_capacity_service as capacity
from app.backend.theory_capacity_cleaning import (
    build_cleaning_plan, parse_cleaning_scenario, MAX_SCAN_POINTS, SECONDS_PER_HOUR,
)

JOB_WAFERS = 13
REQUIRED_REPEATS = 3
TIMING_TOLERANCE = 0.001
BOUNDARY_GUARD_PERIODS = 4
MAX_CLEANING_COUNT = 3


def _window_signature(moves, start, end):
    """返回窗口内动作及相对时刻，保留跨边界占用，忽略会变化的物料与 Move ID。"""
    rows = []
    for move in moves:
        begin, finish = float(move['StartTime']), float(move['EndTime'])
        if finish <= start + TIMING_TOLERANCE or begin >= end - TIMING_TOLERANCE:
            continue
        key = (move.get('MoveType'), str(move.get('ModuleName') or ''),
               str(move.get('Station') or ''), str(move.get('RobotName') or ''),
               tuple(move.get('SrcStationList') or []), tuple(move.get('DestStationList') or []),
               str(move.get('CleanTaskName') or ''), len(move.get('MatIDList') or []))
        rows.append((key, begin - start, finish - start))
    return sorted(rows)


def _same_window(left, right):
    """比较动作语义与相位；容许补片事件的微秒级数值偏移，不只比较总时长。"""
    return len(left) == len(right) and all(a[0] == b[0] and
        abs(a[1] - b[1]) <= TIMING_TOLERANCE and abs(a[2] - b[2]) <= TIMING_TOLERANCE
        for a, b in zip(left, right))


def find_repeating_window(moves, anchors, maximum_phases):
    """找最短候选相位数及连续三个重复窗口，首尾各保留一个边界作为保护。

    anchors 是递增的同义事件时刻。返回 (开始, 结束, 合并候选周期数)，未验证
    重复时拒绝输出，不以有限批次总工期或单个相似间隔代替稳态周期。
    """
    for phases in range(1, maximum_phases + 1):
        for index in range(1, len(anchors) - REQUIRED_REPEATS * phases - 1):
            starts = [anchors[index + repeat * phases] for repeat in range(REQUIRED_REPEATS + 1)]
            durations = [b - a for a, b in zip(starts, starts[1:])]
            if max(durations) - min(durations) > TIMING_TOLERANCE:
                continue
            signatures = [_window_signature(moves, a, b) for a, b in zip(starts, starts[1:])]
            if all(_same_window(signatures[0], other) for other in signatures[1:]):
                return starts[0], starts[1], phases
    raise ValueError('未验证到连续三个相位重复的中段周期，不能用有限批次耗时代替稳态产能')


def _resource_breakdown(moves, start, end, process_chambers=()):
    """裁剪周期内各独占资源活动，按动作种类分解并避免双腔物理展开重复计时。"""
    from src.schedule.core.move_fields import (
        PROCESS_MOVE, STATION_MOVE_TYPES, PICK_MOVE, PLACE_MOVE, SWAP_MOVE,
        PRE_TRANS_MOVE, PREPARE_MOVE, COMPLETE_MOVE, PRE_PREPARE_MOVE,
    )
    intervals = {}
    for move in moves:
        begin, finish = max(start, float(move['StartTime'])), min(end, float(move['EndTime']))
        if finish <= begin:
            continue
        resource = str(move.get('Station') or move.get('ModuleName') or '') if move.get('MoveType') in STATION_MOVE_TYPES else str(move.get('RobotName') or move.get('Robot') or move.get('ModuleName') or '')
        if not resource:
            continue
        if move.get('MoveType') == PROCESS_MOVE:
            label = str(move.get('CleanTaskName') or '产品加工')
        else:
            label = {PICK_MOVE: '取放与换片', PLACE_MOVE: '取放与换片', SWAP_MOVE: '取放与换片',
                     PRE_TRANS_MOVE: '转位', PREPARE_MOVE: '开关门', COMPLETE_MOVE: '开关门',
                     PRE_PREPARE_MOVE: '抽气 / 充气'}.get(move.get('MoveType'), '其它动作')
        intervals.setdefault(resource, []).append((begin, finish, label))
        # 取放期间机器人和目标 PM 同时被占用；跨资源不相加，同资源取区间并集。
        if move.get('MoveType') in (PICK_MOVE, PLACE_MOVE, SWAP_MOVE):
            touched = set(move.get('SrcStationList', []) + move.get('DestStationList', []) + move.get('StationList', []))
            for chamber in touched.intersection(process_chambers):
                intervals.setdefault(chamber, []).append((begin, finish, label))
    work, details = {}, {}
    for resource, segments in intervals.items():
        boundary, groups, terms = start, {}, {}
        for begin, finish, label in sorted(set(segments)):
            seconds = max(0.0, finish - max(begin, boundary))
            boundary = max(boundary, finish)
            if seconds:
                groups[label] = groups.get(label, 0.0) + seconds
                durations = terms.setdefault(label, {})
                duration = round(seconds, 6)
                durations[duration] = durations.get(duration, 0) + 1
        work[resource] = sum(groups.values())
        details[resource] = [{'label': label, 'seconds': seconds, 'terms':
            [{'count': count, 'seconds': duration} for duration, count in sorted(terms[label].items())]}
            for label, seconds in groups.items()]
    return work, details


def solve_periodic_plan(plan_json):
    """执行隔离的连续供片计划，验证中段重复并输出周期证据，不写运行制品。"""
    from app.backend.execution.plan_builder import BuildState, build_round_update, build_task_alg_init
    from src.schedule.strategies.cycle.scheduler import start_schedule_by_cycle
    from src.compiler.dual_chamber import expand_dual_moves
    from src.schedule.core.move_fields import PLACE_MOVE, SWAP_MOVE
    plan = json.loads(plan_json)
    settings = plan.pop('_capacity_periodic')
    init = build_task_alg_init(plan['device'], plan['routes'], plan['rounds'], plan['cleans'])
    plan['device'] = init
    update = build_round_update(plan, plan['rounds'][0], 0.0, BuildState())
    if settings['basis'] == 'wac':
        # WAC 只保留一个 PJob，避免人为 Job 切换重置计数。这里展开理论入口，
        # 不放宽平台面向真实载具的每 Job 25 片输入约束。
        prototype = update['Materials'][0]
        materials = []
        for index in range(settings['supply_wafers']):
            material = deepcopy(prototype)
            material.update(ID=index + 1, Name=str(index + 1), SlotID=index + 1)
            for step in material['Route']['RouteSteps']:
                for visit in step['Visits']:
                    if visit['StationName'] == material['SrcPortName']:
                        visit['SlotID'] = [index + 1]
            materials.append(material)
        update['Materials'] = materials
        update['ProcessJobs'][0]['MatList'] = [material['ID'] for material in materials]
        update['ControlJobs'][0]['MaterialCount'] = len(materials)
    problem = capacity._algorithm().compile_problem(init, update)
    result = start_schedule_by_cycle(problem)
    moves = expand_dual_moves(result.machine_moves, problem.dual_view) if problem.dual_view else result.machine_moves
    product_jobs = {}
    for job in update.get('ProcessJobs', []):
        if not job.get('JobName') or not job.get('MatList'):
            continue
        for material in job['MatList']:
            product_jobs[material] = job['JobName']
    # 以首工序放片完成为边界，零秒产品配方可能没有 ProcessMove，仍有真实入腔。
    # 同步双腔只计一个逻辑访问，物理产出保留两个成员（奇数尾对只有一片）。
    visits = {}
    for move in moves:
        if move.get('MoveType') == PLACE_MOVE:
            arrivals = zip(move.get('MatIDList', []), move.get('DestStationList', []))
        elif move.get('MoveType') == SWAP_MOVE:
            sent = move.get('SendMatList', [])
            arrivals = zip(sent, move.get('StationList', [])[-len(sent):]) if sent else ()
        else:
            continue
        for material, station in arrivals:
            if material in product_jobs and station in settings['first_chambers']:
                key = (float(move['EndTime']), station)
                visits.setdefault(key, set()).add(material)
    ordered = sorted(visits.items())
    if settings['basis'] == 'wac':
        stride = settings['candidate_rounds']
        anchors = [key[0] for key, _ in ordered[::stride]]
        output_per_candidate = stride * settings['members']
    else:
        first_by_job = {}
        for (seconds, _), materials in ordered:
            for material in materials:
                first_by_job.setdefault(product_jobs[material], seconds)
        anchors = sorted(first_by_job.values())
        output_per_candidate = JOB_WAFERS
    start, end, phases = find_repeating_window(moves, anchors, settings['maximum_phases'])
    duration = end - start
    stations = plan['device']['Stations']
    chambers = {name for name, station in stations.items()
                if station.get('Type') in ('Process', 'ProcessChamber', 'MultiProcessChamber')}
    work, details = _resource_breakdown(moves, start, end, chambers)
    ports = {name for name, station in stations.items() if station.get('Type') == 'LoadPort'}
    returned = {(material, float(move['EndTime'])) for move in moves if move.get('MoveType') == PLACE_MOVE
                and start + TIMING_TOLERANCE < float(move['EndTime']) <= end + TIMING_TOLERANCE
                for material, station in zip(move.get('MatIDList', []), move.get('DestStationList', []))
                if material in product_jobs and station in ports}
    if len(returned) != output_per_candidate * phases:
        raise ValueError('重复窗口的实际产品回盒数与周期产出不一致，不能计算稳态产能')
    # Job 相位可能跨两个 Job 才复位；页面显示每 Job 的等效周期并披露完整周期。
    divisor = phases if settings['basis'] == 'job' else 1
    count = output_per_candidate * phases / divisor
    period = duration / divisor
    dominant = max(work, key=work.get)
    reference_resource = max(chambers.intersection(work), key=work.get)
    segments = [dict(row, seconds=row['seconds'] / divisor,
        terms=[dict(term, count=term['count'] / divisor) for term in row['terms']])
        for row in details[reference_resource]]
    segments.append({'label': '同步等待', 'seconds': (duration - work[reference_resource]) / divisor})
    return {'model_kind': 'cycle-periodic', 'period_seconds': period, 'seconds_per_unit': period / count,
        'physical_wafers_per_hour': SECONDS_PER_HOUR * count / period,
        'resource_work_seconds': {name: seconds / divisor for name, seconds in work.items()},
        'resource_period_lower_bound_seconds': work[dominant] / divisor,
        'physical_validation_passed': result.cycle_diagnostics['physicalValidationPassed'],
        'period_timing': {'basis': settings['basis'], 'output_wafers': count, 'phase_count': phases,
            'phase_factors': settings['phase_factors'], 'candidate_rounds': settings['candidate_rounds'],
            'physical_members_per_unit': settings['members'],
            'start_seconds': start, 'end_seconds': end, 'full_period_seconds': duration,
            'divisor': divisor, 'verified_repeats': REQUIRED_REPEATS,
            'reference': '首工序同相位产品放片完成', 'resource': reference_resource, 'segments': segments},
        'scope_note': '无限连续供片模型的 Cycle 重复排程周期；无清洁参照为论文事件图理论值。'}


def read_periodic_capacity(device, route, load_port, scan, theory, values, process):
    """扫描已验证的连续生产周期；无清洁基线始终采用原论文事件图，不随清洁变化。"""
    from app.backend.theory_capacity_workers import capacity_batch_workers
    from src.schedule.strategies.cycle.config import CONFIG_PATH
    scenario = parse_cleaning_scenario({'wac_interval': 2, 'dummy_visits': 1, 'dummy_inventory': 3, **values})
    points = capacity._scan_points_payload(scan)
    if len(points['process_seconds']) > MAX_SCAN_POINTS:
        raise ValueError(f'周期扫描最多 {MAX_SCAN_POINTS} 个点')
    if len(scenario.types) != 1:
        raise ValueError('周期模式请选择一种清洁')
    facts = theory['facts']
    basis = 'wac' if scenario.types == ('wacclean',) else 'job'
    if basis == 'wac' and scenario.wac_interval > MAX_CLEANING_COUNT:
        raise ValueError('周期模式的 WAC 次数仅支持 1～3')
    if scenario.types[0] in ('dummy', 'dummywac') and max(scenario.dummy_visits, scenario.dummy_inventory) > MAX_CLEANING_COUNT:
        raise ValueError('周期模式的 Dummy 趟数与库存仅支持 1～3')
    nodes = [stage['visits'] for stage in route['stages'] if stage.get('needProcess')]
    phase_factors = [2 if facts['lock_mode'] == 'shared-bidirectional' else 1,
                     *(len(node) * scenario.wac_interval for node in nodes)]
    rounds = lcm(*phase_factors)
    members = facts['physical_members_per_unit']
    job_rounds = ceil(JOB_WAFERS / members)
    maximum_phases = facts['rounds_per_cycle'] // gcd(facts['rounds_per_cycle'], job_rounds) if basis == 'job' else 1
    if scenario.types[0] in ('dummy', 'dummywac'):
        # 可复用 Dummy 的分配游标也有相位，混合路线可能每三 Job 才复位。
        maximum_phases = lcm(maximum_phases, scenario.dummy_inventory)
    job_count = REQUIRED_REPEATS * maximum_phases + BOUNDARY_GUARD_PERIODS if basis == 'job' else 1
    wafers = JOB_WAFERS if basis == 'job' else (REQUIRED_REPEATS + BOUNDARY_GUARD_PERIODS) * rounds * members
    scenario = replace(scenario, wafers=JOB_WAFERS, jobs=1, alternation='continuous')
    requests = []
    times = list(dict.fromkeys([process, *points['process_seconds']]))
    for seconds in times:
        plan = build_cleaning_plan(device, route, load_port, seconds, scenario, with_cleaning=True)
        # 无限供片是理论模型的入口条件，不复制真实端口，也不增加可复用 Dummy。
        # 所有 Job 独立计数，共用同一入口的真实搬运计时；存储容量只为离线展开。
        prototype = plan['rounds'][0]['cjobs'][0]
        pjobs = []
        for index in range(job_count):
            job = deepcopy(prototype['pjobs'][0])
            job.update(jobName=f'P{index + 1}', waferCount=JOB_WAFERS)
            pjobs.append(job)
        prototype['pjobs'] = pjobs
        supply = plan['device']['Stations'][prototype['loadPort']]
        supply['Capacity'] = job_count * wafers
        supply['Slots'] = list(range(1, supply['Capacity'] + 1))
        supply['TimeToAvailableOfSlot'] = {str(slot): 0.0 for slot in supply['Slots']}
        plan['_capacity_periodic'] = {'basis': basis, 'candidate_rounds': rounds, 'members': members,
            'phase_factors': phase_factors,
            'supply_wafers': wafers,
            'maximum_phases': maximum_phases,
            'first_chambers': [visit['stationName'] for visit in nodes[0]]}
        requests.append((json.dumps(plan, sort_keys=True, ensure_ascii=False), 1, CONFIG_PATH.read_text(encoding='utf-8-sig')))
    samples = dict(zip(times, capacity_batch_workers.solve(requests)))
    selected = dict(samples[process], route_name=route['name'], status='supported',
                    cleaning_types=list(scenario.types), process_seconds=[process] * len(nodes),
                    baseline_wafers_per_hour=theory['physical_wafers_per_hour'])
    return capacity._round_floats({'ok': True, 'name': route['name'], 'derivation': selected,
        'points': {'process_seconds': points['process_seconds'], 'physical_wafers_per_hour':
                   [samples[t]['physical_wafers_per_hour'] for t in points['process_seconds']]},
        'baseline_points': points})
