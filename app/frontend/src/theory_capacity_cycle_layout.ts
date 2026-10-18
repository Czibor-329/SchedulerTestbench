/** 循环交互图的确定性几何布局与布线：不解释时间、不计算产能。
 * 全量边统一分配端口与不共线通道，分类和选择不影响布局；交叉用跨线桥区分。
 * 节点按完整轮次与 VAC/ATM 分排，横坐标不是实际时刻。
 */
import type {TheoryCapacityCycleDiagram} from "./analysis_contracts";

export const NODE_WIDTH = 144, NODE_HEIGHT = 52, PADDING = 28;
const STEP = 176, TRACK_GAP = 18, PORT_MARGIN = 12, ARROW_GAP = 3;
const MIN_LANE_GAP = 160, MIN_OUTER_BAND = 76, BRIDGE_RADIUS = 5;
type Point = {x:number; y:number};
type Side = "top" | "bottom" | "left" | "right";
type Edge = TheoryCapacityCycleDiagram["edges"][number];
type Node = TheoryCapacityCycleDiagram["events"][number] & Point;
type Route = {edge:Edge; source:Node; target:Node; sourceSide:Side; targetSide:Side; band:string; track:number; points:Point[]};

/** 按完整关系分配互不复用的节点端口，返回节点及每条边的折线路径。 */
export function cycleDiagramLayout(diagram: TheoryCapacityCycleDiagram) {
  const rounds = Math.max(1, ...diagram.events.map(event => event.round));
  const columns = Math.max(4, ...Array.from({length:rounds}, (_, index) => Math.max(...["vacuum", "atmosphere"].map(lane => diagram.events.filter(event => event.round === index + 1 && event.lane === lane).length))));
  const roundWidth = columns * STEP + PADDING;
  const counters = new Map<string,number>();
  const nodes: Node[] = diagram.events.map(event => {
    const key = `${event.round}:${event.lane}`, column = counters.get(key) ?? 0;
    counters.set(key, column + 1);
    return {...event, x:PADDING + (event.round - 1) * roundWidth + column * STEP, y:0};
  });
  const byId = new Map(nodes.map(node => [node.id,node]));
  const bands = new Map<string,Array<Array<[number,number]>>>();
  const ports = new Map<string,Array<{route:Route; source:boolean; otherX:number}>>();
  const routes: Route[] = diagram.edges.map(edge => {
    const source = byId.get(edge.source)!, target = byId.get(edge.target)!;
    const direct = source.lane === target.lane && !edge.cycle_shift && target.x > source.x && ["vacuum","atmosphere"].includes(edge.category);
    const cross = source.lane !== target.lane;
    const band = direct ? "direct" : cross ? "middle" : source.lane === "vacuum" ? "top" : "bottom";
    const sourceSide: Side = direct ? "right" : cross ? source.lane === "vacuum" ? "bottom" : "top" : band === "top" ? "top" : "bottom";
    const targetSide: Side = direct ? "left" : cross ? target.lane === "vacuum" ? "bottom" : "top" : sourceSide;
    // 有交叠的水平区间使用独立通道；不同轮次的分离区间可以复用高度。
    const interval: [number,number] = [Math.min(source.x,target.x), Math.max(source.x,target.x) + NODE_WIDTH];
    const tracks = bands.get(band) ?? [];
    let track = tracks.findIndex(intervals => intervals.every(([start,end]) => interval[1] + PORT_MARGIN < start || interval[0] > end + PORT_MARGIN));
    if (track < 0) { track = tracks.length; tracks.push([]); }
    tracks[track].push(interval); bands.set(band,tracks);
    const route: Route = {edge,source,target,sourceSide,targetSide,band,track,points:[]};
    for (const isSource of [true,false]) {
      const node = isSource ? source : target, side = isSource ? sourceSide : targetSide;
      const key = `${node.id}:${side}`, entries = ports.get(key) ?? [];
      entries.push({route,source:isSource,otherX:isSource ? target.x : source.x}); ports.set(key,entries);
    }
    return route;
  });
  const vacuumY = Math.max(MIN_OUTER_BAND,(bands.get("top")?.length ?? 0) * TRACK_GAP + PADDING + 24);
  const atmosphereY = vacuumY + NODE_HEIGHT + Math.max(MIN_LANE_GAP,((bands.get("middle")?.length ?? 0) + 2) * TRACK_GAP);
  const height = atmosphereY + NODE_HEIGHT + Math.max(MIN_OUTER_BAND,((bands.get("bottom")?.length ?? 0) + 2) * TRACK_GAP);
  nodes.forEach(node => { node.y = node.lane === "vacuum" ? vacuumY : atmosphereY; });
  for (const entries of ports.values()) entries.sort((a,b) => a.otherX - b.otherX || a.route.edge.id.localeCompare(b.route.edge.id) || Number(a.source) - Number(b.source));
  // 同一列的上下排也共享端口分配，避免反向锁边的竖直段共线。
  const columnPorts = new Map<number, Array<{route:Route; source:boolean; otherX:number}>>();
  for (const entries of ports.values()) for (const entry of entries) {
    const side = entry.source ? entry.route.sourceSide : entry.route.targetSide;
    if (side !== "top" && side !== "bottom") continue;
    const x = (entry.source ? entry.route.source : entry.route.target).x;
    const column = columnPorts.get(x) ?? [];
    column.push(entry); columnPorts.set(x,column);
  }
  /** 根据全量端口表取边的起终点，给箭头与节点边框保留间隙。 */
  const port = (route:Route, source:boolean): Point => {
    const node = source ? route.source : route.target, side = source ? route.sourceSide : route.targetSide;
    const horizontal = side === "top" || side === "bottom";
    const entries = horizontal ? columnPorts.get(node.x)! : ports.get(`${node.id}:${side}`)!;
    const index = entries.findIndex(entry => entry.route === route && entry.source === source);
    const ratio = (index + 1) / (entries.length + 1);
    return horizontal ? {x:node.x + PORT_MARGIN + ratio * (NODE_WIDTH - 2 * PORT_MARGIN), y:node.y + (side === "top" ? -ARROW_GAP : NODE_HEIGHT + ARROW_GAP)}
      : {x:node.x + (side === "left" ? -ARROW_GAP : NODE_WIDTH + ARROW_GAP), y:node.y + NODE_HEIGHT * ratio};
  };
  for (const route of routes) {
    const source = port(route,true), target = port(route,false);
    const y = route.band === "top" ? vacuumY - (route.track + 1) * TRACK_GAP - PORT_MARGIN
      : route.band === "bottom" ? atmosphereY + NODE_HEIGHT + (route.track + 1) * TRACK_GAP + PORT_MARGIN
      : vacuumY + NODE_HEIGHT + (route.track + 1) * TRACK_GAP + PORT_MARGIN;
    route.points = route.band === "direct" ? [source,target] : [source,{x:source.x,y},{x:target.x,y},target];
  }
  return {nodes,rounds,roundWidth,width:rounds * roundWidth + PADDING,height,routes};
}

/** 绘制横线跨过竖线的桥形弧；端部截到箭头底边，粗描边不会穿进箭头。 */
export function cycleEdgeGeometry(route: Route, routes: Route[]) {
  const points = route.points.map(point => ({...point}));
  const tip = points[points.length - 1], previous = points[points.length - 2];
  const dx = tip.x - previous.x, dy = tip.y - previous.y, distance = Math.hypot(dx,dy);
  const ux = dx / distance, uy = dy / distance;
  const arrowLength = route.edge.cycle_shift ? 12 : 10, halfWidth = route.edge.cycle_shift ? 5.5 : 4.5;
  const base = {x:tip.x - ux * arrowLength,y:tip.y - uy * arrowLength};
  points[points.length - 1] = base;
  let path = `M${points[0].x},${points[0].y}`;
  for (let index = 1; index < points.length; index++) {
    const left = points[index - 1], right = points[index];
    const crossings: number[] = [];
    if (left.y === right.y) {
      for (const other of routes) {
        if (other === route) continue;
        for (let segment = 1; segment < other.points.length; segment++) {
          const a = other.points[segment - 1], b = other.points[segment];
          if (a.x === b.x && a.x > Math.min(left.x,right.x) + 2 * BRIDGE_RADIUS && a.x < Math.max(left.x,right.x) - 2 * BRIDGE_RADIUS && left.y > Math.min(a.y,b.y) && left.y < Math.max(a.y,b.y)) crossings.push(a.x);
        }
      }
      const direction = right.x > left.x ? 1 : -1;
      const ordered = [...new Set(crossings)].sort((a,b) => direction * (a-b));
      // 邻近交点合为一座桥，避免连续桥弧倒退并互相覆盖。
      for (let crossing = 0; crossing < ordered.length; crossing++) {
        const first = ordered[crossing];
        let last = first;
        while (crossing + 1 < ordered.length && Math.abs(ordered[crossing + 1] - last) < 2 * BRIDGE_RADIUS) last = ordered[++crossing];
        path += ` L${first-direction*BRIDGE_RADIUS},${left.y} Q${(first+last)/2},${left.y-2*BRIDGE_RADIUS} ${last+direction*BRIDGE_RADIUS},${left.y}`;
      }
    }
    path += ` L${right.x},${right.y}`;
  }
  const arrow = `${tip.x},${tip.y} ${base.x-uy*halfWidth},${base.y+ux*halfWidth} ${base.x+uy*halfWidth},${base.y-ux*halfWidth}`;
  const hit = route.points.map((point,index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  return {path,arrow,hit};
}
