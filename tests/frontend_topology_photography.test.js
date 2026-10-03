/** 拓扑摄影的完成边界、真实槽位变化与 PNG 序列 ZIP 契约。 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

// 每次从当前 TypeScript 源码编译隔离模块，不读取仓库已有的 CJS 产物。
const frontendRoot = path.join(__dirname, "../app/frontend");
const esbuild = require(require.resolve("esbuild", { paths: [frontendRoot] }));
const buildRoot = fs.mkdtempSync(path.join(os.tmpdir(), "ct-topology-photography-"));
esbuild.buildSync({
  entryPoints: {
    photography: path.join(frontendRoot, "src/topology_photography.ts"),
    replay: path.join(frontendRoot, "src/workspace_visualizer_test_entry.ts"),
  },
  outdir: buildRoot,
  bundle: true,
  format: "cjs",
  platform: "node",
  target: "node18",
  logLevel: "silent",
});
test.after(() => fs.rmSync(buildRoot, { recursive: true, force: true }));
const photography = require(path.join(buildRoot, "photography.js"));
const { buildWorkspaceSnapshot } = require(path.join(buildRoot, "replay.js"));

/** 最小设备保留两片盒、两个机械手槽位和两槽加工腔的物理位置语义。 */
function slottedDevice() {
  return {
    Stations: {
      LP1: { Type: "LoadPort", Capacity: 2, Slots: [1, 2] },
      LA: { Type: "LoadLock", Capacity: 2, Slots: [1, 2] },
      PM1: { Type: "MultiProcessChamber", Capacity: 2, Slots: [1, 2] },
    },
    Robots: { VTR: { Type: "VacuumRobot", Capacity: 2, ArmInfo: { A: { SlotIDs: [1] }, B: { SlotIDs: [2] } } } },
  };
}

/** 构造单片取放，显式指定站点槽和机械手槽，不隐藏完成时间。 */
function transfer({ id = 1, type = 0, wafer = "W1", station = "LP1", stationSlot = 1, robotSlot = 1, start = 0, end = 10, task = "T1" } = {}) {
  const pick = type === 0 || type === 2;
  return {
    MoveID: id, MoveType: type, ModuleName: "VTR", MatIDList: [wafer], TaskID: [task],
    RobotSlotList: [robotSlot], StartTime: start, EndTime: end,
    [pick ? "SrcStationList" : "DestStationList"]: [station],
    [pick ? "SrcSlotList" : "DestSlotList"]: [stationSlot],
  };
}

/** 依据摄影的公共规则采样，并在相邻完成快照位置相同时合并照片。 */
function changedPhotographyTimes(moves, device, start, end, replenishments = []) {
  const times = photography.photographyCandidateTimes(moves, replenishments.map(item => item.time), start, end, device);
  let previous = null;
  return times.filter(time => {
    const signature = photography.waferPositionSignature(buildWorkspaceSnapshot(moves, device, time, replenishments));
    if (signature === previous) return false;
    previous = signature;
    return true;
  });
}

