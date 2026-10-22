/**
 * 测试新建与复制操作的会话级协调器。
 *
 * 同一时刻只允许一个创建请求；重复触发复用当前 Promise，避免慢请求期间
 * 连续点击在服务端排队生成多个测试。状态回调用于同步按钮禁用和可见进度。
 */

export interface TestCreationContext {
  mode: "new" | "copy";
  sourceTestId: string;
  sourceName: string;
}

export interface TestCreationCoordinator {
  readonly pending: TestCreationContext | null;
  readonly isPending: boolean;
  run<T>(context: TestCreationContext, operation: () => Promise<T>): Promise<T>;
}

/** 创建一个页面会话内共享的测试创建协调器。 */
export function createTestCreationCoordinator(
  onPendingChange: (context: TestCreationContext | null) => void = () => {},
): TestCreationCoordinator {
  let pending: TestCreationContext | null = null;
  let inFlight: Promise<unknown> | null = null;

  return {
    get pending() { return pending; },
    get isPending() { return inFlight !== null; },
    run<T>(context: TestCreationContext, operation: () => Promise<T>): Promise<T> {
      if (inFlight) return inFlight as Promise<T>;
      pending = { ...context };
      onPendingChange(pending);
      const operationPromise = Promise.resolve().then(operation);
      const completionPromise = operationPromise.finally(() => {
        if (inFlight !== completionPromise) return;
        inFlight = null;
        pending = null;
        onPendingChange(null);
      });
      inFlight = completionPromise;
      return completionPromise;
    },
  };
}
