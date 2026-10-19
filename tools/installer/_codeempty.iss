; DroidExtend 安装程序（Inno Setup 6） / DroidExtend installer (Inno Setup 6)
; 编译 / Compile:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" DroidExtend.iss
;   （或者 winget install JRSoftware.InnoSetup，装到 %LOCALAPPDATA%\Programs）
;   (or `winget install JRSoftware.InnoSetup`, which puts it under %LOCALAPPDATA%\Programs)
;
; 设计要点 / Design notes:
;   * 装到 %LOCALAPPDATA%\Programs\DroidExtend，PrivilegesRequired=lowest，
;     全程不弹 UAC。这是刻意的 —— 程序运行时会往 tools\tmp 写 adb 的临时文件，
;     装在 Program Files 里普通权限反而写不进去。
;     Installs into %LOCALAPPDATA%\Programs\DroidExtend with PrivilegesRequired=lowest
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
#define AppVer "1.6.9"

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
DisableDirPage=auto
UsePreviousAppDir=yes
; 1.6.9: the program itself now needs administrator rights (its manifest says
; requireAdministrator), so the installer asks for them too -- the two are on the
; same footing.  It also sidesteps a machine where Inno could not create its
; %TEMP%\is-XXXX.tmp extraction folder under a limited token.
PrivilegesRequired=admin
OutputDir=D:\deinst2
OutputBaseFilename=codeempty
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
; about a file that "cannot" be replaced.  The [Code]
procedure InitializeWizard();
begin
end;
