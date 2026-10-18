"""将无重入、全 Swap 研究见证展开为真实槽位 MoveList 并调用平台物理校验。

从明确构造的非空稳态快照开始，逐片保留原 LP 槽，不修改设备拓扑或计时。
首片历史加工视为快照已完成；后续加工、门、机器人转位、切压全部显式生成。
该检查证明候选稳态片段的物理可行性，不证明空机启动及完整公司任务验收。
"""
from copy import deepcopy

from app.backend.validation.move_validation import (
    MachineState, MaterialState, SlotPhase, VACUUM, validate_move_list,
)

START_MARGIN_SECONDS = 10.0
MATERIAL_ID_OFFSET = 100


def validate_physical_witness(witness, device, timing, repetitions=3):
    """展开指定期数的固定 PM 见证，返回物理校验、MoveList 和初始占用。

    每个工序的候选腔按见证访问推导，拒绝跨工序重入；只支持单一 LoadPort。
    """
    if any(kind != 'S' for _, _, kind in witness['word']):
        raise ValueError('物理见证导出当前只支持 LoadLock Swap')
    graph = witness['graph']
    actions, offsets, period = graph['actions'], graph['offsets'], graph['period_seconds']
    pm_names = {action['station'] for action in actions if action['station'].startswith('PM')}
    ports = {action['station'] for action in actions if action['station'].startswith('LP')}
    if len(ports) != 1:
        raise ValueError('物理见证导出要求单一 LoadPort')
    port = next(iter(ports))
    pm_indices, node_names = {}, {}
    round_cursor = {}
    for event in witness['vacuum_sequence']:
        action = actions[event]
        if action['station'] not in pm_names:
            continue
        node = round_cursor.get(action['round'], 0)
        round_cursor[action['round']] = node + 1
        pm_indices[event] = node
        node_names.setdefault(node, set()).add(action['station'])
    chamber_counts = [len(node_names[index]) for index in range(len(node_names))]
    if sum(chamber_counts) != len(pm_names):
        raise ValueError('物理见证导出不支持跨工序 PM 重入')
    latency = sum(chamber_counts) + 1
    rounds = len(witness['word'])
    metadata = {event: item for task, item in zip(witness['tasks'], witness['task_materials']) for event in task}
    operations = []
    for cycle in range(repetitions):
        for action in actions:
            event, robot, station, kind = action['index'], action['robot'], action['station'], action['kind']
            if robot == 'ATR':
                item = metadata[event]
                material = (cycle + item['material_shift']) * rounds + actions[item['vacuum_event']]['round']
                material -= latency if item['kind'] == 'R' else 0
                send, receive = (material, None) if kind == 'place' else (None, material)
            else:
                incoming = cycle * rounds + action['round']
                if event in pm_indices:
                    node = pm_indices[event]
                    send = incoming - sum(chamber_counts[:node])
                    receive = send - chamber_counts[node]
                else:
                    send, receive = incoming - latency, incoming
            operations.append(dict(action=action, start=offsets[event] + cycle * period + START_MARGIN_SECONDS,
                                   send=send, receive=receive, cycle=cycle))
    operations.sort(key=lambda item: (item['start'], item['action']['index']))
    first, materials = {}, set()
    for item in operations:
        action = item['action']
        for material, location in ((item['receive'], action['station']), (item['send'], action['robot'])):
            if material is not None:
                materials.add(material)
                first.setdefault(material, location)
    home = {material: index + 1 for index, material in enumerate(sorted(materials))}
    initial = MachineState.from_sources(None, device)
    if len(home) > len(initial.stations[port].slots):
        raise ValueError(f'见证物料超过真实 {port} 槽数，应减少展开周期')
    inventories = {name: {} for name in ('LA', 'LB', port, 'ATR', 'VTR', *sorted(pm_names))}
    for material, location in sorted(first.items()):
        if location == port:
            slot = home[material]
        else:
            slots = initial.robots[location].hands if location in initial.robots else initial.stations[location].slots
            slot = next(slot for slot in slots if slot not in inventories[location])
        inventories[location][slot] = material
        value = MaterialState(material + MATERIAL_ID_OFFSET)
        if location in initial.robots:
            initial.robots[location].hands[slot] = value
        else:
            initial.stations[location].slots[slot].material = value
            initial.stations[location].slots[slot].phase = SlotPhase.COMPLETED
    initial_locations = deepcopy(inventories)
    for station in ('LA', 'LB'):
        initial.stations[station].environment = VACUUM
        initial.stations[station].environment_exemption_used = True
    last_pm = next(actions[event]['station'] for event in reversed(witness['vacuum_sequence'])
                   if actions[event]['station'] in pm_names)
    position = {'ATR': port, 'VTR': last_pm}
    for robot, station in position.items():
        initial.robots[robot].position = station
        initial.robots[robot].slot_targets = {slot: (station, 1) for slot in initial.robots[robot].hands}
        initial.robots[robot].slot_options = {slot: set(mapping[station])
                                            for slot, mapping in initial.robots[robot].slot_map.items()}
    moves, previous_body = [], {}

    def emit(kind, start, end, **fields):
        """分配唯一 MoveID，返回已生成 Move 以建立准确依赖。"""
        move = dict(MoveID=len(moves) + 1, MoveType=kind, StartTime=round(start, 7), EndTime=round(end, 7), **fields)
        moves.append(move)
        return move

    # 压力边来自每个候选的真实站点链，不能根据相邻机器人的全局动作猜测切压。
    pressures = {}
    for edge, label in zip(graph['edges'], graph['labels']):
        source, target, _, _ = edge
        left, right = actions[source], actions[target]
        if label.startswith('station:') and left['station'] in ('LA', 'LB') and left['robot'] != right['robot']:
            pressures[source] = ('vent_time' if right['robot'] == 'ATR' else 'pump_time', right['robot'])
    for item in operations:
        action, start, send, receive = item['action'], item['start'], item['send'], item['receive']
        event, robot, station, kind = action['index'], action['robot'], action['station'], action['kind']
        body_end = start + action['body']
        hands, contents = inventories[robot], inventories[station]
        send_hand = next((slot for slot, value in hands.items() if value == send), None) if send is not None else None
        receive_hand = next((slot for slot in initial.robots[robot].hands if slot not in hands), None) if receive is not None else None
        station_send = next((slot for slot, value in contents.items() if value == receive), None) if receive is not None else None
        station_receive = None
        if send is not None:
            station_receive = home[send] if station == port else station_send if kind == 'swap' else next(
                slot for slot in initial.stations[station].slots if slot not in contents)
        if receive is not None and (receive_hand is None or station_send is None):
            raise ValueError(f'事件 {event} 无法分配真实取片槽位')
        if send is not None and send_hand is None:
            raise ValueError(f'事件 {event} 缺少待放物料')
        target_slot = station_send if receive is not None else station_receive
        # 同站不同槽位也生成真实转位；门关/开提供的间隔必须足以容纳该动作。
        travel = timing.move(robot, position[robot], station, int(bool(hands)))
        transfer_slots = list(hands) if hands else [receive_hand]
        transfer = emit(5, start - travel, start, ModuleName=robot,
                        RobotSlotList=transfer_slots, SlotList=[target_slot] * len(transfer_slots),
                        SrcStationList=[position[robot]], DestStationList=[station],
                        MatIDList=[value + MATERIAL_ID_OFFSET for value in hands.values()],
                        PreMoveID=[previous_body[robot]] if robot in previous_body else [])
        prepare = emit(6, start - action['before'], start, ModuleName=station,
                       RelatedRobotType=int(robot == 'VTR'))
        fields = dict(ModuleName=robot, PreMoveID=[transfer['MoveID'], prepare['MoveID']])
        if kind == 'swap':
            body = emit(4, start, body_end, **fields, StationList=[station],
                        SendMatList=[send + MATERIAL_ID_OFFSET], RecvMatList=[receive + MATERIAL_ID_OFFSET],
                        SendSlotList=[send_hand], RecvSlotList=[receive_hand],
                        StnRecvSlotList=[station_receive], StnSendSlotList=[station_send])
        elif kind == 'pick':
            body = emit(0, start, body_end, **fields, MatIDList=[receive + MATERIAL_ID_OFFSET],
                        SrcStationList=[station], SrcSlotList=[station_send], RobotSlotList=[receive_hand])
        else:
            body = emit(1, start, body_end, **fields, MatIDList=[send + MATERIAL_ID_OFFSET],
                        DestStationList=[station], DestSlotList=[station_receive], RobotSlotList=[send_hand])
        complete = emit(7, body_end, body_end + action['after'], ModuleName=station,
                        PreMoveID=[body['MoveID']])
        if receive is not None:
            del contents[station_send]
            hands[receive_hand] = receive
        if send is not None:
            del hands[send_hand]
            contents[station_receive] = send
        if station in pm_names:
            emit(9, complete['EndTime'], complete['EndTime'] + action['process'], ModuleName=station,
                 MatIDList=[send + MATERIAL_ID_OFFSET], SlotList=[station_receive], PreMoveID=[complete['MoveID']])
        if event in pressures:
            field, destination = pressures[event]
            emit(10, complete['EndTime'], complete['EndTime'] + getattr(timing.chambers[station], field),
                 ModuleName=station, LastState=robot, CurState=destination,
                 MatIDList=[value + MATERIAL_ID_OFFSET for value in contents.values()], SlotList=list(contents),
                 PreMoveID=[complete['MoveID']])
        previous_body[robot] = body['MoveID']
        position[robot] = station
    issues = validate_move_list(None, moves, device, initial_state=initial)
    return dict(passed=not issues, issues=issues, move_count=len(moves), repetitions=repetitions,
                boundary='nonempty-snapshot; cold-start and full-task acceptance not checked',
                initial_locations=initial_locations, material_id_offset=MATERIAL_ID_OFFSET,
                moves=moves)
