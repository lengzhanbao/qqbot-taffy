# 创建 NapCat 开机自启任务（最高权限，登录时自动启动，无UAC弹窗）
$root = $PSScriptRoot
Start-Transcript -Path (Join-Path $root 'schtasks_out.log') -Force | Out-Null
Write-Output "=== 创建计划任务 $(Get-Date -Format 'HH:mm:ss') ==="
$taskFile = Join-Path $root 'launch_napcat_hidden.ps1'
$taskCmd = '"powershell.exe" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $taskFile + '"'
schtasks /create /tn "QQBotNapCat" /tr "$taskCmd" /sc onlogon /rl highest /f
Write-Output "创建结果: $LASTEXITCODE"
schtasks /query /tn "QQBotNapCat" /fo list | Select-String "TaskName|Status|Run As User"
Stop-Transcript | Out-Null
