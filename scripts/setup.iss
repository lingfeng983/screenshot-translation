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
;
; 代码签名（强烈建议启用）：
;   本脚本只负责编译，最终 Setup.exe 由 scripts/publish_release.py 的
;   step_sign() 用环境变量 ST_SIGNTOOL_COMMAND 做 Authenticode 签名，
;   验签通过后把证书指纹写进随程序分发的
;   resources/publisher_thumbprint.txt；客户端自动更新据此强制校验签名者。
;   手工编译时请用同一条命令签名，否则客户端只能按 sha256 摘要校验：
;     signtool sign /fd sha256 /sha1 <证书指纹> /tr <RFC3161时间戳> /td sha256 \
;       release\ScreenshotTranslator-Setup.exe
;   若希望连卸载程序一起签，可改用 Inno 原生 SignTool：给 iscc 传
;     /Scustom="signtool sign /fd sha256 /sha1 <指纹> /tr <时间戳> /td sha256 $f"
;   并在 [Setup] 段加 SignTool=custom 与 SignedUninstaller=yes。

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
; 不加 skipifsilent：程序内自动更新走的是 /SILENT 安装，装完要由 Setup 把本体
; 重新拉起来，否则更新完用户得自己去开始菜单找程序
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppNameZh}}"; Flags: nowait postinstall
