@echo off
rem ============================================================================
rem  Uninstall the Virtual Display Driver WITHOUT PowerShell.
rem  不依赖 PowerShell 卸载虚拟显示器驱动（cmd 回退版）。
rem
rem  Same fallback idea as vdd_install_cmd.bat: on a stripped Windows 10 / LTSC
rem  image powershell.exe can exist and still die before its first line
rem  (exit 0xFFFF0000 = 4294901760), which is why the standard install left a
rem  completely empty log.  This is the escape hatch the GUI offers for that
rem  machine, so the whole uninstall has to work with what Windows always ships:
rem  nefconw and pnputil.
rem  和 vdd_install_cmd.bat 同一个思路：有的精简版 Windows 上 powershell.exe
rem  根本跑不起来，所以这里只用 nefconw 与 pnputil。
rem
rem  It removes EXACTLY two things / 只删两样东西:
rem    1. the device node / 设备节点
rem       nefconw --remove-device-node --hardware-id Root\MttVDD
rem               --class-guid 4D36E968-E325-11CE-BFC1-08002BE10318
rem    2. the driver package / 驱动包
rem       the oemNN.inf whose /enum-drivers block mentions MttVDD, deleted with
rem       pnputil /delete-driver oemNN.inf /uninstall /force
rem       /enum-drivers 输出里命中 MttVDD 的那个 oemNN.inf
rem
rem  What it deliberately does NOT touch / 刻意不动的东西:
rem    * C:\VirtualDisplayDriver and the vdd_settings.xml inside it.  That file
rem      may hold resolutions the user added by hand; uninstalling the driver
rem      must not cost them that work.  Do not "tidy it up" later.
rem      C:\VirtualDisplayDriver 与其内部的 vdd_settings.xml 不删：那里可能有
rem      用户自己加的分辨率，卸载驱动不该让用户重加一遍。以后不要顺手清理。
rem    * the app's own settings.json - the app owns it, not the driver.
rem      程序自己的 settings.json 不归本脚本管。
rem    * any certificate trust: the SignPath signer.cer stays installed.  With
rem      the package gone the trust grants nothing, re-installing the same
rem      signed build needs it again, and removing it can break other software
rem      signed by the same publisher on this machine.
rem      不删证书信任（SignPath 的 signer.cer 留着）：包都删了信任不放开什么
rem      权限，以后重装还要用，而且会影响同一发布者签的其它软件。
rem    * other vendors' virtual display adapters (MuMu, Parsec, GameViewer,
rem      SpaceDesk, ...).  They sit under the same Root enumerator and some are
rem      also named "... Virtual Display Adapter", so ONLY the hardware id
rem      Root\MttVDD and driver packages mentioning MttVDD are matched.  Never
rem      match by name.
rem      不碰 MuMu / Parsec / GameViewer / SpaceDesk 等别的软件的虚拟显示适配器：
rem      只认硬件 id Root\MttVDD 和命中 MttVDD 的驱动包，绝不按名字模糊匹配。
rem      注意：MttVDD 节点的实例 id 是 ROOT\DISPLAY\ 加一个序号，里面不含
rem      "MttVDD"，所以按实例 id 也匹配不到。
rem
rem  Usage (the GUI passes the first two):
rem    vdd_uninstall_cmd.bat [Nef] [Log]
rem      Nef  nefconw.exe  (default: vdd\nefcon\x64\nefconw.exe next to this file)
rem      Log  log file     (default: vdd\uninstall.log next to this file)
rem    A first argument of --dry-run (also --dryrun or /dryrun) is understood
rem    anywhere on the line: it prints the command lines into the log and
rem    changes NOTHING, and it needs no elevation.  --help prints this usage.
rem    --dry-run 只把要执行的命令写进日志、什么都不改，不需要管理员；GUI 不会用它。
rem
rem  Exit codes / 退出码:
rem    0  uninstalled - no device node, no driver package.  A package that was
rem       never installed also counts as success / 卸载成功，本来没有驱动包也算成功
rem    2  not elevated / 不是管理员
rem    3  nefconw.exe missing while the device node still has to go / 找不到 nefcon
rem    4  the device node is still there afterwards / 卸载后设备节点仍然存在
rem    5  the driver package is still there afterwards / 卸载后驱动包仍然存在
rem
rem  Log: first line "=== VDD uninstall start ==="; on success the last line is
rem  "=== VDD uninstall done ===" (the GUI greps for that line); on failure the
rem  last line is "=== VDD uninstall failed, code N ===" and the exit code
rem  decides. / 首行固定；成功末行为 done，失败末行为 failed, code N。
rem ============================================================================
setlocal EnableExtensions EnableDelayedExpansion

