"""在独立 Windows 进程中调用算法包的 ``Adapter4Scheduler.dll``。

该模块拥有 Adapter Host 子进程、JSON 行协议和超时清理。平台主进程不加载
.NET、pythonnet 或算法包的 Python 模块，从而隔离同名程序集和 PythonEngine。
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Optional


JsonObject = dict[str, Any]
RESPONSE_PREFIX = "ADAPTER_HOST_RESPONSE:"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0
ADAPTER_DLL_NAME = "Adapter4Scheduler.dll"
ADAPTER_HOST_NAME = "AdapterHost.ps1"


class DotNetAdapterRuntime:
    """管理一个算法包对应的长期 Adapter Host 进程。"""

    def __init__(
        self,
        package_root: Path,
        *,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        """启动 Host，并准备串行请求通道。

        参数:
            package_root: 包含 Adapter DLL、依赖和算法资源的目录；Host 由平台提供。
            timeout_seconds: 单次 init/update 最长等待秒数。
        """
        self.package_root = package_root.expanduser().resolve()
        self.timeout_seconds = float(timeout_seconds)
        self._request_lock = threading.Lock()
        self._responses: queue.Queue[Optional[JsonObject]] = queue.Queue()
        self._diagnostic_lines: deque[str] = deque(maxlen=50)
        self._closed = False

        adapter_path = self.package_root / ADAPTER_DLL_NAME
        host_path = Path(__file__).resolve().with_name(ADAPTER_HOST_NAME)
        if not adapter_path.is_file():
            raise FileNotFoundError(f"算法包缺少 {ADAPTER_DLL_NAME}：{adapter_path}")
        if not host_path.is_file():
            raise FileNotFoundError(f"平台缺少 {ADAPTER_HOST_NAME}：{host_path}")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("运行 Adapter DLL 需要 Windows PowerShell")

        environment = os.environ.copy()
        environment.setdefault("PYTHONIOENCODING", "utf-8")
        environment["ADAPTER_HOST_SCRIPT"] = str(host_path)
        environment["ADAPTER_PACKAGE_ROOT"] = str(self.package_root)
        self._process = subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                (
                    "& ([scriptblock]::Create([IO.File]::ReadAllText("
                    "$env:ADAPTER_HOST_SCRIPT, [Text.Encoding]::UTF8))) "
                    "-PackageRoot $env:ADAPTER_PACKAGE_ROOT"
                ),
            ],
            cwd=str(self.package_root),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            # Windows 管道写入不能使用会在流首自动写 BOM 的 utf-8-sig；
            # Host 端按无 BOM UTF-8 读取逐行 JSON。
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(
            target=self._read_output,
            name=f"AdapterHostReader-{self.package_root.name}",
            daemon=True,
        )
        self._reader.start()

    def _read_output(self) -> None:
        """读取 Host 输出，只把带协议前缀的行交给请求线程。"""
        stream = self._process.stdout
        if stream is None:
            self._responses.put(None)
            return
        for raw_line in stream:
            line = raw_line.strip().lstrip("\ufeff")
            if not line.startswith(RESPONSE_PREFIX):
                if line:
                    self._diagnostic_lines.append(line)
                continue
            try:
                response = json.loads(line[len(RESPONSE_PREFIX):])
            except json.JSONDecodeError:
                self._diagnostic_lines.append(line)
                continue
            self._responses.put(response)
        self._responses.put(None)

    def _request(self, operation: str, **fields: Any) -> JsonObject:
        """发送一条 Host 请求，校验响应或抛出带诊断的异常。"""
        if self._closed:
            raise RuntimeError("Adapter Host 已关闭")
        request = {"operation": operation, **fields}
        with self._request_lock:
            if self._process.poll() is not None:
                raise RuntimeError(self._failure_message("Adapter Host 已提前退出"))
            stdin = self._process.stdin
            if stdin is None:
                raise RuntimeError("Adapter Host 标准输入不可用")
            try:
                stdin.write(
                    json.dumps(
                        request,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ) + "\n"
                )
                stdin.flush()
            except OSError as error:
                # Host 启动失败时，Windows 可能先把关闭的管道表现为 Errno 22/32；
                # 等待读取线程收完 PowerShell/.NET 诊断后再包装为可定位的异常。
                self._reader.join(timeout=1)
                raise RuntimeError(
                    self._failure_message("Adapter Host 输入管道已关闭")
                ) from error
            try:
                response = self._responses.get(timeout=self.timeout_seconds)
            except queue.Empty as error:
                self._terminate()
                raise TimeoutError(
                    self._failure_message(
                        f"Adapter Host 执行 {operation} 超过 {self.timeout_seconds:g} 秒"
                    )
                ) from error
            if response is None:
                raise RuntimeError(self._failure_message("Adapter Host 输出流已关闭"))
            if not response.get("ok"):
                detail = str(response.get("error") or "Adapter Host 调用失败")
                deadlocks = response.get("deadlocks")
                if deadlocks:
                    detail += "：" + json.dumps(deadlocks, ensure_ascii=False)
                raise RuntimeError(self._failure_message(detail))
            return response

    def _failure_message(self, message: str) -> str:
        """给错误附加最近的非协议输出，便于定位 DLL/Python 初始化失败。"""
        diagnostics = "\n".join(self._diagnostic_lines)
        return message if not diagnostics else f"{message}\nAdapter Host 输出：\n{diagnostics}"

    def init(self, payload: Mapping[str, Any]) -> None:
        """调用 Adapter 的 ``InitToolTopo``。"""
        self._request("init", payload=dict(payload))

    def update(self, request_id: int, payload: Mapping[str, Any]) -> JsonObject:
        """调用 ``StartSchedule`` 并返回最后一次 ``OnOutput``。"""
        response = self._request(
            "update",
            requestId=int(request_id),
            payload=dict(payload),
        )
        output_json = response.get("outputJson")
        if not isinstance(output_json, str):
            raise RuntimeError("Adapter Host 成功响应缺少 outputJson")
        output = json.loads(output_json)
        if not isinstance(output, dict):
            raise RuntimeError("Adapter OnOutput 不是 JSON object")
        return output

    def update_move_state(self, payload: Mapping[str, Any]) -> None:
        """把一条平台动作通知送入 Adapter 的 ``UpdateMoveState``。"""
        self._request("updateMoveState", payload=dict(payload))

    def update_move_states(self, payloads: list[Mapping[str, Any]]) -> None:
        """用一次进程通信把一批动作通知依次送入 Adapter。

        DLL 的公开接口仍然是逐条 ``UpdateMoveState``；批处理只合并平台与
        PowerShell Host 之间的 JSON/管道往返，保持通知顺序和 Adapter 语义。
        """
        if not payloads:
            return
        self._request(
            "updateMoveStates",
            payload=[dict(payload) for payload in payloads],
        )

    def close(self) -> None:
        """请求 Host 退出；失败时终止仅由本实例创建的子进程。"""
        if self._closed:
            return
        try:
            if self._process.poll() is None:
                self._request("quit")
        except (RuntimeError, TimeoutError, BrokenPipeError):
            pass
        finally:
            self._closed = True
            self._terminate()

    def _terminate(self) -> None:
        """终止当前 Adapter Host，不影响其他算法进程。"""
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)

    def __enter__(self) -> "DotNetAdapterRuntime":
        """返回已启动的运行时。"""
        return self

    def __exit__(self, *_: object) -> None:
        """离开上下文时关闭 Host。"""
        self.close()
