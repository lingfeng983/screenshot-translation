; 截图翻译（ScreenshotTranslator）标准安装包构建脚本
;
; 安装包只装程序本体（onedir 目录形态）。OCR 组件（Tesseract 便携最小集，
; 解压后约 225MB）不在包里：装填阶段用 scripts\fetch_ocr.ps1 从本仓库的
; Gitee Release 附件下载并解压到当前用户的 ~/.screenshot-translation/resources/，
; 程序运行时按该路径探测（managers/app_paths.py: ocr_resources_dir）。
; 这样 Setup.exe 从 105MB 降到约 10MB，也不用维护 225MB 的二进制入库。
;
; 前置条件（依次执行，publish_release.py 会串起来）：
;   1. python -m PyInstaller screenshot-translator.spec --noconfirm
;   2. 把 dist/ScreenshotTranslator/ 的内容拷到 release/onedir/
;   3. python scripts/collect_ocr_bundle.py
;      python scripts/make_release_package.py        （产出 Release 附件分卷）
;   4. 把 dist/release/ocr-components.zip.* 上传到 tag=v{版本} 的 Release 附件
; 编译：
;   ISCC.exe /DMyAppVersion=1.2.0 /DMyAppOcrTag=v1.2.0 scripts\setup.iss
; 产物：
;   release/ScreenshotTranslator-Setup.exe（单文件标准安装器）

#define MyAppName "ScreenshotTranslator"
#define MyAppNameZh "截图翻译"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
; OCR 组件所在的 Release 标签（附件名据此拼出，须与上传附件的标签一致）
#ifndef MyAppOcrTag
  #define MyAppOcrTag "v" + MyAppVersion
#endif
; OCR 组件的下载来源仓库（本地联调可用 /DMyAppOcrRepo 指到镜像或 http 服务）
#ifndef MyAppOcrRepo
  #define MyAppOcrRepo "https://gitee.com/lingfeng983/screenshot-translation"
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
Name: "ocr"; Description: "下载 OCR 离线识别组件（约 85MB，来自本仓库 Gitee Release 附件）"; GroupDescription: "识别引擎（不勾选则只能用在线 OCR 或系统已装的 Tesseract）:"

[Files]
; onedir 目录形态：exe + _internal（主分发形态，启动免解包）
Source: "..\release\onedir\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
; OCR 下载脚本：只在装填阶段临时释放到 {tmp}，不落到程序目录
Source: "fetch_ocr.ps1"; Flags: dontcopy

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Ps1, Dest, Params: String;
begin
  Result := '';
  if not IsTaskSelected('ocr') then
  begin
    Log('未勾选 OCR 组件下载，仅安装程序本体');
    Exit;
  end;

  { 装到当前用户的数据目录：Program Files 只读、覆盖升级也不该动它，
    与 managers/app_paths.py 的 ocr_resources_dir() 保持同一位置 }
  Dest := AddBackslash(ExpandConstant('{%USERPROFILE}')) +
          '.screenshot-translation\resources';
  ExtractTemporaryFile('fetch_ocr.ps1');
  Ps1 := AddBackslash(ExpandConstant('{tmp}')) + 'fetch_ocr.ps1';
  if not FileExists(Ps1) then
  begin
    Result := '安装包内缺少 fetch_ocr.ps1，无法下载 OCR 组件，安装已中止。';
    Exit;
  end;

  Params := '-NoProfile -ExecutionPolicy Bypass -File "' + Ps1 +
            '" -Tag "{#MyAppOcrTag}" -Dest "' + Dest +
            '" -RepoUrl "{#MyAppOcrRepo}"';
  Log('下载 OCR 组件: powershell ' + Params);
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              Params, '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := '无法启动 Windows PowerShell 下载 OCR 组件，安装已中止。';
    Exit;
  end;
  if ResultCode <> 0 then
    Result := 'OCR 组件下载失败（PowerShell 退出码 ' + IntToStr(ResultCode) +
              '），安装已中止。' + #13#10 +
              '多为网络问题或 Release {#MyAppOcrTag} 尚未上传 ocr-components 附件；' +
              '可重试安装，或取消勾选“下载 OCR 组件”只装程序本体。';
end;

[Icons]
Name: "{autoprograms}\{#MyAppNameZh}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppNameZh}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppNameZh}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时清掉装填阶段下载的识别组件（用户数据目录里的配置与缓存保留）
Type: filesandordirs; Name: "{%USERPROFILE}\.screenshot-translation\resources\tesseract"
Type: filesandordirs; Name: "{%USERPROFILE}\.screenshot-translation\resources\tessdata"
Type: files; Name: "{%USERPROFILE}\.screenshot-translation\resources\ocr-version.txt"
; 只剩空壳时把 resources 目录本身也收掉；非空（用户自己放过东西）就不碰
Type: dirifempty; Name: "{%USERPROFILE}\.screenshot-translation\resources"

