# Register QQ-Bridge-StartOnBoot (user-level, no admin needed)
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File <项目根>\start_bridge_on_boot.ps1'
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Delay = 'PT2M'
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
try {
    Register-ScheduledTask -TaskName 'QQ-Bridge-StartOnLogon' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Start QQ bridge after user logon' -Force -ErrorAction Stop
    Write-Output "OK scheduled task registered"
    $t = Get-ScheduledTask -TaskName 'QQ-Bridge-StartOnLogon'
    Write-Output ("  State: " + $t.State)
} catch {
    Write-Output ("FAIL: " + $_.Exception.Message)
}
