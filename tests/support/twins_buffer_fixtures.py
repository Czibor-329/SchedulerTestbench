"""Buffer 配置补齐测试的合成设备：保留既有时间，并包含两个缓存与禁用手臂。"""

from __future__ import annotations

from typing import Any


def buffer_device() -> dict[str, Any]:
    """返回独立设备数据；时间仅用于测试补齐规则，不代表真实设备。"""
    return {
        "Stations": {
            "P1": {"Type": "LoadPort", "Slots": [1, 2]},
            "P2": {"Type": "LoadPort", "Slots": [1, 2]},
            "Buffer1": {
                "Type": "Buffer", "Slots": [1, 2],
                "PickPrepareTime": {"ATR": 1.5, "ATMRobot": 2.0},
                "PlacePrepareTime": {"ATR": 1.5},
            },
            "Buffer2": {"Type": "Buffer", "Slots": [1, 2]},
        },
        "Robots": {"ATMRobot": {
            "PickTime": {"P1": 11.0, "Buffer1": 7.0},
            "PlaceTime": {"P1": 20.0},
            "PrepTransTime": [
                {"SrcStation": "P1", "DestStation": "P2", "TransType": 0, "Time": 1.3},
                {"SrcStation": "P1", "DestStation": "P2", "TransType": 1, "Time": 2.7},
            ],
            "ArmInfo": {
                "ArmA": {
                    "IsEnable": True, "SlotIDs": [1, 2],
                    "AccessibleStations": ["P1", "P2"], "SlotsStationMap": {},
                },
                "DisabledArm": {
                    "IsEnable": False, "SlotIDs": [3],
                    "AccessibleStations": ["P1"], "SlotsStationMap": {},
                },
            },
        }},
    }
