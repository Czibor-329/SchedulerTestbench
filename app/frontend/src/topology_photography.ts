/**
 * 拓扑摄影的离散时刻、晶圆位置比较和 PNG 序列归档。
 *
 * 摄影以回放快照的动作完成边界为准；门、加工颜色和机械手运动不产生新照片。
 * 本模块仅计算候选时刻与位置签名，并生成浏览器可下载的标准 store ZIP；
 * 图像绘制、播放状态和下载交互由回放页面拥有，不在此维护 DOM 或持久化数据。
 */
import type { DeviceDefinition, MoveRecord } from "./analysis_contracts";
import type { LoadPortSlotSnapshot, WorkspaceSnapshot } from "./workspace_visualizer";

/** 连续摄影包含起始帧在内的最大照片数。 */
export const MAX_TOPOLOGY_PHOTOGRAPHS = 20;

export interface PhotographyFile {
  name: string;
  data: Blob | Uint8Array;
}

const PICK_MOVE_TYPES = new Set([0, 2]);
const POSITION_CHANGE_MOVE_TYPES = new Set([0, 1, 2, 3, 4]);
const ZIP_LOCAL_SIGNATURE = 0x04034b50;
const ZIP_CENTRAL_SIGNATURE = 0x02014b50;
const ZIP_END_SIGNATURE = 0x06054b50;
const ZIP_VERSION = 20;
const ZIP_UTF8_FLAG = 0x0800;
const ZIP_LOCAL_HEADER_BYTES = 30;
const ZIP_CENTRAL_HEADER_BYTES = 46;
const ZIP_END_HEADER_BYTES = 22;
const ZIP_MAXIMUM_UINT16 = 0xffff;
const ZIP_MAXIMUM_UINT32 = 0xffffffff;
const ZIP_DOS_EPOCH_DATE = 0x0021;
const CRC32_POLYNOMIAL = 0xedb88320;
const CRC32_YIELD_BYTES = 1024 * 1024;
const PHOTO_SEQUENCE_MINIMUM_DIGITS = 4;

/** 将可选协议时间规范为有限数字，规则与回放时间轴保持一致。 */
function finiteNumber(value: unknown, fallback = 0): number {
  const result = Number(value);
  return Number.isFinite(result) ? result : fallback;
}

