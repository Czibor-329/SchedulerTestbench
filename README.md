# 测试工作台

本仓库不实现、训练或发布调度算法。

## 快速开始

```powershell
python -m app.backend.main --open
```

浏览器会打开本地控制台；默认地址为 <http://127.0.0.1:8765/config_editor.html>。服务默认只监听本机。用 `Ctrl+C` 停止服务。

## 部署算法

外部算法只能以受信任的本地 DLL 算法包接入。请只部署来源可信、已完成安全审查的交付包；平台不支持从页面上传算法，也不应把算法包放入设备或测试集交换包。

### 1. 放置算法包

默认算法根目录是本仓库的 `alg/`。每个外部算法必须是 `other_alg` 下的一级目录，目录名即算法 ID：

```text
alg/other_alg/<算法名称>/Adapter4Scheduler.dll
```

DLL 必须按公司标准调度接口实现。

### 2. 使用独立算法目录（可选）

若要将算法与工作台分开部署，创建以下结构：

```text
D:\scheduler-algorithms\other_alg\<算法名称>\Adapter4Scheduler.dll
```

启动服务前设置 `CT_ALGORITHM_ROOT`；它必须指向**算法根目录**，而不是某一个算法目录：

```powershell
$env:CT_ALGORITHM_ROOT = "D:\scheduler-algorithms"
python -m app.backend.main --open
```

该变量会同时改变内置和外部算法的查找位置。若仍需要内置算法，请在新根目录保留它们所需的文件。
