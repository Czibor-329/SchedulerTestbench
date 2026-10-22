var GanttMoveSemantics = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/gantt_move_semantics.ts
  var gantt_move_semantics_exports = {};
  __export(gantt_move_semantics_exports, {
    MOVE_TYPE_CHINESE_NAMES: () => MOVE_TYPE_CHINESE_NAMES,
    describeMove: () => describeMove
  });
  var MOVE_TYPE = {
    PICK: 0,
    PLACE: 1,
    MULTI_PICK: 2,
    MULTI_PLACE: 3,
    SWAP: 4,
    PRE_TRANS: 5,
    PREPARE: 6,
    COMPLETE: 7,
    POST_COMPLETE: 8,
    PROCESS: 9,
    PRE_PREPARE: 10,
    ALIGN: 11,
    PUMP: 12,
    VENT: 13,
    CLEAN: 14
  };
  var SWAP_PLACE_FIRST = 1;
  var SWAP_PICK_FIRST = 0;
  var ACTION_NAMES = { 0: "\u653E\u7247", 1: "\u53D6\u7247", 2: "\u6362\u7247" };
  var MOVE_TYPE_CHINESE_NAMES = {
    0: "\u53D6\u7247",
    1: "\u653E\u7247",
    2: "\u591A\u7247\u540C\u65F6\u53D6\u7247",
    3: "\u591A\u7247\u540C\u65F6\u653E\u7247",
    4: "\u6362\u7247",
    5: "\u8F6C\u4F4D",
    6: "\u5F00\u95E8\u51C6\u5907",
    7: "\u5173\u95E8\u5B8C\u6210",
    8: "\u5B8C\u6210\u540E\u6536\u5C3E",
    9: "\u5DE5\u827A\u5904\u7406",
    10: "\u72B6\u6001\u8F6C\u6362",
    11: "\u5BF9\u51C6",
    12: "\u62BD\u6C14",
    13: "\u5145\u6C14",
    14: "\u6E05\u6D01"
  };
  function fieldText(value) {
    if (typeof value === "number" && !Number.isFinite(value)) return "";
    return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
  }
  function fieldList(value) {
    if (Array.isArray(value)) return value.map(fieldText);
    const text = fieldText(value);
    return text ? [text] : [];
  }
  function enumNumber(value) {
    const text = fieldText(value);
    if (!text) return null;
    const number = Number(text);
    return Number.isInteger(number) ? number : null;
  }
  function locationList(move, field, fallback) {
    return move[field] !== void 0 && move[field] !== null ? fieldList(move[field]) : fieldList(move[fallback]);
  }
  function stationLocation(station, slot, fallback) {
    const name = station || fallback;
    return slot ? `${name} \u7684 ${slot} \u53F7\u69FD` : name;
  }
  function robotLocation(slot) {
    return slot ? `\u673A\u68B0\u624B ${slot} \u53F7\u624B\u69FD` : "\u673A\u68B0\u624B\uFF08\u624B\u69FD\u672A\u63D0\u4F9B\uFF09";
  }
  function transferDescriptions(materials, stations, stationSlots, robotSlots, picking, simultaneous, actionCount = Math.max(1, materials.length, stations.length, stationSlots.length, robotSlots.length)) {
    return Array.from({ length: actionCount }, (_, index) => {
      const material = materials[index] ? `\u6676\u5706 ${materials[index]}` : "\u6676\u5706\uFF08\u7F16\u53F7\u672A\u63D0\u4F9B\uFF09";
      const station = stationLocation(stations[index], stationSlots[index], picking ? "\u6765\u6E90\u7AD9\u70B9" : "\u76EE\u6807\u7AD9\u70B9");
      const robot = robotLocation(robotSlots[index]);
      if (!picking) return `\u5C06${robot}\u4E2D\u7684${material} \u653E\u5165 ${station}`;
      return simultaneous ? `${material} \u4ECE ${station} \u8F6C\u79FB\u5230${robot}` : `\u4ECE ${station} \u53D6\u51FA${material}\uFF0C\u653E\u5230${robot}`;
    });
  }
  function positionSummary(stations, slots) {
    return Array.from(
      { length: Math.max(stations.length, slots.length) },
      (_, index) => stationLocation(stations[index], slots[index], "\u7AD9\u70B9\u672A\u63D0\u4F9B")
    ).join("\u3001");
  }
  function relatedRobot(move) {
    const type = enumNumber(move.RelatedRobotType);
    if (type === 0 || type === 2) return "\u5927\u6C14\u673A\u68B0\u624B";
    if (type === 1) return "\u771F\u7A7A\u673A\u68B0\u624B";
    return type === null ? "" : `\u672A\u77E5\u673A\u68B0\u624B\u7C7B\u578B\uFF08${type}\uFF09`;
  }
  function stateName(value) {
    const text = fieldText(value);
    const known = {
      vacuum: "\u771F\u7A7A",
      atmosphere: "\u5927\u6C14",
      atmospheric: "\u5927\u6C14",
      air: "\u5927\u6C14"
    };
    return known[text.toLowerCase()] || text;
  }
  function stateTransition(move) {
    const previous = stateName(move.LastState), current = stateName(move.CurState);
    if (previous && current) return `\u72B6\u6001\u4ECE ${previous} \u8F6C\u4E3A ${current}`;
    if (current) return `\u72B6\u6001\u8F6C\u4E3A ${current}`;
    if (previous) return `\u8F6C\u6362\u524D\u72B6\u6001\u4E3A ${previous}`;
    return "";
  }
  function preparationName(value) {
    const text = fieldText(value);
    const names = {
      pump: "\u62BD\u6C14",
      vent: "\u5145\u6C14",
      rotate: "\u65CB\u8F6C",
      rotation: "\u65CB\u8F6C",
      temperature: "\u63A7\u6E29",
      temperaturecontrol: "\u63A7\u6E29",
      heating: "\u52A0\u70ED",
      cooling: "\u51B7\u5374"
    };
    return names[text.toLowerCase()] || text || "\u72B6\u6001\u8F6C\u6362";
  }
  function processDescription(move, actor, cleanType) {
    const materials = fieldList(move.MatIDList), slots = fieldList(move.SlotList);
    const recipe = fieldText(move.ProcessRecipe) || fieldText(move.CleanRecipe) || fieldText(move.RecipeName);
    const task = fieldText(move.CleanTaskName);
    const cleaning = cleanType || Boolean(task) || Boolean(fieldText(move.CleanRecipe)) || Array.isArray(move.MatIDList) && move.MatIDList.length === 0;
    const targets = materials.map(
      (material, index) => `${slots[index] ? `${slots[index]} \u53F7\u69FD\u4E2D\u7684` : ""}${material ? `\u6676\u5706 ${material}` : "\u6676\u5706\uFF08\u7F16\u53F7\u672A\u63D0\u4F9B\uFF09"}`
    ).join("\u3001");
    const emptySlots = !materials.length && slots.length ? `\u5728 ${slots.filter(Boolean).join("\u3001")} \u53F7\u69FD` : "";
    const action = cleaning ? "\u6267\u884C\u6E05\u6D01" : "\u6267\u884C\u5DE5\u827A";
    const details = [recipe ? `\u914D\u65B9\uFF1A${recipe}` : "", task ? `\u6E05\u6D01\u4EFB\u52A1\uFF1A${task}` : ""].filter(Boolean);
    const lastClean = cleaning && move.IsLastCleanTaskMove === true ? "\uFF1B\u8FD9\u662F\u8BE5\u6E05\u6D01\u4EFB\u52A1\u7684\u6700\u540E\u4E00\u4E2A\u5DE5\u827A\u52A8\u4F5C" : "";
    return `${actor} ${targets ? `\u5BF9${targets}` : emptySlots}${action}${details.length ? `\uFF08${details.join("\uFF1B")}\uFF09` : ""}${lastClean}\u3002`;
  }
  function describeMove(raw) {
    const move = raw || {};
    const moveType = enumNumber(move.MoveType);
    const actor = fieldText(move.ModuleName) || fieldText(move.Robot) || fieldText(move.Station) || fieldText(move.RobotPort) || (moveType !== null && moveType <= MOVE_TYPE.PRE_TRANS ? "\u673A\u68B0\u624B" : "\u6A21\u5757");
    const materials = fieldList(move.MatIDList);
    const source = locationList(move, "SrcStationList", "Source");
    const destination = locationList(move, "DestStationList", "Destination");
    const robotSlots = fieldList(move.RobotSlotList);
    switch (moveType) {
      case MOVE_TYPE.PICK:
      case MOVE_TYPE.MULTI_PICK: {
        const simultaneous = moveType === MOVE_TYPE.MULTI_PICK;
        const actions = transferDescriptions(materials, source, fieldList(move.SrcSlotList), robotSlots, true, simultaneous);
        return `${actor} ${simultaneous ? "\u540C\u65F6\u53D6\u7247\uFF1A" : ""}${actions.join("\uFF1B")}\u3002`;
      }
      case MOVE_TYPE.PLACE:
      case MOVE_TYPE.MULTI_PLACE: {
        const actions = transferDescriptions(materials, destination, fieldList(move.DestSlotList), robotSlots, false, false);
        return `${actor} ${moveType === MOVE_TYPE.MULTI_PLACE ? "\u540C\u65F6\u653E\u7247\uFF1A" : ""}${actions.join("\uFF1B")}\u3002`;
      }
      case MOVE_TYPE.SWAP: {
        const stations = locationList(move, "StationList", "Station");
        const receiveMaterials = fieldList(move.RecvMatList), sendMaterials = fieldList(move.SendMatList);
        const receiveStationSlots = fieldList(move.StnSendSlotList), sendStationSlots = fieldList(move.StnRecvSlotList);
        const receiveRobotSlots = fieldList(move.RecvSlotList), sendRobotSlots = fieldList(move.SendSlotList);
        const receive = transferDescriptions(
          receiveMaterials,
          stations,
          receiveStationSlots,
          receiveRobotSlots,
          true,
          false,
          Math.max(receiveMaterials.length, receiveStationSlots.length, receiveRobotSlots.length)
        ).join("\uFF1B");
        const send = transferDescriptions(
          sendMaterials,
          stations,
          sendStationSlots,
          sendRobotSlots,
          false,
          false,
          Math.max(sendMaterials.length, sendStationSlots.length, sendRobotSlots.length)
        ).join("\uFF1B");
        const mode = enumNumber(move.SwapMode);
        if (mode !== null && mode !== SWAP_PICK_FIRST && mode !== SWAP_PLACE_FIRST) {
          return `${actor} \u6362\u7247\uFF08\u672A\u77E5 SwapMode=${mode}\uFF0C\u987A\u5E8F\u65E0\u6CD5\u786E\u5B9A\uFF09\uFF1A${[receive, send].filter(Boolean).join("\uFF1B") || "\u53D6\u653E\u4FE1\u606F\u672A\u63D0\u4F9B"}\u3002`;
        }
        if (!receive || !send) return `${actor} \u6362\u7247\uFF1A${receive || send || "\u53D6\u653E\u4FE1\u606F\u672A\u63D0\u4F9B"}\u3002`;
        const placeFirst = mode === SWAP_PLACE_FIRST;
        return `${actor} \u6362\u7247\uFF1A\u5148${placeFirst ? send : receive}\uFF1B\u518D${placeFirst ? receive : send}\u3002`;
      }
      case MOVE_TYPE.PRE_TRANS: {
        const from = positionSummary(source, fieldList(move.SrcSlotList));
        const to = positionSummary(destination, fieldList(move.DestSlotList));
        const carrying = materials.length ? `\u643A\u5E26\u6676\u5706 ${materials.join("\u3001")}` : Array.isArray(move.MatIDList) ? "\u7A7A\u8F7D" : "";
        const actionType = enumNumber(move.RalatedActionType ?? move.RelatedActionType);
        const action = ACTION_NAMES[actionType ?? -1];
        const preparation = action ? `\uFF0C\u4E3A${action}\u505A\u51C6\u5907` : actionType === null ? "" : `\uFF0C\u5173\u8054\u52A8\u4F5C\u7C7B\u578B\uFF1A${actionType}`;
        return `${actor} ${carrying ? `${carrying}\uFF0C` : ""}${from ? `\u4ECE ${from} ` : ""}${to ? `\u8F6C\u4F4D\u5230 ${to}` : "\u6267\u884C\u8F6C\u4F4D"}${preparation}\u3002`;
      }
      // 门动作只使用显式关联类型，收尾动作不等同于再次关门。
      case MOVE_TYPE.PREPARE:
      case MOVE_TYPE.COMPLETE: {
        const robot = relatedRobot(move);
        const actionType = enumNumber(move.RelatedActionType);
        const action = ACTION_NAMES[actionType ?? -1] || "";
        const unknownAction = actionType !== null && !action ? `\uFF08\u5173\u8054\u52A8\u4F5C\u7C7B\u578B\uFF1A${actionType}\uFF09` : "";
        return moveType === MOVE_TYPE.PREPARE ? `${actor} ${robot || action ? `\u4E3A${robot}${action}` : ""}\u5F00\u95E8${unknownAction}\u3002` : `${actor} ${robot || action ? `\u5728${robot}${action || "\u53D6\u653E"}\u540E` : ""}\u5173\u95E8${unknownAction}\u3002`;
      }
      case MOVE_TYPE.POST_COMPLETE:
        return `${actor} \u6267\u884C${relatedRobot(move)}\u53D6\u653E\u540E\u7684\u6536\u5C3E\u52A8\u4F5C\u3002`;
      case MOVE_TYPE.PROCESS:
      case MOVE_TYPE.CLEAN:
        return processDescription(move, actor, moveType === MOVE_TYPE.CLEAN);
      case MOVE_TYPE.PRE_PREPARE: {
        const transition = stateTransition(move);
        return `${actor} \u6267\u884C${preparationName(move.PrePrepareType)}${transition ? `\uFF0C${transition}` : ""}\u3002`;
      }
      case MOVE_TYPE.PUMP:
      case MOVE_TYPE.VENT: {
        const transition = stateTransition(move);
        return `${actor} \u6267\u884C${moveType === MOVE_TYPE.PUMP ? "\u62BD\u6C14" : "\u5145\u6C14"}${transition ? `\uFF0C${transition}` : ""}\u3002`;
      }
      case MOVE_TYPE.ALIGN:
        return `${actor} \u5BF9${materials.length ? `\u6676\u5706 ${materials.join("\u3001")}` : "\u6676\u5706\uFF08\u7F16\u53F7\u672A\u63D0\u4F9B\uFF09"}\u6267\u884C\u5BF9\u51C6\u3002`;
      default:
        return `${actor} \u6267\u884C${moveType === null ? "\u672A\u77E5\u7C7B\u578B\u7684\u52A8\u4F5C" : `\u672A\u77E5\u52A8\u4F5C\uFF08MoveType=${moveType}\uFF09`}\u3002`;
    }
  }
  return __toCommonJS(gantt_move_semantics_exports);
})();