rem  GBK console AND GBK log: the GUI tries utf-8 first and then gbk, and the
rem  Chinese hints below have to survive the trip.
rem  控制台与日志都用 GBK（936），中文提示才不会变成乱码。
chcp 936 >nul

rem  %~dp0 keeps changing further down: "shift" moves the argument in %1 into
rem  %0, so the script directory has to be captured ONCE, before the loop.
rem  shift 会把 %1 移到 %0，脚本目录必须在解析参数之前先存下来。
set "HERE=%~dp0"
set "NAME=%~nx0"

set "DRYRUN="
set "NEF="
set "LOG="

rem --- arguments -----------------------------------------------------------
rem  --dry-run / --help may appear anywhere; the first two positionals are
rem  Nef and Log, in that order.
:ParseArgs
if "%~1"=="" goto :ArgsDone
if /i "%~1"=="--help" goto :Usage
if /i "%~1"=="/?" goto :Usage
if /i "%~1"=="--dry-run" (
    set "DRYRUN=1"
    shift
    goto :ParseArgs
)
if /i "%~1"=="--dryrun" (
    set "DRYRUN=1"
    shift
    goto :ParseArgs
)
if /i "%~1"=="/dryrun" (
    set "DRYRUN=1"
    shift
    goto :ParseArgs
)
if not defined NEF (
    set "NEF=%~1"
    shift
    goto :ParseArgs
)
if not defined LOG (
    set "LOG=%~1"
    shift
    goto :ParseArgs
)
echo Unknown argument: %~1
echo Run "%NAME% --help" for usage.
exit /b 1

:ArgsDone
if not defined NEF set "NEF=%HERE%vdd\nefcon\x64\nefconw.exe"
if not defined LOG set "LOG=%HERE%vdd\uninstall.log"

set "HWID=Root\MttVDD"
set "CLASSGUID=4D36E968-E325-11CE-BFC1-08002BE10318"
set "PNP=%TEMP%\droidextend_vdd_uninstall_pnp.txt"
set "DEV=%TEMP%\droidextend_vdd_uninstall_dev.txt"
set "RESULT=0"
set "NODEPRESENT="
set "PKGLIST="

rem  Start the log from scratch: the GUI reads the FIRST line as the start
rem  marker, so an old log must not be appended to.
rem  日志从头写：GUI 按首行判断开始，旧日志不能追加。
if exist "%LOG%" del /q "%LOG%" >nul 2>&1
if not exist "%LOG%" break > "%LOG%"

call :Log "=== VDD uninstall start ==="
call :Log "time    = %DATE% %TIME%"
call :Log "nefcon  = %NEF%"
call :Log "log     = %LOG%"
if "%DRYRUN%"=="1" call :Log "dry run = yes - only the command lines are logged / 只打印命令，不改任何东西"

rem --- 1. must be elevated --------------------------------------------------
rem  A real uninstall rewrites driver state, so it has to be elevated.  A dry
rem  run changes nothing, so it is allowed to continue unelevated (hand checks
rem  and automation both benefit). / 真正卸载要管理员；dry run 什么都不改，
rem  允许非管理员跑，方便验证。
whoami /groups | findstr /C:"S-1-16-12288" >nul 2>&1
if errorlevel 1 (
    if "%DRYRUN%"=="1" (
        call :Log "dry run = not elevated, continuing anyway"
    ) else (
        call :Log "ERROR: not running as administrator / 不是管理员，无法卸载驱动"
        set "RESULT=2"
        goto :Done
    )
) else (
    call :Log "elevated = yes"
)

rem --- 2. what is on the machine right now ----------------------------------
call :Log "---- inspect / 检查当前状态 ----"
call :NodePresent
if "!NODEPRESENT!"=="1" (
    call :Log "device node Root\MttVDD = present"
) else (
    call :Log "device node Root\MttVDD = ABSENT"
)
call :FindPackages

