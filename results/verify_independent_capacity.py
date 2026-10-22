"""禁用算法导入，核对平台求解与之前记录的 PSE300 真实重复排程。"""
from pathlib import Path
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['CT_ALGORITHM_ROOT'] = str(ROOT / 'results/nonexistent-algorithm')
sys.modules['src'] = None
from app.backend import theory_capacity_service as service
from app.backend.theory_capacity_presets import build_preset_device
from app.backend.theory_capacity.device_graph import analyze_cycle_capacity_model

reference = json.loads((ROOT / 'results/exp-no-clean-verified.json').read_text(encoding='utf-8'))
device = service.get_workspace_device_overview('460fad299cda43298409d7ce16d54906')
rows = []
for case in reference['cases']:
    temporary, route, port = build_preset_device(device, case['kind'], case['lock'])
    config = service._default_route_config(route)
    for stage in config['stages'].values():
        stage.update(processTime=case['process'], recipeTime=case['process'])
    problem, topology = service._compile_route(temporary, route['name'], config, port)
    value = analyze_cycle_capacity_model(problem, topology).report.seconds_per_unit
    rows.append({'lock': case['lock'], 'kind': case['kind'], 'process': case['process'],
        'reference_cycle_seconds': case['actual'], 'independent_seconds': value,
        'difference_seconds': value - case['actual']})
report = {'cases': rows, 'case_count': len(rows),
    'max_error_seconds': max(abs(row['difference_seconds']) for row in rows),
    'algorithm_modules_loaded': [name for name in sys.modules if name.startswith('src.')]}
(ROOT / 'results/capacity-independent-pse300.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(report['case_count'], report['max_error_seconds'], report['algorithm_modules_loaded'])
assert report['max_error_seconds'] < 1e-6
assert not report['algorithm_modules_loaded']
