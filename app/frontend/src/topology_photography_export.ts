/**
 * 拓扑摄影的浏览器导出边界。
 *
 * 接收回放工作台提供的冻结快照与俯视 HTML，使用当前拓扑样式离屏绘制 PNG，
 * 管理摄影菜单、连续摄影进度、取消与 ZIP 下载。不推进页面时间轴，不读取槽位面板，
 * 不修改设备、MoveList 或持久数据；离散位置规则由 topology_photography 拥有。
 */
import {
  createPhotographyArchive,
  photographyFileName,
  waferPositionSignature,
  MAX_TOPOLOGY_PHOTOGRAPHS,
} from "./topology_photography";
import type { WorkspaceSnapshot } from "./workspace_visualizer";

const TOPOLOGY_PHOTO_WIDTH = 1000;
const PHOTO_PIXEL_SCALE = 2;
const MAX_PHOTOGRAPHY_BYTES = 512 * 1024 * 1024;
const DOWNLOAD_URL_LIFETIME_MS = 60_000;
const XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml";

/** 摄影开始时冻结的输入；生成后续画面不会改变当前回放。 */
export interface TopologyPhotographySession {
  sourceName: string;
  times: number[];
  currentMarkup: string;
  snapshotAt(time: number): WorkspaceSnapshot;
  renderSnapshot(snapshot: WorkspaceSnapshot): string;
}

/** 工作台持有摄影控制器，以便输入更换、销毁时中止旧任务。 */
export interface TopologyPhotographyController {
  setAvailable(available: boolean): void;
  cancel(): void;
  destroy(): void;
}

/** 从同源平台样式表读取拓扑规则，保留伪元素与 CSS 变量，避免外部字体或图片依赖。 */
function topologyPhotographyStyles(root: Document): string {
  const sheets = Array.from(root.styleSheets).filter(sheet =>
    !sheet.href || new URL(sheet.href, root.baseURI).pathname.endsWith("/assets/config_editor.css"));
  const styles = sheets.map(sheet => Array.from(sheet.cssRules, rule => rule.cssText).join("\n")).join("\n");
  if (!styles) throw new Error("无法读取拓扑样式，请刷新页面后重试");
  return styles;
}

/**
 * 将完整俯视拓扑 HTML 绘制为两倍像素 PNG。
 * 使用独立 1000px 布局基准和自包含 SVG data URI，保持模块位置、伪元素和机械臂；
 * 不克隆屏幕上的槽位、工具栏或观察窗口，底图固定为无网格浅色。
 */
export async function captureTopologyPng(root: Document, markup: string, styles?: string): Promise<Blob> {
  const container = root.createElementNS(XHTML_NAMESPACE, "div") as HTMLElement;
  container.dataset.topologyPhoto = "true";
  container.innerHTML = markup;
  // 摄影只保留设备状态，屏幕上的状态图例不进入任何照片。
  container.querySelectorAll(".topology-status-legend").forEach(legend => legend.remove());
  const topology = container.querySelector<HTMLElement>(".reference-grid-canvas");
  if (!topology) throw new Error("当前没有可拍摄的设备拓扑");
  const height = Number.parseFloat(topology.style.getPropertyValue("--topology-canvas-height"));
  if (!Number.isFinite(height) || height <= 0) throw new Error("拓扑画面尺寸无效");
  // 独立根保留页面字体和主题变量，不依赖正在显示的工作台祖先。
  const view = root.defaultView;
  if (!view) throw new Error("当前浏览器无法创建拓扑照片");
  container.style.font = view.getComputedStyle(root.body).font;
  const variables = view.getComputedStyle(root.documentElement);
  Array.from(variables).filter(name => name.startsWith("--")).forEach(name =>
    container.style.setProperty(name, variables.getPropertyValue(name)));
  container.style.width = `${TOPOLOGY_PHOTO_WIDTH}px`;
  container.style.height = `${height}px`;
  const style = root.createElementNS(XHTML_NAMESPACE, "style");
  style.textContent = `${styles ?? topologyPhotographyStyles(root)}
    [data-topology-photo], [data-topology-photo] .equipment-schematic {
      width: ${TOPOLOGY_PHOTO_WIDTH}px !important; height: ${height}px !important;
      margin: 0 !important; border: 0 !important; border-radius: 0 !important;
      background: #f6f8fb !important; box-shadow: none !important;
    }
    [data-topology-photo] .reference-grid-canvas {
      width: ${TOPOLOGY_PHOTO_WIDTH}px !important; margin: 0 !important;
      background: #f6f8fb !important; background-image: none !important;
    }
    [data-topology-photo] *, [data-topology-photo] *::before, [data-topology-photo] *::after {
      animation: none !important; transition: none !important;
    }`;
  container.prepend(style);
  const xhtml = new XMLSerializer().serializeToString(container);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${TOPOLOGY_PHOTO_WIDTH}" height="${height}"><foreignObject width="100%" height="100%">${xhtml}</foreignObject></svg>`;
  const image = new view.Image();
  // data URI 避免带 foreignObject 的 Blob SVG 在部分浏览器中污染 canvas。
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  await image.decode();
  const canvas = root.createElement("canvas");
  canvas.width = TOPOLOGY_PHOTO_WIDTH * PHOTO_PIXEL_SCALE;
  canvas.height = Math.ceil(height * PHOTO_PIXEL_SCALE);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("当前浏览器不支持 PNG 摄影");
  context.fillStyle = "#f6f8fb";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) => canvas.toBlob(blob => {
    if (blob) resolve(blob);
    else reject(new Error("PNG 生成失败，请重试"));
  }, "image/png"));
}

