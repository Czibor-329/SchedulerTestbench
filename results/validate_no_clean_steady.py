"""在隔离内存中验证无清洁事件图；长供片只用于稳态检验，不改真实设备容量。"""
import json,sys
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path.cwd()));sys.stdout.reconfigure(encoding='utf-8')
from app.backend import application
from app.backend import theory_capacity_service as c
from app.backend.theory_capacity_presets import build_preset_device
from app.backend.theory_capacity_cleaning import build_cleaning_plan,parse_cleaning_scenario
from app.backend.theory_capacity_periodic import solve_periodic_plan
from app.backend.theory_capacity.device_graph import analyze_cycle_capacity_model
ID='460fad299cda43298409d7ce16d54906';device=c.get_workspace_device_overview(ID)
rows=[]
combinations=[('shared-bidirectional',x) for x in ('single-node','serial','parallel','mixed')]+[('dedicated-entry-exit',x) for x in ('single-node','serial','parallel')]+[('single-bidirectional',x) for x in ('single-node','parallel')]
if '--retry' in sys.argv:
 combinations=[('dedicated-entry-exit','serial')]
for lock,kind in combinations:
 for seconds in ((10,) if '--retry' in sys.argv else (10,80,240)):
  d,route,port=build_preset_device(device,kind,lock);cfg=c._default_route_config(route)
  for stage in cfg['stages'].values(): stage.update(processTime=seconds,recipeTime=seconds)
  problem,topology=c._compile_route(d,route['name'],cfg,port)
  theory=analyze_cycle_capacity_model(problem,topology); rounds=theory.facts.rounds_per_cycle
  plan=build_cleaning_plan(d,route,port,seconds,parse_cleaning_scenario({}),with_cleaning=False)
  plan['rounds'][0]['cjobs']=plan['rounds'][0]['cjobs'][:1]
  plan['rounds'][0]['cjobs'][0]['pjobs']=plan['rounds'][0]['cjobs'][0]['pjobs'][:1]
  supply=600
  station=plan['device']['Stations'][port];station.update(Capacity=supply,Slots=list(range(1,supply+1)),TimeToAvailableOfSlot={str(i):0.0 for i in range(1,supply+1)})
  # 复用连续单 PJob 的供片展开边界；plan 中没有任何 Clean 定义或引用。
  plan['_capacity_periodic']={'basis':'wac','candidate_rounds':rounds,'members':1,'phase_factors':[rounds],'supply_wafers':supply,'maximum_phases':12,'first_chambers':list(theory.facts.process_nodes[0].chambers)}
  try:
   result=solve_periodic_plan(json.dumps(plan));actual=result['seconds_per_unit']
   row={'lock':lock,'kind':kind,'process':seconds,'actual':actual,'theory':theory.report.seconds_per_unit,'difference':theory.report.seconds_per_unit-actual,'validation':result['physical_validation_passed'],'window':result['period_timing']}
  except Exception as error: row={'lock':lock,'kind':kind,'process':seconds,'error':str(error)}
  rows.append(row);print(lock,kind,seconds,round(row.get('difference',0),7),row.get('error',''),flush=True)
  Path('results/exp-no-clean-steady-retry.json' if '--retry' in sys.argv else 'results/exp-no-clean-steady-validation.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
