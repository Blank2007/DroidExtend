# Install the Virtual Display Driver (VDD 25.7.23, SignPath-signed) - must run ELEVATED.
# Creates C:\VirtualDisplayDriver, drops in vdd_settings.xml, trusts the catalog signer,
# then root-enumerates + installs the device with nefcon.
#
# The mode table lives in ONE place: tools\vdd\vdd25\VirtualDisplayDriver\vdd_settings.xml.
# It is copied here together with the driver binaries and is what the driver reads when its
# device node starts.  Keep it broad - DroidExtend reads the phone's native resolution over
# adb and picks the closest entry, so more entries mean less black bar on the phone.
#
# RELOCATABLE: every path below defaults to whatever sits next to THIS file, so the script
# works from a source checkout, from {app}\tools of an installed copy, and from the
# spaceless %TEMP% copy the GUI makes before elevating.  The GUI always passes -Src / -Nef /
# -Log explicitly anyway; the defaults only matter for a hand-run.
param(
    [string]$Src  = (Join-Path $PSScriptRoot 'vdd\vdd25\VirtualDisplayDriver'),
    [string]$Nef  = (Join-Path $PSScriptRoot 'vdd\nefcon\x64\nefconw.exe'),
    [string]$Dest = 'C:\VirtualDisplayDriver',
    [string]$Log  = (Join-Path $PSScriptRoot 'vdd\install.log')
)
$ErrorActionPreference = 'Continue'
$CLASSGUID = '4D36E968-E325-11CE-BFC1-08002BE10318'
$HWID      = 'Root\MttVDD'

function Log([string]$m) {
    $s = (Get-Date -Format 'HH:mm:ss') + '  ' + $m
    Write-Host $s
    # -ErrorAction Stop + a real catch: Add-Content only raises a NON-terminating error by
    # default, which the old empty `catch {}` swallowed, so failures went unreported.
    try {
        Add-Content -LiteralPath $Log -Value $s -Encoding UTF8 -ErrorAction Stop
    } catch {
        Write-Warning ('could not append to ' + $Log + ': ' + $_.Exception.Message)
    }
}

# Add-Content never creates the parent directory.  The log is the ONLY channel the GUI can
# read (an elevated child gets its own console, so its stdout is lost), hence: make sure the
# directory exists before the first Log call, and never let a bad $Log kill the install.
try {
    $logDir = Split-Path -Parent $Log
    if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    }
} catch {
    Write-Warning ('could not create log directory for ' + $Log + ': ' + $_.Exception.Message)
}

Log '=== VDD install start ==='
# Print every path up front: this is what makes a remote bug report actionable.
Log ('PSScriptRoot = ' + $PSScriptRoot)
Log ('Src  = ' + $Src)
Log ('Nef  = ' + $Nef)
Log ('Dest = ' + $Dest)
Log ('Log  = ' + $Log)

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated = $admin"
if (-not $admin) { Log 'ERROR: not elevated'; exit 2 }

# Validate BOTH inputs before touching anything, so a missing file fails in one second
# instead of after the driver has already been half-staged.
if (-not (Test-Path -LiteralPath $Src)) {
    Log ('ERROR: driver source folder missing: ' + $Src)
    Log '  expected the "vdd" folder to sit next to vdd_install.ps1; pass -Src to override'
    exit 3
}
if (-not (Test-Path -LiteralPath $Nef)) {
    Log ('ERROR: nefcon missing: ' + $Nef)
    Log '  expected "vdd\nefcon\x64\nefconw.exe" next to vdd_install.ps1; pass -Nef to override'
    exit 3
}

# ---- 1. stage driver + settings into C:\VirtualDisplayDriver -----------------
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
# -LiteralPath on the source: a path with [ ] or wildcard characters must not be globbed.
# Copy-Item through the pipeline raises a NON-terminating error on failure, so the file
# count below is the real proof that something landed - never trust "it did not throw".
Get-ChildItem -LiteralPath $Src -Force | Copy-Item -Destination $Dest -Recurse -Force
$staged = @(Get-ChildItem -LiteralPath $Dest -Force -ErrorAction SilentlyContinue).Count
Log ('staged ' + $staged + ' entries into ' + $Dest)
if ($staged -eq 0) {
    Log ('ERROR: nothing was copied from ' + $Src + ' into ' + $Dest)
    exit 3
}

$settings = Join-Path $Dest 'vdd_settings.xml'
$inf      = Join-Path $Dest 'MttVDD.inf'
foreach ($need in @($settings, $inf)) {
    if (-not (Test-Path -LiteralPath $need)) {
        Log ('ERROR: ' + $need + ' did not make it into ' + $Dest + ' - cannot continue')
        exit 5
    }
}

try {
    [xml]$cfg = Get-Content -LiteralPath $settings -Raw -ErrorAction Stop
    $res = @($cfg.vdd_settings.resolutions.resolution)
    $hz  = @($cfg.vdd_settings.global.g_refresh_rate)
    Log ("vdd_settings.xml: " + $res.Count + " resolutions x " + $hz.Count + " refresh rates (" + ($hz -join '/') + " Hz)")
    foreach ($r in $res) { Log ("  mode " + $r.width + "x" + $r.height) }
} catch {
    Log ("WARNING: could not parse " + $settings + " : " + $_)
}

# ---- 2. trust the catalog signer -------------------------------------------
$cat = Join-Path $Dest 'mttvdd.cat'
try {
    & certutil.exe -addstore -f TrustedPublisher $cat *>&1 | ForEach-Object { Log ("certutil: " + $_) }
} catch { Log "certutil failed: $_" }
try {
    $sig = Get-AuthenticodeSignature $cat
    Log ("catalog signature status: " + $sig.Status + "  signer: " + $sig.SignerCertificate.Subject)
    if ($sig.SignerCertificate) {
        $cer = Join-Path $Dest 'signer.cer'
        Export-Certificate -Cert $sig.SignerCertificate -FilePath $cer -Force | Out-Null
        Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher | Out-Null
        Log 'imported signer certificate into LocalMachine\TrustedPublisher'
    }
} catch { Log "cert import failed: $_" }

# ---- 3. remove any stale node, then create + install ------------------------
& $Nef --remove-device-node --hardware-id $HWID --class-guid $CLASSGUID *>&1 |
    ForEach-Object { Log ("nefcon/rm: " + $_) }
Start-Sleep -Milliseconds 500
& $Nef install $inf $HWID *>&1 |
    ForEach-Object { Log ("nefcon/install: " + $_) }
Log ("nefcon install exit code = " + $LASTEXITCODE)

# ---- 4. verify --------------------------------------------------------------
Start-Sleep -Seconds 2
$dev = Get-PnpDevice -Class Display -ErrorAction SilentlyContinue |
       Where-Object { $_.InstanceId -like 'ROOT\MTTVDD*' -or $_.FriendlyName -like '*MttVDD*' -or $_.FriendlyName -like '*Virtual Display*' }
$found = $false
if ($dev) {
    foreach ($d in $dev) {
        Log ("device: " + $d.InstanceId + "  status=" + $d.Status + "  name=" + $d.FriendlyName)
        if ($d.Status -eq 'OK') { $found = $true }
    }
} else {
    Log 'no MttVDD display device found after install'
}

# ---- 5. make sure the extra screen is actually switched on -------------------
if ($found) {
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
        $n = [System.Windows.Forms.Screen]::AllScreens.Count
        Log ("screens now attached to the desktop: " + $n)
    } catch { Log ("screen count check skipped: " + $_) }
}

Log '=== VDD install done ==='
if ($found) { exit 0 } else { exit 4 }
