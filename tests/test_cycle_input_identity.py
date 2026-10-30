"""补片实例使用新 Task/Job 标识，同时保持企业原工艺条件身份。"""

from app.backend.execution.plan_builder import BuildState, build_round_update
from app.backend.execution.cjob_cycle import (
    _bind_cycle_base_pjob_names, _cycle_cjob, _cycle_states_for_round,
    _initial_cycle_task_serial,
)


def test_cycle_keeps_original_clean_alias_and_uses_replenished_slot_name():
    """不能将条件 Alias 重命名为补片 TaskID，也不能把物料显示名当全局 ID。"""
    plan = {
        "device": {"Stations": {"LP1": {"Type": "LoadPort", "Capacity": 2},
                                  "PM1": {"Type": "ProcessChamber", "Capacity": 1}},
                   "Robots": {"ATR": {"Type": "ATMRobot", "Capacity": 1}}},
        "recipes": [{"name": "product", "time": 1, "modules": ["PM1"]}],
        "cleans": [{"name": "clean", "cleanType": "preclean", "recipeTime": 1, "modules": ["PM1"]}],
        "routes": [{"name": "R", "prePJobCleanRefs": ["clean"], "stages": [
            {"stations": ["LP1"]}, {"stations": ["ATR"]},
            {"visits": [{"stationName": "PM1", "processRecipe": "product", "recipeTime": 1}]},
            {"stations": ["ATR"]}, {"stations": ["LP1"]},
        ]}],
    }
    state = BuildState()
    first = {"cjobs": [{"taskId": "1", "loadPort": "LP1", "pjobs": [
        {"jobName": "P1", "waferCount": 1, "routeRef": "R"},
    ]}]}
    initial = build_round_update(plan, first, 0.0, state)
    name = initial["ProcessJobs"][0]["JobName"]
    state.next_slot_by_port["LP1"] = 0
    cycle = {"cjobs": [{"taskId": "4", "loadPort": "LP1", "pjobs": [
        {"jobName": "P1", "waferCount": 1, "routeRef": "R", "_basePJobName": name},
    ]}]}
    update = build_round_update(plan, cycle, 20, state)
    alias = initial["ProcessJobs"][0]["OriginRoute"]["PrePJob"]["PM1"][0]["ExecuteOrder"][0]["Alias"]
    condition = update["ProcessJobs"][0]["OriginRoute"]["PrePJob"]["PM1"][0]
    assert condition["ExecuteOrder"][0]["Alias"] == alias
    assert list(condition["CheckConditions"]) == [alias]
    assert update["Materials"][0]["ID"] == 1
    assert update["Materials"][0]["Name"] == f"0_{name}_4"
    assert update["Materials"][0]["TaskID"] == "4"
    assert update["Materials"][0]["PJobName"] == f"{name}_4"


def test_cycle_serial_avoids_all_configured_rounds_and_keeps_template_identity():
    """后续计划 TaskID 也参与分配上界；补片不修改原模板或清洗身份。"""
    first = {'cjobs': [{'taskId':'1','loadPort':'LP1','cjobCycle':2,
                       'pjobs':[{'jobName':'P1','routeRef':'R'}]}]}
    later = {'cjobs': [{'taskId':'5','loadPort':'LP2','pjobs':[]}]}
    serial = _initial_cycle_task_serial([first,later])
    assert serial==6
    states = _cycle_states_for_round(first,1)
    _bind_cycle_base_pjob_names(states,{'ProcessJobs':[
        {'TaskID':'1','JobName':'1.PJOB_1'}, {'TaskID':'5','JobName':'5.PJOB_2'},
    ]})
    result = _cycle_cjob(states[0].template,str(serial),states[0].base_pjob_names)
    assert result['taskId']=='6'
    assert result['pjobs'][0]['_basePJobName']=='1.PJOB_1'
    assert first['cjobs'][0]['taskId']=='1'
    assert '_basePJobName' not in first['cjobs'][0]['pjobs'][0]
