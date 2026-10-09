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
    # 下载单个文件；失败（含 404）返回 $false，不抛异常打断调用方
    try {
        Invoke-WebRequest -Uri $Url -OutFile $OutPath -UseBasicParsing `
            -TimeoutSec 1800 -ErrorAction Stop
        return (Test-Path $OutPath)
    } catch {
        Write-Step "下载失败：$Url`n  $($_.Exception.Message)"
        if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
        return $false
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
    for ($i = 1; $i -le $MaxParts; $i++) {
        $name = "{0}.zip.{1:D3}" -f $Label, $i
        $partPath = Join-Path $tmp $name
        if (-not (Get-RemoteFile "$baseUrl/$name" $partPath)) {
            if ($i -eq 1) {
                Write-Step "首卷都取不到：Release $Tag 可能还没上传 $name 附件"
                exit 1
            }
            break   # 分卷到此为止
        }
        $parts += $partPath
        $sizeMb = [math]::Round((Get-Item $partPath).Length / 1MB, 1)
        Write-Step "已下载 $name（$sizeMb MB）"
    }

    $shaUrl = "$baseUrl/$Label.zip.sha256"
    $shaPath = Join-Path $tmp "$Label.zip.sha256"
    if (Get-RemoteFile $shaUrl $shaPath) {
        $expected = (Get-Content $shaPath -Raw).Split()[0].ToLower()
        $actual = Get-Sha256 $parts
        if ($actual -ne $expected) {
            Write-Step "SHA256 校验不通过 期望=$expected 实际=$actual"
            exit 1
        }
        Write-Step "SHA256 校验通过"
    } else {
        Write-Step "无校验文件，跳过完整性校验"
    }

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
