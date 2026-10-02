/**
 * 甘特图 Move 的中文动作说明。
 *
 * 依据标准 Move 字段逐片关联晶圆、站点槽位和机械手手槽，供悬浮提示与
 * 选中详情共用。仅翻译已有信息，不回放状态、不推断等待原因或设备拓扑，
 * 不修改输入；字段缺失时保留未知项。接口语义见独立文档仓库 interface-output。
 */

type MoveRecord = Record<string, unknown>;

const MOVE_TYPE = {
  PICK: 0, PLACE: 1, MULTI_PICK: 2, MULTI_PLACE: 3, SWAP: 4,
  PRE_TRANS: 5, PREPARE: 6, COMPLETE: 7, POST_COMPLETE: 8,
  PROCESS: 9, PRE_PREPARE: 10, ALIGN: 11, PUMP: 12, VENT: 13, CLEAN: 14,
} as const;
const SWAP_PLACE_FIRST = 1;
const SWAP_PICK_FIRST = 0;
const ACTION_NAMES: Record<number, string> = { 0: "放片", 1: "取片", 2: "换片" };

/** MoveType 的中文名称；12 至 14 为查看器兼容的历史动作类型。 */
export const MOVE_TYPE_CHINESE_NAMES: Record<number, string> = {
  0: "取片", 1: "放片", 2: "多片同时取片", 3: "多片同时放片", 4: "换片",
  5: "转位", 6: "开门准备", 7: "关门完成", 8: "完成后收尾", 9: "工艺处理",
  10: "状态转换", 11: "对准", 12: "抽气", 13: "充气", 14: "清洁",
};

/** 读取字段文本，拒绝把对象或缺失值显示成 JavaScript 字面量。 */
function fieldText(value: unknown): string {
  if (typeof value === "number" && !Number.isFinite(value)) return "";
  return typeof value === "string" || typeof value === "number"
    ? String(value).trim() : "";
}

/** 保留列表下标及空项，避免缺失字段造成多片对应关系错位。 */
function fieldList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(fieldText);
  const text = fieldText(value);
  return text ? [text] : [];
}

/** 读取数值枚举，同时兼容日志中的数字字符串。 */
function enumNumber(value: unknown): number | null {
  const text = fieldText(value);
  if (!text) return null;
  const number = Number(text);
  return Number.isInteger(number) ? number : null;
}

/** 优先使用标准列表，只有列表未提供时才采用旧格式位置字段。 */
function locationList(move: MoveRecord, field: string, fallback: string): string[] {
  return move[field] !== undefined && move[field] !== null
    ? fieldList(move[field]) : fieldList(move[fallback]);
}

/** 返回站点和具体槽位；没有槽位时只陈述已知站点。 */
function stationLocation(station: string, slot: string, fallback: string): string {
  const name = station || fallback;
  return slot ? `${name} 的 ${slot} 号槽` : name;
}

/** 返回机械手手槽，不用站点 SlotList 猜测 RobotSlotList。 */
function robotLocation(slot: string): string {
  return slot ? `机械手 ${slot} 号手槽` : "机械手（手槽未提供）";
}

/**
 * 根据物料、站点、站点槽位与手槽的同下标字段生成搬运分句。
 * picking 决定取放方向，simultaneous 决定取片同时性的表达；actionCount 可指定
 * Swap 单组动作数量，避免公共 StationList 扩大空组或不对称组。返回逐片纯文本列表，
 * 不复用长度不匹配的列表项，也不修改传入列表。
 */
function transferDescriptions(
  materials: string[], stations: string[], stationSlots: string[], robotSlots: string[],
  picking: boolean, simultaneous: boolean,
  actionCount = Math.max(1, materials.length, stations.length, stationSlots.length, robotSlots.length),
): string[] {
  return Array.from({ length: actionCount }, (_, index) => {
    const material = materials[index] ? `晶圆 ${materials[index]}` : "晶圆（编号未提供）";
    const station = stationLocation(stations[index], stationSlots[index], picking ? "来源站点" : "目标站点");
    const robot = robotLocation(robotSlots[index]);
    if (!picking) return `将${robot}中的${material} 放入 ${station}`;
    return simultaneous ? `${material} 从 ${station} 转移到${robot}`
      : `从 ${station} 取出${material}，放到${robot}`;
  });
}

/** 描述已提供的源或目标位置；该列表只用于转位位置，不关联物料。 */
function positionSummary(stations: string[], slots: string[]): string {
  return Array.from({ length: Math.max(stations.length, slots.length) }, (_, index) =>
    stationLocation(stations[index], slots[index], "站点未提供"),
  ).join("、");
}

