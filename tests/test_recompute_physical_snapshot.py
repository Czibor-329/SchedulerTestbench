"""企业重算字段分别取真实压力/物料现场和已提交机器人指向目标。"""

from app.backend.execution.recompute_state import apply_machine_state_to_update, merge_algorithm_update
from app.backend.validation.move_state import ATMOSPHERE, LoadLockState, MachineState, RobotState, SlotState


def test_pressure_stays_physical_while_robot_pointer_reports_motion_target():
    """转压尚未结束不能提前改 LastItem；运动目标已知也不意味着 Robot 可用。"""
    physical = MachineState(
        stations={"LB": LoadLockState("LB", "LoadLock", {1: SlotState()},
                                      environment=ATMOSPHERE, environment_busy_until=18)},
        robots={"ATR": RobotState("ATR", hands={1: None}, position="LP1", busy_until=12,
                                   arm_slots={"ArmA": {1}}, arm_use_counts={"ArmA": 5})},
    )
    target = physical.clone()
    target.robots["ATR"].position = "DummyPort"
    target.robots["ATR"].slot_targets = {1: ("DummyPort", 1)}
    update = {"Materials": [], "Stations": {"LB": {"LastItem": "ATR"}},
              "Robots": {"ATR": {"ArmInfo": {"ArmA": {"Name": "ArmA", "UseCount": 0}}}}}
    apply_machine_state_to_update(update, physical, 10, robot_pointer_state=target)
    assert update["Stations"]["LB"]["LastItem"] == "ATR"
    assert update["Stations"]["LB"]["TimeToAvailableOfSlot"]["1"] == 8
    assert update["Robots"]["ATR"]["TimeToAvailable"] == 2
    assert update["Robots"]["ATR"]["ArmInfo"]["ArmA"]["SlotAtStation"] == "DummyPort"
    assert update["Robots"]["ATR"]["ArmInfo"]["ArmA"]["UseCount"] == 5


def test_new_products_precede_reusable_dummy_inventory_without_sorting_ids():
    """补片后保持产品先、Dummy 后；产品及库存各自仍保留原先顺序。"""
    stations = {"LP1": {"Type": "LoadPort"}, "DummyPort": {"Type": "DummyPort"}}
    previous = {"Materials": [{"ID": 4, "SrcPortName": "LP1"}, {"ID": 2, "SrcPortName": "DummyPort"}]}
    new = {"Stations": stations, "Materials": [{"ID": 1, "SrcPortName": "LP1"}]}
    result = merge_algorithm_update(previous, new)
    assert [m["ID"] for m in result["Materials"]] == [4, 1, 2]
