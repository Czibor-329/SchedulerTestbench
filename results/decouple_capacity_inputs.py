"""收敛理论产能到平台静态输入，迁移轨迹证据测试并暂停清洁入口。"""
from pathlib import Path
import re

ROOT = Path('D:/project/milp')
package = ROOT / 'app/backend/theory_capacity'
names = {'Problem': 'CapacityInput', 'Stage': 'RouteStage', 'Wafer': 'ReferenceProduct',
         'Robot': 'RobotTiming', 'Chamber': 'StationTiming', 'CleanSpec': 'CleaningRequirement', 'Durations': 'DeviceDurations'}
for relative in ['app/backend/theory_capacity/device_graph.py', 'app/backend/theory_capacity/timing_graph.py',
                 'tests/support/capacity_problem_builders.py']:
    path = ROOT / relative
    text = path.read_text(encoding='utf-8')
    text = text.replace('src.compiler.dual_chamber', 'app.backend.theory_capacity.device_input')
    text = text.replace('src.domain.model', 'app.backend.theory_capacity.input_model')
    text = text.replace('src.schedule.strategies.cycle.topology', 'app.backend.theory_capacity.topology')
    for old, new in names.items():
        text = re.sub(r'\b' + old + r'\b', new, text)
    path.write_text(text, encoding='utf-8')
path = ROOT / 'app/backend/theory_capacity_service.py'
text = path.read_text(encoding='utf-8')
start = text.index('    try:\n', text.index('def _capacity_runtime'))
end = text.index('    from app.backend.theory_capacity.device_graph', start)
text = text[:start] + ('    from app.backend.theory_capacity.device_input import compile_capacity_input\n'
                       '    from app.backend.theory_capacity.topology import analyze_capacity_topology\n') + text[end:]
text = text.replace('compile_problem: Callable', 'compile_capacity_input: Callable').replace('analyze_cycle_topology: Callable', 'analyze_capacity_topology: Callable')
text = text.replace('compile_problem=compile_problem', 'compile_capacity_input=compile_capacity_input').replace('analyze_cycle_topology=analyze_cycle_topology', 'analyze_capacity_topology=analyze_capacity_topology')
text = text.replace('runtime.compile_problem(', 'runtime.compile_capacity_input(').replace('runtime.analyze_cycle_topology(', 'runtime.analyze_capacity_topology(')
text = text.replace('平台求解器及按需加载的算法编译、拓扑能力集合。', '平台自己的输入解析、路线分类和事件图求解能力集合。')
text = text.replace('    算法仓库是可选依赖：平台在它缺失时仍必须能启动并给出可读错误，因此这里不用\n    模块级导入，而是在首次调用时加载。', '    所有能力均在 app.backend.theory_capacity；首次请求时装配并复用，不加载算法仓库。')
text = text.replace('装配平台建图与算法输入能力；可选算法依赖缺失时给出可读错误。', '装配完全属于平台的输入、建图与求解能力，无外部算法依赖。')
text = text.replace('``compile_problem``', '``compile_capacity_input``').replace('得到 ``Problem``', '得到平台 ``CapacityInput``')
path.write_text(text, encoding='utf-8')
for filename in ['test_theory_capacity_device_graph.py', 'test_theory_capacity_timing.py']:
    path = ROOT / 'tests' / filename
    text = path.read_text(encoding='utf-8')
    text = text.replace('from app.backend import bootstrap\n\n# 设备建图和轨迹核对需要可选的算法领域模型。\npytest.importorskip("src.domain.model", reason="需要 CT_ALGORITHM_ROOT 指向可用算法仓库")\n', '')
    text = text.replace('from src.schedule.strategies.cycle.topology import analyze_cycle_topology',
                        'from app.backend.theory_capacity.topology import analyze_capacity_topology')
    text = text.replace('analyze_cycle_topology(', 'analyze_capacity_topology(')
    if filename.endswith('_timing.py'):
        text = text.replace('from src.schedule.strategies.cycle.scheduler import start_schedule_by_cycle',
            'import json\nfrom pathlib import Path\n\nREFERENCE_TRACES = {row["key"]: row for row in json.loads(\n    (Path(__file__).parent / "support/capacity_reference_traces.json").read_text(encoding="utf-8"))["cases"]}')
        text = text.replace('数学事件图与生产 Machine 的重复轨迹。', '数学事件图与迁移前已验证的生产重复轨迹证据。')
        text = text.replace('不读取公司数据或用模型自己生成期望排程。', '不读取公司数据或用模型生成期望值，也不加载算法仓库；轨迹证据含原始 MoveList 哈希。')
        text = text.replace('    result = start_schedule_by_cycle(problem)\n    assert result.cycle_diagnostics[\'physicalValidationPassed\']\n    arrivals = sorted(m[\'EndTime\'] for m in result.machine_moves\n        if m[\'MoveType\'] == 4 and set(nodes[0]).intersection(m.get(\'StationList\', [])))',
            '    evidence = REFERENCE_TRACES[f"rotation:{mode}:{nodes}:{seconds}"]\n    assert evidence["physical_validation_passed"]\n    arrivals = sorted(row["seconds"] for row in evidence["swap_arrivals"]\n        if set(nodes[0]).intersection(row["stations"]))')
        old = '    result = start_schedule_by_cycle(problem)\n    assert result.cycle_diagnostics[\'physicalValidationPassed\']\n    arrivals = sorted(m[\'EndTime\'] for m in result.machine_moves\n        if m[\'MoveType\'] == 4 and \'PM1\' in m.get(\'StationList\', []))'
        replacement = '    evidence = REFERENCE_TRACES[KEY]\n    assert evidence["physical_validation_passed"]\n    arrivals = sorted(row["seconds"] for row in evidence["swap_arrivals"] if "PM1" in row["stations"])'
        text = text.replace(old, replacement.replace('KEY', 'f"asymmetric:{mode}:{seconds}"'), 1)
        text = text.replace(old, replacement.replace('KEY', '"dedicated-pressure"'), 1)
    path.write_text(text, encoding='utf-8')
# 已明确暂停的功能与其运行池整体退出，旧代码可从 Git 恢复；替代测试验证明确拒绝。
for filename in ['theory_capacity_cleaning.py', 'theory_capacity_periodic.py', 'theory_capacity_workers.py']:
    (ROOT / 'app/backend' / filename).unlink()
    (ROOT / 'tests' / f'test_{filename}').unlink()