/** 将显式关联机械手类型翻成中文，不推断级联设备的上下门方向。 */
function relatedRobot(move: MoveRecord): string {
  const type = enumNumber(move.RelatedRobotType);
  // 平台日志使用 0=大气、1=真空；兼容标准接口的 2=大气。
  if (type === 0 || type === 2) return "大气机械手";
  if (type === 1) return "真空机械手";
  return type === null ? "" : `未知机械手类型（${type}）`;
}

/** 保留未知状态原文，仅翻译确定的压力状态名称。 */
function stateName(value: unknown): string {
  const text = fieldText(value);
  const known: Record<string, string> = {
    vacuum: "真空", atmosphere: "大气", atmospheric: "大气", air: "大气",
  };
  return known[text.toLowerCase()] || text;
}

/** 描述状态转换端点，允许只提供转换前或转换后的状态。 */
function stateTransition(move: MoveRecord): string {
  const previous = stateName(move.LastState), current = stateName(move.CurState);
  if (previous && current) return `状态从 ${previous} 转为 ${current}`;
  if (current) return `状态转为 ${current}`;
  if (previous) return `转换前状态为 ${previous}`;
  return "";
}

/** 翻译明确的 PrePrepareType，未知设备类型原样展示。 */
function preparationName(value: unknown): string {
  const text = fieldText(value);
  const names: Record<string, string> = {
    pump: "抽气", vent: "充气", rotate: "旋转", rotation: "旋转",
    temperature: "控温", temperaturecontrol: "控温", heating: "加热", cooling: "冷却",
  };
  return names[text.toLowerCase()] || text || "状态转换";
}

/**
 * 根据 move 的物料、SlotList、配方和清洁任务生成站点处理说明。
 * actor 为显示模块名，cleanType 表示历史 CLEAN 动作类型；返回纯文本，
 * 按物料索引保留加工槽位，不根据模块名或配方名称猜测工艺类别。
 */
function processDescription(move: MoveRecord, actor: string, cleanType: boolean): string {
  const materials = fieldList(move.MatIDList), slots = fieldList(move.SlotList);
  const recipe = fieldText(move.ProcessRecipe) || fieldText(move.CleanRecipe) || fieldText(move.RecipeName);
  const task = fieldText(move.CleanTaskName);
  const cleaning = cleanType || Boolean(task) || Boolean(fieldText(move.CleanRecipe))
    || (Array.isArray(move.MatIDList) && move.MatIDList.length === 0);
  const targets = materials.map((material, index) =>
    `${slots[index] ? `${slots[index]} 号槽中的` : ""}${material ? `晶圆 ${material}` : "晶圆（编号未提供）"}`,
  ).join("、");
  const emptySlots = !materials.length && slots.length ? `在 ${slots.filter(Boolean).join("、")} 号槽` : "";
  const action = cleaning ? "执行清洁" : "执行工艺";
  const details = [recipe ? `配方：${recipe}` : "", task ? `清洁任务：${task}` : ""].filter(Boolean);
  const lastClean = cleaning && move.IsLastCleanTaskMove === true ? "；这是该清洁任务的最后一个工艺动作" : "";
  return `${actor} ${targets ? `对${targets}` : emptySlots}${action}${details.length ? `（${details.join("；")}）` : ""}${lastClean}。`;
}

/**
 * 将单条原始 Move 翻译为中文动作说明。
 * @param raw 标准 Move 或历史查看器字段；允许缺失或 null。
 * @returns 可直接显示的纯文本，调用方负责 HTML 转义；无状态和输入修改副作用。
 */
