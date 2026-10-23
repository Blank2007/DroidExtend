; DroidExtend 安装程序（Inno Setup 6） / DroidExtend installer (Inno Setup 6)
; 编译 / Compile:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" DroidExtend.iss
;   （或者 winget install JRSoftware.InnoSetup，装到 %LOCALAPPDATA%\Programs）
;   (or `winget install JRSoftware.InnoSetup`, which puts it under %LOCALAPPDATA%\Programs)
;
; 设计要点 / Design notes:
;   * 装到 %LOCALAPPDATA%\Programs\DroidExtend，PrivilegesRequired=admin，
;     全程不弹 UAC。这是刻意的 —— 程序运行时会往 tools\tmp 写 adb 的临时文件，
;     装在 Program Files 里普通权限反而写不进去。
;     Installs into %LOCALAPPDATA%\Programs\DroidExtend with PrivilegesRequired=admin
;     and never shows a UAC prompt.  This is deliberate -- at runtime the program
;     writes adb's temporary files under tools\tmp, and with ordinary rights an
;     install under Program Files could not write there.
;   * 虚拟显示驱动需要管理员，安装包不代劳；装完启动程序，点界面上的
;     "安装虚拟显示器驱动（需要管理员）" 按钮即可（那一步会自己提权）。
;     The virtual display driver needs administrator rights and the installer does
;     not do it for you; after installing, start the program and click the
;     "Install virtual display driver (needs admin)" button in the UI (that step
;     elevates itself).
;
; 双语 / Bilingual:
;   [Languages] 声明两种语言，Inno 在启动时按系统语言自动选，也可以让用户选
;   （两个以上语言时向导第一页会出现语言下拉，见 ShowLanguageDialog）。
;   项目专属的文案在 [Messages] 里同时写 cn. 和 en. 两份；其余通用文案由
;   Default.isl（英文，Inno 自带）和 ChineseSimplified.isl（随仓库附带）提供。
;   [Languages] declares both; Inno picks by system language and, with more than
;   one language, the wizard's first page offers a drop-down (see
;   ShowLanguageDialog).  Our own strings are written twice under cn. and en.;
;   everything else comes from Default.isl (English, ships with Inno) and the
;   ChineseSimplified.isl copied into this folder.

#define P "..\.."
#define AppName "DroidExtend"
#define AppVer "1.8.2"

