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
  - the .exe's description and product name (file Properties, Task Manager)
    are Evergreen's;
  - no window it opens is titled with Firefox's name (the self-extractor shows
    a progress window while it unpacks);
  - the welcome page's left panel shows Evergreen's image (mostly green), not
    a blank or black panel;
  - the next page's header shows Evergreen's icon (green, at its right).
Prints the colours it sampled from the panel, so a failure can be read from
the log alone. Exits 1 when a check fails.

With -Scale, first sets the display's scaling (percent, as in Windows'
display settings) to the nearest step the display allows, to check the
installer as it looks on a scaled display.

  pwsh tests/installer/check_installer.ps1 -Setup Evergreen-0.1-win64-setup.exe [-Scale 150]
#>
param(
  [Parameter(Mandatory = $true)] [string] $Setup,
  [string] $OutDir = 'installer-ui',
  [int] $Scale = 0
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
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr hWnd, int id);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr w, IntPtr l);
}

// Display scaling, through the display-config calls Windows' settings app
// uses (device info types -3 and -4 are its get and set DPI scale).
public static class EgDisplay {
  [StructLayout(LayoutKind.Sequential)] struct LUID { public uint Low; public int High; }
  [StructLayout(LayoutKind.Sequential)] struct PathSourceInfo { public LUID AdapterId; public uint Id; public uint ModeInfoIdx; public uint StatusFlags; }
  [StructLayout(LayoutKind.Sequential)] struct PathTargetInfo {
    public LUID AdapterId; public uint Id; public uint ModeInfoIdx; public int OutputTechnology; public int Rotation;
    public int Scaling; public uint RefreshNum; public uint RefreshDen; public int ScanLineOrdering; public int TargetAvailable; public uint StatusFlags;
  }
  [StructLayout(LayoutKind.Sequential)] struct PathInfo { public PathSourceInfo Source; public PathTargetInfo Target; public uint Flags; }
  [StructLayout(LayoutKind.Sequential, Size = 64)] struct ModeInfo { public int InfoType; }
  [StructLayout(LayoutKind.Sequential)] struct Header { public int Type; public uint Size; public LUID AdapterId; public uint Id; }
  [StructLayout(LayoutKind.Sequential)] struct ScaleGet { public Header Header; public int Min; public int Cur; public int Max; }
  [StructLayout(LayoutKind.Sequential)] struct ScaleSet { public Header Header; public int Rel; }
  [DllImport("user32.dll")] static extern int GetDisplayConfigBufferSizes(uint flags, out uint paths, out uint modes);
  [DllImport("user32.dll")] static extern int QueryDisplayConfig(uint flags, ref uint paths, [Out] PathInfo[] pathArray, ref uint modes, [Out] ModeInfo[] modeArray, IntPtr topology);
  [DllImport("user32.dll")] static extern int DisplayConfigGetDeviceInfo(ref ScaleGet info);
  [DllImport("user32.dll")] static extern int DisplayConfigSetDeviceInfo(ref ScaleSet info);
  static readonly int[] Steps = { 100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500 };

  static PathSourceInfo Primary() {
    uint np, nm;
    if (GetDisplayConfigBufferSizes(2, out np, out nm) != 0) throw new Exception("GetDisplayConfigBufferSizes failed");
    var paths = new PathInfo[np]; var modes = new ModeInfo[nm];
    if (QueryDisplayConfig(2, ref np, paths, ref nm, modes, IntPtr.Zero) != 0) throw new Exception("QueryDisplayConfig failed");
    return paths[0].Source;
  }

  // Returns "current/recommended/allowed range" after setting the nearest
  // allowed step to `percent`.
  public static string SetScale(int percent) {
    var src = Primary();
    var get = new ScaleGet();
    get.Header.Type = -3; get.Header.Size = (uint)Marshal.SizeOf(typeof(ScaleGet));
    get.Header.AdapterId = src.AdapterId; get.Header.Id = src.Id;
    if (DisplayConfigGetDeviceInfo(ref get) != 0) throw new Exception("reading the display scale failed");
    int recommended = Math.Abs(get.Min);
    int want = Array.IndexOf(Steps, percent);
    if (want < 0) throw new Exception("not a scaling step: " + percent);
    int rel = Math.Max(get.Min, Math.Min(get.Max, want - recommended));
    var set = new ScaleSet();
    set.Header.Type = -4; set.Header.Size = (uint)Marshal.SizeOf(typeof(ScaleSet));
    set.Header.AdapterId = src.AdapterId; set.Header.Id = src.Id; set.Rel = rel;
    if (DisplayConfigSetDeviceInfo(ref set) != 0) throw new Exception("setting the display scale failed");
    return String.Format("set {0}% (recommended {1}%, allowed {2}% to {3}%)",
      Steps[recommended + rel], Steps[recommended], Steps[recommended + get.Min], Steps[Math.Min(Steps.Length - 1, recommended + get.Max)]);
  }
}
'@
New-Item -ItemType Directory -Force $OutDir | Out-Null

if ($Scale) {
  "Display scaling: $([EgDisplay]::SetScale($Scale))"
  Start-Sleep -Seconds 2
}
# Real pixels for every window, whatever the scaling was when pwsh started.
[EgWin]::SetThreadDpiAwarenessContext([IntPtr]::new(-4)) | Out-Null  # per-monitor v2

$failures = [System.Collections.Generic.List[string]]::new()
$info = (Get-Item $Setup).VersionInfo
"File description '$($info.FileDescription)', product '$($info.ProductName)' $($info.ProductVersion)"
if ("$($info.FileDescription) $($info.ProductName)" -match 'Firefox|Mozilla') {
  $failures.Add("the installer's version information names Firefox")
}
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

# The colour at (x, y) of the client area in a Save-Window screenshot, or black
# outside it.
function Get-Sample($shot, [int] $x, [int] $y) {
  $x += $shot.X
  $y += $shot.Y
  if ($x -lt 0 -or $y -lt 0 -or $x -ge $shot.Bitmap.Width -or $y -ge $shot.Bitmap.Height) {
    return [System.Drawing.Color]::Black
  }
  return $shot.Bitmap.GetPixel($x, $y)
}

try {
  "Starting $Setup"
  $page = Watch-Windows 120
  if (-not $page) {
    throw "The installer's welcome page did not appear within two minutes"
  }
  Start-Sleep -Seconds 3  # let the page finish drawing
  # The installer may restart itself (elevated) in a new process; take the
  # window that is up now.
  Get-Process -Name setup -ErrorAction SilentlyContinue |
    ForEach-Object { "  setup.exe pid $($_.Id): window $($_.MainWindowHandle) '$($_.MainWindowTitle)'" }
  $page = Get-Process -Name setup -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like '*Setup*' } |
    Select-Object -Last 1
  if (-not $page) {
    throw "The installer's welcome page closed"
  }
  $dpi = [EgWin]::GetDpiForWindow($page.MainWindowHandle)
  "Welcome page: '$($page.MainWindowTitle)' at $dpi dpi"

  # The image the installer unpacked for the page.
  Get-ChildItem $env:TEMP -Filter 'ns*.tmp' -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { Get-Item (Join-Path $_.FullName 'modern-wizard.bmp') -ErrorAction SilentlyContinue } |
    ForEach-Object { "  unpacked: $($_.FullName) ($($_.Length) bytes, sha256 $((Get-FileHash $_.FullName).Hash.Substring(0, 16)))" }

  $shot = Save-Window $page "welcome-$dpi-dpi"
  # The image fills the left 164 x 314 dialog pixels of the page (at 96 dpi).
  $factor = $dpi / 96.0
  $panelW = [int](164 * $factor)
  $panelH = [int](314 * $factor)
  $sum = @(0, 0, 0)
  $count = 0
  $green = 0
  $grid = @()
  for ($row = 0; $row -lt 8; $row++) {
    $line = @()
    for ($col = 0; $col -lt 6; $col++) {
      $c = Get-Sample $shot ([int](($col + 0.5) * $panelW / 6)) ([int](($row + 0.5) * $panelH / 8))
      $sum[0] += $c.R; $sum[1] += $c.G; $sum[2] += $c.B; $count++
      if ($c.G -gt $c.R + 25 -and $c.G -gt $c.B + 10) { $green++ }
      $line += '{0:x2}{1:x2}{2:x2}' -f $c.R, $c.G, $c.B
    }
    $grid += ($line -join ' ')
  }
  "Window $($shot.Bitmap.Width) x $($shot.Bitmap.Height) px, page $($shot.Width) x $($shot.Height) px"
  "Left panel ($panelW x $panelH px), sampled colours:"
  $grid | ForEach-Object { "  $_" }
  "  mean #{0:x2}{1:x2}{2:x2}; {3} of {4} samples green" -f [int]($sum[0] / $count), [int]($sum[1] / $count), [int]($sum[2] / $count), $green, $count
  if ($green -lt $count / 2) {
    $failures.Add("the welcome page's left panel does not show Evergreen's image (see the sampled colours)")
  }

  # Next (the wizard's button 1): the options page, whose header has the icon.
  [EgWin]::SendMessage([EgWin]::GetDlgItem($page.MainWindowHandle, 1), 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null  # BM_CLICK
  Start-Sleep -Seconds 2
  $shot = Save-Window $page "options-$dpi-dpi"
  $headerH = [int](57 * $factor)
  $headerW = [int](150 * $factor)
  $greenPixels = 0
  for ($y = 0; $y -lt $headerH; $y += 2) {
    for ($x = $shot.Width - $headerW; $x -lt $shot.Width; $x += 2) {
      $c = Get-Sample $shot $x $y
      if ($c.G -gt $c.R + 40 -and $c.G -gt $c.B + 20) { $greenPixels++ }
    }
  }
  "Options page header ($headerW x $headerH px at the right): $greenPixels green samples"
  if ($greenPixels -lt 20) {
    $failures.Add("the options page's header does not show Evergreen's icon")
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
