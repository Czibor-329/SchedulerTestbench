"""经平台目录服务追加用户授权的 exp 无清洁组合，保持已有测试不变。"""
import json,sys
from pathlib import Path
from copy import deepcopy
sys.path.insert(0,str(Path.cwd()))
sys.stdout.reconfigure(encoding='utf-8')
from app.backend import application
from app.backend.workspace.catalog_service import create_workspace_test,update_workspace_routes
from app.backend.workspace.repository import get_workspace_device_overview
from app.backend import theory_capacity_service as capacity
from app.backend.theory_capacity_presets import build_preset_device
ID='460fad299cda43298409d7ce16d54906'
device=get_workspace_device_overview(ID)
combinations=[('shared-bidirectional',x) for x in ('single-node','serial','parallel','mixed')]+[('dedicated-entry-exit',x) for x in ('single-node','serial','parallel')]+[('single-bidirectional','parallel')]
labels={'shared-bidirectional':'双向双通道','dedicated-entry-exit':'单向双通道','single-bidirectional':'双向单通道','single-node':'单节点','serial':'串行','parallel':'并行','mixed':'混合'}
routes=deepcopy(device['routes']); records=[]
for lock,kind in combinations:
 _,route,port=build_preset_device(device,kind,lock)
 # 复用完全相同的现有拓扑，缺少时以明确名称新增共享模板。
 topology=lambda r:[(s['kind'],s.get('needProcess',False),tuple(v['stationName'] for v in s['visits'])) for s in r['stages']]
 existing=next((r for r in routes if topology(r)==topology(route)),None)
 if existing: route=existing
 else:
  route['name']=f"exp-{labels[lock]}-{labels[kind]}"; route['group']='exp'; routes.append(route)
 records.append((lock,kind,route,port))
if len(routes)!=len(device['routes']): update_workspace_routes(ID,{'routes':routes},include_tests=False)
# 单一 LP1 连续补盒，避免混合不同 LoadPort 的计时；200 片满足页面中间 120 片窗口。
existing_names={r['name'] for r in get_workspace_device_overview(ID)['tests'] if r.get('group')=='exp'}
manifest=[]
for lock,kind,route,port in records:
 for seconds in (10,80,240):
  name=f"NC-{labels[lock]}-{labels[kind]}-P{seconds}"
  if name in existing_names: continue
  cfg=capacity._default_route_config(route)
  for s in cfg['stages'].values(): s.update(processTime=seconds,recipeTime=seconds)
  test=capacity._synthetic_test_case(route['name'],cfg,port)
  test.update(name=name,group='exp',strategy='cycle'); test.pop('id',None)
  cjob=test['rounds'][0]['cjobs'][0]; cjob.update(taskId='1',cjobCycle=8)
  cjob['pjobs'][0].update(waferCount=25,routeConfig=deepcopy(cfg))
  created=create_workspace_test(ID,test)
  manifest.append({'id':created['id'],'name':name,'kind':kind,'lock':lock,'process':seconds,'route':route['name']})
  print(created['id'],name)
Path('results/exp-no-clean-cases.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
