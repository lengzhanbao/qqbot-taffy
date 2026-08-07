# 创建 NapCat 开机自启任务（最高权限，登录时自动启动，无UAC弹窗）
Start-Transcript -Path '<项目根>\schtasks_out.log' -Force | Out-Null
Write-Output "=== 创建计划任务 $(Get-Date -Format 'HH:mm:ss') ==="
$taskCmd = '"powershell.exe" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "<项目根>\launch_napcat_hidden.ps1"'
schtasks /create /tn "QQBotNapCat" /tr "$taskCmd" /sc onlogon /rl highest /f
Write-Output "创建结果: $LASTEXITCODE"
schtasks /query /tn "QQBotNapCat" /fo list | Select-String "TaskName|Status|Run As User"
Stop-Transcript | Out-Null