test("唯一摄影入口紧邻导入日志，摄影设置弹层及状态保持可访问性契约", () => {
  const html = fs.readFileSync(path.join(frontendRoot, "config_editor.html"), "utf8");
  const ids = ["visualPhotographyButton", "visualPhotographyMenu", "visualPhotographyCurrent",
    "visualPhotographySequence", "visualPhotographyStatus", "visualPhotographyCancel",
    "visualPhotographyStartTime", "visualPhotographyCount"];
  for (const id of ids) assert.equal((html.match(new RegExp(`\\bid="${id}"`, "g")) || []).length, 1);
  // 按稳定 ID 读取起始标签，检查属性而不绑定整段 HTML 实现。
  const tag = id => html.match(new RegExp(`<[^>]+\\bid="${id}"[^>]*>`))[0];
  assert.match(tag("visualPhotographyButton"), /^<button\b/);
  assert.match(tag("visualPhotographyButton"), /aria-label="拓扑拍照"/);
  assert.match(tag("visualPhotographyButton"), /aria-haspopup="dialog"/);
  assert.match(tag("visualPhotographyButton"), /aria-controls="visualPhotographyMenu"/);
  assert.match(tag("visualPhotographyButton"), /aria-expanded="false"/);
  assert.match(tag("visualPhotographyMenu"), /role="dialog"/);
  assert.match(tag("visualPhotographyMenu"), /aria-label="拓扑拍照方式"/);
  assert.match(tag("visualPhotographyMenu"), /\bhidden\b/);
  for (const id of ["visualPhotographyCurrent", "visualPhotographySequence"]) {
    assert.match(tag(id), /^<button\b/);
    assert.match(tag(id), /type="button"/);
  }
  assert.match(tag("visualPhotographyStartTime"), /type="number"/);
  assert.match(tag("visualPhotographyStartTime"), /min="0"/);
  assert.match(tag("visualPhotographyCount"), /min="1"/);
  assert.match(tag("visualPhotographyCount"), /max="20"/);
  assert.match(tag("visualPhotographyCount"), /value="20"/);
  assert.match(tag("visualPhotographyStatus"), /role="status"/);
  assert.match(tag("visualPhotographyStatus"), /aria-live="polite"/);
  assert.match(tag("visualPhotographyCancel"), /\bhidden\b/);
  // 摄影菜单容器与日志按钮是相邻入口，中间不插入其它操作控件。
  assert.match(html, /id="visualPhotographySequence"[^>]*>[\s\S]*?<\/button>\s*<\/div>\s*<\/div>\s*<button[^>]*id="visualImportButton"/);
});

test("摄影候选包含当前时刻及五类完成边界，同时间合并并排除其它动作", () => {
  const moves = [0, 1, 2, 3, 4].map((type, index) => transfer({ type, start: index, end: 10 + index }));
  moves.push(...[5, 6, 7, 9, 10, 11, 12, 13, 14].map(type => ({ MoveType: type, StartTime: 3, EndTime: 8 })));
  moves.push(transfer({ end: 10 }), transfer({ start: 15, end: 25 }));
  assert.deepEqual(photography.photographyCandidateTimes(moves, [7, 7, 14, 30, NaN], 5, 20), [5, 7, 10, 11, 12, 13, 14]);
  assert.deepEqual(photography.photographyCandidateTimes(moves, [], 12, 20), [12, 13, 14]);
});

test("当前边界只拍一次，越界时间被裁剪，空计划仍保留当前照片", () => {
  assert.deepEqual(photography.photographyCandidateTimes([], [], 0, 0), [0]);
  assert.deepEqual(photography.photographyCandidateTimes([], [10], 30, 20), [20]);
  assert.deepEqual(photography.photographyCandidateTimes([], [], -5, 20), [0]);
  assert.deepEqual(photography.photographyCandidateTimes([
    transfer({ start: 4, end: 2 }), transfer({ start: 6, end: NaN }),
  ], [], 0, 20), [0, 4, 6]);
});

test("取片和放片只在完成后触发，门加工与转位不增加照片", () => {
  const moves = [
    transfer({ end: 10 }),
    { MoveID: 2, MoveType: 5, ModuleName: "VTR", StartTime: 10, EndTime: 12 },
    transfer({ id: 3, type: 1, station: "PM1", stationSlot: 2, start: 12, end: 20 }),
    { MoveID: 4, MoveType: 9, ModuleName: "PM1", MatIDList: ["W1"], TaskID: ["T1"], StartTime: 20, EndTime: 30 },
    { MoveID: 5, MoveType: 6, ModuleName: "PM1", StartTime: 30, EndTime: 31 },
  ];
  assert.deepEqual(changedPhotographyTimes(moves, slottedDevice(), 5, 31), [5, 10, 20]);
  const before = buildWorkspaceSnapshot(moves, slottedDevice(), 19);
  const completed = buildWorkspaceSnapshot(moves, slottedDevice(), 20);
  assert.notEqual(photography.waferPositionSignature(before), photography.waferPositionSignature(completed));
  assert.equal(photography.waferPositionSignature(completed), photography.waferPositionSignature(buildWorkspaceSnapshot(moves, slottedDevice(), 31)));
});