rem --- 3. remove the device node --------------------------------------------
rem  nefcon first: while a device still uses the package, deleting the package
rem  is fighting Windows. / 先删设备节点，再删驱动包。
call :Log "---- remove the device node / 移除设备节点 ----"
if "%DRYRUN%"=="1" (
    call :Log "DRY RUN: %NEF% --remove-device-node --hardware-id %HWID% --class-guid %CLASSGUID%"
) else (
    if "!NODEPRESENT!"=="1" (
        if not exist "%NEF%" (
            call :Log "ERROR: nefcon missing: %NEF% / 找不到 nefcon"
            call :Log "  expected vdd\nefcon\x64\nefconw.exe next to this script; pass it as the first argument"
            set "RESULT=3"
            goto :Done
        )
        "%NEF%" --remove-device-node --hardware-id "%HWID%" --class-guid "%CLASSGUID%" >>"%LOG%" 2>&1
        call :Log "nefcon remove-device-node exit = !ERRORLEVEL!"
        timeout /t 1 /nobreak >nul 2>&1
    ) else (
        call :Log "the device node is not present - nothing to remove / 设备节点不存在，跳过"
    )
)

rem --- 4. delete the driver package(s) --------------------------------------
call :Log "---- delete the driver package / 删除驱动包 ----"
if "%DRYRUN%"=="1" (
    call :Log "DRY RUN: pnputil /enum-drivers   (find the oemNN.inf whose block mentions MttVDD)"
    if defined PKGLIST (
        for %%p in (!PKGLIST!) do call :Log "DRY RUN: pnputil /delete-driver %%p /uninstall /force"
    ) else (
        call :Log "DRY RUN: pnputil /delete-driver oemNN.inf /uninstall /force"
    )
) else (
    if not defined PKGLIST (
        rem  A package that never existed is NOT a failure: the user may have
        rem  deleted it in Device Manager or with pnputil already, and what we
        rem  care about is that it is gone when we are done.
        rem  本来就没有这个驱动包不算失败：用户可能自己在别处删过，结果是"没有它"就行。
        call :Log "no MttVDD driver package was installed - nothing to delete (still success)"
    ) else (
        for %%p in (!PKGLIST!) do (
            call :Log "delete: pnputil /delete-driver %%p /uninstall /force"
            pnputil /delete-driver %%p /uninstall /force >>"%LOG%" 2>&1
            call :Log "pnputil delete-driver %%p exit = !ERRORLEVEL!"
        )
    )
)

rem --- 5. verify ------------------------------------------------------------
call :Log "---- verify / 验证 ----"
if "%DRYRUN%"=="1" (
    call :Log "DRY RUN: verification skipped - nothing was changed"
    set "RESULT=0"
    goto :Done
)
call :NodePresent
if "!NODEPRESENT!"=="1" (
    call :Log "ERROR: the device node Root\MttVDD is still present / 设备节点仍然存在"
    set "RESULT=4"
) else (
    call :Log "device node after = absent"
)
call :FindPackages
if defined PKGLIST (
    call :Log "ERROR: a MttVDD driver package is still present: !PKGLIST! / 驱动包仍然存在"
    if "!RESULT!"=="0" set "RESULT=5"
) else (
    call :Log "driver packages after = none"
)

:Done
if "!RESULT!"=="0" (
    call :Log "uninstalled: no device node, no driver package / 卸载完成"
    rem  Say it out loud, so the next reader does not think the cleanup was forgotten.
    rem  写明目录是故意留的，别以为漏了。
    call :Log "kept on purpose: C:\VirtualDisplayDriver + vdd_settings.xml, settings.json, certificates"
    call :Log "=== VDD uninstall done ==="
) else (
    call :Log "=== VDD uninstall failed, code !RESULT! ==="
)
if exist "%PNP%" del /q "%PNP%" >nul 2>&1
if exist "%DEV%" del /q "%DEV%" >nul 2>&1
echo Log: %LOG%
exit /b !RESULT!

rem --- helpers --------------------------------------------------------------
:Usage
echo Usage: %NAME% [Nef] [Log]
echo   Nef        nefconw.exe (default: vdd\nefcon\x64\nefconw.exe next to this script)
echo   Log        log file    (default: vdd\uninstall.log next to this script)
echo   --dry-run  print the command lines into the log and change nothing
echo.
echo Removes ONLY the Root\MttVDD device node and the MttVDD driver package.
echo Keeps C:\VirtualDisplayDriver, vdd_settings.xml, settings.json and all
echo certificate trust.
echo.
echo Exit codes: 0 ok, 2 not elevated, 3 nefcon missing, 4 device node left,
echo             5 driver package left
exit /b 0

