"""为 TWINS 设备补齐 ATM 机械手到 Buffer 模块的能力配置。

TWINS 设备已声明 ``Buffer1``~``Buffer4``（``Type=Buffer``），但 ATM 手没有任何到
Buffer 的可达性、取放时间与转位时间，Buffer 站点自身的动作时间还错误地沿用了
PSE300 的机器人名 ``ATR``。本脚本按设备现有约定补齐：

- ``PickTime``/``PlaceTime``：Buffer 取放时间沿用当前 ``ATMRobot`` 在 ``P1`` 的
  取值（``--pick-time``/``--place-time`` 可覆盖）。
- ``ArmInfo.*.AccessibleStations`` 与 ``SlotsStationMap``：按每个启用手槽到
  Buffer 全部槽位的候选映射补齐。
- ``PrepTransTime``：按现有完整矩阵的写法补齐 Buffer ↔ 其它站点；与 ``P1``
  之间以及与其它 Buffer 之间使用现有跨站值（``P1``↔``P2``），与其它站点之间
  沿用 ``P1`` 到该站点的取值；同站转位为 0，缺少参照时拒绝写入。
- Buffer 站点动作时间的机器人键名 ``ATR`` 改为设备实际机器人名 ``ATMRobot``。

时间取值属于工程数据，脚本只做同构迁移并打印改动摘要，便于后续按实测值修正。
脚本幂等：已存在的字段不覆盖；写入前把 ``device.json``/``metadata.json`` 备份到
``.tmp/dataset-backup-<时间戳>/``，并同步刷新 ``metadata.json`` 的设备指纹。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET_STORE = PROJECT_ROOT / "app" / "data" / "datasets"
BACKUP_ROOT = PROJECT_ROOT / ".tmp"
DEVICE_ID = "0c32dbfb6fb748eb99365fec835c4646"
BUFFER_STATION_TYPE = "buffer"
ATM_ROBOT = "ATMRobot"
LEGACY_ATM_NAME = "ATR"
REFERENCE_STATION = "P1"
CROSS_STATION_REFERENCE = "P2"
TRANSFER_TYPES = (0, 1)
STATION_ROBOT_TIMING_FIELDS = (
    "PickPrepareTime", "PickCompleteTime", "PlacePrepareTime",
    "PlaceCompleteTime", "PostCompleteTime",
)


def _device_directory(store_dir: Path, device_id: str) -> Path:
    """按 ``metadata.json`` 中的稳定 ID 定位设备目录。"""
    for candidate in sorted(store_dir.iterdir()):
        metadata_file = candidate / "metadata.json"
        if not candidate.is_dir() or not metadata_file.is_file():
            continue
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        if isinstance(metadata, Mapping) and str(metadata.get("id") or "") == device_id:
            return candidate
    raise ValueError(f"数据目录中找不到设备 {device_id}")


def _buffer_station_names(stations: Mapping[str, Any]) -> List[str]:
    """按名称顺序返回设备声明的 Buffer 站点。"""
    return sorted(
        str(name)
        for name, station in stations.items()
        if isinstance(station, Mapping)
        and str(station.get("Type") or "").strip().casefold() == BUFFER_STATION_TYPE
    )


def _transfer_times(robot: Mapping[str, Any]) -> Dict[tuple[str, str, int], float]:
    """把机器人转位矩阵读成 ``(src, dst, trans_type) -> time`` 字典。"""
    matrix: Dict[tuple[str, str, int], float] = {}
    for row in robot.get("PrepTransTime") or []:
        if not isinstance(row, Mapping):
            continue
        key = (
            str(row.get("SrcStation") or ""),
            str(row.get("DestStation") or ""),
            int(row.get("TransType", -1)),
        )
        matrix[key] = float(row.get("Time") or 0.0)
    return matrix


def _reference_transfer_time(
    matrix: Mapping[tuple[str, str, int], float],
    other: str,
    trans_type: int,
) -> float:
    """给出 Buffer 到另一站点的转位时间：跨站沿用 P1↔P2，其余沿用 P1 到该站。"""
    key = (REFERENCE_STATION, other, trans_type)
    if other == REFERENCE_STATION or key not in matrix:
        key = (REFERENCE_STATION, CROSS_STATION_REFERENCE, trans_type)
    if key not in matrix:
        raise ValueError(f"缺少 Buffer 转位时间参照 {key}")
    return float(matrix[key])


def _complete_station_timing_keys(
    stations: MutableMapping[str, Any],
    buffer_names: Sequence[str],
) -> List[str]:
    """把 Buffer 站点动作时间中的旧机器人名改写为设备实际机器人名。"""
    changed: List[str] = []
    for name in buffer_names:
        station = stations.get(name)
        if not isinstance(station, dict):
            continue
        for field_name in STATION_ROBOT_TIMING_FIELDS:
            timing = station.get(field_name)
            if not isinstance(timing, dict) or LEGACY_ATM_NAME not in timing:
                continue
            timing.setdefault(ATM_ROBOT, timing.pop(LEGACY_ATM_NAME))
            changed.append(f"{name}.{field_name}[{LEGACY_ATM_NAME}->{ATM_ROBOT}]")
    return changed


def _complete_robot_times(
    robot: MutableMapping[str, Any],
    buffer_names: Sequence[str],
    *,
    pick_time: float,
    place_time: float,
) -> List[str]:
    """补齐 ATM 手对 Buffer 的取放时间。"""
    changed: List[str] = []
    for field_name, value in (("PickTime", pick_time), ("PlaceTime", place_time)):
        timing = robot.setdefault(field_name, {})
        if not isinstance(timing, dict):
            continue
        for name in buffer_names:
            if name in timing:
                continue
            timing[name] = value
            changed.append(f"{ATM_ROBOT}.{field_name}[{name}]={value}")
    return changed


def _complete_arm_scope(
    robot: MutableMapping[str, Any],
    stations: Mapping[str, Any],
    buffer_names: Sequence[str],
) -> List[str]:
    """把 Buffer 站点加入每个启用臂的可达范围与槽位映射。"""
    changed: List[str] = []
    arm_info = robot.get("ArmInfo")
    if not isinstance(arm_info, Mapping):
        return changed
    for arm_name, arm in arm_info.items():
        if not isinstance(arm, dict) or arm.get("IsEnable") is False:
            continue
        accessible = arm.setdefault("AccessibleStations", [])
        slot_map = arm.setdefault("SlotsStationMap", {})
        slots = [int(slot) for slot in arm.get("SlotIDs") or []]
        for name in buffer_names:
            if name not in accessible:
                accessible.append(name)
                changed.append(f"{ATM_ROBOT}.{arm_name}.AccessibleStations+{name}")
            if name in slot_map:
                continue
            station_slots = [
                int(slot) for slot in ((stations.get(name) or {}).get("Slots") or [])
            ] or [1]
            slot_map[name] = {
                str(slot): [
                    {"Key": name, "Value": station_slot}
                    for station_slot in station_slots
                ]
                for slot in slots
            }
            changed.append(f"{ATM_ROBOT}.{arm_name}.SlotsStationMap+{name}")
    return changed


def _complete_transfers(
    robot: MutableMapping[str, Any],
    buffer_names: Sequence[str],
) -> List[str]:
    """补齐 Buffer 与其它站点的双向转位时间，保持设备现有完整矩阵写法。"""
    rows = robot.setdefault("PrepTransTime", [])
    matrix = _transfer_times(robot)
    other_stations = sorted({
        src for src, _dst, _type in matrix
    } | {dst for _src, dst, _type in matrix} | set(buffer_names))
    changed: List[str] = []
    for name in buffer_names:
        for partner in other_stations:
            for src, dst in ((name, partner), (partner, name)):
                for trans_type in TRANSFER_TYPES:
                    if (src, dst, trans_type) in matrix:
                        continue
                    time = 0.0 if src == dst else _reference_transfer_time(
                        matrix, partner if src == name else src, trans_type,
                    )
                    rows.append({
                        "SrcStation": src,
                        "DestStation": dst,
                        "Time": time,
                        "TransType": trans_type,
                    })
                    matrix[(src, dst, trans_type)] = time
                    changed.append(f"{ATM_ROBOT}.PrepTransTime[{src}->{dst}/{trans_type}]={time}")
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    """执行幂等补齐并打印改动摘要。"""
    parser = argparse.ArgumentParser(description="补齐 TWINS 设备的 Buffer 机械手能力")
    parser.add_argument("--store-dir", type=Path, default=DATASET_STORE)
    parser.add_argument("--device-id", default=DEVICE_ID)
    parser.add_argument("--pick-time", type=float, default=None, help="缺省取当前 ATMRobot 在 P1 的 PickTime")
    parser.add_argument("--place-time", type=float, default=None, help="缺省取当前 ATMRobot 在 P1 的 PlaceTime")
    parser.add_argument("--apply", action="store_true", help="实际写入，缺省只做比对")
    args = parser.parse_args(argv)

    device_dir = _device_directory(args.store_dir, args.device_id)
    device_path = device_dir / "device.json"
    metadata_path = device_dir / "metadata.json"
    device = json.loads(device_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stations = device.get("Stations")
    robot = (device.get("Robots") or {}).get(ATM_ROBOT)
    if not isinstance(stations, dict) or not isinstance(robot, dict):
        raise ValueError("设备缺少 Stations 或 ATMRobot 配置")
    buffer_names = _buffer_station_names(stations)
    if not buffer_names:
        raise ValueError("设备没有 Type=Buffer 站点")

    pick_time = args.pick_time
    place_time = args.place_time
    if pick_time is None or place_time is None:
        p1_pick = (robot.get("PickTime") or {}).get(REFERENCE_STATION)
        p1_place = (robot.get("PlaceTime") or {}).get(REFERENCE_STATION)
        pick_time = p1_pick if pick_time is None else pick_time
        place_time = p1_place if place_time is None else place_time
    for label, value in (("PickTime", pick_time), ("PlaceTime", place_time)):
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"{label} 必须提供非负有限时间或有效 P1 参照")
    pick_time, place_time = float(pick_time), float(place_time)

    changes = [
        *_complete_station_timing_keys(stations, buffer_names),
        *_complete_robot_times(
            robot, buffer_names, pick_time=pick_time, place_time=place_time,
        ),
        *_complete_arm_scope(robot, stations, buffer_names),
        *_complete_transfers(robot, buffer_names),
    ]
    print(f"设备目录：{device_dir}")
    print(f"Buffer 站点：{', '.join(buffer_names)}")
    print(f"取放时间：PickTime={pick_time}（P1 取值），PlaceTime={place_time}（P1 取值）")
    print(f"改动项：{len(changes)}")
    for item in changes[:8]:
        print(f"  {item}")
    if len(changes) > 8:
        print(f"  ... 其余 {len(changes) - 8} 项")
    if not changes:
        print("设备已具备 Buffer 机械手能力，无需改写")
        return 0
    if not args.apply:
        print("比对模式：加 --apply 才会写入")
        return 0

    backup_directory = BACKUP_ROOT / f"dataset-backup-{datetime.now():%Y%m%d-%H%M%S}"
    backup_directory.mkdir(parents=True, exist_ok=True)
    for path in (device_path, metadata_path):
        (backup_directory / path.name).write_bytes(path.read_bytes())

    from app.backend.workspace import repository

    device_path.write_text(
        json.dumps(device, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    metadata["fingerprint"] = repository._device_fingerprint(device)
    metadata["updatedAt"] = datetime.now().astimezone().replace(microsecond=0).isoformat()
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, allow_nan=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写入 {len(changes)} 项改动；新指纹 {metadata['fingerprint'][:12]}…")
    print(f"备份目录：{backup_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