/** 缺省协议列表按空数组读取，避免从非标准标量推断物料。 */
function listValues(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** 读取多片动作的对应字段，单站点或单任务字段可复用于全部晶圆。 */
function indexedText(move: MoveRecord, field: string, index: number): string {
  const values = listValues(move[field]);
  return String(values[index] ?? values[0] ?? "");
}

/** 按设备类型和现有回放命名规则识别 LoadPort 与 DummyPort。 */
function isLoadPort(name: string, device?: DeviceDefinition | null): boolean {
  const type = String(device?.Stations?.[name]?.Type ?? "").trim().toLowerCase();
  return type === "loadport" || type === "dummyport"
    || (/DUMMY/i.test(name) && /PORT/i.test(name))
    || /^(LP\d*|P\d+|.*PORT)$/i.test(name);
}

/**
 * 查找没有显式补片记录时，回放按盒次推断的整盒装入边界。
 *
 * 同一模块槽位的第二个不同 MatID/TaskID/PJob 实例代表下一盒；整盒时刻是
 * 该盒首次 Pick 的开始时间。Dummy 原片反复取放不会增加盒次。
 */
function inferredReplenishmentTimes(
  records: readonly MoveRecord[],
  device?: DeviceDefinition | null,
): number[] {
  const ordered = records.map((move, index) => ({
    move,
    start: finiteNumber(move.StartTime),
    end: Math.max(finiteNumber(move.StartTime), finiteNumber(move.EndTime, finiteNumber(move.StartTime))),
    id: finiteNumber(move.MoveID, index + 1),
  })).sort((left, right) => left.start - right.start || left.end - right.end || left.id - right.id);
  const slotHistories = new Map<string, string[]>();
  const generationStarts = new Map<string, number>();
  for (const { move, start } of ordered) {
    if (!PICK_MOVE_TYPES.has(finiteNumber(move.MoveType, -1))) continue;
    listValues(move.MatIDList).map(String).filter(Boolean).forEach((wafer, index) => {
      const station = indexedText(move, "SrcStationList", index);
      const slot = finiteNumber(indexedText(move, "SrcSlotList", index));
      if (!station || !isLoadPort(station, device) || !Number.isInteger(slot) || slot <= 0) return;
      const tasks = listValues(move.TaskID).map(String).filter(Boolean);
      const jobs = listValues(move.PJobName).map(String).filter(Boolean);
      const task = tasks[index] ?? tasks[0] ?? jobs[index] ?? jobs[0] ?? "";
      const instance = task ? `${wafer}\u0000${task}` : wafer;
      const slotKey = JSON.stringify([station, slot]);
      const history = slotHistories.get(slotKey) ?? [];
      let generation = history.indexOf(instance);
      if (generation < 0) {
        generation = history.length;
        history.push(instance);
        slotHistories.set(slotKey, history);
      }
      if (generation === 0) return;
      const generationKey = JSON.stringify([station, generation]);
      generationStarts.set(generationKey, Math.min(generationStarts.get(generationKey) ?? Infinity, start));
    });
  }
  return [...generationStarts.values()];
}

/**
 * 返回从当前时刻到回放终点的摄影候选时刻，按时间升序且同刻合并。
 *
 * records 为当前合并 MoveList，replenishmentTimes 为日志确认的补片时刻；
 * 可选 device 用于识别自定义名称的 LoadPort。首项始终为当前时刻，其后仅包含
 * Pick/Place/多片/Swap 完成与补片边界；调用方比较快照签名后跳过无位置变化的候选。
 */
export function photographyCandidateTimes(
  records: readonly MoveRecord[],
  replenishmentTimes: readonly number[],
  startTime: number,
  endTime: number,
  device?: DeviceDefinition | null,
): number[] {
  const end = Math.max(0, finiteNumber(endTime));
  const start = Math.min(end, Math.max(0, finiteNumber(startTime)));
  const candidates = new Set<number>([start]);
  const addBoundary = (time: number): void => {
    if (Number.isFinite(time) && time > start && time <= end) candidates.add(time);
  };
  for (const move of records) {
    if (!POSITION_CHANGE_MOVE_TYPES.has(finiteNumber(move.MoveType, -1))) continue;
    const moveStart = finiteNumber(move.StartTime);
    addBoundary(Math.max(moveStart, finiteNumber(move.EndTime, moveStart)));
  }
  for (const time of replenishmentTimes) addBoundary(time);
  for (const time of inferredReplenishmentTimes(records, device)) addBoundary(time);
  return [...candidates].sort((left, right) => left - right);
}

/** 只序列化实际占位，空槽数量与槽位数组顺序不属于晶圆位置。 */
function occupiedSlots(slots: readonly LoadPortSlotSnapshot[]): Array<[number, string]> {
  return slots.filter(slot => Boolean(slot.wafer))
    .map(slot => [slot.slot, slot.wafer] as [number, string])
    .sort((left, right) => left[0] - right[0] || left[1].localeCompare(right[1]));
}

/**
 * 为动作完成快照生成稳定的晶圆位置签名，不修改输入。
 *
 * 模块的 LoadPort/LoadLock/双腔槽位和机械手物理槽位优先；旧快照没有槽位信息时
 * 按模块中的晶圆集合回退。签名忽略门、加工颜色、时间、运动进度和选中状态。
 */
export function waferPositionSignature(snapshot: Pick<WorkspaceSnapshot, "modules" | "robots">): string {
  const positions: Array<[string, string, Array<[number | null, string]>]> = [];
  for (const module of snapshot.modules) {
    const slots = module.loadPortSlots?.length ? module.loadPortSlots
      : module.loadLockSlots?.length ? module.loadLockSlots
        : module.processSlots?.length ? module.processSlots : null;
    const occupancy: Array<[number | null, string]> = slots
      ? occupiedSlots(slots)
      : module.wafers.filter(Boolean).slice().sort().map(wafer => [null, wafer]);
    if (occupancy.length) positions.push(["module", module.name, occupancy]);
  }
  for (const robot of snapshot.robots) {
    const occupancy: Array<[number | null, string]> = robot.slotWafers
      ? Object.entries(robot.slotWafers).filter(([, wafer]) => Boolean(wafer))
        .map(([slot, wafer]) => [Number(slot), wafer] as [number, string])
        .sort((left, right) => Number(left[0]) - Number(right[0]) || left[1].localeCompare(right[1]))
      : robot.wafers.filter(Boolean).slice().sort().map(wafer => [null, wafer]);
    if (occupancy.length) positions.push(["robot", robot.name, occupancy]);
  }
  positions.sort((left, right) => left[0].localeCompare(right[0]) || left[1].localeCompare(right[1]));
  return JSON.stringify(positions);
}

/** 返回含一基序号和完整仿真秒数的 PNG 文件名；不舍弃相邻时刻的精度。 */
export function photographyFileName(index: number, time: number): string {
  const sequence = Math.max(1, Math.trunc(finiteNumber(index, 1)));
  const seconds = Math.max(0, finiteNumber(time));
  return `topology-${String(sequence).padStart(PHOTO_SEQUENCE_MINIMUM_DIGITS, "0")}-t${seconds}s.png`;
}

/** 生成标准 CRC32 查表，文件校验使用 ZIP 的 IEEE 多项式。 */
function crc32Table(): Uint32Array {
  const table = new Uint32Array(256);
  for (let index = 0; index < table.length; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) value = (value >>> 1) ^ ((value & 1) ? CRC32_POLYNOMIAL : 0);
    table[index] = value >>> 0;
  }
  return table;
}