rem --- is the device node there? -------------------------------------------
:NodePresent
rem  pnputil /enum-devices exists from Windows 10 1903 on.  With a matching node
rem  its block carries a "Hardware IDs: Root\MttVDD" line; with no match it only
rem  prints "No devices were found on the system." and STILL exits 0, so the exit
rem  code proves nothing and the text is what gets searched.
rem  老系统没有 /enum-devices，所以下面还有一条注册表回退。
set "NODEPRESENT="
if exist "%DEV%" del /q "%DEV%" >nul 2>&1
pnputil /enum-devices /deviceid "%HWID%" /deviceids > "%DEV%" 2>&1
if exist "%DEV%" findstr /i /c:"%HWID%" "%DEV%" >nul 2>&1
if not errorlevel 1 set "NODEPRESENT=1"
if not defined NODEPRESENT (
    rem  Fallback: the Root enumerator keeps HardwareID values for every devnode.
    rem  回退：查 Root 枚举器下每个设备的 HardwareID。
    reg query "HKLM\SYSTEM\CurrentControlSet\Enum\ROOT" /s /v HardwareID 2>nul | findstr /i /c:"%HWID%" >nul && set "NODEPRESENT=1"
)
if defined NODEPRESENT if exist "%DEV%" type "%DEV%" >>"%LOG%"
goto :eof

rem --- list the MttVDD driver package(s) ------------------------------------
:FindPackages
rem  One block = one driver package: the run of non-empty lines between two
rem  blank lines in /enum-drivers output.  A plain "for /f" skips blank lines,
rem  so the output is numbered with findstr /n first - that keeps the blank
rem  lines visible as empty values and makes the block boundaries addressable.
rem  每个驱动包在 /enum-drivers 输出里是一段以空行分隔的文本；findstr /n 给每行
rem  编号（空行也有），才能按空行切块。
set "PKGLIST="
set "BLOCK_TEXT="
set "BLOCK_INF="
if exist "%PNP%" del /q "%PNP%" >nul 2>&1
pnputil /enum-drivers > "%PNP%" 2>&1
call :Log "pnputil /enum-drivers exit = !ERRORLEVEL!"
if not exist "%PNP%" goto :eof
for /f "tokens=1* delims=:" %%A in ('type "%PNP%" ^| findstr /n "^"') do (
    set "LINE=%%B"
    if "!LINE!"=="" (
        call :CheckBlock
        set "BLOCK_TEXT="
    ) else (
        set "BLOCK_TEXT=!BLOCK_TEXT! !LINE!"
    )
)
call :CheckBlock
goto :eof

rem  One block of /enum-drivers output.  Only the block TEXT is matched, never
rem  the field labels: pnputil localises them on a Chinese Windows, while
rem  "oem29.inf" and "mttvdd.inf" are file names and never translated.  The
rem  provider name and the original name both live in this block, so this is
rem  "Provider OR Original Name mentions MttVDD" without parsing labels.
rem  只匹配整块文本，不解析字段名（字段名会被本地化，文件名不会）。
:CheckBlock
set "BLOCK_INF="
if not defined BLOCK_TEXT goto :eof
rem  Cheap case-insensitive substring test, no process spawn: cmd string
rem  substitution ignores case, so a block mentioning MtTvDd matches too.
rem  cmd 的字符串替换不分大小写，所以这一点就够了。
if "!BLOCK_TEXT!"=="!BLOCK_TEXT:mttvdd=!" goto :eof
for %%q in (!BLOCK_TEXT!) do (
    echo %%~q| findstr /i /r /c:"^oem[0-9][0-9]*.inf$" >nul && set "BLOCK_INF=%%~q"
)
if not defined BLOCK_INF goto :eof
call :Log "MttVDD driver package found: !BLOCK_INF!"
call :Log "  !BLOCK_TEXT!"
if defined PKGLIST (
    set "PKGLIST=!PKGLIST! !BLOCK_INF!"
) else (
    set "PKGLIST=!BLOCK_INF!"
)
goto :eof

:Log
set "LINE=%~1"
>>"%LOG%" echo(!LINE!
exit /b 0
