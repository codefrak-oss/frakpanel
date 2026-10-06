# Installs (or reinstalls) frakpanel on a Windows panel host from the folder
# this script sits in: an unpacked frakpanel-<ver>-windows-x64.zip, which
# carries its own embedded Python, or a source checkout (then pythonw.exe
# must be on PATH). Lays out %LOCALAPPDATA%\frakpanel (see launcher.py):
#   launcher.py, runtime\ (embedded Python), versions\<ver>\, data\
# and (re)creates the Scheduled Tasks:
#   frakpanel        launcher.py at logon, which runs the current version's
#                    edged.py (kiosk browser, HTTP 7781, relay 7782), restarts
#                    it, and applies updates. Restarted a minute after any exit.
#   frakpanel-shot   on demand only: desktop screenshot to edge-shot.png
#                    (see edge-shot.ps1).
# Run as the user who stays logged in at the panel (the kiosk needs that
# user's interactive desktop, so this cannot be a service):
#   powershell -ExecutionPolicy Bypass -File install-windows.ps1
# Needs Google Chrome. Updates are off until data\update.json picks a channel
# (README, "Releases and updates").
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$root = Join-Path $env:LOCALAPPDATA "frakpanel"
$ver = if (Test-Path "$src\VERSION") { (Get-Content "$src\VERSION" -Raw).Trim() } else { "0.0.0" }
# A checkout's VERSION is only the next planned release: install it as a dev
# build, which never self-updates (updater.py).
if (Test-Path "$src\.git") { $ver = "$ver-dev" }
$user = $env:USERNAME

Stop-ScheduledTask -TaskName "frakpanel" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process | Where-Object { $_.Name -like "python*" -and ($_.CommandLine -like "*edged.py*" -or $_.CommandLine -like "*launcher.py*") -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1

New-Item -ItemType Directory -Force -Path "$root\versions", "$root\data" | Out-Null
$vdir = Join-Path $root "versions\$ver"
if ((Resolve-Path $src).Path -ne $vdir) {
    if (Test-Path $vdir) { Remove-Item -Recurse -Force $vdir }
    robocopy $src $vdir /E /NFL /NDL /NJH /NJS /XD .git __pycache__ /XF VERSION edge_layout.json edge_relays.json edged.log edge-shot.png local_tiles.json | Out-Null
}
if (Test-Path "$src\python\pythonw.exe") {
    # The launcher's own interpreter. Each version also runs on its own python\.
    robocopy "$src\python" "$root\runtime" /MIR /NFL /NDL /NJH /NJS | Out-Null
    $py = "$root\runtime\pythonw.exe"
} else {
    $py = (Get-Command pythonw.exe).Source
}
[IO.File]::WriteAllText("$vdir\VERSION", $ver)
Copy-Item "$src\launcher.py", "$src\edge-shot.ps1" $root -Force
# Inbound 7781/7782 for the one interpreter every version runs on (launcher.py
# says why it is one path). Needs an elevated shell; otherwise Windows asks on
# the panel's desktop, behind the kiosk, and an unanswered prompt becomes a
# block rule.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) {
    Get-NetFirewallApplicationFilter | Where-Object { $_.Program -like "$root\*" } | Get-NetFirewallRule | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "frakpanel" -Direction Inbound -Action Allow -Program $py -Protocol TCP -LocalPort 7781, 7782 -Profile Private, Domain | Out-Null
} else {
    Write-Warning "not elevated: no firewall rule added. Rerun from an elevated PowerShell, or allow $py on private networks when Windows asks."
}
if ((Test-Path "$src\local_tiles.json") -and -not (Test-Path "$root\data\local_tiles.json")) {
    Copy-Item "$src\local_tiles.json" "$root\data\"
}
# No BOM: Python's json would reject one.
[IO.File]::WriteAllText("$root\launcher.json", (@{ current = $ver; pending = $false } | ConvertTo-Json -Compress))

$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
Unregister-ScheduledTask -TaskName "frakpanel" -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute $py -Argument "launcher.py" -WorkingDirectory $root
Register-ScheduledTask -TaskName "frakpanel" -Action $action -Trigger $trigger -Settings $settings -User $user -RunLevel Limited | Out-Null
Start-ScheduledTask -TaskName "frakpanel"
# On-demand only (no trigger): frakpanel-shot screenshots the desktop to edge-shot.png.
Unregister-ScheduledTask -TaskName "frakpanel-shot" -Confirm:$false -ErrorAction SilentlyContinue
$shot = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\edge-shot.ps1`"" -WorkingDirectory $root
Register-ScheduledTask -TaskName "frakpanel-shot" -Action $shot -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 1)) -User $user -RunLevel Limited | Out-Null
Start-Sleep 8
Get-ScheduledTask -TaskName "frakpanel*" | Select-Object TaskName, State
"frakpanel $ver installed in $root"
