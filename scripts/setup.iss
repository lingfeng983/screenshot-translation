; 截图翻译（ScreenshotTranslator）标准安装包构建脚本
;
; 安装包只装程序本体（onedir 目录形态）。OCR 模型（PP-OCRv6 检测/识别/分类）
; 由 RapidOCR 随 exe 一起分发，安装过程不再下载任何识别组件，也不需要系统
; 预装 Tesseract：装完即可离线识别，全程不起外部进程、不弹控制台窗口。
;
; 前置条件（依次执行，publish_release.py 会串起来）：
;   1. python -m PyInstaller screenshot-translator.spec --noconfirm
;   2. 把 dist/ScreenshotTranslator/ 的内容拷到 release/onedir/
;   3. 编安装器：
;      ISCC.exe /DMyAppVersion=1.2.0 scripts\setup.iss
; 产物：
;   release/ScreenshotTranslator-Setup.exe（单文件标准安装器）

#define MyAppName "ScreenshotTranslator"
#define MyAppNameZh "截图翻译"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
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
; OCR 模型让安装体积涨到 200MB 级，用最大字典的 LZMA2 换体积；
; 代价只在出包与安装时各多花十几秒，换用户少下几十 MB
Compression=lzma2/ultra64
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
; onedir 目录形态：exe + _internal（含 RapidOCR 模型，启动免解包）
Source: "..\release\onedir\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{autoprograms}\{#MyAppNameZh}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppNameZh}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppNameZh}}"; Flags: nowait postinstall skipifsilent
