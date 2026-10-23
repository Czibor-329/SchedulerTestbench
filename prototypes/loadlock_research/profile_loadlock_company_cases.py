"""读取 PSE300 公司 130 例正式请求，核对双槽授权并比较无清洁稳态候选。

只经平台正式计划构造读取 datasets，不修改测试、Cycle 或持久设备。
保留清洁/重入/多 PJob 边界，清洁用例不以删除清洁后的结果冒充真实收益。
整例基线由 run_dataset_suite.py 独立产生；本入口只做输入画像和数学研究。
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.backend import application
from app.backend.execution.batch_service import build_workspace_batch_plan
from app.backend.execution.plan_builder import build_round_update, BuildState
from prototypes.loadlock_research.search_loadlock_cycles import PSE300, read_timing
from prototypes.loadlock_research.template_comparison import compare_templates
from prototypes.loadlock_research.physical_witness import validate_physical_witness


def route_facts(route, recipes):
    """从标准 Route 读取 PM 工艺、真实锁槽位和清洁义务，不读取 UI 默认值。"""
    nodes, locks, clean_refs, limits, relays = [], [], [], [], []
    for name in ('PrePJob', 'PostPJob', 'PostCJob'):
        if route.get(name):
            clean_refs.append(name)
    for step in route['RouteSteps']:
        visits = step['Visits']
        names = [visit['StationName'] for visit in visits]
        if all(name in ('LA', 'LB') for name in names):
            locks.append({visit['StationName']: visit['SlotID'] for visit in visits})
        elif step.get('NeedProcess'):
            if all(name.startswith('PM') for name in names):
                nodes.append({visit['StationName']: recipes[(visit['StationName'], visit['ProcessRecipe'])]
                              + float((visit.get('MoveTimeOffset') or {}).get('9', 0)) for visit in visits})
            else:
                relays.extend(names)
        for visit in visits:
            for field in ('BeforeInPM', 'AfterOutPM'):
                if visit.get(field):
                    clean_refs.append(field)
            if visit.get('QTimeLimit', -1) >= 0 or visit.get('ResidencyConstraint', -1) >= 0:
                limits.append(visit['StationName'])
    seen = set()
    reentry = False
    for node in nodes:
        reentry |= bool(seen.intersection(node))
        seen.update(node)
    return dict(nodes=nodes, loadlocks=locks, all_loadlocks_two_slots=bool(locks) and all(
        set(slots) == {1, 2} for lock in locks for slots in lock.values()),
        clean_boundaries=sorted(set(clean_refs)), reentry=reentry,
        limits=limits, other_process_stations=relays,
        port=route['RouteSteps'][0]['Visits'][0]['StationName'])


def main():
    """逐例组合正式请求并去重计算稳态模板，保存画像和候选证据。"""
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp/loadlock-research/pse300-company130-profile.json')
    arguments = parser.parse_args()
    device = application.get_workspace_device(PSE300)
    source = ROOT / 'app/data/datasets' / PSE300 / 'device.json'
    timing, digest = read_timing(source)
    physical_device = json.loads(source.read_text(encoding='utf-8-sig'))
    cases = sorted((row for row in device['tests'] if row['group'] == '公司单腔示例集-130'),
                   key=lambda row: int(re.search(r'\d+', row['name'])[0]))
    output = dict(device_sha256=digest, group='公司单腔示例集-130', cases=[], profiles={})
    for case in cases:
        try:
            plan = build_workspace_batch_plan(device, case, 'cycle', {}, hongye_check=False)
            facts = []
            for round in plan['rounds']:
                update = build_round_update(plan, round, 0, BuildState())
                recipes = {(row['ModuleName'], row['Name']): float(row['Time']) for row in update['ProcessRecipes']}
                for job in update['ProcessJobs']:
                    facts.append(route_facts(job['OriginRoute'], recipes))
            cleaning = sorted({str(row['cleanType']) for row in plan['cleans']})
            profile_ids = []
            for fact in facts:
                if cleaning or fact['clean_boundaries'] or fact['reentry'] or fact['limits'] or fact['other_process_stations']:
                    continue
                if len(fact['loadlocks']) != 2 or any(set(lock) != {'LA', 'LB'} for lock in fact['loadlocks']):
                    continue
                if not fact['all_loadlocks_two_slots']:
                    continue
                key = hashlib.sha256(json.dumps([fact['nodes'], fact['port']], sort_keys=True).encode()).hexdigest()[:16]
                if key not in output['profiles']:
                    print(f"分析 {case['name']} {fact['port']} {fact['nodes']}", flush=True)
                    output['profiles'][key] = dict(nodes=fact['nodes'], port=fact['port'],
                        **compare_templates(timing, tuple(fact['nodes']), fact['port']))
                    best = output['profiles'][key]['best']
                    if best is not None:
                        best['physical_validation'] = validate_physical_witness(
                            best['witness'], physical_device, timing, repetitions=1)
                profile_ids.append(key)
            output['cases'].append(dict(id=case['id'], name=case['name'], clean_types=cleaning,
                input_sha256=hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                jobs=facts, profile_ids=profile_ids, cjob_cycles=[job.get('cjobCycle', 1)
                    for round in plan['rounds'] for job in round['cjobs']], finite_batch_improvement=None))
        except (KeyError, ValueError) as error:
            output['cases'].append(dict(id=case['id'], name=case['name'], error=str(error)))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = Counter('input-error' if row.get('error') else 'with-clean' if row['clean_types'] or any(
        job['clean_boundaries'] for job in row['jobs']) else 'no-clean' for row in output['cases'])
    print(json.dumps(dict(summary=summary, profiles=len(output['profiles']), output=str(arguments.output)), ensure_ascii=False))


if __name__ == '__main__':
    main()
