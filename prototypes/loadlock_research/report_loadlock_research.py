"""把 PSE300 离线搜索结果汇总为可审阅 HTML 和平台物理校验证据。

只消费已保存研究 JSON 与只读设备计时，不改变生产代码或主数据。
图中的条带来自标准 MoveList，展示稳态窗口；结论严格区分候选最好与下界证明。
"""
import argparse
from html import escape
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from prototypes.loadlock_research.search_loadlock_cycles import PSE300, read_timing
from prototypes.loadlock_research.physical_witness import validate_physical_witness

COLORS = {0: '#60a5fa', 1: '#38bdf8', 4: '#818cf8', 5: '#94a3b8',
          6: '#fbbf24', 7: '#f59e0b', 9: '#34d399', 10: '#f472b6'}
NAMES = {0: 'Pick', 1: 'Place', 4: 'Swap', 5: '转位', 6: '开门', 7: '关门', 9: '加工', 10: '切压'}


def timeline(physical, period):
    """绘制一个周期的 MoveList 资源时间线，悬停显示真实时间和物料。"""
    start, end = period + 10, 2 * period + 10
    resources = ['VTR', 'ATR', 'PM1', 'LA', 'LB', 'LP1']
    left, scale, row_height = 65, 1000 / period, 50
    parts = ['<svg viewBox="0 0 1090 355" role="img" aria-label="最优候选的六资源稳态甘特图">']
    for tick in range(0, int(period) + 1, 20):
        x = left + tick * scale
        parts.append(f'<path d="M{x} 20V320" stroke="#273449"/><text x="{x}" y="343" fill="#9aa9bd">{tick}s</text>')
    for index, resource in enumerate(resources):
        y = 28 + index * row_height
        parts.append(f'<text x="2" y="{y+22}" fill="#dce7f5">{resource}</text>')
        for move in physical['moves']:
            contacted = (move.get('StationList', []) + move.get('SrcStationList', [])
                         + move.get('DestStationList', [])) if move['MoveType'] in (0, 1, 4) else []
            if (move['ModuleName'] != resource and resource not in contacted) or move['EndTime'] <= start or move['StartTime'] >= end:
                continue
            x = left + (max(move['StartTime'], start) - start) * scale
            width = max(.7, (min(move['EndTime'], end) - max(move['StartTime'], start)) * scale)
            label = NAMES[move['MoveType']]
            if resource == 'VTR' and move['MoveType'] == 4:
                label = move['StationList'][0] + ' Swap'
            tooltip = escape(f"{resource} {label} | {move['StartTime']-start:.3f} → {move['EndTime']-start:.3f}s | MoveID {move['MoveID']}")
            parts.append(f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="32" rx="3" fill="{COLORS[move["MoveType"]]}"><title>{tooltip}</title></rect>')
            if width > 35:
                parts.append(f'<text x="{x+4:.2f}" y="{y+21}" fill="#101827" font-size="11">{label}</text>')
    return ''.join(parts) + '</svg>'


def main():
    """验证代表候选、写入完整 JSON 和一个包含计时证据的 HTML 页面。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--search', type=Path, default=ROOT / '.tmp/loadlock-research/pse300-single.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.tmp/loadlock-research/pse300-report.html')
    arguments = parser.parse_args()
    payload = json.loads(arguments.search.read_text(encoding='utf-8'))
    source = ROOT / 'app/data/datasets' / PSE300 / 'device.json'
    timing, digest = read_timing(source)
    if digest != payload['device_sha256']:
        raise ValueError('设备已变化，请重新搜索，不能混用旧结果和新设备做物理验证')
    device = json.loads(source.read_text(encoding='utf-8-sig'))
    chosen = [result for result in payload['results'] if result['process_seconds'] == 10 and result['witness']]
    if len(chosen) < 2:
        raise ValueError('报告需要加工 10 秒的两轮与四轮见证')
    evidence = []
    for result in chosen:
        physical = validate_physical_witness(result['witness'], device, timing)
        if not physical['passed']:
            raise ValueError(str(physical['issues']))
        evidence.append(dict(rounds=result['rounds'], process_seconds=10,
                             seconds_per_wafer=result['best_seconds_per_wafer'], **physical))
    evidence_path = arguments.output.with_name('pse300-physical-verification.json')
    evidence_path.write_text(json.dumps(dict(device_sha256=digest, results=evidence), ensure_ascii=False, indent=2), encoding='utf-8')
    best = min(chosen, key=lambda result: result['best_seconds_per_wafer'])
    physical = next(row for row in evidence if row['rounds'] == best['rounds'])
    period = best['witness']['graph']['period_seconds']
    baseline, candidate = best['baseline_seconds_per_wafer'], best['best_seconds_per_wafer']
    gain = baseline / candidate - 1
    proof_path = arguments.output.with_name('pse300-full-four.json')
    proof_note = '当前报告只有可行候选，尚未读取到完整四轮搜索证据。'
    if proof_path.is_file():
        proof = json.loads(proof_path.read_text(encoding='utf-8'))
        if proof['device_sha256'] == digest and any(row['completed'] and row['rounds'] == 4
                and row['family'] == 'all' and row['process_seconds'] == 10
                and abs(row['best_seconds_per_wafer'] - candidate) < 1e-5 for row in proof['results']):
            proof_note = '四轮搜索空间已全部枚举，包括 Swap、先放后取、先取后放、压力批分段与 ATM 顺序；31.95 秒/片为该空间最优。'
    rows = []
    for process in sorted({row['process_seconds'] for row in payload['results']}):
        row = min((row for row in payload['results'] if row['process_seconds'] == process), key=lambda row: row['best_seconds_per_wafer'])
        status = '达到固定 PM 下界' if row['certificate'] == 'fixed-PM-resource-bound' else '当前最佳；全局未证明'
        rows.append(f'<tr><td>{process:g} s</td><td>{row["baseline_seconds_per_wafer"]:.3f}</td><td>{row["best_seconds_per_wafer"]:.3f}</td><td>{status}</td></tr>')
    legend = ''.join(f'<span><i style="background:{COLORS[key]}"></i>{name}</span>' for key, name in NAMES.items())
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PSE300 · LoadLock 循环研究</title><style>
body{margin:0;background:#0d1523;color:#dce7f5;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif}main{max-width:1120px;margin:auto;padding:40px 28px}h1{font-size:32px;line-height:1.25}h2{margin-top:36px;font-size:21px}p{color:#afbdd0;max-width:950px}.eyebrow{color:#7dd3fc;letter-spacing:.13em;font-size:12px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.card{background:#172236;border:1px solid #293852;border-radius:12px;padding:20px}.metric{font-size:32px;font-weight:650;color:#fff}.small{font-size:13px;color:#afbdd0}table{border-collapse:collapse;width:100%;background:#121e30}td,th{text-align:left;padding:12px 16px;border-bottom:1px solid #28374c}th{color:#92c9e7}code{color:#a5d9fc}a{color:#7dd3fc}.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:12px}.legend i{display:inline-block;width:10px;height:10px;margin-right:5px}.note{border-left:3px solid #fbbf24;padding-left:16px}svg{width:100%;font-family:system-ui}details{margin-top:20px}pre{white-space:pre-wrap;font-size:12px;background:#172236;padding:15px;border-radius:8px}@media(max-width:700px){.cards{grid-template-columns:1fr}main{padding:24px 16px}td,th{padding:8px}}
</style><main><div class="eyebrow">OFFLINE RESEARCH · PSE300 · 2026-10-16</div>
<h1>固定 PM Swap 后，LoadLock 仍有优化空间</h1>
<p>范围：LP1 → LA/LB → PM1 → LA/LB → 原 LP1 槽；加工 10 秒、无清洁、无限连续供片。计时来自真实设备，全部循环使用真实双槽容量。</p>
<div class="cards"><div class="card"><div class="small">现有 A/B 单片循环</div><div class="metric">BASE s/片</div></div><div class="card"><div class="small">A/A/B/B 两片压力批</div><div class="metric">BEST s/片</div></div><div class="card"><div class="small">稳态吞吐提升</div><div class="metric">GAIN%</div></div></div>
<h2>找到的循环</h2><p><code>A Swap → PM1 Swap → A Swap → PM1 Swap → B Swap → PM1 Swap → B Swap → PM1 Swap</code></p>
<p>每把锁连续交接两次，两个成品到齐后 Vent；ATM 回收两片、补入两片，然后 Pump。另一把锁及 PM 同时工作。这个循环采用跨锁回程；单片 A/B 方案中还找到“ATM 先补空槽、再收成品”的 32.34 秒/片方案。</p>
<h2>按物理 MoveList 展开的稳态周期</h2><p>完整周期 PERIOD 秒，产出 4 片。悬停色块查看时间；图只截取一个重复窗口。</p>LEGEND CHART
<h2>加工时间改变后的结果</h2><table><thead><tr><th>PM 加工时间</th><th>现有循环 s/片</th><th>找到的最好值 s/片</th><th>证明状态</th></tr></thead><tbody>ROWS</tbody></table>
<h2>已经验证与尚未证明</h2><p>代表方案均通过独立 Gurobi LP 与 8 周期物料回放；两片压力批另从非空快照展开 3 个周期、312 条标准动作，通过平台 MoveList 物理校验。</p>
<p class="note">PROOF 更长周期尚未全部穷尽，该数值尚不是全局最优证明。固定单 PM 骨架的放松下界约 31.04 秒/片。尚未验证空机启动、整批收尾、清洁和公司数据集整例。</p>
<p>生产 Cycle、前端版本、设备与测试数据未修改。<a href="pse300-physical-verification.json">完整物理动作证据</a> · <a href="pse300-single.json">搜索与事件图证据</a></p>
<details><summary>复现入口与设备指纹</summary><pre>venv/Scripts/python.exe prototypes/loadlock_research/search_loadlock_cycles.py --process 0 10 15 20 30 60 120 --rounds 2 4 --seconds 5
venv/Scripts/python.exe prototypes/loadlock_research/search_loadlock_cycles.py --process 10 --rounds 4 --family all --seconds 600 --workers 4 --output .tmp/loadlock-research/pse300-full-four.json
venv/Scripts/python.exe prototypes/loadlock_research/search_loadlock_cycles.py --process 10 --rounds 4 --return-policy original --seconds 600 --workers 4 --output .tmp/loadlock-research/pse300-original-four.json
venv/Scripts/python.exe prototypes/loadlock_research/report_loadlock_research.py
SHA-256: DIGEST</pre></details></main></html>'''
    replacements = {'BASE': f'{baseline:.2f}', 'BEST': f'{candidate:.2f}', 'GAIN': f'{gain * 100:.1f}',
                    'PERIOD': f'{period:.2f}', 'LEGEND': f'<div class="legend">{legend}</div>',
                    'CHART': timeline(physical, period), 'ROWS': ''.join(rows), 'DIGEST': digest, 'PROOF': proof_note}
    for key, value in replacements.items():
        html = html.replace(key, value)
    arguments.output.write_text(html, encoding='utf-8')
    print(json.dumps(dict(report=str(arguments.output), physical_evidence=str(evidence_path),
                          physical=[{key: row[key] for key in ('rounds', 'passed', 'move_count')} for row in evidence]), ensure_ascii=False))


if __name__ == '__main__':
    main()