test("同时完成的多片动作与 Swap 在边界只产生一张新的系统状态", () => {
  const pick = { ...transfer({ type: 2, end: 10 }), MatIDList: ["W1", "W2"], SrcSlotList: [1, 2], RobotSlotList: [1, 2], TaskID: ["T1", "T1"] };
  const place = { ...transfer({ id: 2, type: 3, station: "PM1", start: 10, end: 20 }), MatIDList: ["W1", "W2"], DestSlotList: [1, 2], RobotSlotList: [1, 2], TaskID: ["T1", "T1"] };
  const door = { MoveType: 6, ModuleName: "LA", StartTime: 18, EndTime: 20 };
  assert.deepEqual(changedPhotographyTimes([pick, place, door], slottedDevice(), 0, 20), [0, 10, 20]);

  // 新片位于一号手槽，旧片位于 PM 一号槽，换片收回到空闲二号手槽。
  const swap = {
    MoveID: 2, MoveType: 4, ModuleName: "VTR", StationList: ["PM1"],
    RecvMatList: ["OLD"], SendMatList: ["NEW"], RecvSlotList: [2], SendSlotList: [1],
    StnSendSlotList: [1], StnRecvSlotList: [1], StartTime: 10, EndTime: 20,
  };
  assert.deepEqual(changedPhotographyTimes([transfer({ wafer: "NEW", end: 10 }), swap, door], slottedDevice(), 0, 20), [0, 10, 20]);
});

test("模块槽位或手槽改变会改变位置签名，顺序颜色门态与时间不会", () => {
  const snapshot = buildWorkspaceSnapshot([transfer({ end: 10 })], slottedDevice(), 10);
  const original = photography.waferPositionSignature(snapshot);
  const decoration = structuredClone(snapshot);
  decoration.time = 99;
  decoration.modules.reverse();
  decoration.modules.forEach(module => { module.status = "processing"; module.door = "open"; module.processedWafers = [...module.wafers]; });
  decoration.robots.forEach(robot => { robot.busy = true; robot.target = "PM1"; robot.processedWafers = [...robot.wafers]; });
  assert.equal(photography.waferPositionSignature(decoration), original);
  const shiftedHand = structuredClone(snapshot);
  shiftedHand.robots[0].slotWafers = { 2: "W1" };
  assert.notEqual(photography.waferPositionSignature(shiftedHand), original);

  for (const field of ["loadPortSlots", "loadLockSlots", "processSlots"]) {
    const before = { modules: [{ name: "module", wafers: ["W1"], [field]: [{ slot: 1, wafer: "W1", processed: false }, { slot: 2, wafer: "", processed: false }] }], robots: [] };
    const after = structuredClone(before);
    after.modules[0][field].reverse();
    after.modules[0][field].forEach(slot => { slot.processed = true; });
    assert.equal(photography.waferPositionSignature(after), photography.waferPositionSignature(before));
    after.modules[0][field] = [{ slot: 1, wafer: "" }, { slot: 2, wafer: "W1" }];
    assert.notEqual(photography.waferPositionSignature(after), photography.waferPositionSignature(before));
  }
});

test("槽位快照优先于过时的模块晶圆列表，旧快照按位置集合兼容", () => {
  const current = { modules: [{ name: "LP1", wafers: ["W1", "FUTURE"], loadPortSlots: [{ slot: 1, wafer: "W1" }] }], robots: [] };
  const cleaned = structuredClone(current);
  cleaned.modules[0].wafers = ["W1"];
  assert.equal(photography.waferPositionSignature(current), photography.waferPositionSignature(cleaned));
  const legacy = { modules: [{ name: "PM1", wafers: ["W2", "W1"] }], robots: [{ name: "VTR", wafers: ["W3"] }] };
  const reordered = structuredClone(legacy);
  reordered.modules[0].wafers.reverse();
  assert.equal(photography.waferPositionSignature(legacy), photography.waferPositionSignature(reordered));
});

