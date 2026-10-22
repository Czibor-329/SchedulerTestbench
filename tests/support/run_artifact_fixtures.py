"""提供运行制品保存边界的最小响应夹具。

只构造结果与日志的稳定 API 字段；批量和 HTTP 编排测试用此夹具替代磁盘写入，
真实文件、迁移和保留期由运行制品仓储测试覆盖。
"""

from __future__ import annotations


def saved_run_fields(artifact_id: str = "result-id", *, has_output: bool = True) -> dict:
    """返回单次保存的制品地址；无输出状态仅提供摘要与日志，不提供诊断链接。"""
    fields = {
        "artifactId": artifact_id,
        "summaryUrl": f"/api/artifacts/{artifact_id}/summary",
        "logUrl": f"/api/logs/{artifact_id}",
        "logFileName": f"ct-input-log-{artifact_id[:8]}.json",
    }
    if has_output:
        fields.update({
            "resultId": artifact_id,
            "resultUrl": f"/api/results/{artifact_id}",
            "ganttUrl": f"/movelist_gantt_viewer.html?src=/api/results/{artifact_id}?view=movelist",
        })
    return fields
