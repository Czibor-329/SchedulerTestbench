"""离线分析显式周期事件 JSON，不改 Cycle 排程结果或设备数据。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.backend.theory_capacity.solver import CapacityModel, WaitingConstraint, analyze_capacity


def main() -> None:
    """读取模型、求最小周期，并输出带假设与等待链的 UTF-8 JSON。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    payload = json.loads(arguments.model.read_text(encoding="utf-8-sig"))
    model = CapacityModel(payload["name"], tuple(payload["events"]),
                          tuple(WaitingConstraint(**edge) for edge in payload["constraints"]),
                          payload["units_per_cycle"], payload.get("resource_work_seconds", {}),
                          tuple(payload.get("assumptions", ())))
    report = json.dumps(analyze_capacity(model).to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report + "\n", encoding="utf-8")
    else:
        print(report)


if __name__ == "__main__":
    main()
