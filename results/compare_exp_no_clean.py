"""读取统一入口的完整排程，以重复动作窗口独立核对无清洁事件图。"""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
sys.stdout.reconfigure(encoding='utf-8')
from app.backend import application
from app.backend.artifacts.repository import read_result,read_replay_context
from app.backend.theory_capacity_presets import read_theory_capacity_preset
from app.backend.theory_capacity_periodic import find_repeating_window
from app.backend.analysis import _production_throughput
manifest={r['id']:r for r in json.load(open('results/exp-no-clean-cases.json',encoding='utf-8'))}
run=json.load(open('results/exp-no-clean-cycle.json',encoding='utf-8'))
rows=[]
for item in run['items']:
 if not item['ok']: continue
 meta=manifest.get(item['testId']) or {'kind':'single-node','lock':'single-bidirectional','process':int(item['testName'][1:])}
 p=meta['process']; theory=read_theory_capacity_preset(run['deviceId'],kind=meta['kind'],lock=meta['lock'],first_seconds=p,last_seconds=p+1,step_seconds=1,process_seconds=p)['derivation']
 result=read_result(item['resultId']); ctx=read_replay_context(item['resultId']); moves=result['MoveList']
 first=theory['facts']['process_nodes'][0]['chambers']
 anchors=[]
 for m in moves:
  if m['MoveType']==4 and set(first).intersection(m.get('StationList',[])) and m.get('SendMatList'):
   anchors.append(m['EndTime'])
  elif m['MoveType']==1 and set(first).intersection(m.get('DestStationList',[])):
   anchors.append(m['EndTime'])
 try:
  a,b,phases=find_repeating_window(moves,sorted(set(anchors)),theory['facts']['rounds_per_cycle'])
  observed=(b-a)/phases
 except ValueError as e:
  print('NO PERIOD',item['testName'],e); continue
 row={'name':item['testName'],'id':item['testId'],'kind':meta['kind'],'lock':meta['lock'],'process':p,'actual_period':observed,'theory_period':theory['seconds_per_unit'],'difference':theory['seconds_per_unit']-observed,'phases':phases,'window':[a,b],'display_wph':_production_throughput(moves,ctx['plan']['device'])['throughputPerHour'],'theory_wph':theory['physical_wafers_per_hour']}
 rows.append(row); print(row['name'],round(observed,5),round(row['theory_period'],5),round(row['difference'],5),flush=True)
Path('results/exp-no-clean-comparison.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
