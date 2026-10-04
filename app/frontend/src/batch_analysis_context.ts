/**
 * 批量运行的分析快照：冻结设备与路径，运行提交后按需读取所选测试。
 * 分析数据读取不参与算法启动关键路径；失败不会取消已经提交的运行。
 */

interface TestReference { id: string; [key: string]: unknown }
interface AnalysisContextOptions {
  device: unknown;
  routes: unknown[];
  selectedTests: TestReference[];
  previousTests: TestReference[];
  readTest: (testId: string) => Promise<TestReference>;
}

const ANALYSIS_READ_CONCURRENCY = 4;

/** 冻结本次分析输入，返回可在运行请求受理后启动的唯一读取任务。 */
export function createBatchAnalysisContext(options: AnalysisContextOptions) {
  const selectedIds = options.selectedTests.map(test => String(test.id));
  const context = {
    device: structuredClone(options.device),
    routes: structuredClone(options.routes),
    tests: structuredClone(options.previousTests),
    ready: null as Promise<void> | null,
    load,
  };

  /** 按固定并发读取所选测试并合并历史卡片的上下文；重复调用复用同一任务。 */
  function load(): Promise<void> {
    if (context.ready) return context.ready;
    context.ready = (async () => {
      const testsById = new Map(context.tests.map(test => [String(test.id), test]));
      for (let offset = 0; offset < selectedIds.length; offset += ANALYSIS_READ_CONCURRENCY) {
        const tests = await Promise.all(selectedIds.slice(offset, offset + ANALYSIS_READ_CONCURRENCY).map(options.readTest));
        for (const test of tests) testsById.set(String(test.id), structuredClone(test));
      }
      context.tests = [...testsById.values()];
    })();
    return context.ready;
  }

  return context;
}