[Setup]
AppId={{7C4E1A62-9D3B-4F58-9E21-5B7A0C3D6E14}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=DroidExtend
; Shown in Apps & features, which has no per-language mechanism, so it carries
; both.
AppComments=用数据线把安卓手机变成 Windows 的拓展屏 / Turn an Android phone into a Windows extended display over USB
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; An update has to land in the folder the running copy is already in, without
; asking: "auto" hides the destination page exactly when Setup found an earlier
; installation -- and then reuses that folder -- while a first install, where
; there is nothing to reuse and the choice is real, still gets the page.
; The in-app updater passes /DIR= as well, so a silent update cannot drift into
; a second copy of the program either.
; UsePreviousAppDir is spelled out because it is the whole point here.
DisableDirPage=no
UsePreviousAppDir=yes
PrivilegesRequired=admin
OutputDir={#P}\dist
OutputBaseFilename=DroidExtend-Setup-{#AppVer}
SetupIconFile={#P}\DroidExtend.ico
UninstallDisplayIcon={app}\DroidExtend.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AllowNoIcons=yes
; Left off on purpose: the restart manager closes applications by sending them
; WM_CLOSE, and this program's close button only hides it to the tray (that is
; what tray_on_close means), so the dialog would come back every time asking
; about a file that "cannot" be replaced.  The [Code] section at the bottom kills
; the image outright instead, on upgrade and on uninstall.
CloseApplications=force
RestartApplications=no
; "auto" shows the picker when the system language is not one of ours, which is
; exactly the case we want it in: an English Windows gets an English installer
; without anybody having to find a setting.
ShowLanguageDialog=auto

[Languages]
Name: "cn"; MessagesFile: "ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

; Only our own wording lives here.  Everything else -- buttons, page headings,
; error dialogs -- comes from the two .isl files, so there is nothing to keep in
; sync twice.
;
; 每一页的大标题是 Wizard*，标题下面那行说明才是 SelectDirDesc / ReadyLabel*。
; The big heading on each page comes from Wizard*, while the explanation line
; under it comes from SelectDirDesc / ReadyLabel*.
[Messages]
cn.SetupAppTitle=安装程序
cn.SetupWindowTitle=安装 - %1
en.SetupAppTitle=DroidExtend Setup
en.SetupWindowTitle=Setup - %1

cn.WelcomeLabel2=即将把 [name/ver] 安装到你的电脑上。%n%n这套程序用数据线把安卓手机变成 Windows 的第二块屏幕：延迟约 10 毫秒、支持手机触摸控制、拔线或点“停止”后虚拟屏自动消失。%n%n继续前请准备：一部开了 USB 调试的安卓手机、一根数据线。
en.WelcomeLabel2=This will install [name/ver] on your computer.%n%nIt turns an Android phone into a second screen for Windows over the USB cable: about 10 ms of latency, your phone's touch controls the PC, and the virtual screen disappears when you unplug or press Stop.%n%nBefore you continue, have an Android phone with USB debugging enabled and a USB cable ready.

cn.WizardSelectDir=选择安装位置
cn.SelectDirDesc=要把 [name] 装到哪里？
cn.SelectDirLabel3=安装程序会把 [name] 装到下面的文件夹。
cn.SelectDirBrowseLabel=点“下一步”继续；想换位置就点“浏览”。
cn.DiskSpaceMBLabel=至少需要 [mb] MB 可用空间。

cn.WizardSelectTasks=选择附加任务
cn.SelectTasksDesc=选择附加任务
cn.SelectTasksLabel2=请选择安装时要一并完成的附加任务，然后点“下一步”。

cn.WizardReady=准备安装
cn.ReadyLabel1=安装程序已准备好，点“安装”即可开始。
cn.ReadyLabel2a=点“安装”开始；想检查或修改设置就点“上一步”。
cn.ReadyLabel2b=点“安装”开始。
cn.ReadyMemoDir=安装位置：

cn.WizardInstalling=正在安装
cn.InstallingLabel=正在安装 [name]，请稍候……

cn.FinishedHeadingLabel=正在完成 [name] 安装向导
cn.FinishedLabelNoIcons=安装完成。[name] 已经可以使用了。
cn.FinishedLabel=安装完成。[name] 已经可以使用了。%n%n最后一步：启动程序后点界面上的“安装虚拟显示器驱动（需要管理员）”按钮，弹出 UAC 时点“是”。Windows 不允许程序凭空多出一块屏幕，必须装一次这个驱动（只需装一次）。

cn.ConfirmUninstall=确定要卸载 %1 吗？虚拟显示驱动会保留（想彻底删除请到设备管理器里卸载 “Virtual Display Driver”）。
cn.UninstalledAll=%1 已成功卸载。
cn.UninstallAppFullTitle=卸载 %1
cn.UninstallStatusLabel=正在从电脑上删除 %1，请稍候……

en.WizardSelectDir=Choose the install folder
en.SelectDirDesc=Where should [name] be installed?
en.SelectDirLabel3=Setup will install [name] into the folder below.
en.SelectDirBrowseLabel=Click Next to continue, or Browse to pick another folder.
en.DiskSpaceMBLabel=At least [mb] MB of free space is required.

en.WizardSelectTasks=Choose the extra tasks
en.SelectTasksDesc=Choose the extra tasks
en.SelectTasksLabel2=Pick the extra tasks you want done while installing, then click Next.

en.WizardReady=Ready to install
en.ReadyLabel1=Setup is ready; click Install to begin.
en.ReadyLabel2a=Click Install to begin, or Back to review the settings.
en.ReadyLabel2b=Click Install to begin.
en.ReadyMemoDir=Install folder:

en.WizardInstalling=Installing
en.InstallingLabel=Installing [name], please wait...

en.FinishedHeadingLabel=Completing the [name] Setup Wizard
en.FinishedLabelNoIcons=Setup has finished installing [name]. It is ready to use.
en.FinishedLabel=Setup has finished installing [name]. It is ready to use.%n%nOne step is left: start the program and click "Install virtual display driver (needs admin)", then answer Yes to the UAC prompt. Windows will not let a program conjure an extra screen out of nowhere -- this driver has to be installed once.

en.ConfirmUninstall=Remove %1? The virtual display driver is kept; uninstall "Virtual Display Driver" from Device Manager to remove that as well.
en.UninstalledAll=%1 was removed successfully.
en.UninstallAppFullTitle=Uninstall %1
en.UninstallStatusLabel=Removing %1 from your computer, please wait...

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; Languages: cn; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; Languages: en; Flags: checkedonce

[Files]
Source: "{#P}\DroidExtend.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#P}\DroidExtend.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#P}\README.md"; DestDir: "{app}"; Flags: ignoreversion
; MIT (VDD, nefcon) / Apache-2.0 (adb) / BSD-3 (R8) all require the notice to
; travel with the binary copy, so the licence file ships next to the exe and
; 帮助 -> 第三方组件许可 opens it. / "帮助 -> 第三方组件许可" is the
; Help -> Third-party component licences menu item.
Source: "{#P}\第三方组件许可.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#P}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#P}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#P}\tools\ffmpeg\bin\ffmpeg.exe"; DestDir: "{app}\tools\ffmpeg\bin"; Flags: ignoreversion
Source: "{#P}\tools\platform-tools\adb.exe"; DestDir: "{app}\tools\platform-tools"; Flags: ignoreversion
Source: "{#P}\tools\platform-tools\AdbWinApi.dll"; DestDir: "{app}\tools\platform-tools"; Flags: ignoreversion
Source: "{#P}\tools\platform-tools\AdbWinUsbApi.dll"; DestDir: "{app}\tools\platform-tools"; Flags: ignoreversion
Source: "{#P}\tools\vdd_install.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "{#P}\tools\vdd\nefcon\x64\nefconw.exe"; DestDir: "{app}\tools\vdd\nefcon\x64"; Flags: ignoreversion
Source: "{#P}\tools\vdd\vdd25\VirtualDisplayDriver\*"; DestDir: "{app}\tools\vdd\vdd25\VirtualDisplayDriver"; Flags: ignoreversion
Source: "{#P}\dist\DroidExtend.apk"; DestDir: "{app}\dist"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\DroidExtend.exe"; IconFilename: "{app}\DroidExtend.ico"; Comment: "用数据线把安卓手机变成拓展屏"; Languages: cn
Name: "{group}\{#AppName}"; Filename: "{app}\DroidExtend.exe"; IconFilename: "{app}\DroidExtend.ico"; Comment: "Turn an Android phone into an extended display over USB"; Languages: en
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"; Languages: cn
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Languages: en
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DroidExtend.exe"; IconFilename: "{app}\DroidExtend.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\DroidExtend.exe"; Description: "立即启动 {#AppName}"; Languages: cn; Flags: nowait postinstall skipifsilent shellexec
Filename: "{app}\DroidExtend.exe"; Description: "Start {#AppName} now"; Languages: en; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Since 1.6.6 everything the program writes lives in {app}: settings.json, the
; log, the downloaded updates and the short-lived PowerShell files.  Inno only
; removes files it installed itself, so each of these has to be named -- without
; them an uninstall leaves the folder behind with the user's data still in it.
Type: files;          Name: "{app}\settings.json"
Type: files;          Name: "{app}\DroidExtend.log"
Type: files;          Name: "{app}\vdd_install.log"
Type: files;          Name: "{app}\vdd_off.log"
Type: files;          Name: "{app}\data_dir.txt"
Type: filesandordirs; Name: "{app}\updates"
Type: filesandordirs; Name: "{app}\tmp"
Type: filesandordirs; Name: "{app}\tools"
; The pointer file 1.6.5 and earlier kept in the user profile.
Type: files;          Name: "{localappdata}\DroidExtend\data_dir.txt"
Type: filesandordirs; Name: "{localappdata}\DroidExtend"

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM DroidExtend.exe /T"; Flags: runhidden; RunOnceId: "stopapp"
