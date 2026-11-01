#requires -Version 5.1
<#
  在 Windows 上准备 build-runtime/（免安装便携版内置的 Python / Node / ffplay）。
  用法：powershell -ExecutionPolicy Bypass -File tools/build_windows_runtime.ps1 [-Force]
  完成后执行：npx electron-builder --win portable
#>
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # 避免 Invoke-WebRequest 进度条拖慢下载

$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'build-runtime'
$pyDir = Join-Path $runtime 'python'
$nodeDir = Join-Path $runtime 'node'
$ffDir = Join-Path $runtime 'ffmpeg\bin'
New-Item -ItemType Directory -Force -Path $runtime, $pyDir, $nodeDir, $ffDir | Out-Null

function Get-File($urls, $out) {
  if ((Test-Path $out) -and -not $Force) { return }
  foreach ($url in $urls) {
    try {
      Write-Host "下载 $url"
      Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
      return
    } catch {
      Write-Warning "下载失败，尝试下一个镜像：$url"
    }
  }
  throw "下载失败：$out"
}

# ---------------------------------------------------------------- Python 3.12
$pyExe = Join-Path $pyDir 'python.exe'
if (-not (Test-Path $pyExe) -or $Force) {
  $zip = Join-Path $runtime 'python-3.12.8-embed-amd64.zip'
  Get-File @(
    'https://mirrors.huaweicloud.com/python/3.12.8/python-3.12.8-embed-amd64.zip',
    'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip'
  ) $zip
  Expand-Archive -Path $zip -DestinationPath $pyDir -Force
}

# embeddable 版带 ._pth 隔离模式：显式加入 site-packages，且不自动执行 site
$pth = Join-Path $pyDir 'python312._pth'
Set-Content -Path $pth -Encoding ASCII -Value @(
  'python312.zip'
  '.'
  'Lib\site-packages'
  ''
  '#import site'
)

# ---------------------------------------------------------------- Python 依赖
$hostPython = Get-Command python -ErrorAction SilentlyContinue
if (-not $hostPython) {
  throw '未找到 python 命令。请先安装 Python 3.x 并加入 PATH（只用于下载依赖，最终运行时使用内置 Python）。'
}
$site = Join-Path $pyDir 'Lib\site-packages'
$common = @('-m', 'pip', 'install', '--upgrade', '--target', $site,
  '--platform', 'win_amd64', '--python-version', '3.12', '--abi', 'cp312',
  '--implementation', 'cp', '--only-binary=:all:')

Write-Host '安装 Windows x64 / CPython 3.12 依赖...'
& python @common -r (Join-Path $root 'requirements-win.txt')
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }

# 这两个包只有 sdist，纯 Python，按无依赖方式安装
& python -m pip install --upgrade --target $site --no-deps jieba qrcode-terminal
if ($LASTEXITCODE -ne 0) { throw 'jieba / qrcode-terminal 安装失败' }

# ---------------------------------------------------------------- 运行时瘦身
# 删除运行时不使用的可选组件（jieba 的 LAC 模型/词性标注/关键词分析、Pillow 的 AVIF 插件），
# 相当一部分体积，且不影响分词、拼音模糊匹配与播放。
foreach ($p in @('jieba\lac_small', 'jieba\analyse', 'jieba\posseg')) {
  $full = Join-Path $site $p
  if (Test-Path $full) { Remove-Item $full -Recurse -Force }
}
Get-ChildItem (Join-Path $site 'PIL') -Filter '_avif*.pyd' -ErrorAction SilentlyContinue |
  Remove-Item -Force

# ---------------------------------------------------------------- Node
$nodeExe = Join-Path $nodeDir 'node.exe'
Get-File @(
  'https://nodejs.org/dist/v22.14.0/win-x64/node.exe',
  'https://cdn.npmmirror.com/binaries/node/v22.14.0/win-x64/node.exe'
) $nodeExe

# ---------------------------------------------------------------- ffplay
$ffplay = Join-Path $ffDir 'ffplay.exe'
if (-not (Test-Path $ffplay) -or $Force) {
  $zip = Join-Path $runtime 'ffmpeg-release-essentials.zip'
  Get-File @(
    'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
  ) $zip
  $tmp = Join-Path $runtime 'ffmpeg-extract'
  Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $found = Get-ChildItem -Path $tmp -Recurse -Filter 'ffplay.exe' | Select-Object -First 1
  if (-not $found) { throw 'FFmpeg 压缩包中未找到 ffplay.exe' }
  Copy-Item $found.FullName $ffplay -Force
  Remove-Item $tmp -Recurse -Force
}

# ---------------------------------------------------------------- 自检
Write-Host '校验内置运行时...'
& $pyExe -c "import aiohttp, jieba, rapidfuzz, pypinyin, bilibili_api, yaml, jwt, bs4, brotli, qrcode, apscheduler, PIL, Cryptodome; print('RUNTIME_OK')"
if ($LASTEXITCODE -ne 0) { throw '内置 Python 依赖校验失败' }
& $nodeExe --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw '内置 Node 校验失败' }

Write-Host ''
Write-Host '运行时准备完成：'
Write-Host "  python : $pyExe"
Write-Host "  node   : $nodeExe"
Write-Host "  ffplay : $ffplay"
Write-Host '下一步：npx electron-builder --win portable'
