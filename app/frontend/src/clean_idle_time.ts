/**
 * 前置清洁 IdleTime 配置：维护已有 lower/upper 字段的默认值、校验和弹窗读写。
 * 仅处理编辑与接口参数，不计算设备空闲时间，也不执行调度触发判断。
 * Min 是触发下限；Max 保留在标准接口中，不参与触发判定。
 */
export const DEFAULT_IDLE_TIME_MIN = 0;
export const DEFAULT_IDLE_TIME_MAX = 99999;

/** 判断编辑器类别是否提供前置清洁 IdleTime 配置。 */
export function supportsCleanIdleTime(cleanType: string): boolean {
  return ["preclean", "dummy", "dummywac"].includes(cleanType);
}

/** 从已有字段读取阈值；仅缺失值使用默认值，非法输入抛错，不修改传入对象。 */
export function cleanIdleTimeValues(clean: { lower?: unknown; upper?: unknown }): { lower: number; upper: number } {
  const parse = (value: unknown, fallback: number): number => {
    if (value === undefined || value === null) return fallback;
    if (typeof value !== "number" && typeof value !== "string") throw new Error("IdleTime 必须是非负有限数值。");
    if (typeof value === "string" && !value.trim()) throw new Error("请填写 IdleTime 最小值和最大值。");
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) throw new Error("IdleTime 必须是非负有限数值。");
    return number;
  };
  const lower = parse(clean.lower, DEFAULT_IDLE_TIME_MIN);
  const upper = parse(clean.upper, DEFAULT_IDLE_TIME_MAX);
  if (upper < lower) throw new Error("IdleTime 最大值不能小于最小值。");
  return { lower, upper };
}

/** 按清洁类型显示并启用阈值输入；隐藏字段不参与表单有效性校验。 */
export function updateCleanIdleTimeFields(root: Document, cleanType: string): void {
  const enabled = supportsCleanIdleTime(cleanType);
  root.getElementById("cleanIdleTimeFields")!.hidden = !enabled;
  for (const suffix of ["Min", "Max"]) {
    root.getElementById(`cleanIdleTime${suffix}Field`)!.hidden = !enabled;
    (root.getElementById(`cleanIdleTime${suffix}`) as HTMLInputElement).disabled = !enabled;
  }
}
