; 截图翻译（ScreenshotTranslator）标准安装包构建脚本
;
; 安装包只装程序本体（onedir 目录形态）。OCR 组件（Tesseract 便携最小集，
; 解压后约 225MB）不在包里：装填阶段由 Setup 自己从本仓库的 GitHub Release 附件
; 下载（带进度、SHA256 由 Setup 校验）并解压到当前用户的
; ~/.screenshot-translation/resources/，程序运行时按该路径探测
; （managers/app_paths.py: ocr_resources_dir）。全程不起外部进程，
; 所以不会再弹控制台窗口。
; 这样 Setup.exe 从 105MB 降到约 30MB，也不用维护 225MB 的二进制入库。
;
; 前置条件（依次执行，publish_release.py 会串起来）：
;   1. python -m PyInstaller screenshot-translator.spec --noconfirm
;   2. 把 dist/ScreenshotTranslator/ 的内容拷到 release/onedir/
;   3. python scripts/collect_ocr_bundle.py
;      python scripts/make_release_package.py   （分卷 + .sha256 + ocr-artifact.iss）
;      第 3 步产出的 ocr-artifact.iss 是本脚本编译期必读的：download 标志要求
;      在编译期就给出 ExternalSize 与 SHA256，装填端没法现算
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
  #define MyAppOcrRepo "https://github.com/lingfeng983/screenshot-translation"
#endif
#define MyAppPublisher "ScreenshotTranslation Project"
#define MyAppExeName "ScreenshotTranslator.exe"

; 出包脚本写进这个文件的三元组：附件名、字节数、整包 SHA256。
; 编译期拿不到就没法用内置下载（download 标志强制要 ExternalSize），
; 届时勾了 OCR 组件的安装会直接中止并说明原因，而不是悄悄装个没识别能力的包。
; 这里的相对路径按本脚本所在目录解析（不是 iscc 的工作目录），从仓库根编译也对。
#define MyAppOcrArtifact "..\dist\release\ocr-artifact.iss"
#define MyAppOcrPartName ""
#define MyAppOcrSize 0
#define MyAppOcrSha ""
#define MyAppOcrParts 0
#if FileExists(MyAppOcrArtifact)
  #include MyAppOcrArtifact
#endif
#if MyAppOcrSha == ""
  #define MyAppOcrDownloadReady 0
#else
  #if MyAppOcrParts != 1
    ; 内置下载只能取单个附件，切成多卷就没法边下边解压
    #define MyAppOcrDownloadReady 0
  #else
    #define MyAppOcrDownloadReady 1
  #endif
#endif

; 编译期把结论打在输出里：[Files] 里的 download 条目是条件编译的，编出来的
; Setup.exe 名字一样、看不出有没有带 OCR 下载地址，而勾了 OCR 的安装要到用户
; 机器上才中止。发版脚本（publish_release.py）抓这行 ASCII 哨兵，没就绪就中止。
#if MyAppOcrDownloadReady
  #pragma message "OCRDownloadReady=1 part=" + MyAppOcrPartName
#else
  #pragma message "OCRDownloadReady=0 reason=" + \
    (MyAppOcrSha == "" ? "no-artifact-include" : "multi-part")
#endif

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
; 下载来的 OCR 组件是 zip，装填阶段由 Setup 自己解压（不再借外部解压命令）
ArchiveExtraction=auto

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "ocr"; Description: "下载 OCR 离线识别组件（约 85MB，来自本仓库 GitHub Release 附件）"; GroupDescription: "识别引擎（不勾选则只能用在线 OCR 或系统已装的 Tesseract）:"

[Files]
; onedir 目录形态：exe + _internal（主分发形态，启动免解包）
Source: "..\release\onedir\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs
#if MyAppOcrDownloadReady
; OCR 组件由 Setup 自己从 Release 附件下载并解压（进度在向导里，SHA256 由 Setup
; 校验，全程不起外部进程）。落盘位置是当前用户的数据目录：Program Files 只读、
; 覆盖升级也不该动它，与 managers/app_paths.py 的 ocr_resources_dir() 同源。
; UAC 提权不换用户，USERPROFILE 不变；只有"以其他用户身份运行 Setup"才会把组件
; 下到别人的配置文件里，那种装法请取消勾选 OCR 组件。
Source: "{#MyAppOcrRepo}/releases/download/{#MyAppOcrTag}/{#MyAppOcrPartName}"; \
  DestName: "ocr-components.zip"; DestDir: "{%USERPROFILE}\.screenshot-translation\resources"; \
  ExternalSize: {#MyAppOcrSize}; Hash: "{#MyAppOcrSha}"; \
  Flags: external download extractarchive recursesubdirs ignoreversion; \
  Check: ShouldFetchOcr
#endif

[Code]
const
  OcrTag = '{#MyAppOcrTag}';

function OcrResourcesDir: String;
begin
  Result := AddBackslash(ExpandConstant('{%USERPROFILE}')) +
            '.screenshot-translation\resources';
end;

function OcrVersionFile: String;
begin
  Result := AddBackslash(OcrResourcesDir) + 'ocr-version.txt';
end;

function TrimmedOcrTag(const Raw: String): String;
var
  Last: Integer;
begin
  Result := Trim(Raw);
  { 标记文件可能是老版本用 PowerShell 写的，末尾带 CRLF：不剥掉就永远判定
    "没装过"，每次重装都白下一遍 85MB }
  while Length(Result) > 0 do
  begin
    Last := Length(Result);
    if (Result[Last] = #13) or (Result[Last] = #10) or (Result[Last] = ' ') then
      Result := Copy(Result, 1, Last - 1)
    else
      Break;
  end;
end;

function InstalledOcrTag: String;
var
  Buffer: AnsiString;
begin
  Result := '';
  if LoadStringFromFile(OcrVersionFile, Buffer) then
    Result := TrimmedOcrTag(Buffer);
end;

{ Inno 7 的 Check 函数不接受 Param 形参（写 (Param: String) 会报 Invalid prototype） }
function ShouldFetchOcr: Boolean;
begin
  Result := False;
  if not WizardIsTaskSelected('ocr') then
    Exit;
  if InstalledOcrTag = OcrTag then
  begin
    Log('OCR 组件已是 ' + OcrTag + '，跳过下载');
    Exit;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not WizardIsTaskSelected('ocr') then
  begin
    Log('未勾选 OCR 组件下载，仅安装程序本体');
    Exit;
  end;

  if ExpandConstant('{%USERPROFILE}') = '' then
  begin
    Result := '当前安装上下文没有 USERPROFILE，无法确定 OCR 组件的落盘位置，' +
              '安装已中止。请取消勾选"下载 OCR 组件"只装程序本体，' +
              '或改用普通用户会话重新安装。';
    Exit;
  end;

#if MyAppOcrDownloadReady == 0
  Result := '本安装包没有内置 OCR 组件的下载地址：编译时找不到 ' +
            'dist\release\ocr-artifact.iss，或组件被切成了多卷' +
            '（内置下载只支持单附件）。请先跑 python scripts/make_release_package.py ' +
            '再编译安装包；已装好程序的本机可用 python scripts/setup_ocr.py --release ' +
            OcrTag + ' 手动补装。';
  Exit;
#endif
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    Exit;
  if not WizardIsTaskSelected('ocr') then
    Exit;
  { 标记的作用就是"这个标签的组件已经装好了"，所以只在组件真的在位时才写 }
  if not FileExists(AddBackslash(OcrResourcesDir) + 'tesseract\tesseract.exe') then
  begin
    Log('未检测到 tesseract.exe，不写 OCR 版本标记');
    Exit;
  end;
  SaveStringToFile(OcrVersionFile, OcrTag, False);
  Log('OCR 组件已就位，标记版本 ' + OcrTag);
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

