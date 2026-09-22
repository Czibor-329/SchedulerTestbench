<#
Cycle Adapter 独立进程宿主。

平台通过标准输入发送单行 JSON，请求本脚本加载算法包根目录中的
Adapter4Scheduler.dll，并仅通过 IScheduler 生命周期调用算法。算法包的
Python 入口、模型和原生依赖均由 Adapter 自己管理。
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$PackageRoot
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$resolvedPackageRoot = [System.IO.Path]::GetFullPath($PackageRoot)
$requiredAssemblies = @(
    "SchedulerStandardInterface.dll",
    "Newtonsoft.Json.dll",
    "log4net.dll",
    "Python.Runtime.dll",
    "Adapter4Scheduler.dll"
)

foreach ($assemblyName in $requiredAssemblies) {
    $assemblyPath = Join-Path $resolvedPackageRoot $assemblyName
    if (-not (Test-Path -LiteralPath $assemblyPath -PathType Leaf)) {
        throw "Adapter 运行文件不存在：$assemblyPath"
    }
    [void][System.Reflection.Assembly]::LoadFrom($assemblyPath)
}

# JObject 本身实现 IEnumerable，在 Windows PowerShell 5.1 赋值时会被自动展开。
# 使用不具备枚举语义的强类型信封，确保协议字段和原始 payload 都完整保留。
$requestTypeDefinition = "using Newtonsoft.Json.Linq; public sealed class AdapterHostRequest { public string Operation { get; set; } public int RequestId { get; set; } public JToken Payload { get; set; } }"
Add-Type -TypeDefinition $requestTypeDefinition `
    -ReferencedAssemblies (Join-Path $resolvedPackageRoot "Newtonsoft.Json.dll")
$adapterHostRequestType = "AdapterHostRequest" -as [type]

$jsonSettings = [Newtonsoft.Json.JsonSerializerSettings]::new()
$jsonSettings.Converters.Add([Adapter4Scheduler.SchedulerInterfaceConverter]::new())
$scheduler = [Adapter4Scheduler.Scheduler]::GetIns()
$script:outputs = [System.Collections.Generic.List[object]]::new()
$script:deadlocks = [System.Collections.Generic.List[object]]::new()
$outputHandler = [SchedulerStandardInterface.Interface.OnOutput] {
    param($sender, $eventArgs)
    $script:outputs.Add($eventArgs.OutputParams)
}
$deadlockHandler = [SchedulerStandardInterface.Interface.OnDeadLock] {
    param($sender, $eventArgs)
    $script:deadlocks.Add($eventArgs.DeadLockInfo)
}
$scheduler.add_OutputHandler($outputHandler)
$scheduler.add_DeadLockHandler($deadlockHandler)

function Write-AdapterResponse {
    param([hashtable]$Response)

    $json = [Newtonsoft.Json.JsonConvert]::SerializeObject($Response)
    [Console]::Out.WriteLine("ADAPTER_HOST_RESPONSE:" + $json)
    [Console]::Out.Flush()
}

try {
    while (($line = [Console]::In.ReadLine()) -ne $null) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        try {
            # 不经 PowerShell PSCustomObject 中转，避免 Newtonsoft 再序列化时
            # 丢失 Robots、Stations、ControlJobs 等嵌套属性。
            $request = [Newtonsoft.Json.JsonConvert]::DeserializeObject(
                $line,
                $adapterHostRequestType
            )
            $operation = $request.Operation
            $payloadJson = if ($null -eq $request.Payload) {
                "null"
            }
            else {
                $request.Payload.ToString([Newtonsoft.Json.Formatting]::None)
            }
            switch ($operation) {
                "init" {
                    $topology = [Newtonsoft.Json.JsonConvert]::DeserializeObject(
                        $payloadJson,
                        [SchedulerStandardInterface.Interface.Modules.IToolTopo],
                        $jsonSettings
                    )
                    $scheduler.InitToolTopo($topology)
                    Write-AdapterResponse @{ ok = $true }
                }
                "update" {
                    $script:outputs.Clear()
                    $script:deadlocks.Clear()
                    $update = [Newtonsoft.Json.JsonConvert]::DeserializeObject(
                        $payloadJson,
                        [SchedulerStandardInterface.Interface.IUpdateParams],
                        $jsonSettings
                    )
                    $requestId = $request.RequestId
                    $scheduler.StartSchedule($requestId, $update)
                    if ($script:outputs.Count -gt 0) {
                        $outputJson = [Newtonsoft.Json.JsonConvert]::SerializeObject(
                            $script:outputs[$script:outputs.Count - 1]
                        )
                        Write-AdapterResponse @{
                            ok = $true
                            outputJson = $outputJson
                            outputCount = $script:outputs.Count
                        }
                    }
                    elseif ($script:deadlocks.Count -gt 0) {
                        Write-AdapterResponse @{
                            ok = $false
                            error = "Adapter 触发 DeadLock"
                            deadlocks = @($script:deadlocks)
                        }
                    }
                    else {
                        Write-AdapterResponse @{
                            ok = $false
                            error = "Adapter 未产生 OnOutput 或 DeadLock"
                        }
                    }
                }
                "updateMoveState" {
                    $moveState = [Newtonsoft.Json.JsonConvert]::DeserializeObject(
                        $payloadJson,
                        [SchedulerStandardInterface.Interface.IMoveStateInfo],
                        $jsonSettings
                    )
                    $scheduler.UpdateMoveState($moveState)
                    Write-AdapterResponse @{ ok = $true }
                }
                "updateMoveStates" {
                    if ($null -eq $request.Payload -or
                        $request.Payload.Type -ne [Newtonsoft.Json.Linq.JTokenType]::Array) {
                        throw "updateMoveStates payload 必须是 JSON array"
                    }
                    $moveStateCount = 0
                    foreach ($moveStateToken in $request.Payload.Children()) {
                        $moveState = $moveStateToken.ToObject(
                            [SchedulerStandardInterface.Interface.IMoveStateInfo],
                            [Newtonsoft.Json.JsonSerializer]::Create($jsonSettings)
                        )
                        $scheduler.UpdateMoveState($moveState)
                        $moveStateCount++
                    }
                    Write-AdapterResponse @{
                        ok = $true
                        moveStateCount = $moveStateCount
                    }
                }
                "quit" {
                    $scheduler.QuitSchedule()
                    Write-AdapterResponse @{ ok = $true }
                    break
                }
                default {
                    Write-AdapterResponse @{
                        ok = $false
                        error = "未知 Adapter Host 操作：$operation"
                    }
                }
            }
            if ($operation -eq "quit") {
                break
            }
        }
        catch {
            $errorDetail = $_.Exception.ToString() + "`n" + `
                $_.InvocationInfo.PositionMessage + "`n" + $_.ScriptStackTrace
            Write-AdapterResponse @{
                ok = $false
                error = $errorDetail
            }
        }
    }
}
finally {
    if ($null -ne $scheduler) {
        $scheduler.Dispose()
    }
}