const CRC32_TABLE = crc32Table();

/** 取消时抛出稳定 AbortError，使调用方与其它可取消操作使用同一失败语义。 */
function checkArchiveCancellation(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("照片归档已取消。", "AbortError");
}

/** 让出主线程处理取消按钮，再检查任务信号，不用固定等待估算任务进度。 */
async function yieldArchiveWork(signal?: AbortSignal): Promise<void> {
  await new Promise<void>(resolve => setTimeout(resolve, 0));
  checkArchiveCancellation(signal);
}

/** 分块计算文件 CRC32，每处理 1 MiB 让出主线程，使大照片仍可取消。 */
async function crc32(bytes: Uint8Array, signal?: AbortSignal): Promise<number> {
  let value = ZIP_MAXIMUM_UINT32;
  for (let start = 0; start < bytes.byteLength; start += CRC32_YIELD_BYTES) {
    checkArchiveCancellation(signal);
    const end = Math.min(start + CRC32_YIELD_BYTES, bytes.byteLength);
    for (let index = start; index < end; index += 1) {
      value = (value >>> 8) ^ CRC32_TABLE[(value ^ bytes[index]) & 0xff];
    }
    if (end < bytes.byteLength) await yieldArchiveWork(signal);
  }
  return (value ^ ZIP_MAXIMUM_UINT32) >>> 0;
}

/** 复制成普通 ArrayBuffer，兼容 BlobPart 不接受 SharedArrayBuffer 的类型边界。 */
function blobBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

/** 创建本地或中央目录的 ZIP 头，所有多字节字段均为小端序。 */
function zipFileHeader(
  central: boolean,
  nameLength: number,
  dataLength: number,
  checksum: number,
  localOffset: number,
): ArrayBuffer {
  const buffer = new ArrayBuffer(central ? ZIP_CENTRAL_HEADER_BYTES : ZIP_LOCAL_HEADER_BYTES);
  const view = new DataView(buffer);
  view.setUint32(0, central ? ZIP_CENTRAL_SIGNATURE : ZIP_LOCAL_SIGNATURE, true);
  if (central) view.setUint16(4, ZIP_VERSION, true);
  const offset = central ? 2 : 0;
  view.setUint16(4 + offset, ZIP_VERSION, true);
  view.setUint16(6 + offset, ZIP_UTF8_FLAG, true);
  view.setUint16(12 + offset, ZIP_DOS_EPOCH_DATE, true);
  view.setUint32(14 + offset, checksum, true);
  view.setUint32(18 + offset, dataLength, true);
  view.setUint32(22 + offset, dataLength, true);
  view.setUint16(26 + offset, nameLength, true);
  if (central) view.setUint32(42, localOffset, true);
  return buffer;
}

