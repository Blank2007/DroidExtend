@echo off
rem ============================================================================
rem  Install the Virtual Display Driver WITHOUT PowerShell.
rem
rem  This is the fallback the GUI offers when tools\vdd_install.ps1 cannot
rem  run -- on a stripped Windows 10 / LTSC image powershell.exe exists but
rem  dies before its first line (exit 0xFFFF0000 = 4294901760), which is why
rem  the standard install left a completely empty log.
rem
rem  Same job as vdd_install.ps1, using only tools Windows always ships:
rem    * copy the driver package into the destination folder
rem    * drop the mode table back to the shipped list when it is over the
rem      driver's ceiling (90 combinations)
rem    * trust the catalog signer with certutil
rem    * remove any stale node and install the device with nefcon
rem    * report what PnP says afterwards
rem
rem  Usage (the GUI passes all four):
rem    vdd_install_cmd.bat [Src] [Nef] [Dest] [Log]
rem  Exit codes: 0 ok, 2 not elevated, 3 driver package incomplete,
rem              4 installed but no MttVDD device appeared, 5 settings missing.
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

set "SRC=%~1"
set "NEF=%~2"
set "DEST=%~3"
set "LOG=%~4"
if "%SRC%"==""  set "SRC=%~dp0vdd\vdd25\VirtualDisplayDriver"
if "%NEF%"==""  set "NEF=%~dp0vdd\nefcon\x64\nefconw.exe"
if "%DEST%"=="" set "DEST=C:\VirtualDisplayDriver"
if "%LOG%"==""  set "LOG=%~dp0vdd_install_cmd.log"

set "HWID=Root\MttVDD"
set "CLASSGUID=4D36E968-E325-11CE-BFC1-08002BE10318"
set "INF=%DEST%\MttVDD.inf"
set "CAT=%DEST%\mttvdd.cat"
set "SETTINGS=%DEST%\vdd_settings.xml"
set "PNP=%TEMP%\droidextend_vdd_pnp.txt"
set "RESULT=4"
set "NEFRC="

if not exist "%LOG%" break > "%LOG%"

call :Log "=== VDD install (cmd fallback) start ==="
call :Log "time    = %DATE% %TIME%"
call :Log "src     = %SRC%"
call :Log "nefcon  = %NEF%"
call :Log "dest    = %DEST%"
call :Log "log     = %LOG%"

rem --- 1. must be elevated -----------------------------------------------
whoami /groups | findstr /C:"S-1-16-12288" >nul 2>&1
if errorlevel 1 (
    call :Log "ERROR: not running as administrator"
    set "RESULT=2"
    goto :Done
)
call :Log "elevated = yes"

rem --- 2. inputs ----------------------------------------------------------
if not exist "%SRC%\" (
    call :Log "ERROR: driver source folder missing: %SRC%"
    set "RESULT=3"
    goto :Done
)
if not exist "%NEF%" (
    call :Log "ERROR: nefcon missing: %NEF%"
    set "RESULT=3"
    goto :Done
)

rem --- 3. stage the driver package ---------------------------------------
if not exist "%DEST%" mkdir "%DEST%" >nul 2>&1
rem 1.9.1 -- vdd_settings.xml may hold resolutions the user added by hand, and
rem the xcopy below overwrites it.  Uninstall keeps that file on purpose; a
rem re-install has to keep the old copy around as well.
rem 1.9.1 —— vdd_settings.xml 里可能有用户自己加的分辨率，下面的 xcopy 会覆盖它。
rem 卸载时这份文件是刻意保留的，重新安装也要把它留一份备份。
if exist "%SETTINGS%" if not exist "%SETTINGS%.bak" copy /Y "%SETTINGS%" "%SETTINGS%.bak" >nul 2>&1
if exist "%SETTINGS%.bak" call :Log "kept the previous mode table as %SETTINGS%.bak"
xcopy "%SRC%\*" "%DEST%\" /E /I /Y /Q >nul 2>&1
set "XCRC=!ERRORLEVEL!"
call :Log "xcopy exit = !XCRC!"
if not exist "%INF%" (
    call :Log "ERROR: %INF% did not make it into %DEST%"
    set "RESULT=3"
    goto :Done
)
if not exist "%SETTINGS%" (
    call :Log "ERROR: %SETTINGS% did not make it into %DEST%"
    set "RESULT=5"
    goto :Done
)

