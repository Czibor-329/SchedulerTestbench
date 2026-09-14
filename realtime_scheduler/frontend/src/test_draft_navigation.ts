/**
 * 测试编辑离开流程：保存、放弃或继续编辑。仅负责会话草稿决策，不访问存储接口。
 * 保存与还原由编辑器提供；失败向调用方传播，导航只能在成功解决草稿后继续。
 */
export type DraftChoice = "save" | "discard" | "cancel";

/** 解决一次离开请求；返回是否允许导航，取消时不触发任何写入。 */
export async function resolveTestDraft(dirty: boolean, choose: () => Promise<DraftChoice>, save: () => Promise<unknown>, discard: () => Promise<unknown>): Promise<boolean> {
  if (!dirty) return true;
  const choice = await choose();
  if (choice === "cancel") return false;
  if (choice === "save") await save();
  else await discard();
  return true;
}

/** 打开具名模态对话框并等待选择；Escape 等同继续编辑，重复导航不重复弹窗。 */
export function createDraftChoiceDialog(dialog: HTMLDialogElement): () => Promise<DraftChoice> {
  let pending: Promise<DraftChoice> | null = null;
  return () => {
    if (pending) return pending;
    pending = new Promise<DraftChoice>(resolve => {
      dialog.returnValue = "cancel";
      dialog.addEventListener("close", () => {
        pending = null;
        resolve(dialog.returnValue === "save" || dialog.returnValue === "discard" ? dialog.returnValue : "cancel");
      }, { once: true });
      dialog.showModal();
    });
    return pending;
  };
}
