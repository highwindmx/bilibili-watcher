#Requires -Version 5.1
<#
.SYNOPSIS
  把本项目复制到 DSH workspace 外的干净目录，消除 Low 完整性级别标签。
.DESCRIPTION
  DSH 的 workspace-write 沙箱会给根目录及其下所有文件打上
  "Mandatory Label\Low Mandatory Level" + "S-1-4-*" + "Everyone(DENY)"。
  低 IL 进程无法向 Explorer 通知区添加图标（Shell_NotifyIconW 被 UIPI 静默拒绝），
  托盘因此不显示。把项目复制到干净目录后，以普通（Medium IL）桌面会话运行即可。

  用法（在 PowerShell 里）：
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\migrate.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\migrate.ps1 -Destination D:\Tools\Bili
    powershell -NoProfile -ExecutionPolicy Bypass -File tools\migrate.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string]$Destination = "D:\Apps\BiliLiveWatcher",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Source = Split-Path -Parent $PSScriptRoot   # tools\.. = 项目根

$ExcludedDirs = @(".venv", ".uv-cache", "_scratch", "__pycache__", "_icon_preview")
$ExcludedFiles = @("watcher.log", "debug.log", "state.json", "state.json.tmp")

Write-Host "源目录:   $Source"
Write-Host "目标目录: $Destination"

if (-not (Test-Path -LiteralPath $Source)) { throw "源目录不存在: $Source" }

# 检查源目录是否被打了 trust mark
$acl = & icacls $Source 2>&1 | Out-String
$stamped = ($acl -match "Low Mandatory Level") -or ($acl -match "S-1-4-") -or ($acl -match "Everyone.*DENY")
if ($stamped) {
    Write-Host "[INFO] 源目录带有 Low-IL trust mark（符合预期，这正是要搬走的原因）。" -ForegroundColor Yellow
} else {
    Write-Host "[INFO] 源目录未检测到 trust mark，搬家仍然安全（可用于换机器/换目录）。" -ForegroundColor Gray
}

$items = Get-ChildItem -LiteralPath $Source -Force | Where-Object {
    -not ($_.PSIsContainer -and ($ExcludedDirs -contains $_.Name)) -and
    -not ((-not $_.PSIsContainer) -and ($ExcludedFiles -contains $_.Name))
}

if ($DryRun) {
    Write-Host "[DRYRUN] 将复制以下条目:"
    $items | ForEach-Object { Write-Host "  $($_.Name)" }
    return
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
foreach ($it in $items) {
    Copy-Item -LiteralPath $it.FullName -Destination $Destination -Recurse -Force
    Write-Host "  已复制: $($it.Name)"
}

# 清掉可能被带过来的 __pycache__
$pc = Join-Path $Destination "tools\__pycache__"
if (Test-Path -LiteralPath $pc) { Remove-Item -LiteralPath $pc -Recurse -Force }

# 验证目标目录无 trust mark
$dstAcl = & icacls $Destination 2>&1 | Out-String
$dstStamped = ($dstAcl -match "Low Mandatory Level") -or ($dstAcl -match "S-1-4-") -or ($dstAcl -match "Everyone.*DENY")
if ($dstStamped) {
    Write-Host "[WARN] 目标目录仍带 trust mark，请换一个未被沙箱标记的目录。" -ForegroundColor Red
} else {
    Write-Host "[OK] 目标目录干净，无 Low-IL 标记。" -ForegroundColor Green
}

Write-Host ""
Write-Host "下一步（必须用你自己的桌面会话，不要用 DSH 沙箱）："
Write-Host "  cd /d $Destination"
Write-Host "  if exist .venv rmdir /s /q .venv"
Write-Host "  启动.bat"
Write-Host ""
Write-Host "启动.bat 会重建 .venv（此时 pythonw.exe 是 Medium IL），托盘图标即可显示。"
