# Uninstall the Virtual Display Driver (VDD 25.7.23, SignPath-signed) - must run ELEVATED.
# 卸载内置的虚拟显示器驱动（VDD 25.7.23，SignPath 签名版）—— 需要管理员权限。
#
# It removes EXACTLY two things / 只删两样东西:
#   1. the device node / 设备节点
#      nefconw --remove-device-node --hardware-id Root\MttVDD
#               --class-guid 4D36E968-E325-11CE-BFC1-08002BE10318
#   2. the driver package / 驱动包
#      the oemNN.inf that pnputil /enum-drivers lists with MttVDD in its block,
#      deleted with pnputil /delete-driver oemNN.inf /uninstall /force
#      pnputil /enum-drivers 里命中 MttVDD 的那个 oemNN.inf，用
#      pnputil /delete-driver oemNN.inf /uninstall /force 删掉
#
# What is deliberately NOT touched / 刻意不动的东西:
#
#  * C:\VirtualDisplayDriver is NOT deleted, and neither is the vdd_settings.xml
#    inside it.  That file can hold resolutions the user added by hand; wiping it
#    on uninstall would force them to redo that work, and it is outside what
#    "uninstall the driver" means.  Please do not "tidy it up" later.
#    C:\VirtualDisplayDriver 目录不删，里面的 vdd_settings.xml 也不删：那里可能有
#    用户自己加的分辨率，卸载驱动不该让用户重加一遍，也不属于"卸载驱动"的范围。
#    以后不要顺手清理。
#    (Sanity: that file is <resolution> rows x <g_refresh_rate> values, and the
#     driver dies past ~90 combinations - IddCxMonitorArrival returns
#     STATUS_NOT_SUPPORTED 0xC00000BB.  See MAX_TARGET_MODES = 90 in host/vdd.py.)
#
#  * the app's own settings.json is NOT touched - it belongs to the app, not to
#    the driver. / 程序自己的 settings.json 不归本脚本管。
#
#  * NO certificate trust is removed.  The SignPath signer.cer (and the catalog
#    in LocalMachine\TrustedPublisher) stays: with the package gone the trust
#    grants nothing, re-installing the same signed build needs it again, and
#    yanking a publisher/root trust out from under other software signed by the
#    same publisher on this machine is a side effect nobody asked for.
#    不删任何证书信任（SignPath 的 signer.cer 留着）：驱动包都删了，信任不放开
#    什么权限；以后重装同一份签名驱动还要它，而且同一发布者签的其它软件也会受
#    影响。
#
#  * other vendors' virtual display adapters are NOT touched: MuMu, Parsec,
#    GameViewer, SpaceDesk, ...  They hang off the same Root enumerator and some
#    are also called "... Virtual Display Adapter", so the only two things this
#    script matches are the hardware id Root\MttVDD and driver packages that
#    mention MttVDD.  No fuzzy name matching, ever.
#    不碰 MuMu / Parsec / GameViewer / SpaceDesk 等别的软件的虚拟显示适配器：它们
#    同样挂在 Root 枚举器下，有的也叫 "xxx Virtual Display Adapter"。只认硬件 id
#    Root\MttVDD 和命中 MttVDD 的驱动包，绝不按名字模糊匹配。
#    Note: the MttVDD node's instance id is ROOT\DISPLAY\ plus an index and does
#    NOT contain the string "MttVDD", so instance-id matching cannot work either.
#    注意：本机 MttVDD 节点的实例 id 是 ROOT\DISPLAY\ 加一个序号，里面并不含
#    "MttVDD"，所以也不能按实例 id 匹配。
#
# RELOCATABLE: the defaults sit next to THIS file, so the script works from a
# source checkout, from {app}\tools of an installed copy and from the spaceless
# %TEMP% copy the GUI makes before elevating.  The GUI always passes -Nef / -Log
# explicitly anyway; the defaults only matter for a hand-run.
# 参数可重定位：默认值都在本文件旁边；GUI 总会显式传 -Nef / -Log。
#
# -DryRun: log the exact command lines and change NOTHING.  Added so this script
# can be exercised on a real machine (by whoever maintains it) without
# uninstalling a driver, and so the log + exit codes can be checked for real.
# The GUI never passes it, and it does not need elevation.
# -DryRun：只把要执行的命令行写进日志，什么都不改，用来在不卸载任何东西的前提下
# 验证日志与退出码；GUI 不会用，也不需要管理员权限。
#
# Exit codes / 退出码:
#   0  uninstalled - no device node and no driver package left.  A package that
#      was never installed counts as success too: the user may have deleted it
#      by hand, and the question is only whether it is gone now.
#      卸载成功：设备节点和驱动包都不在了；驱动包本来就不存在也算成功。
#   2  not elevated / 不是管理员
#   3  nefconw.exe missing while the device node still has to go / 找不到 nefcon
#   4  the device node is still there after the uninstall / 设备节点仍然存在
#   5  the driver package is still there after the uninstall / 驱动包仍然存在
#
# Log / 日志: the first line is "=== VDD uninstall start ===" and, on success,
# the last line is "=== VDD uninstall done ===" - the GUI greps for that line.
# A failing run ends with "=== VDD uninstall failed, code N ===" and the exit
# code decides. / 首行固定；成功时最后一行为 done（GUI 靠它判断），失败时最后一
# 行为 failed, code N，以退出码为准。
param(
    [string]$Nef = (Join-Path $PSScriptRoot 'vdd\nefcon\x64\nefconw.exe'),
    [string]$Log = (Join-Path $PSScriptRoot 'vdd\uninstall.log'),
    [switch]$DryRun
)
$ErrorActionPreference = 'Continue'
$CLASSGUID = '4D36E968-E325-11CE-BFC1-08002BE10318'
$HWID      = 'Root\MttVDD'

