"""汇总公司 130 例 Cycle 实测基线与独立稳态候选，严格区分两种证据。

输出 HTML 和紧凑 JSON；失败例不参与工期收益，未运行的新策略整例收益为 null。
输入来自统一数据集入口和 profile_loadlock_company_cases.py，不读取或修改主数据。
"""
from collections import Counter
from html import escape
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1] / 'results/loadlock-research'
TOLERANCE = 1e-5


def main():
    """按稳定测试 ID 连接证据，保存可复核的对比边界及逐例结果。"""
    baseline = json.loads((DIRECTORY / 'pse300-company130-cycle-baseline.json').read_text(encoding='utf-8'))
    profile = json.loads((DIRECTORY / 'pse300-company130-profile.json').read_text(encoding='utf-8'))
    measured = {row['testId']: row for row in baseline['items']}
    rows, html_rows = [], []
    counts = Counter()
    for case in profile['cases']:
        old = measured[case['id']]
        models = [profile['profiles'][key] for key in set(case.get('profile_ids', []))]
        gains = [max(0.0, row['baseline_seconds_per_wafer'] / row['best']['seconds_per_wafer'] - 1)
                 for row in models if row['best'] and row['best']['physical_validation']['passed']]
        if case.get('error'):
            category = '研究输入错误'
        elif gains:
            category = '找到稳态改善' if max(gains) > TOLERANCE else '候选未改善稳态'
        elif case['clean_types']:
            category = '清洁未建模'
        else:
            category = '重入未建模'
        counts[category] += 1
        row = dict(test_id=case['id'], name=case['name'], baseline_passed=old['ok'],
                   baseline_makespan_seconds=old.get('makespan') if old['ok'] else None,
                   baseline_error=old.get('error'), clean_types=case.get('clean_types'),
                   research_category=category, steady_throughput_gain_min=min(gains) if gains else None,
                   steady_throughput_gain_max=max(gains) if gains else None,
                   optimized_finite_makespan_seconds=None, finite_makespan_reduction=None)
        rows.append(row)
        time = f"{old['makespan']:,.2f}" if old['ok'] else '失败'
        gain = f'{100 * min(gains):.2f}%—{100 * max(gains):.2f}%' if gains else '未计算'
        details = escape(old.get('error') or '')
        label = escape(case['name'])
        if details:
            label = f'<details><summary>{label}</summary><pre>{details}</pre></details>'
        html_rows.append(f'<tr><td>{label}</td><td>{escape(", ".join(case.get("clean_types", [])) or "无清洁")}</td>'
                         f'<td>{time}</td><td>{category}</td><td>{gain}</td></tr>')
    failures = Counter('Dummy 补片模板停滞' if 'DummyPort' in (row.get('error') or '') else
                       '有限驻留不支持' if 'residency-not-supported' in (row.get('error') or '') else
                       'Q-time 不支持' if 'qtime-not-supported' in (row.get('error') or '') else '其它'
                       for row in baseline['items'] if not row['ok'])
    report = dict(baseline_passed=baseline['succeeded'], baseline_failed=baseline['failed'],
                  baseline_failure_categories=failures, research_categories=counts,
                  optimized_finite_batch_run=False, all_loadlocks_two_slots=all(
                      all(job['all_loadlocks_two_slots'] for job in case['jobs']) for case in profile['cases']),
                  rows=rows)
    (DIRECTORY / 'pse300-company130-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSE300 公司 130 例 · LoadLock 研究</title><style>
body{margin:0;background:#101925;color:#dce7f5;font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1150px;margin:auto;padding:36px 24px}h1{font-size:29px}h2{font-size:21px;margin-top:32px}p{color:#b3c2d5}.note{padding:15px 20px;border-left:3px solid #f9bf50;background:#202b3b}table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;border-bottom:1px solid #304055;padding:10px}th{background:#213146;position:sticky;top:0}a{color:#7dd3fc}pre{white-space:pre-wrap;max-width:500px;font-size:12px}code{color:#a5d9fc}.cards{display:flex;gap:16px;flex-wrap:wrap}.cards>div{background:#1b293b;padding:20px;flex:1;border-radius:10px}.cards b{display:block;font-size:30px;color:#fff}input{padding:10px;margin:12px 0;background:#213146;color:white;border:1px solid #53657d;border-radius:6px;width:260px}</style>
<main><p>2026-10-16 · 原始测试不变 · 固定 PM 访问顺序</p><h1>PSE300：两片 LoadLock 的收益取决于工况</h1>
<div class="cards"><div>Cycle 整例基线<b>BASELINE_COUNT 通过</b></div><div>无清洁稳态比较<b>IMPROVED_COUNT 例有改善</b></div><div>清洁用例<b>CLEAN_COUNT 例待建模</b></div></div>
<p class="note">尚未运行“优化后的 Cycle”完整 130 例。下表的工期是原 Cycle 实测；改善百分比是各单端口、同工艺无限稳态模型的吞吐潜力，两者不能相减得出整例收益。</p>
<h2>本轮得到的证据</h2><p>全部 130 例的标准 Route 都授权 LA/LB 两个槽。23 例无清洁，其中 20 例可比较，3 例重入未建模；107 例含清洁。36 个工艺与端口组合的最佳候选通过独立 LP、8 周期物料回放和平台稳态物理 MoveList 校验。</p>
<p>test6/7/73/74/75/121–124 的三 PM 并行短工艺：37.930–38.230 → 31.768–31.968 秒/片，稳态吞吐提升 19.40%–19.59%。test8：37.930–38.230 → 32.453 秒/片，提升 16.88%–17.80%。这不是任意长周期全局最优证明。</p>
<p>原 Cycle 基线有 25 例 Dummy 补片模板停滞、1 例有限驻留不支持、1 例 Q-time 不支持；失败例不计算收益。当前 cleanHandoffLoadLockOverlap 已开启，不能把这项已有优化重复计为新收益。</p>
<h2>搜索空间与接入方式</h2><p>固定 PM 骨架后，每轮有 10 种 VAC 进出锁与取放组合；k 轮原始序列为 10ᵏ，另需枚举压力分批和 ATM 顺序。仅保留 LoadLock Swap 时为 2ᵏ。周期长度不设上限则不是固定有限种；三 PM 轮转与 AABB 要共同展开 12 轮。</p>
<p>可以在 Cycle 宏计划之前增加候选选择，仍由 Machine 处理真实门、压力和清洁。需要同时落地完整压力批、有限批次启动收尾、重算状态恢复和新序列审计。当前 dedicatedLoadLockBatchSize=2 仅覆盖分向锁，不能直接开启共享锁 AABB。</p>
<p>无清洁模型已计入 Pump/Vent，固定候选的周期是最短可行周期。加入清洁后须增加空腔交接、WAC 计数、PJob 与 Dummy 库存相位；仅把清洁时长平均摊入加工时间不能证明最优。</p>
<h2>逐例证据</h2><label>筛选 <input id="filter" placeholder="测试名、清洁类型或状态"></label><table><thead><tr><th>测试</th><th>清洁</th><th>原 Cycle 工期 / 秒</th><th>研究范围</th><th>稳态吞吐潜力</th></tr></thead><tbody>ROWS</tbody></table>
<p><a href="pse300-company130-summary.json">紧凑汇总</a> · <a href="pse300-company130-cycle-baseline.json">完整基线结果</a> · <a href="pse300-company130-profile.json">输入画像与候选动作证据</a></p></main>
<script>document.getElementById('filter').addEventListener('input',event=>{const query=event.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(query));});</script></html>'''
    replacements = {'ROWS': ''.join(html_rows), 'BASELINE_COUNT': f"{baseline['succeeded']} / {baseline['testCount']}",
                    'IMPROVED_COUNT': str(counts['找到稳态改善']), 'CLEAN_COUNT': str(counts['清洁未建模'])}
    for marker, value in replacements.items():
        html = html.replace(marker, value)
    (DIRECTORY / 'pse300-company130-report.html').write_text(html, encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
