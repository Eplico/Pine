# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

<#
.SYNOPSIS
Opens the Windows installer, checks what it shows, and closes it without
installing anything.

.DESCRIPTION
Runs the installer .exe, waits for its welcome page and saves a screenshot.
Checks that:
  - no window it opens is titled with Firefox's name (the self-extractor shows
    a progress window while it unpacks);
  - the welcome page's left panel shows Evergreen's image (mostly green), not
    a blank or black panel.
Prints the colours it sampled from the panel, so a failure can be read from
the log alone. Exits 1 when a check fails.

  pwsh tests/installer/check_installer.ps1 -Setup Evergreen-0.1-win64-setup.exe
#>
param(
  [Parameter(Mandatory = $true)] [string] $Setup,
  [string] $OutDir = 'installer-ui'
)
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class EgWin {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr hWnd, ref POINT point);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hWnd);
}
'@
[EgWin]::SetProcessDPIAware() | Out-Null
New-Item -ItemType Directory -Force $OutDir | Out-Null

$failures = [System.Collections.Generic.List[string]]::new()
$titles = [System.Collections.Generic.HashSet[string]]::new()
$stub = Start-Process -FilePath (Resolve-Path $Setup).Path -PassThru

# Every window title the installer shows until its welcome page is up. The
# self-extractor (the .exe itself) unpacks to a temporary folder and runs
# setup.exe from there.
function Watch-Windows([int] $seconds) {
  $deadline = (Get-Date).AddSeconds($seconds)
  while ((Get-Date) -lt $deadline) {
    $procs = Get-Process -Name $stub.ProcessName, 'setup' -ErrorAction SilentlyContinue |
      Where-Object { $_.MainWindowHandle -ne 0 }
    foreach ($p in $procs) {
      if ($p.MainWindowTitle -and $titles.Add("$($p.ProcessName): $($p.MainWindowTitle)")) {
        "  window: $($p.ProcessName): $($p.MainWindowTitle)" | Out-Host
      }
      if ($p.ProcessName -eq 'setup' -and $p.MainWindowTitle -like '*Setup*') {
        return $p
      }
    }
    Start-Sleep -Milliseconds 100
  }
  return $null
}

function Save-Window($proc, [string] $name) {
  $h = $proc.MainWindowHandle
  $rect = New-Object EgWin+RECT
  [EgWin]::GetWindowRect($h, [ref] $rect) | Out-Null
  $bmp = New-Object System.Drawing.Bitmap ($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top)
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $hdc = $g.GetHdc()
  $printed = [EgWin]::PrintWindow($h, $hdc, 2)  # PW_RENDERFULLCONTENT
  $g.ReleaseHdc($hdc)
  if (-not $printed) {
    $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bmp.Size)
  }
  $g.Dispose()
  $bmp.Save((Join-Path $OutDir "$name.png"), [System.Drawing.Imaging.ImageFormat]::Png)
  # Where the client area starts inside the window image.
  $origin = New-Object EgWin+POINT
  [EgWin]::ClientToScreen($h, [ref] $origin) | Out-Null
  $client = New-Object EgWin+RECT
  [EgWin]::GetClientRect($h, [ref] $client) | Out-Null
  return @{
    Bitmap = $bmp
    X = $origin.X - $rect.Left
    Y = $origin.Y - $rect.Top
    Width = $client.Right
    Height = $client.Bottom
  }
}

try {
  "Starting $Setup"
  $setup = Watch-Windows 120
  if (-not $setup) {
    throw "The installer's welcome page did not appear within two minutes"
  }
  Start-Sleep -Seconds 3  # let the page finish drawing
  # The installer may restart itself (elevated) in a new process; take the
  # window that is up now.
  Get-Process -Name setup -ErrorAction SilentlyContinue |
    ForEach-Object { "  setup.exe pid $($_.Id): window $($_.MainWindowHandle) '$($_.MainWindowTitle)'" }
  $setup = Get-Process -Name setup -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like '*Setup*' } |
    Select-Object -Last 1
  if (-not $setup) {
    throw "The installer's welcome page closed"
  }
  $dpi = [EgWin]::GetDpiForWindow($setup.MainWindowHandle)
  "Welcome page: '$($setup.MainWindowTitle)' at $dpi dpi"

  # The image the installer unpacked for the page.
  Get-ChildItem $env:TEMP -Filter 'ns*.tmp' -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Get-Item (Join-Path $_.FullName 'modern-wizard.bmp') -ErrorAction SilentlyContinue } |
    ForEach-Object { "  unpacked: $($_.FullName) ($($_.Length) bytes, sha256 $((Get-FileHash $_.FullName).Hash.Substring(0, 16)))" }

  $shot = Save-Window $setup 'welcome'
  # The image fills the left 164 x 314 dialog pixels of the page (at 96 dpi).
  $scale = $dpi / 96.0
  $panelW = [int](164 * $scale)
  $panelH = [int](314 * $scale)
  $sum = @(0, 0, 0)
  $count = 0
  $green = 0
  $grid = @()
  for ($row = 0; $row -lt 8; $row++) {
    $line = @()
    for ($col = 0; $col -lt 6; $col++) {
      $x = $shot.X + [int](($col + 0.5) * $panelW / 6)
      $y = $shot.Y + [int](($row + 0.5) * $panelH / 8)
      $c = $shot.Bitmap.GetPixel($x, $y)
      $sum[0] += $c.R; $sum[1] += $c.G; $sum[2] += $c.B; $count++
      if ($c.G -gt $c.R + 25 -and $c.G -gt $c.B + 10) { $green++ }
      $line += '{0:x2}{1:x2}{2:x2}' -f $c.R, $c.G, $c.B
    }
    $grid += ($line -join ' ')
  }
  "Left panel ($panelW x $panelH px), sampled colours:"
  $grid | ForEach-Object { "  $_" }
  "  mean #{0:x2}{1:x2}{2:x2}; {3} of {4} samples green" -f [int]($sum[0] / $count), [int]($sum[1] / $count), [int]($sum[2] / $count), $green, $count
  if ($green -lt $count / 2) {
    $failures.Add("the welcome page's left panel does not show Evergreen's image (see the sampled colours)")
  }
} finally {
  # Close everything the installer started; nothing has been installed yet.
  Get-Process -Name 'setup', $stub.ProcessName -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

$firefox = @($titles | Where-Object { $_ -match 'Firefox|Mozilla' })
if ($firefox) {
  $failures.Add("windows titled with Firefox's name: $($firefox -join '; ')")
}
if ($failures.Count) {
  $failures | ForEach-Object { "FAIL: $_" }
  exit 1
}
"Installer check passed"
