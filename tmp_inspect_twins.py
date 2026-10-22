"""临时诊断脚本：读取平台设备目录，确认前端实际能收到的 TWINS 站点集合。"""

import json
from collections import Counter

from app.backend.workspace import repository


def main() -> None:
    """打印设备目录中每台设备的站点类型统计与 Buffer 识别结果。"""
    catalog = repository.list_workspace_devices()
    print("store version:", catalog.get("version"))
    for device in catalog.get("devices") or []:
        device_name = device.get("name")
        stations = (device.get("device") or {}).get("Stations") or {}
        types = Counter(
            str((station or {}).get("Type") or "") for station in stations.values()
        )
        print("-", device_name, device.get("id"), "schemaVersion", device.get("schemaVersion"))
        print("   station types:", dict(types))
        print("   station names:", sorted(stations.keys()))
        buffers = [
            name
            for name, station in stations.items()
            if str((station or {}).get("Type") or "").strip().lower() == "buffer"
            or str(name).upper().startswith("BUF")
        ]
        print("   buffer-like:", buffers)
        print("   metadata fingerprint:", device.get("fingerprint"))
        print("   metadata:", json.dumps(device.get("initOptions") or {}, ensure_ascii=False)[:200])


if __name__ == "__main__":
    main()