export function describeMove(raw: MoveRecord | null | undefined): string {
  const move = raw || {};
  const moveType = enumNumber(move.MoveType);
  const actor = fieldText(move.ModuleName) || fieldText(move.Robot)
    || fieldText(move.Station) || fieldText(move.RobotPort)
    || (moveType !== null && moveType <= MOVE_TYPE.PRE_TRANS ? "机械手" : "模块");
  const materials = fieldList(move.MatIDList);
  const source = locationList(move, "SrcStationList", "Source");
  const destination = locationList(move, "DestStationList", "Destination");
  const robotSlots = fieldList(move.RobotSlotList);

  // 搬运动作逐片关联位置；多片动作保持同时性，Swap 另按声明顺序组合。
  switch (moveType) {
    case MOVE_TYPE.PICK:
    case MOVE_TYPE.MULTI_PICK: {
      const simultaneous = moveType === MOVE_TYPE.MULTI_PICK;
      const actions = transferDescriptions(materials, source, fieldList(move.SrcSlotList), robotSlots, true, simultaneous);
      return `${actor} ${simultaneous ? "同时取片：" : ""}${actions.join("；")}。`;
    }
    case MOVE_TYPE.PLACE:
    case MOVE_TYPE.MULTI_PLACE: {
      const actions = transferDescriptions(materials, destination, fieldList(move.DestSlotList), robotSlots, false, false);
      return `${actor} ${moveType === MOVE_TYPE.MULTI_PLACE ? "同时放片：" : ""}${actions.join("；")}。`;
    }
    case MOVE_TYPE.SWAP: {
      const stations = locationList(move, "StationList", "Station");
      const receiveMaterials = fieldList(move.RecvMatList), sendMaterials = fieldList(move.SendMatList);
      const receiveStationSlots = fieldList(move.StnSendSlotList), sendStationSlots = fieldList(move.StnRecvSlotList);
      const receiveRobotSlots = fieldList(move.RecvSlotList), sendRobotSlots = fieldList(move.SendSlotList);
      // 孪生锁允许不对称收发；公共 StationList 仅为各组提供按下标对应的站点。
      const receive = transferDescriptions(receiveMaterials, stations, receiveStationSlots, receiveRobotSlots, true, false,
        Math.max(receiveMaterials.length, receiveStationSlots.length, receiveRobotSlots.length)).join("；");
      const send = transferDescriptions(sendMaterials, stations, sendStationSlots, sendRobotSlots, false, false,
        Math.max(sendMaterials.length, sendStationSlots.length, sendRobotSlots.length)).join("；");
      const mode = enumNumber(move.SwapMode);
      if (mode !== null && mode !== SWAP_PICK_FIRST && mode !== SWAP_PLACE_FIRST) {
        return `${actor} 换片（未知 SwapMode=${mode}，顺序无法确定）：${[receive, send].filter(Boolean).join("；") || "取放信息未提供"}。`;
      }
      if (!receive || !send) return `${actor} 换片：${receive || send || "取放信息未提供"}。`;
      const placeFirst = mode === SWAP_PLACE_FIRST;
      return `${actor} 换片：先${placeFirst ? send : receive}；再${placeFirst ? receive : send}。`;
    }
    case MOVE_TYPE.PRE_TRANS: {
      const from = positionSummary(source, fieldList(move.SrcSlotList));
      const to = positionSummary(destination, fieldList(move.DestSlotList));
      const carrying = materials.length ? `携带晶圆 ${materials.join("、")}`
        : Array.isArray(move.MatIDList) ? "空载" : "";
      const actionType = enumNumber(move.RalatedActionType ?? move.RelatedActionType);
      const action = ACTION_NAMES[actionType ?? -1];
      const preparation = action ? `，为${action}做准备`
        : actionType === null ? "" : `，关联动作类型：${actionType}`;
      return `${actor} ${carrying ? `${carrying}，` : ""}${from ? `从 ${from} ` : ""}${to ? `转位到 ${to}` : "执行转位"}${preparation}。`;
    }
    // 门动作只使用显式关联类型，收尾动作不等同于再次关门。
    case MOVE_TYPE.PREPARE:
    case MOVE_TYPE.COMPLETE: {
      const robot = relatedRobot(move);
      const actionType = enumNumber(move.RelatedActionType);
      const action = ACTION_NAMES[actionType ?? -1] || "";
      const unknownAction = actionType !== null && !action ? `（关联动作类型：${actionType}）` : "";
      return moveType === MOVE_TYPE.PREPARE
        ? `${actor} ${robot || action ? `为${robot}${action}` : ""}开门${unknownAction}。`
        : `${actor} ${robot || action ? `在${robot}${action || "取放"}后` : ""}关门${unknownAction}。`;
    }
    case MOVE_TYPE.POST_COMPLETE:
      return `${actor} 执行${relatedRobot(move)}取放后的收尾动作。`;
    case MOVE_TYPE.PROCESS:
    case MOVE_TYPE.CLEAN:
      return processDescription(move, actor, moveType === MOVE_TYPE.CLEAN);
    case MOVE_TYPE.PRE_PREPARE: {
      const transition = stateTransition(move);
      return `${actor} 执行${preparationName(move.PrePrepareType)}${transition ? `，${transition}` : ""}。`;
    }
    case MOVE_TYPE.PUMP:
    case MOVE_TYPE.VENT: {
      const transition = stateTransition(move);
      return `${actor} 执行${moveType === MOVE_TYPE.PUMP ? "抽气" : "充气"}${transition ? `，${transition}` : ""}。`;
    }
    case MOVE_TYPE.ALIGN:
      return `${actor} 对${materials.length ? `晶圆 ${materials.join("、")}` : "晶圆（编号未提供）"}执行对准。`;
    default:
      return `${actor} 执行${moveType === null ? "未知类型的动作" : `未知动作（MoveType=${moveType}）`}。`;
  }
}
