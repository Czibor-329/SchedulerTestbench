"""同步独立理论产能的当前边界，历史验收仅保留为历史记录。"""
from pathlib import Path

ROOT = Path('D:/project/milp')
DOCS = Path('D:/ct-scheduler-docs/pages')

path = DOCS / 'algorithm/26-cycle-capacity.md'
text = path.read_text(encoding='utf-8')
start, end = text.index('## 清洁与连续生产周期'), text.index('## 先看片间隔')
text = text[:start] + '''## 当前支持范围

理论产能完全由平台 `app/backend/theory_capacity/` 计算，不导入算法仓库。
`device_input.py` 读取平台标准设备与路线载荷，`input_model.py` 定义专用静态输入，
`topology.py` 识别锁分工与加工组合，`device_graph.py` / `timing_graph.py` 建图，
`solver.py` 求解周期；`contracts.py` 定义输出契约。不会运行 Cycle 或 Machine。

当前仅开放 **No Clean / mode=steady**。PreClean、PostClean、WAC、Dummy、DummyWAC
及 `mode=batch`、`mode=periodic` 暂不支持；页面禁用对应选项，旧请求返回 HTTP 400
和明确说明，不回退到无清洁结果。原清洁批次、周期模块和进程池已退出当前运行代码。
生产调度中的清洁规则继续见[五种清洁与 WAC 计数](#documentation/algorithm-cleaning)。

无清洁使用无限连续供片、固定入口、固定轮转和单片压力批假设。完整相位周期除以
轮数得到平均交替周期；产出按物理成员数计算。独立输入读取精确取放/转位四元组，
拒绝缺失或非法时间、不可达站点、分支路线、清洁义务及当前未建模的非加工动作偏移。
加工配方时间包含 `MoveTimeOffset[9]`；有限驻留和 Q-time 明确拒绝。

''' + text[end:]
text = text.replace('输入是生产编译器输出的 `Problem`\n（含双腔逻辑对片视图）与 `analyze_cycle_topology(problem)` 的识别结果',
    '输入是平台静态解析器输出的 `CapacityInput`\n（含双腔逻辑计时视图）与 `analyze_capacity_topology(problem)` 的识别结果')
start = text.index('`device_graph.py` 额外依赖')
end = text.index('相关测试全部在平台', start)
text = text[:start] + '''设备解析、双腔成对时间和模板识别也全部归平台，安装或启动算法仓库不再是
理论产能的前提。算法仓库不保留原求解器实现或转发入口。
用户请求的文章位于 `alg/paper/src/main.tex`；它作为制品引用平台求解器，
当前使用与接口规则仍以本文为准。

''' + text[end:]
text = text.replace('具名合成 Problem', '具名合成 CapacityInput')
text = text.replace('## 无清洁模型原始核对记录', '## 历史验收记录\n\n以下清洁、有限批次及旧模块的记录反映对应日期的版本；当前功能以“当前支持范围”为准。\n\n### 无清洁模型原始核对记录')
for title in ['清洁页面验收（2026-10-13）', '清洁扫描性能', '连续周期页面回归（2026-10-13）', '无清洁动作计时一致性（2026-10-14）']:
    text = text.replace('## ' + title, '### ' + title)
text += '''
## 平台独立化迁移（2026-10-14）

迁移前算法侧相关 79 项、平台侧相关 107 项全部通过，无失败或跳过。
79 项求解、建图和计时断言迁入平台，保留相同参数覆盖。43 个生产轨迹案例先完成
原物理校验和周期断言，再冻结为平台 `tests/support/capacity_reference_traces.json`，
记录入腔事件与完整 MoveList 的 SHA-256；测试直接对照这些独立证据，不加载算法。
这证明迁移与已记录行为一致，不等同于每次测试都重跑生产排程。

按用户确认，本阶段暂停清洁计算：原清洁批次、周期和工作池的 63 项测试退出，
改由 19 项范围/HTTP 拒绝测试覆盖五种清洁和旧计算口径，防止静默给出错误结果。
新增隔离进程测试禁止整个算法命名空间，并将算法目录设为不存在，验证纯求解、
CLI 与全部 9 种页面组合仍然可用。静态输入另覆盖配方偏移、双腔通道、不可达
站点、非有限时间和分支拒绝。无清洁数学断言未放宽。
'''
path.write_text(text, encoding='utf-8')

