# (Re)creates frakpanel's Scheduled Tasks on a Windows panel host, running
# from the directory this script sits in:
#   frakpanel        edged.py at logon: the kiosk browser, HTTP on 7781 and the
#                    relay on 7782. Restarted a minute after any exit.
#   frakpanel-shot   on demand only: desktop screenshot to edge-shot.png
#                    (see edge-shot.ps1).
# Run as the user who stays logged in at the panel (the kiosk needs that
# user's interactive desktop, so this cannot be a service):
#   powershell -ExecutionPolicy Bypass -File install-windows.ps1
# Needs Python 3.9+ (pythonw.exe on PATH) and Google Chrome.
$dir = $PSScriptRoot
$py = (Get-Command pythonw.exe -ErrorAction Stop).Source
$user = $env:USERNAME
Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and $_.CommandLine -like "*edged.py*" -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
Unregister-ScheduledTask -TaskName "frakpanel" -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $py -Argument "edged.py" -WorkingDirectory $dir
Register-ScheduledTask -TaskName "frakpanel" -Action $action -Trigger $trigger -Settings $settings -User $user -RunLevel Limited | Out-Null
Start-ScheduledTask -TaskName "frakpanel"
# On-demand only (no trigger): frakpanel-shot screenshots the desktop to edge-shot.png.
Unregister-ScheduledTask -TaskName "frakpanel-shot" -Confirm:$false -ErrorAction SilentlyContinue
$shot = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\edge-shot.ps1`"" -WorkingDirectory $dir
Register-ScheduledTask -TaskName "frakpanel-shot" -Action $shot -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)) -User $user -RunLevel Limited | Out-Null
Start-Sleep 8
Get-ScheduledTask -TaskName "frakpanel*" | Select-Object TaskName, State