rem --- 4. keep the mode table under the driver's ceiling ------------------
rem  Every <resolution> is multiplied by every <g_refresh_rate>.  Over 90
rem  combinations the driver refuses to report the monitor at all (upstream
rem  issue #254), so a table that is already too big gets replaced.
set "RES=0"
set "HZ=0"
for /f %%c in ('type "%SETTINGS%" ^| findstr /c:"<resolution" ^| find /c /v ""') do set "RES=%%c"
for /f %%c in ('type "%SETTINGS%" ^| findstr /c:"<g_refresh_rate>" ^| find /c /v ""') do set "HZ=%%c"
set /a MODES=!RES! * !HZ!
call :Log "mode table = !RES! resolutions x !HZ! refresh rates = !MODES! combinations"
if !MODES! GTR 90 (
    if exist "%SRC%\vdd_settings.xml" (
        copy /Y "%SRC%\vdd_settings.xml" "%SETTINGS%" >nul 2>&1
        call :Log "table was over the ceiling of 90 - reset to the shipped list"
    ) else (
        call :Log "WARNING: table is over the ceiling of 90 and there is no shipped copy"
    )
)

rem --- 5. trust the catalog signer ---------------------------------------
rem  certutil is part of Windows, so this needs no PowerShell.  The .cat is
rem  added as a publisher AND the signer certificate travels with this script
rem  (tools\vdd_signer.cer): the .ps1 used to export it from the catalog, which
rem  certutil cannot do.  The driver package may carry its own copy, so all
rem  three places are searched -- a package fetched from elsewhere works too.
set "SIGNER=%~dp0vdd_signer.cer"
if not exist "%SIGNER%" set "SIGNER=%DEST%\signer.cer"
if not exist "%SIGNER%" set "SIGNER=%SRC%\signer.cer"
if exist "%CAT%" (
    certutil -addstore -f TrustedPublisher "%CAT%" >>"%LOG%" 2>&1
    call :Log "certutil catalog exit = !ERRORLEVEL!"
) else (
    call :Log "WARNING: no catalog at %CAT% - the driver may be refused"
)
if exist "%SIGNER%" (
    certutil -addstore -f TrustedPublisher "%SIGNER%" >>"%LOG%" 2>&1
    call :Log "certutil signer exit = !ERRORLEVEL!"
) else (
    call :Log "WARNING: no signer certificate found (looked next to this script, in %DEST% and in %SRC%)"
)

rem --- 6. remove any stale node, then install ----------------------------
"%NEF%" --remove-device-node --hardware-id "%HWID%" --class-guid "%CLASSGUID%" >>"%LOG%" 2>&1
call :Log "nefcon remove-device-node exit = !ERRORLEVEL!"
timeout /t 1 /nobreak >nul 2>&1
"%NEF%" install "%INF%" "%HWID%" >>"%LOG%" 2>&1
set "NEFRC=!ERRORLEVEL!"
call :Log "nefcon install exit = !NEFRC!"

rem --- 7. did the device actually appear? ---------------------------------
timeout /t 2 /nobreak >nul 2>&1
if exist "%PNP%" del /q "%PNP%" >nul 2>&1
pnputil /enum-devices /class Display > "%PNP%" 2>&1
set "PNPRC=!ERRORLEVEL!"
call :Log "pnputil exit = !PNPRC!"
if exist "%PNP%" type "%PNP%" >>"%LOG%"
findstr /i /c:"MttVDD" /c:"Virtual Display" "%PNP%" >nul 2>&1
if errorlevel 1 (
    call :Log "no MttVDD display device in the PnP list"
) else (
    call :Log "MttVDD is present in the PnP device list"
    set "RESULT=0"
)
rem  pnputil only grew /enum-devices in Windows 10 1903.  On anything older
rem  there is nothing to grep, so fall back to nefcon's own exit code.
if !RESULT! NEQ 0 (
    if "!NEFRC!"=="0" (
        call :Log "device list unavailable - trusting nefcon exit code 0"
        set "RESULT=0"
    )
)

:Done
if "!RESULT!"=="0" (
    call :Log "=== VDD install (cmd fallback) done ==="
) else (
    call :Log "=== VDD install (cmd fallback) failed, code !RESULT! ==="
)
if exist "%PNP%" del /q "%PNP%" >nul 2>&1
echo Log: %LOG%
exit /b !RESULT!

rem --- helpers --------------------------------------------------------------
:Log
set "LINE=%~1"
>>"%LOG%" echo(!LINE!
exit /b 0
