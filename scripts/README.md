# 维护脚本

这里只保留工作台日常测试与复现入口。从仓库根目录执行：

| 脚本 | 用途 |
| --- | --- |
| `run_test_suite.py` | 隔离运行平台、算法及前端测试；使用 `--suite` 选择范围，pytest 筛选放在 `--` 后。 |
| `run_dataset_suite.py` | 列出并验收 `app/data/datasets/` 的设备、测试组与测试。 |
| `run_performance_suite.py` | 在隔离服务中测量 HTTP 性能；默认报告写入 `.tmp/performance-report.json`。 |
| `replay_config_log.py` | 通过正式执行链路复现前端导出的输入日志。 |

离线 LoadLock 研究位于 `prototypes/loadlock_research/`。已完成的一次性数据修补、
临时 PDF 抽取、已退役的 Heuristic 打包入口和专用验收工作区 builder 已删除，
可通过 Git 历史追溯。

本地调试报告统一写入 `.tmp/`，不要在仓库根目录创建 `result`、`results`、
`artifacts` 等结果目录。平台运行制品继续使用 `app/exports/runs/`，不改变主数据存储规则。