test("日志补片边界与隐式新盒首 Pick 开始边界均可摄影，Dummy 原片复用不误判", () => {
  const moves = [
    transfer({ end: 5 }),
    transfer({ id: 2, type: 1, station: "PM1", start: 5, end: 10 }),
    transfer({ id: 3, wafer: "W2", task: "T2", start: 20, end: 25 }),
  ];
  assert.deepEqual(photography.photographyCandidateTimes(moves, [], 0, 25, slottedDevice()), [0, 5, 10, 20, 25]);
  assert.deepEqual(changedPhotographyTimes(moves, slottedDevice(), 0, 25), [0, 5, 10, 20, 25]);
  const replenishments = [{ time: 15, moduleName: "LP1", materials: [{ wafer: "W2", slot: 1, taskId: "T2" }] }];
  assert.deepEqual(changedPhotographyTimes(moves, slottedDevice(), 0, 25, replenishments), [0, 5, 10, 15, 25]);
  assert.deepEqual(photography.photographyCandidateTimes([
    transfer({ wafer: "DUMMY", station: "DummyPort", end: 5 }),
    transfer({ id: 2, wafer: "DUMMY", station: "DummyPort", start: 20, end: 25 }),
  ], [], 0, 25), [0, 5, 25]);
});

test("同 MatID 的新任务与自定义 LoadPort 名称仍能识别下一盒", () => {
  const moves = [transfer({ station: "Cassette", end: 5 }), transfer({ id: 2, station: "Cassette", task: "T2", start: 20, end: 25 })];
  assert.deepEqual(photography.photographyCandidateTimes(moves, [], 0, 25, { Stations: { Cassette: { Type: "LoadPort" } } }), [0, 5, 20, 25]);
  const shuffled = [moves[1], moves[0]];
  assert.deepEqual(photography.photographyCandidateTimes(shuffled, [], 0, 25, { Stations: { Cassette: { Type: "LoadPort" } } }), [0, 5, 20, 25]);
});

test("照片名称包含顺序和仿真秒数，相邻小数时刻不因舍入同名", () => {
  assert.equal(photography.photographyFileName(1, 12.25), "topology-0001-t12.25s.png");
  assert.equal(photography.photographyFileName(10000, 0), "topology-10000-t0s.png");
  assert.notEqual(photography.photographyFileName(1, 1.00001), photography.photographyFileName(1, 1.00002));
});