path = DOCS / 'platform/09-analysis-basics.md'
text = path.read_text(encoding='utf-8').replace('缺少指定 PM、锁或 DummyPort 时明确报错。', '缺少指定 PM 或锁时明确报错。求解和输入解析完全由平台完成，无需算法仓库。')
start, end = text.index('### 清洁类型与参数'), text.index('### 与测试运行产能比较')
text = text[:start] + '''### 当前计算范围

当前只支持 **No Clean（无清洁）**。PreClean、PostClean、WAC、Dummy、DummyWAC
选项显示“暂不支持”并禁用；清洁参数区隐藏。旧客户端请求清洁、有限批次或
Cycle 排程周期时会得到明确错误，不能用无清洁曲线替代。

LoadLock 提供双向双通道、单向双通道、双向单通道；分别支持相应固定 PM 组合。
无清洁事件图区分门、原子换片、机器人转位和切压的并行/等待关系，求锁与 PM
轮转相位共同复位的完整周期，再除以交替轮数。假定固定入口和无限连续供片，
不包括生产启动、收尾及 Job 交接，也不生成可执行 MoveList。

### 读取计算结果

编辑设备、组合、LoadLock 或扫描区间后点击“重新计算”。加工时间滑块用于查看
选定工艺时间的产能及公式；右侧显示周期产出、资源下界、关键等待链和每小时产能。
计算失败时不继续显示上一组输入的旧结果。所有计时来自设备当前配置，计算不会
写入设备或测试主数据。

''' + text[end:]
path.write_text(text, encoding='utf-8')

path = DOCS / 'engineering/architecture.md'
text = path.read_text(encoding='utf-8')
text = text.replace('把编译结果与 Cycle 路线事实', '把平台静态输入与模板事实')
text = text.replace('设备建图读取算法编译后的 Problem 和 Cycle 拓扑。\n服务按需装配这两类能力。', '设备建图、静态输入读取、双腔计时与模板识别也完全属于平台。\n服务按需装配这些能力，不导入算法仓库。')
text = text.replace('三者都不写数据、不修改设备 `updatedAt`；算法仓库缺失时\n返回 503，参数非法时返回 400。', '三者都不写数据、不修改设备 `updatedAt`，不依赖算法仓库；参数非法时返回 400。\n固定组合入口只接受无清洁稳态；清洁与旧 batch/periodic 口径暂不支持并返回 400。')
insert = '| `app/backend/theory_capacity/contracts.py`'
position = text.index(insert)
text = text[:position] + '''| `app/backend/theory_capacity/input_model.py` | 专用静态计时、参考路线和输入契约 | 生产排程状态 |
| `app/backend/theory_capacity/device_input.py` | 标准设备/路线读取、双腔成对计时、精确转位与输入拒绝 | 算法编译器、工作空间写入 |
| `app/backend/theory_capacity/topology.py` | 静态锁分工、加工组合及可建图条件 | 冷启动与重算相位 |
''' + text[position:]
path.write_text(text, encoding='utf-8')

path = ROOT / 'app/backend/theory_capacity/__init__.py'
path.write_text('''"""平台独立理论产能：静态设备输入、路线分类、事件图与最小周期求解。

全部模块仅依赖标准库和本包，不导入算法仓库，不执行生产排程。
页面服务负责平台载荷构造及缓存；当前只支持无清洁稳态。
"""
''', encoding='utf-8')
path = ROOT / 'app/backend/theory_capacity_service.py'
text = path.read_text(encoding='utf-8').replace('算法侧能力集合', '平台产能能力集合').replace('不需要导入算法侧异常类型', '不需要外部异常类型')
text = text.replace('按与真实运行完全相同的编译链路', '按平台标准载荷构造链路')
text = text.replace('（由 Cycle 拓扑\n识别明确拒绝）', '（由平台静态路线\n识别明确拒绝）')
path.write_text(text, encoding='utf-8')
path = ROOT / 'app/backend/theory_capacity/device_graph.py'
text = path.read_text(encoding='utf-8').replace('Cycle 策略识别出的', '平台静态识别器输出的').replace('CycleTopology', 'CapacityTopology').replace('analyze_cycle_topology', 'analyze_capacity_topology')
text = text.replace('避免在生产编译器之外产生第二套时间口径', '保证扫描与单点推导使用同一输入口径')
path.write_text(text, encoding='utf-8')
