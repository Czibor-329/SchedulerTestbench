"""企业 Running Swap 仅投影参与晶圆，真实状态和其他在途动作保持原口径。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.backend.execution import recompute_state
from app.backend.execution.run_state import _build_platform_recompute_update
from app.backend.validation.move_state import (
    ATMOSPHERE, VACUUM, LoadLockState, MachineState, MaterialState,
    RobotState, SlotPhase, SlotState, StationState,
)


def snapshot_case():
    """同时构造 Swap、Process、转压与 PreTrans，隔离各字段的语义。"""
    physical=MachineState(stations={
        'PM2':StationState('PM2','ProcessChamber',{
            1:SlotState(SlotPhase.COMPLETED,MaterialState(29,'PJ2',4),material_process_count=7)},
            transfer_busy_until=20,state_variables={'IdleTime':0,'ProcessCount':7},idle_reference_time=2),
        'PM1':StationState('PM1','ProcessChamber',{
            1:SlotState(SlotPhase.UNPROCESSED,MaterialState(30,'PJ2',4),busy_until=16,material_process_count=4)},
            state_variables={'IdleTime':0,'ProcessCount':4},idle_reference_time=1),
        'LA':LoadLockState('LA','LoadLock',{
            1:SlotState(SlotPhase.COMPLETED,MaterialState(55,'PJ2',2))},
            environment=ATMOSPHERE,environment_busy_until=18),
        'LP2':StationState('LP2','LoadPort',{
            1:SlotState(SlotPhase.COMPLETED,MaterialState(40,'PJ2',0))}),
    },robots={
        'VTR':RobotState('VTR',hands={1:None,2:MaterialState(28,'PJ1',3)},
            position='PM2',busy_until=20,arm_use_counts={'ArmA':3}),
        'ATR':RobotState('ATR',hands={1:None},position='LP2',busy_until=15),
    })
    physical.dummy_material_counts={'100':4}
    projected=physical.clone()
    projected.stations['PM2'].slots[1].material=MaterialState(28,'PJ1',4)
    projected.robots['VTR'].hands={1:MaterialState(29,'PJ2',5),2:None}
    projected.robots['VTR'].arm_use_counts={'ArmA':5}
    projected.stations['PM1'].slots[1].material_process_count=99
    projected.stations['PM1'].state_variables['ProcessCount']=99
    projected.stations['PM1'].idle_reference_time=16
    projected.stations['LA'].environment=VACUUM
    projected.stations['LP2'].slots[1].material=None
    projected.robots['ATR'].hands[1]=MaterialState(40,'PJ2',1)
    projected.dummy_material_counts={'100':9}
    materials=[dict(ID=mid,PJobName=job,CurrentModuleName=module,SlotID=slot,StepID=step)
        for mid,job,module,slot,step in [(28,'PJ1','VTR',2,3),(29,'PJ2','PM2',1,4),
            (30,'PJ2','PM1',1,4),(55,'PJ2','LA',1,2),(40,'PJ2','LP2',1,0)]]
    update=dict(CurrentTime=10,Materials=materials,Stations={
        name:{'Type':station.station_type} for name,station in physical.stations.items()},
        Robots={'VTR':{'ArmInfo':{'ArmA':{'Name':'ArmA','UseCount':0}}},'ATR':{}},
        ProcessJobs=[],ControlJobs=[])
    swap=dict(MoveID=1,MoveType=4,ModuleName='VTR',SwapMode=0,
        SendMatList=[28],RecvMatList=[29],StartTime=1,EndTime=20)
    moves=[swap,dict(MoveID=2,MoveType=9,ModuleName='PM1',SlotList=[1],StartTime=2,EndTime=16),
        dict(MoveID=3,MoveType=10,ModuleName='LA',SlotList=[1],StartTime=3,EndTime=18),
        dict(MoveID=4,MoveType=5,ModuleName='ATR',StartTime=4,EndTime=15),
        dict(MoveID=99,MoveType=6,ModuleName='LP2',StartTime=11,EndTime=12)]
    states=[dict(MoveID=mid,MoveState=0,StartTime=mid,EndTime=-1) for mid in (1,2,3,4)]
    runtime=SimpleNamespace(current_update=deepcopy(update),execution_plan=moves,current_plan=moves,state=physical)
    return update,physical,projected,moves,states,runtime


def project(*args):
    """原实现缺少入口时产生明确失败，不把基线变成收集错误。"""
    function=getattr(recompute_state,'project_running_swap_materials',None)
    assert callable(function),'缺少 Running Swap 企业物料投影入口'
    return function(*args)


def positions(update):
    """只提取协议位置字段，便于核对非 Swap 晶圆不变。"""
    return {m['ID']:(m['CurrentModuleName'],m['SlotID'],m['StepID']) for m in update['Materials']}


@pytest.mark.parametrize('swap_mode',[0,1])
def test_swap_material_projection_keeps_running_and_other_state(swap_mode):
    """两种 SwapMode 都只取真实完成投影结果，不按耗时猜测子阶段。"""
    update,physical,projected,moves,states,_=snapshot_case()
    moves[0]['SwapMode']=swap_mode
    before=deepcopy((update,physical,projected,moves,states))
    project(update,moves,states,projected)
    assert positions(update)[28]==('PM2',1,4)
    assert positions(update)[29]==('VTR',1,5)
    assert all(positions(update)[mid]==positions(before[0])[mid] for mid in (30,40,55))
    assert update['Stations']==before[0]['Stations']
    assert update['Robots']==before[0]['Robots']
    assert (physical,projected,moves,states)==before[1:]


def test_standard_recompute_combines_swap_end_positions_with_physical_resources():
    """生产构造入口保留忙时、压力、真实计数和 RemoveList。"""
    update,physical,projected,moves,states,runtime=snapshot_case()
    before=deepcopy((update,physical,projected,moves,states))
    result=_build_platform_recompute_update(runtime,deepcopy(update),10,states,
        projected_state=physical,completed_process_state=physical,
        robot_pointer_state=projected,running_swap_state=projected)
    assert positions(result)[28]==('PM2',1,4)
    assert positions(result)[29]==('VTR',1,5)
    assert positions(result)[30]==('PM1',1,4)
    assert positions(result)[40]==('LP2',1,0)
    assert result['MoveStates']==states
    assert result['CurrentTime']==10
    assert result['RemoveList']==[99]
    assert result['Robots']['VTR']['TimeToAvailable']==10
    assert result['Robots']['VTR']['ArmInfo']['ArmA']['UseCount']==3
    assert result['Stations']['PM2']['TimeToAvailableOfSlot']['1']==10
    assert result['Stations']['PM2']['StateVariables']['ProcessCount']['Value']['Value']==7
    assert result['Stations']['PM1']['StateVariables']['ProcessCount']['Value']['Value']==4
    # 企业单腔 PM 快照不发送 MaterialCount；槽计数仍保留于真实状态机。
    assert 'MaterialCount' not in result['Stations']['PM2']
    assert physical.stations['PM2'].slots[1].material_process_count==7
    assert physical.stations['PM1'].slots[1].material_process_count==4
    assert result['Stations']['PM1']['StateVariables']['IdleTime']['Value']['Value']==9
    assert result['Stations']['LA']['LastItem']=='ATR'
    assert result['Stations']['LA']['TimeToAvailableOfSlot']['1']==8
    assert (update,physical,projected,moves,states)==before


def test_multi_process_snapshot_keeps_real_slot_process_count():
    """双腔 PM 的 MaterialCount 字段仍使用真实完成次数，而非完成投影。"""
    update,physical,projected,_,states,runtime=snapshot_case()
    for name in ('PM1','PM2'):
        update['Stations'][name]['Type']='MultiProcessChamber'
        runtime.current_update['Stations'][name]['Type']='MultiProcessChamber'
    result=_build_platform_recompute_update(runtime,update,10,states,
        projected_state=physical,completed_process_state=physical,running_swap_state=projected)
    assert result['Stations']['PM1']['MaterialCount']['1']==4
    assert result['Stations']['PM2']['MaterialCount']['1']==7
    assert positions(result)[29]==('VTR',1,5)


def test_no_swap_projection_parameter_keeps_existing_builder_behavior():
    """内置策略或未请求此协议投影的调用不受影响。"""
    update,physical,projected,_,states,runtime=snapshot_case()
    result=_build_platform_recompute_update(runtime,deepcopy(update),10,states,projected_state=physical)
    assert positions(result)[28]==('VTR',2,3)
    assert positions(result)[29]==('PM2',1,4)


@pytest.mark.parametrize('terminal_status',[1,2])
def test_done_or_aborted_swap_is_not_reprojected(terminal_status):
    """已有 Done/Abort 时不把历史 Running 当作当前动作。"""
    update,_,projected,moves,states,_=snapshot_case()
    states.append(dict(MoveID=1,MoveState=terminal_status))
    before=deepcopy(update)
    project(update,moves,states,projected)
    assert update==before


@pytest.mark.parametrize('status',[None,1,2])
def test_no_running_swap_does_not_scan_completed_state(status):
    """快速路径不遍历状态机，控制无 Swap 重算的新增成本。"""
    update,_,_,moves,states,_=snapshot_case()
    states=[s for s in states if s['MoveID']!=1]
    if status is not None:
        states.append(dict(MoveID=1,MoveState=status))
    before=deepcopy(update)
    project(update,moves,states,object())
    assert update==before


def test_grouped_swap_projects_every_participant_atomically():
    """成组 Swap 可跨 LA/LB，保持各自槽位和 Route 步骤。"""
    update,_,projected,moves,states,_=snapshot_case()
    sources={28:('VTR',2,3),40:('VTR',4,3),29:('LA',1,4),55:('LB',1,4)}
    for material in update['Materials']:
        if material['ID'] in sources:
            module,slot,step=sources[material['ID']]
            material.update(CurrentModuleName=module,SlotID=slot,StepID=step)
    projected.stations['PM2'].slots[1].material=None
    projected.stations['LA'].slots={1:SlotState(),2:SlotState(SlotPhase.UNPROCESSED,MaterialState(28,'PJ1',4))}
    projected.stations['LB']=LoadLockState('LB','LoadLock',{
        1:SlotState(),2:SlotState(SlotPhase.UNPROCESSED,MaterialState(40,'PJ2',4))})
    projected.robots['ATR'].hands[1]=None
    projected.robots['VTR'].hands={1:MaterialState(29,'PJ2',5),2:None,
                                 3:MaterialState(55,'PJ2',5),4:None}
    moves[0].update(SendMatList=[28,40],RecvMatList=[29,55],SwapMode=1,
        StationList=['LA','LB'],SendSlotList=[2,4],RecvSlotList=[1,3],
        StnRecvSlotList=[2,2],StnSendSlotList=[1,1])
    project(update,moves,states,projected)
    assert positions(update)[28]==('LA',2,4)
    assert positions(update)[40]==('LB',2,4)
    assert positions(update)[55]==('VTR',3,5)
    assert positions(update)[29]==('VTR',1,5)


@pytest.mark.parametrize('missing_from',['update','projection'])
def test_missing_participant_does_not_publish_half_swap(missing_from):
    """缺少参与片时保留整个输入，不生成一半前态一半后态。"""
    update,_,projected,moves,states,_=snapshot_case()
    if missing_from=='update':
        update['Materials']=[m for m in update['Materials'] if m['ID']!=29]
    else:
        projected.robots['VTR'].hands[1]=None
    before=deepcopy(update)
    with pytest.raises(ValueError,match='Running Swap.*29'):
        project(update,moves,states,projected)
    assert update==before
