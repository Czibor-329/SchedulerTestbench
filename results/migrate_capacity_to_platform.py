"""把理论产能模块及其测试迁入平台；只移动明确列出的文件。"""
from pathlib import Path

ROOT = Path('D:/project/milp')
DOCS = Path('D:/ct-scheduler-docs')
module_names = {
    'cycle_capacity': 'solver',
    'cycle_capacity_graph': 'device_graph',
    'cycle_capacity_contracts': 'contracts',
    'cycle_capacity_timing': 'timing_graph',
}
replacements = {f'src.analysis.{old}': f'app.backend.theory_capacity.{new}'
                for old, new in module_names.items()}

def rewrite(text):
    """替换模块引用，优先处理较长的名称。"""
    for old, new in sorted(replacements.items(), key=lambda row: -len(row[0])):
        text = text.replace(old, new)
    return text

def move(source, target):
    """迁移单个已知文件并统一导入，不覆盖已有目标。"""
    assert source.is_file() and not target.exists(), (source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rewrite(source.read_text(encoding='utf-8')), encoding='utf-8')
    source.unlink()

package = ROOT / 'app/backend/theory_capacity'
for old, new in module_names.items():
    move(ROOT / f'alg/src/analysis/{old}.py', package / f'{new}.py')
(package / '__init__.py').write_text(
    '"""平台理论产能求解：数学事件图、设备建图和计时契约。\n\n'
    'solver 只使用标准库；device_graph/timing_graph 读取算法领域与拓扑。\n'
    '页面服务负责输入编译和缓存，本包不执行生产排程或读写工作空间。\n"""\n', encoding='utf-8')
(ROOT / 'alg/src/analysis/__init__.py').unlink()
for old, new in [('test_cycle_capacity', 'test_theory_capacity_solver'),
                 ('test_device_cycle_capacity', 'test_theory_capacity_device_graph'),
                 ('test_cycle_capacity_timing', 'test_theory_capacity_timing')]:
    target = ROOT / f'tests/{new}.py'
    move(ROOT / f'alg/tests/{old}.py', target)
    text = target.read_text(encoding='utf-8').replace('tests.support.cycle_builders', 'tests.support.capacity_problem_builders')
    text = text.replace('tests/support/cycle_builders.py', 'tests/support/capacity_problem_builders.py')
    text = text.replace('test_cycle_capacity.py', 'test_theory_capacity_solver.py')
    if new != 'test_theory_capacity_solver':
        text = text.replace('import pytest\n', 'import pytest\n\nfrom app.backend import bootstrap\n\n# 设备建图和轨迹核对需要可选的算法领域模型。\npytest.importorskip("src.domain.model", reason="需要 CT_ALGORITHM_ROOT 指向可用算法仓库")\n')
    target.write_text(text, encoding='utf-8')
fixture = (ROOT / 'alg/tests/support/cycle_builders.py').read_text(encoding='utf-8')
fixture = fixture.replace('构造 Cycle 单片循环策略测试设备与路线的具名 builder。',
    '平台理论产能测试的确定性领域 builder。\n\n迁移自 Cycle 合成夹具，保持已有计时与拒绝分支前提；每次独立构造 Problem，\n不导入算法 tests 包，也不跨仓库共享可变实例。')
(ROOT / 'tests/support/capacity_problem_builders.py').write_text(fixture, encoding='utf-8')
move(ROOT / 'alg/scripts/analyze_cycle_capacity.py', ROOT / 'scripts/analyze_cycle_capacity.py')
for path in [ROOT / 'app/backend/theory_capacity_service.py', ROOT / 'alg/paper/scripts/prepare_examples.py']:
    path.write_text(rewrite(path.read_text(encoding='utf-8')), encoding='utf-8')
# 论文制品脚本仍从算法根目录运行；只为平台求解器加入父仓库路径，保留算法测试夹具优先级。
path = ROOT / 'alg/paper/scripts/prepare_examples.py'
text = path.read_text(encoding='utf-8').replace('sys.path.insert(0, str(ROOT))',
    'sys.path.insert(0, str(ROOT))\nsys.path.insert(1, str(ROOT.parent))')
path.write_text(text, encoding='utf-8')
# 同一能力只保留一套名称，更新运行源码、测试和主动维护的验证脚本。
for folder in ['app/backend', 'tests', 'results']:
    for path in (ROOT / folder).rglob('*.py'):
        if path == Path(__file__):
            continue
        text = path.read_text(encoding='utf-8')
        updated = rewrite(text)
        updated = updated.replace('_AlgorithmRuntime', '_CapacityRuntime').replace('_ALGORITHM_RUNTIME', '_CAPACITY_RUNTIME')
        if path.name.startswith('theory_capacity'):
            updated = updated.replace('_algorithm()', '_capacity_runtime()')
        if updated != text:
            path.write_text(updated, encoding='utf-8')
for path in [DOCS / 'pages/algorithm/26-cycle-capacity.md', DOCS / 'pages/engineering/architecture.md', ROOT / 'alg/paper/README.md']:
    text = path.read_text(encoding='utf-8')
    for old, new in module_names.items():
        text = text.replace(f'alg/src/analysis/{old}.py', f'app/backend/theory_capacity/{new}.py')
        text = text.replace(f'`{old}.py`', f'`{new}.py`')
    text = text.replace('alg/tests/test_device_cycle_capacity.py', 'tests/test_theory_capacity_device_graph.py')
    path.write_text(text, encoding='utf-8')
# 源码说明同步到新的模块边界。
for path in package.glob('*.py'):
    text = path.read_text(encoding='utf-8')
    for old, new in module_names.items():
        text = text.replace(f'{old}.py', f'{new}.py').replace(f'{old}.CycleActionTimingGraph', f'{new}.CycleActionTimingGraph')
    path.write_text(text, encoding='utf-8')