test("ZIP 支持 UTF-8 名称、Blob 与字节文件，目录长度偏移 CRC 和原始数据可校验", async () => {
  const files = [{ name: "摄影/0001.png", data: new Blob(["123456789"]) }, { name: "topology-0002-t20s.png", data: new Uint8Array() }];
  const archive = await photography.createPhotographyArchive(files);
  assert.equal(archive.type, "application/zip");
  const bytes = Buffer.from(await archive.arrayBuffer());
  const endOffset = bytes.length - 22;
  assert.equal(bytes.readUInt32LE(endOffset), 0x06054b50);
  assert.equal(bytes.readUInt16LE(endOffset + 8), 2);
  assert.equal(bytes.readUInt16LE(endOffset + 10), 2);
  const centralSize = bytes.readUInt32LE(endOffset + 12);
  const centralOffset = bytes.readUInt32LE(endOffset + 16);
  assert.equal(centralOffset + centralSize, endOffset);
  let cursor = centralOffset;
  for (const [index, expected] of files.entries()) {
    assert.equal(bytes.readUInt32LE(cursor), 0x02014b50);
    assert.equal(bytes.readUInt16LE(cursor + 8), 0x0800);
    assert.equal(bytes.readUInt16LE(cursor + 10), 0);
    assert.equal(bytes.readUInt32LE(cursor + 16), index === 0 ? 0xcbf43926 : 0);
    const nameLength = bytes.readUInt16LE(cursor + 28);
    const localOffset = bytes.readUInt32LE(cursor + 42);
    const name = bytes.subarray(cursor + 46, cursor + 46 + nameLength).toString("utf8");
    assert.equal(name, expected.name);
    assert.equal(bytes.readUInt32LE(localOffset), 0x04034b50);
    assert.equal(bytes.readUInt32LE(localOffset + 14), bytes.readUInt32LE(cursor + 16));
    assert.equal(bytes.readUInt32LE(localOffset + 18), bytes.readUInt32LE(cursor + 20));
    assert.equal(bytes.readUInt32LE(localOffset + 22), bytes.readUInt32LE(cursor + 24));
    assert.equal(bytes.readUInt16LE(localOffset + 26), nameLength);
    assert.equal(bytes.subarray(localOffset + 30, localOffset + 30 + nameLength).toString("utf8"), name);
    const length = bytes.readUInt32LE(localOffset + 22);
    assert.equal(bytes.subarray(localOffset + 30 + nameLength, localOffset + 30 + nameLength + length).toString(), index === 0 ? "123456789" : "");
    cursor += 46 + nameLength;
  }
  assert.equal(cursor, endOffset);
});

test("空 ZIP 仍有效，归档拒绝路径穿越和重复文件名", async () => {
  const empty = Buffer.from(await (await photography.createPhotographyArchive([])).arrayBuffer());
  assert.equal(empty.length, 22);
  assert.equal(empty.readUInt32LE(0), 0x06054b50);
  assert.equal(empty.readUInt16LE(10), 0);
  for (const name of ["../wafer.png", "/wafer.png", "C:\\wafer.png", "a//b.png", "a/./b.png"]) {
    await assert.rejects(photography.createPhotographyArchive([{ name, data: new Uint8Array() }]), /相对路径/);
  }
  await assert.rejects(photography.createPhotographyArchive([
    { name: "photo.png", data: new Uint8Array() }, { name: "photo.png", data: new Blob() },
  ]), /重复名称/);
});

test("已取消信号立即拒绝归档，大照片 CRC 处理中也能响应真实取消", async () => {
  const cancelled = new AbortController();
  cancelled.abort();
  await assert.rejects(photography.createPhotographyArchive([], cancelled.signal), { name: "AbortError" });

  const active = new AbortController();
  const pending = photography.createPhotographyArchive([
    { name: "large.png", data: new Uint8Array(3 * 1024 * 1024) },
  ], active.signal);
  // 在真实事件循环中取消；归档必须让出主线程，不能先同步完成整张 CRC。
  const cancellationTimer = setTimeout(() => active.abort(), 0);
  try {
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(active.signal.aborted, true);
  } finally {
    clearTimeout(cancellationTimer);
  }
});

test("Blob 读取完成后检查取消，Uint8Array 输入随后变化不会改写归档内容", async () => {
  const active = new AbortController();
  class CancelAfterReadBlob extends Blob {
    /** 在真实 Blob 读取完成边界发出取消，覆盖异步读取后的信号检查。 */
    async arrayBuffer() {
      const result = await super.arrayBuffer();
      active.abort();
      return result;
    }
  }
  await assert.rejects(photography.createPhotographyArchive([
    { name: "cancelled.png", data: new CancelAfterReadBlob(["123456789"]) },
  ], active.signal), { name: "AbortError" });

  const originalBytes = new Uint8Array([1, 2, 3]);
  const pending = photography.createPhotographyArchive([{ name: "stable.png", data: originalBytes }]);
  originalBytes.fill(9);
  const archive = Buffer.from(await (await pending).arrayBuffer());
  const contentStart = 30 + archive.readUInt16LE(26);
  assert.deepEqual([...archive.subarray(contentStart, contentStart + 3)], [1, 2, 3]);
});
