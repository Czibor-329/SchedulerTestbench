"""工作区持久化测试的最小合成 init；每次调用返回独立数据，不读取算法或公司数据集。"""


def workspace_device(load_port_name: str = "LP1") -> dict:
    """按 load_port_name 创建单端口单臂 init，返回与其它调用互不共享的字典。"""
    return {
        "Robots": {
            "R1": {
                "Name": "R1",
                "Type": "ATMRobot",
                "Capacity": 1,
                "ArmInfo": {"ArmA": {"SlotIDs": [1]}},
            },
        },
        "Stations": {
            load_port_name: {
                "Name": load_port_name,
                "Type": "LoadPort",
                "Capacity": 1,
                "Slots": [1],
            },
        },
    }
