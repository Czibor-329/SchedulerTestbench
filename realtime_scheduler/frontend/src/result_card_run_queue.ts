/**
 * 结果卡片双击运行队列。
 *
 * 本模块只管理去重、入队顺序和串行执行边界；卡片状态、运行请求与
 * 错误展示由页面入口负责。同一测试在等待或运行时不会重复入队。
 */

export type ResultCardRunQueueEvent = "queued" | "running" | "settled";

export interface ResultCardRunQueueOptions {
  runTest: (testId: string) => Promise<void>;
  onStateChange?: (testId: string, event: ResultCardRunQueueEvent) => void;
}

/** 创建一个按入队顺序串行执行的结果卡片运行队列。 */
export function createResultCardRunQueue(options: ResultCardRunQueueOptions) {
  const pendingTestIds: string[] = [];
  const queuedOrRunning = new Set<string>();
  let draining = false;

  /** 持续取出队首测试；单项失败不会阻断后续测试。 */
  async function drain(): Promise<void> {
    if (draining) return;
    draining = true;
    try {
      while (pendingTestIds.length) {
        const testId = pendingTestIds.shift() as string;
        options.onStateChange?.(testId, "running");
        try {
          await options.runTest(testId);
        } catch {
          // 页面入口负责展示单项错误；队列必须继续处理后续测试。
        } finally {
          queuedOrRunning.delete(testId);
          options.onStateChange?.(testId, "settled");
        }
      }
    } finally {
      draining = false;
    }
  }

  return {
    /** 将测试加入队尾，返回 false 表示该测试已在等待或运行。 */
    enqueue(rawTestId: string): boolean {
      const testId = String(rawTestId || "").trim();
      if (!testId || queuedOrRunning.has(testId)) return false;
      pendingTestIds.push(testId);
      queuedOrRunning.add(testId);
      options.onStateChange?.(testId, "queued");
      queueMicrotask(() => void drain());
      return true;
    },
    /** 返回当前等待与运行的测试数，供页面提示使用。 */
    get size(): number {
      return queuedOrRunning.size;
    },
    /** 移除尚未开始的队列项；当前正在运行的测试交由后端终止。 */
    clearPending(): string[] {
      const removed = pendingTestIds.splice(0);
      for (const testId of removed) {
        queuedOrRunning.delete(testId);
        options.onStateChange?.(testId, "settled");
      }
      return removed;
    },
  };
}
