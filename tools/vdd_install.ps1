# Install the Virtual Display Driver (VDD 25.7.23, SignPath-signed) - must run ELEVATED.
# Creates C:\VirtualDisplayDriver, writes vdd_settings.xml, trusts the catalog signer,
# then root-enumerates + installs the device with nefcon.
param(
    [string]$Src  = 'D:\dsh projects\droid-extend\tools\vdd\vdd25\VirtualDisplayDriver',
    [string]$Nef  = 'D:\dsh projects\droid-extend\tools\vdd\nefcon\x64\nefconw.exe',
    [string]$Dest = 'C:\VirtualDisplayDriver',
    [string]$Log  = 'D:\dsh projects\droid-extend\tools\vdd\install.log'
)
$ErrorActionPreference = 'Continue'
$CLASSGUID = '4D36E968-E325-11CE-BFC1-08002BE10318'
$HWID      = 'Root\MttVDD'

function Log([string]$m) {
    $s = (Get-Date -Format 'HH:mm:ss') + '  ' + $m
    Write-Host $s
    try { Add-Content -Path $Log -Value $s -Encoding UTF8 } catch {}
}

Log "=== VDD install start ==="
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Log "elevated = $admin"
if (-not $admin) { Log 'ERROR: not elevated'; exit 2 }

if (-not (Test-Path $Src)) { Log "ERROR: source missing: $Src"; exit 3 }

# ---- 1. stage driver + settings into C:\VirtualDisplayDriver -----------------
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item -Path (Join-Path $Src '*') -Destination $Dest -Recurse -Force
Log "copied driver files -> $Dest"

$xml = @'
<?xml version="1.0" encoding="utf-8"?>
<vdd_settings>
    <monitors>
        <count>1</count>
    </monitors>
    <gpu>
        <friendlyname>default</friendlyname>
    </gpu>
    <global>
        <g_refresh_rate>30</g_refresh_rate>
        <g_refresh_rate>60</g_refresh_rate>
        <g_refresh_rate>90</g_refresh_rate>
        <g_refresh_rate>120</g_refresh_rate>
        <g_refresh_rate>144</g_refresh_rate>
    </global>
    <resolutions>
        <resolution><width>1280</width><height>720</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>1600</width><height>900</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>1920</width><height>1080</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>1920</width><height>1200</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>2160</width><height>1080</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>2560</width><height>1440</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>2560</width><height>1600</height><refresh_rate>60</refresh_rate></resolution>
        <resolution><width>3840</width><height>2160</height><refresh_rate>60</refresh_rate></resolution>
    </resolutions>
    <options>
        <CustomEdid>false</CustomEdid>
        <PreventSpoof>false</PreventSpoof>
        <EdidCeaOverride>false</EdidCeaOverride>
        <HardwareCursor>true</HardwareCursor>
        <SDR10bit>false</SDR10bit>
        <HDRPlus>false</HDRPlus>
        <logging>false</logging>
        <debuglogging>false</debuglogging>
    </options>
</vdd_settings>
'@
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $Dest 'vdd_settings.xml'), $xml, $utf8NoBom)
Log 'wrote vdd_settings.xml (8 resolutions x 30/60/90/120/144 Hz)'

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
& $Nef install (Join-Path $Dest 'MttVDD.inf') $HWID *>&1 |
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