function Log([string]$m) {
    $s = (Get-Date -Format 'HH:mm:ss') + '  ' + $m
    Write-Host $s
    # -ErrorAction Stop + a real catch: Add-Content only raises a NON-terminating error by
    # default, which an empty catch {} would swallow, so failures went unreported.
    try {
        Add-Content -LiteralPath $Log -Value $s -Encoding UTF8 -ErrorAction Stop
    } catch {
        Write-Warning ('could not append to ' + $Log + ': ' + $_.Exception.Message)
    }
}

# Add-Content never creates the parent directory.  The log is the ONLY channel the GUI can
# read (an elevated child gets its own console, so its stdout is lost), hence: make sure the
# directory exists before the first Log call, and never let a bad $Log kill the uninstall.
try {
    $logDir = Split-Path -Parent $Log
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }
} catch {
    Write-Warning ('could not create log directory for ' + $Log + ': ' + $_.Exception.Message)
}

Log '=== VDD uninstall start ==='
# Print every path up front: this is what makes a remote bug report actionable.
Log ('PSScriptRoot = ' + $PSScriptRoot)
Log ('Nef  = ' + $Nef)
Log ('Log  = ' + $Log)

# Is the device node Root\MttVDD still around?
#   Preferred: pnputil /enum-devices (Windows 10 2004+).  When the node exists its block
#   carries a "Hardware IDs: Root\MttVDD" line; when it does not, pnputil prints
#   "No devices were found on the system." and STILL exits 0 - the exit code proves
#   nothing, the text is what matters.
#   首选 pnputil /enum-devices：命中时输出里带 Hardware IDs: Root\MttVDD；没命中时只
#   打印一条 "No devices were found"，退出码仍然是 0，所以只能看输出文本。
#   Fallback: Win32_PnPEntity compared on HardwareID, which is not localised and works on
#   builds that have no /enum-devices switch. / 回退：按 HardwareID 精确比对。
function Test-VddNodePresent {
    $out = @(& pnputil.exe /enum-devices /deviceid $HWID /deviceids 2>&1)
    $txt = [string]::Join([Environment]::NewLine, $out)
    if ($txt.IndexOf($HWID, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        foreach ($line in $out) {
            if (([string]$line).Trim() -ne '') { Log ('pnputil: ' + ([string]$line).Trim()) }
        }
        return $true
    }
    try {
        $hit = @(Get-CimInstance -ClassName Win32_PnPEntity -ErrorAction Stop |
                 Where-Object { @($_.HardwareID) -contains $HWID })
        foreach ($d in $hit) { Log ('pnpcim: ' + $d.DeviceID + '  ' + $d.Name) }
        return ($hit.Count -gt 0)
    } catch {
        Log ('WARNING: could not decide whether the device node exists: ' + $_.Exception.Message)
        return $false
    }
}

# Every driver package whose oemNN.inf block mentions MttVDD.
# Only the block TEXT is matched, never the field labels: pnputil localises them
# ("发布名称:" on a Chinese Windows) while "oem29.inf" and "mttvdd.inf" are not translated.
# So one block = one package, and a block is the run of non-empty lines between two blank
# lines.  Both Provider Name and Original Name live in that block, which is what makes
# "Provider or Original Name mentions MttVDD" true without parsing labels.
# 只匹配整块文本，不解析字段名（字段名会被本地化，而 oemNN.inf / mttvdd.inf 不会）。
function Get-MttVddPackages {
    $result = @()
    $out = @(& pnputil.exe /enum-drivers 2>&1)
    $rc = $LASTEXITCODE
    $block = @()
    foreach ($line in ($out + @(''))) {
        $t = ([string]$line).TrimEnd()
        if ($t.Trim() -eq '') {
            if ($block.Count -gt 0) {
                $txt = [string]::Join([Environment]::NewLine, $block)
                if ($txt -match '(?i)mttvdd') {
                    $m = [regex]::Match($txt, '(?i)oem\d+\.inf')
                    if ($m.Success) {
                        $result += [pscustomobject]@{ Inf = $m.Value; Text = $txt }
                    }
                }
                $block = @()
            }
            continue
        }
        $block += $t
    }
    Log ('pnputil /enum-drivers exit = ' + $rc + ', MttVDD package(s) = ' + $result.Count)
    return $result
}

# ---------------------------------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated = $admin"
if (-not $admin) {
    if ($DryRun) {
        # A dry run changes nothing, so it needs no elevation; useful for a hand check.
        # dry run 不改任何东西，所以不需要管理员，方便手工验证。
        Log 'dry run: not elevated, continuing anyway'
    } else {
        Log 'ERROR: not elevated'
        Log '=== VDD uninstall failed, code 2 ==='
        exit 2
    }
}

# ---- 1. what is on this machine right now -----------------------------------
# 当前状态：设备节点在不在，驱动包在不在。
# 检查当前状态 / inspect the current state
Log '---- 1. inspect ----'
$nodeBefore = Test-VddNodePresent
if ($nodeBefore) { Log ('device node Root\MttVDD = present') } else { Log ('device node Root\MttVDD = ABSENT') }
$pkgsBefore = @(Get-MttVddPackages)
if ($pkgsBefore.Count -gt 0) {
    Log ('driver packages matching MttVDD = ' + $pkgsBefore.Count)
    foreach ($p in $pkgsBefore) {
        Log ('  published name: ' + $p.Inf)
        foreach ($ln in ($p.Text -split [Environment]::NewLine)) { Log ('    ' + $ln.Trim()) }
    }
} else {
    Log 'driver packages matching MttVDD = none'
}

# ---- 2. remove the device node ----------------------------------------------
# nefcon first: a package cannot be deleted cleanly while a device still uses it.
# 先删设备节点：设备还在的时候删驱动包是删不干净的。
# 移除设备节点 / remove the device node
Log '---- 2. remove the device node ----'
if ($DryRun) {
    Log ('DRY RUN: "' + $Nef + '" --remove-device-node --hardware-id ' + $HWID + ' --class-guid ' + $CLASSGUID)
} elseif (-not $nodeBefore) {
    Log 'the device node is not present - nothing to remove'
} else {
    if (-not (Test-Path -LiteralPath $Nef)) {
        Log ('ERROR: nefcon missing: ' + $Nef)
        Log '  expected "vdd\nefcon\x64\nefconw.exe" next to vdd_uninstall.ps1; pass -Nef to override'
        Log '=== VDD uninstall failed, code 3 ==='
        exit 3
    }
    & $Nef --remove-device-node --hardware-id $HWID --class-guid $CLASSGUID *>&1 |
        ForEach-Object { Log ('nefcon/rm: ' + $_) }
    Log ('nefcon remove-device-node exit code = ' + $LASTEXITCODE)
    Start-Sleep -Milliseconds 800
}

# ---- 3. delete the driver package(s) ----------------------------------------
# 删除驱动包 / delete the driver package
Log '---- 3. delete the driver package ----'
if ($DryRun) {
    Log 'DRY RUN: pnputil /enum-drivers   (find the oemNN.inf whose block mentions MttVDD)'
    if ($pkgsBefore.Count -gt 0) {
        foreach ($p in $pkgsBefore) { Log ('DRY RUN: pnputil /delete-driver ' + $p.Inf + ' /uninstall /force') }
    } else {
        Log 'DRY RUN: pnputil /delete-driver oemNN.inf /uninstall /force'
    }
} elseif ($pkgsBefore.Count -eq 0) {
    # A package that never existed is NOT a failure.  The question is only whether the
    # driver package is gone when we are done; the user may have removed it by hand.
    # 本来就没有这个驱动包不算失败：用户可能自己在别处删过，我们要的结果是"没有它"。
    Log 'no MttVDD driver package was installed - nothing to delete (this still counts as success)'
} else {
    foreach ($p in $pkgsBefore) {
        Log ('delete: pnputil /delete-driver ' + $p.Inf + ' /uninstall /force')
        $out = @(& pnputil.exe /delete-driver $p.Inf /uninstall /force 2>&1)
        $rc = $LASTEXITCODE
        foreach ($line in $out) { Log ('pnputil: ' + [string]$line) }
        Log ('pnputil delete-driver exit code = ' + $rc)
    }
    Start-Sleep -Milliseconds 500
}

# ---- 4. verify --------------------------------------------------------------
# 验证 / verify that node and package are really gone
Log '---- 4. verify ----'
if ($DryRun) {
    Log 'DRY RUN: verification skipped - nothing was changed'
    Log '=== VDD uninstall done ==='
    exit 0
}
$nodeAfter = Test-VddNodePresent
if ($nodeAfter) { Log 'device node after  = present' } else { Log 'device node after  = absent' }
$pkgsAfter = @(Get-MttVddPackages)
Log ('driver packages after = ' + $pkgsAfter.Count)
foreach ($p in $pkgsAfter) { Log ('  still there: ' + $p.Inf) }

if ($nodeAfter) {
    Log 'ERROR: the device node Root\MttVDD is still present'
    Log '=== VDD uninstall failed, code 4 ==='
    exit 4
}
if ($pkgsAfter.Count -gt 0) {
    Log 'ERROR: a MttVDD driver package is still present'
    Log '=== VDD uninstall failed, code 5 ==='
    exit 5
}

Log 'uninstalled: no device node, no driver package'
# Say it out loud in the log, so the next reader does not think the cleanup was forgotten.
# 在日志里写明：目录是故意留的，别以为漏了。
Log 'kept on purpose: C:\VirtualDisplayDriver and its vdd_settings.xml (user resolutions), settings.json, certificates'
Log '=== VDD uninstall done ==='
exit 0