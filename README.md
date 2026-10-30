# 测试工作台

本仓库不实现、训练或发布调度算法。

## 快速开始

```powershell
python -m app.backend.main --open
```

浏览器会打开本地控制台；默认地址为 <http://127.0.0.1:8765/config_editor.html>。服务默认只监听本机。用 `Ctrl+C` 停止服务。

## 企业接口对齐

- 算法 `init()` 接收完整设备拓扑，不再按当前任务裁剪模块或时序字典。
- 新运行的产品 ID 从 `0`、Dummy ID 从 `1000000` 起；PJob 使用
  `{TaskID}.PJOB_{CJob序号}` 命名，补片 TaskID 全局递增。历史已保存日志不改写。
- 标准算法重算接收物理现场；在途机器人目标指向和 Running Swap 的交换后
  物料位置按协议单独投影，不能提前提交压力转换、加工计数或 Dummy 寿命。
- Dummy/DummyWac 的显式 IdleTime Min/Max 保持不变；仅缺省字段采用企业值。
  同一模块的同名 Recipe 若存在不同定义，拒绝运行并报告冲突，不静默取首条。

这些是请求构造与仿真快照语义，不包含算法流控、模型或 C# 分段返回实现。
对照企业与本地结果时，应使用相同完整设备、任务配置、算法版本及模型。

## 部署算法

外部算法以受信任的本地 Python 包形式运行在服务进程中。请只部署来源可信、已完成安全审查的交付包；平台不支持从页面上传算法，也不应把算法包放入设备或测试集交换包。

### 1. 放置算法包

默认算法根目录是本仓库的 `alg/`。每个外部算法必须是 `other_alg` 下的一级目录，目录名即算法 ID：

```text
alg/other_alg/<算法名称>/src/infer/scheduler.py			#新版接口格式
alg/other_alg/<算法名称>/CT/infer/scheduler.py			#旧版接口格式
```

`scheduler.py`必须实现 `init()` 和 `update()`

### 2. 使用独立算法目录（可选）

若要将算法与工作台分开部署，创建以下结构：

```text
D:\scheduler-algorithms\other_alg\<算法名称>\src\infer\scheduler.py
```

启动服务前设置 `CT_ALGORITHM_ROOT`；它必须指向**算法根目录**，而不是某一个算法目录：

```powershell
$env:CT_ALGORITHM_ROOT = "D:\scheduler-algorithms"
python -m app.backend.main --open
```

该变量会同时改变内置和外部算法的查找位置。若仍需要内置算法，请在新根目录保留它们所需的文件。