/** 下载本地生成制品，延迟回收 URL 以允许浏览器完成读取。 */
function downloadPhotography(root: Document, blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = root.createElement("a");
  anchor.href = url;
  anchor.download = name;
  root.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_URL_LIFETIME_MS);
}

/** 去除平台禁止的文件名字符，让照片/压缩包可以直接保存到 Windows。 */
function photographySourceName(source: string): string {
  return (source.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/[. ]+$/g, "").slice(0, 80)
    || "拓扑回放");
}

/**
 * 绑定摄影入口、键盘菜单与导出任务，返回可释放的控制器。
 * createSession 按可选起始时间冻结输入，pausePlayback 在开始摄影时保留当前时间轴位置，
 * getTimeRange 提供设置输入的当前默认值与有效范围；连续照片数限制为 1～20。
 */
export function mountTopologyPhotography(
  root: Document,
  createSession: (startTime?: number) => TopologyPhotographySession | null,
  pausePlayback: () => void,
  getTimeRange: () => { currentTime: number; endTime: number },
): TopologyPhotographyController {
  const button = root.getElementById("visualPhotographyButton") as HTMLButtonElement | null;
  const menu = root.getElementById("visualPhotographyMenu");
  const currentButton = root.getElementById("visualPhotographyCurrent") as HTMLButtonElement | null;
  const sequenceButton = root.getElementById("visualPhotographySequence") as HTMLButtonElement | null;
  const status = root.getElementById("visualPhotographyStatus");
  const cancelButton = root.getElementById("visualPhotographyCancel") as HTMLButtonElement | null;
  const startInput = root.getElementById("visualPhotographyStartTime") as HTMLInputElement | null;
  const countInput = root.getElementById("visualPhotographyCount") as HTMLInputElement | null;
  // 未包含摄影入口的嵌入式工作台仍可独立回放，不注册摄影专属文档事件。
  if (!button) return {
    setAvailable(): void {},
    cancel(): void {},
    destroy(): void {},
  };
  const controls = new AbortController();
  let task: AbortController | null = null;
  let available = false;
  let startTimeEdited = false;

  /** 同步当前可用性与忙碌状态，允许任务期间随时取消。 */
  const updateControls = (): void => {
    if (button) button.disabled = !available || Boolean(task);
    if (currentButton) currentButton.disabled = !available || Boolean(task);
    if (sequenceButton) sequenceButton.disabled = !available || Boolean(task);
    if (cancelButton) cancelButton.hidden = !task;
  };
  /** 开关菜单并同步辅助技术需要的展开状态。 */
  const showMenu = (open: boolean): void => {
    if (menu) menu.hidden = !open;
    button?.setAttribute("aria-expanded", String(open));
  };
  /** 只在摄影状态区域显示进度或错误，保持已加载回放可用。 */
  const report = (message: string, error = false): void => {
    if (!status) return;
    status.textContent = message;
    status.dataset.error = String(error);
  };
  /** 在离屏画布串行摄影，对相邻物理位置签名去重，最后只下载一个文件。 */
  const photograph = async (sequence: boolean): Promise<void> => {
    if (task || !available) return;
    const bounds = getTimeRange();
    const startTime = sequence ? startInput?.valueAsNumber : undefined;
    const photoLimit = sequence ? countInput?.valueAsNumber : 1;
    if (sequence && (!Number.isFinite(startTime) || startTime < 0 || startTime > bounds.endTime)) {
      report(`起始时间须在 0 至 ${bounds.endTime} 秒之间`, true);
      startInput?.focus();
      return;
    }
    if (!Number.isInteger(photoLimit) || photoLimit < 1 || photoLimit > MAX_TOPOLOGY_PHOTOGRAPHS) {
      report(`拍摄张数须为 1 至 ${MAX_TOPOLOGY_PHOTOGRAPHS} 的整数`, true);
      countInput?.focus();
      return;
    }
    pausePlayback();
    const session = createSession(startTime);
    if (!session) return;
    showMenu(false);
    const activeTask = new AbortController();
    task = activeTask;
    updateControls();
    try {
      const styles = topologyPhotographyStyles(root);
      const times = sequence ? session.times : session.times.slice(0, 1);
      const photos: Array<{ name: string; data: Blob }> = [];
      let signature: string | null = null;
      let totalBytes = 0;
      for (let index = 0; index < times.length; index += 1) {
        // 去重候选也让出事件循环，保证进度、取消和页面查看保持响应。
        await new Promise<void>(resolve => setTimeout(resolve, 0));
        activeTask.signal.throwIfAborted();
        const snapshot = session.snapshotAt(times[index]);
        const nextSignature = waferPositionSignature(snapshot);
        report(`正在拍摄：${photos.length} 张 · ${index + 1}/${times.length}`);
        if (index > 0 && nextSignature === signature) continue;
        const markup = index === 0 ? session.currentMarkup : session.renderSnapshot(snapshot);
        const photo = await captureTopologyPng(root, markup, styles);
        activeTask.signal.throwIfAborted();
        totalBytes += photo.size;
        if (totalBytes > MAX_PHOTOGRAPHY_BYTES) {
          throw new Error("照片总量超过 512 MB，请将时间轴移到靠后位置分段拍摄");
        }
        photos.push({ name: photographyFileName(photos.length + 1, times[index]), data: photo });
        signature = nextSignature;
        // 上限按实际照片计算，重复位置不占额度，起始状态算第一张。
        if (photos.length >= photoLimit) break;
      }
      activeTask.signal.throwIfAborted();
      const source = photographySourceName(session.sourceName);
      if (sequence) {
        report(`正在打包 ${photos.length} 张照片…`);
        const archive = await createPhotographyArchive(photos, activeTask.signal);
        activeTask.signal.throwIfAborted();
        downloadPhotography(root, archive, `${source}-连续拍照.zip`);
      } else {
        downloadPhotography(root, photos[0].data, `${source}-${photos[0].name}`);
      }
      report(sequence ? `已下载 ${photos.length} 张照片` : "已下载当前照片");
    } catch (error) {
      if (activeTask.signal.aborted) report("拍摄已取消");
      else report(error instanceof Error ? error.message : String(error), true);
    } finally {
      if (task === activeTask) task = null;
      updateControls();
    }
  };

  button?.addEventListener("click", () => {
    const bounds = getTimeRange();
    if (startInput) {
      startInput.max = String(bounds.endTime);
      if (!startTimeEdited) startInput.value = String(bounds.currentTime);
    }
    showMenu(Boolean(menu?.hidden));
    if (menu && !menu.hidden) currentButton?.focus();
  }, { signal: controls.signal });
  startInput?.addEventListener("input", () => { startTimeEdited = true; }, { signal: controls.signal });
  currentButton?.addEventListener("click", () => void photograph(false), { signal: controls.signal });
  sequenceButton?.addEventListener("click", () => void photograph(true), { signal: controls.signal });
  cancelButton?.addEventListener("click", () => task?.abort(), { signal: controls.signal });
  root.addEventListener("click", event => {
    if (!(event.target as Element).closest(".topology-photography")) showMenu(false);
  }, { signal: controls.signal });
  root.addEventListener("focusin", event => {
    if (!(event.target as Element).closest(".topology-photography")) showMenu(false);
  }, { signal: controls.signal });
  root.addEventListener("keydown", event => {
    if (menu?.hidden || !root.activeElement?.closest(".topology-photography")) return;
    if (event.key === "Escape") {
      showMenu(false);
      button?.focus();
    }
  }, { signal: controls.signal });
  updateControls();
  return {
    setAvailable(value): void {
      available = value;
      if (!value) startTimeEdited = false;
      updateControls();
    },
    cancel(): void { task?.abort(); showMenu(false); },
    destroy(): void { task?.abort(); controls.abort(); showMenu(false); },
  };
}
