; 截图翻译（ScreenshotTranslator）标准安装包构建脚本
;
; 前置条件（依次执行）：
;   1. python scripts/collect_ocr_bundle.py        （若 dist/ocr-bundle 缺失）
;   2. python -m PyInstaller screenshot-translator.spec --noconfirm
;   3. 把 dist/ScreenshotTranslator/ 的内容拷到 release/onedir/
; 编译：
;   ISCC.exe scripts\setup.iss
; 产物：
;   release/ScreenshotTranslator-Setup.exe（单文件标准安装器）

#define MyAppName "ScreenshotTranslator"
#define MyAppNameZh "截图翻译"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "ScreenshotTranslation Project"
#define MyAppExeName "ScreenshotTranslator.exe"

[Setup]
; AppId 决定安装目录与卸载项身份，改动会导致"另一个版本已安装"，勿动
AppId={{7C2E9B4A-3F51-4D86-B0A7-51E3D9C4A826}
AppName={#MyAppNameZh}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=ScreenshotTranslator-Setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\resources\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppNameZh}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 托盘常驻时升级安装：用关闭程序页结束旧进程再覆盖文件
CloseApplications=force

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; onedir 目录形态：exe + _internal（主分发形态，启动免解包）
Source: "..\release\onedir\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\{#MyAppNameZh}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppNameZh}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppNameZh}}"; Flags: nowait postinstall skipifsilent