/**
 * 将命名 PNG 文件打包为 UTF-8、无压缩的标准 ZIP，并返回可下载 Blob。
 *
 * 文件按输入顺序保存，内容和名称保持原值；PNG 本身已压缩，因此使用 store
 * 避免新增压缩依赖。拒绝目录穿越、重复名称及超出非 ZIP64 范围的归档。
 * 可选 signal 在文件读取与 CRC 分块边界取消任务，取消时抛出 AbortError。
 * Blob 在归档中复用，仅临时读取字节计算校验；Uint8Array 复制为不可变归档数据。
 * 本函数不触发下载，也不修改输入 Blob 或字节数组。
 */
export async function createPhotographyArchive(
  files: readonly PhotographyFile[],
  signal?: AbortSignal,
): Promise<Blob> {
  checkArchiveCancellation(signal);
  if (files.length > ZIP_MAXIMUM_UINT16) throw new Error("照片数量超出标准 ZIP 的 65535 张限制。");
  const encoder = new TextEncoder();
  const localParts: BlobPart[] = [];
  const centralParts: ArrayBuffer[] = [];
  const names = new Set<string>();
  let localOffset = 0;
  let centralSize = 0;

  // 顺序读取单张照片，生成校验码和目录偏移；保持连续摄影的照片顺序。
  for (const file of files) {
    checkArchiveCancellation(signal);
    const normalizedName = file.name.replace(/\\/g, "/");
    if (!normalizedName || normalizedName.startsWith("/") || /^[A-Za-z]:/.test(normalizedName)
      || normalizedName.split("/").some(part => !part || part === "." || part === "..")
      || normalizedName.includes("\u0000")) throw new Error("照片名称必须是有效的 ZIP 相对路径。");
    if (names.has(normalizedName)) throw new Error("照片归档中不能包含重复名称。");
    names.add(normalizedName);
    const name = encoder.encode(normalizedName);
    if (name.byteLength > ZIP_MAXIMUM_UINT16) throw new Error("照片名称超出标准 ZIP 的长度限制。");
    const data = file.data instanceof Uint8Array ? new Uint8Array(file.data) : file.data;
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(await data.arrayBuffer());
    checkArchiveCancellation(signal);
    const nextLocalOffset = localOffset + ZIP_LOCAL_HEADER_BYTES + name.byteLength + bytes.byteLength;
    const nextCentralSize = centralSize + ZIP_CENTRAL_HEADER_BYTES + name.byteLength;
    if (bytes.byteLength > ZIP_MAXIMUM_UINT32
      || nextLocalOffset + nextCentralSize + ZIP_END_HEADER_BYTES > ZIP_MAXIMUM_UINT32) {
      throw new Error("照片归档超出标准 ZIP 的 4 GiB 限制，请缩短拍摄范围。");
    }
    const checksum = await crc32(bytes, signal);
    const dataPart: BlobPart = data instanceof Uint8Array ? data.buffer : data;
    localParts.push(zipFileHeader(false, name.byteLength, bytes.byteLength, checksum, localOffset),
      blobBuffer(name), dataPart);
    centralParts.push(zipFileHeader(true, name.byteLength, bytes.byteLength, checksum, localOffset), blobBuffer(name));
    localOffset = nextLocalOffset;
    centralSize = nextCentralSize;
    await yieldArchiveWork(signal);
  }

  // 写入中央目录终止记录，使常用解压器可直接枚举并校验所有 PNG。
  checkArchiveCancellation(signal);
  const end = new ArrayBuffer(ZIP_END_HEADER_BYTES);
  const view = new DataView(end);
  view.setUint32(0, ZIP_END_SIGNATURE, true);
  view.setUint16(8, files.length, true);
  view.setUint16(10, files.length, true);
  view.setUint32(12, centralSize, true);
  view.setUint32(16, localOffset, true);
  return new Blob([...localParts, ...centralParts, end], { type: "application/zip" });
}
