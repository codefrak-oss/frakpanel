# Screenshots the interactive desktop to edge-shot.png beside this script,
# so the Edge panel can be checked remotely (ssh can't see the desktop).
# Run by the on-demand Scheduled Task "frakpanel-shot" (install-windows.ps1)
# in the logged-in user's session:
#   ssh <panel-host> 'schtasks /run /tn frakpanel-shot'
#   scp '<panel-host>:AppData/Local/frakpanel/edge-shot.png' .
# While an RDP client is attached this captures the RDP-sized desktop, not
# the panel; hand the session back to the console first (README, "RDP").
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System.Runtime.InteropServices;
public static class Dpi { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@
[Dpi]::SetProcessDPIAware() | Out-Null
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$bmp.Save((Join-Path $PSScriptRoot "edge-shot.png"), [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
