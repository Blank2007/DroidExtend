; DroidExtend 安装程序（Inno Setup 6）
; 编译： "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" DroidExtend.iss
;       （或者 winget install JRSoftware.InnoSetup 装到 %LOCALAPPDATA%\Programs）
;
; 设计要点：
;   * 装到 %LOCALAPPDATA%\Programs\DroidExtend，PrivilegesRequired=lowest，
;     全程不弹 UAC。这是刻意的 —— 程序运行时会往 tools\tmp 写 adb 的临时文件，
;     装在 Program Files 里普通权限反而写不进去。
;   * 虚拟显示驱动需要管理员，安装包不代劳；装完启动程序，点界面上的
;     “安装虚拟显示器驱动（需要管理员）” 按钮即可（那一步会自己提权）。

#define P "..\.."
#define AppName "DroidExtend"
#define AppVer "1.5.1"

[Setup]
AppId={{7C4E1A62-9D3B-4F58-9E21-5B7A0C3D6E14}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher=DroidExtend
AppComments=用数据线把安卓手机变成 Windows 的拓展屏
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Always offer the destination page: with the default "auto" Inno silently
; reuses the previous directory on an upgrade and the user never gets to pick.
DisableDirPage=no
PrivilegesRequired=lowest
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
CloseApplications=no

[Languages]
Name: "cn"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=即将把 [name/ver] 安装到你的电脑上。%n%n这套程序用数据线把安卓手机变成 Windows 的第二块屏幕：延迟约 10 毫秒、支持手机触摸控制、拔线或点“停止”后虚拟屏自动消失。%n%n继续前请准备：一部开了 USB 调试的安卓手机、一根数据线。
; 每一页的大标题是 Wizard*，标题下面那行说明才是 SelectDirDesc / ReadyLabel*
WizardSelectDir=选择安装位置
SelectDirDesc=要把 [name] 装到哪里？
SelectDirLabel3=安装程序会把 [name] 装到下面的文件夹。
SelectDirBrowseLabel=点“下一步”继续；想换位置就点“浏览”。
DiskSpaceMBLabel=至少需要 [mb] MB 可用空间。
WizardSelectTasks=选择附加任务
SelectTasksLabel2=请选择安装时要一并完成的附加任务，然后点“下一步”。
SelectTasksDesc=选择附加任务
WizardReady=准备安装
ReadyLabel1=安装程序已准备好，点“安装”即可开始。
ReadyLabel2a=点“安装”开始；想检查或修改设置就点“上一步”。
ReadyLabel2b=点“安装”开始。
ReadyMemoDir=安装位置：
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候……
FinishedHeadingLabel=正在完成 [name] 安装向导
FinishedLabelNoIcons=安装完成。[name] 已经可以使用了。
FinishedLabel=安装完成。[name] 已经可以使用了。%n%n最后一步：启动程序后点界面上的“安装虚拟显示器驱动（需要管理员）”按钮，弹出 UAC 时点“是”。Windows 不允许程序凭空多出一块屏幕，必须装一次这个驱动（只需装一次）。
ClickNext=下一步(&N) >
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonCancel=取消
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&R)…
ButtonWizardBrowse=浏览(&R)…
ButtonYes=是(&Y)
ButtonNo=否(&N)
ConfirmUninstall=确定要卸载 %1 吗？虚拟显示驱动会保留（想彻底删除请到设备管理器里卸载 “Virtual Display Driver”）。
UninstalledAll=%1 已成功卸载。
UninstallAppFullTitle=卸载 %1
UninstallStatusLabel=正在从电脑上删除 %1，请稍候……

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce

[Files]
Source: "{#P}\DroidExtend.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#P}\DroidExtend.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#P}\README.md"; DestDir: "{app}"; Flags: ignoreversion
; MIT (VDD, nefcon) / Apache-2.0 (adb) / BSD-3 (R8) all require the notice to
; travel with the binary copy, so the licence file ships next to the exe and
; 帮助 -> 第三方组件许可 opens it.
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
Name: "{group}\{#AppName}"; Filename: "{app}\DroidExtend.exe"; IconFilename: "{app}\DroidExtend.ico"; Comment: "用数据线把安卓手机变成拓展屏"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\DroidExtend.exe"; IconFilename: "{app}\DroidExtend.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\DroidExtend.exe"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent
