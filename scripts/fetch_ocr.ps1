# 从 Gitee Release 附件下载并安装 OCR 组件（Tesseract 便携最小集）
#
# 供安装包（scripts/setup.iss）在装填阶段调用，也可单独手动执行补装：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_ocr.ps1 `
#            -Tag v1.2.0 -Dest "$env:USERPROFILE\.screenshot-translation\resources"
#
# 退出码：0 成功（含“已装同一标签，跳过”）；非 0 失败，调用方据此中止安装。
# 落盘位置由 -Dest 决定，目录内产出 tesseract\ 与 tessdata\ 两份内容，
# 外加 ocr-version.txt 标记（记录已装标签，重复调用据此跳过重复下载）。

param(
    # 必填：从哪个 Release 标签下载附件
    [Parameter(Mandatory = $true)][string]$Tag,
    # 必填：组件解压目标（其下生成 tesseract\ 与 tessdata\）
    [Parameter(Mandatory = $true)][string]$Dest,
    # 附件名前缀，与 scripts/make_release_package.py 保持一致
    [string]$Label = 'ocr-components',
    # 仓库地址（测试可指向本地 http 服务）
    [string]$RepoUrl = 'https://gitee.com/lingfeng983/screenshot-translation',
    # 分卷数上限，防止探测循环失控
    [int]$MaxParts = 20
)

$ErrorActionPreference = 'Stop'
# 进度条会让 Invoke-WebRequest 在大文件上慢一个数量级
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# 日志一律走 Write-Host：Write-Output 会混进函数的返回值，
# 让 if (-not (Get-RemoteFile ...)) 拿到 [日志, $false] 这种真值数组
function Write-Step([string]$Message) {
    Write-Host "[fetch_ocr] $Message"
}

function Get-RemoteFile([string]$Url, [string]$OutPath) {
    # 下载单个文件，返回三态字符串，调用方据此区分"附件不存在"和"网络不通"：
    #   ok      下载成功
    #   missing 远端明确 404（分卷到此为止 / 附件确实没传）
    #   error   超时、断连、5xx 等，无法判断远端到底有什么
    # 把 error 当成 missing 会把半套组件当成完整包，校验失败也说不清根因
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutPath -UseBasicParsing `
            -TimeoutSec 1800 -ErrorAction Stop
        if (Test-Path $OutPath) { return 'ok' }
        Write-Step "下载结束但文件不在：$Url"
        return 'error'
    } catch {
        $code = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $code = [int]$_.Exception.Response.StatusCode
        }
        Write-Step "请求失败（HTTP $code）：$Url`n  $($_.Exception.Message)"
        if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
        if ($code -eq 404) { return 'missing' }
        return 'error'
    }
}

function Get-Sha256([string[]]$Paths) {
    # 按给定顺序把多个分卷当作一条字节流算 sha256（与打包脚本的算法一致）
    $stream = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($path in $Paths) {
            $fs = [System.IO.File]::OpenRead($path)
            try {
                $buf = New-Object byte[] 1048576
                while (($read = $fs.Read($buf, 0, $buf.Length)) -gt 0) {
                    $stream.TransformBlock($buf, 0, $read, $null, 0) | Out-Null
                }
            } finally { $fs.Dispose() }
        }
        $null = $stream.TransformFinalBlock((New-Object byte[] 0), 0, 0)
        return ([BitConverter]::ToString($stream.Hash) -replace '-', '').ToLower()
    } finally { $stream.Dispose() }
}

$marker = Join-Path $Dest 'ocr-version.txt'
if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $Tag)) {
    Write-Step "OCR 组件已是 $Tag，跳过下载"
    exit 0
}

$baseUrl = "$($RepoUrl -replace '\.git$', '')/releases/download/$Tag"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("ocr-fetch-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    Write-Step "从 $baseUrl 下载 OCR 组件"

    $parts = @()
    $reachedMax = $true
    for ($i = 1; $i -le $MaxParts; $i++) {
        $name = "{0}.zip.{1:D3}" -f $Label, $i
        $partPath = Join-Path $tmp $name
        $status = Get-RemoteFile "$baseUrl/$name" $partPath
        if ($status -eq 'missing') {
            $reachedMax = $false
            break   # 404 才算分卷到此为止
        }
        if ($status -ne 'ok') {
            Write-Step "取 $name 时网络或服务端出错，无法判断组件是否完整"
            exit 1
        }
        $parts += $partPath
        $sizeMb = [math]::Round((Get-Item $partPath).Length / 1MB, 1)
        Write-Step "已下载 $name（$sizeMb MB）"
    }

    if ($parts.Count -eq 0) {
        Write-Step "首卷都取不到：Release $Tag 可能还没上传 $Label.zip.001 附件"
        exit 1
    }
    if ($reachedMax) {
        Write-Step "连续取到 $MaxParts 卷还没遇到结尾，判定为异常（多半是分卷数变了而远端仍有旧卷）"
        exit 1
    }

    # 校验文件是必需的：85MB 的组件拼错了字节，装完也只是"识别不出字"
    $shaUrl = "$baseUrl/$Label.zip.sha256"
    $shaPath = Join-Path $tmp "$Label.zip.sha256"
    if ((Get-RemoteFile $shaUrl $shaPath) -ne 'ok') {
        Write-Step "取不到校验文件 $shaUrl，无法确认组件完整性，放弃安装"
        exit 1
    }
    $expected = (Get-Content $shaPath -Raw).Split()[0].ToLower()
    $actual = Get-Sha256 $parts
    if ($actual -ne $expected) {
        Write-Step "SHA256 校验不通过 期望=$expected 实际=$actual"
        exit 1
    }
    Write-Step "SHA256 校验通过"

    # 合并分卷：zip 被按字节切开了，按序拼回去才是完整包
    $merged = Join-Path $tmp "$Label.zip"
    $out = [System.IO.File]::Create($merged)
    try {
        foreach ($part in $parts) {
            $in = [System.IO.File]::OpenRead($part)
            try { $in.CopyTo($out) } finally { $in.Dispose() }
        }
    } finally { $out.Dispose() }

    # 先解到临时目录，全部成功后才替换目标目录：中途失败不留下半套文件
    $staging = Join-Path $tmp 'staging'
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    Write-Step "解压到临时目录"
    Expand-Archive -LiteralPath $merged -DestinationPath $staging -Force

    foreach ($probe in @('tesseract\tesseract.exe', 'tessdata\eng.traineddata',
                         'tessdata\chi_sim.traineddata')) {
        if (-not (Test-Path (Join-Path $staging $probe))) {
            Write-Step "组件包缺少关键文件：$probe"
            exit 1
        }
    }

    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    foreach ($sub in @('tesseract', 'tessdata')) {
        $target = Join-Path $Dest $sub
        if (Test-Path $target) { Remove-Item $target -Recurse -Force }
        Move-Item (Join-Path $staging $sub) $target
    }
    Set-Content -Path $marker -Value $Tag -Encoding ASCII
    Write-Step "OCR 组件已安装到 $Dest（$Tag）"
    exit 0
} catch {
    Write-Step "失败：$($_.Exception.Message)"
    exit 1
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
