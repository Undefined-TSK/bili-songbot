#requires -Version 5.1
<#
  准备 build-runtime/ffmpeg/bin/ffplay.exe（免安装便携版内置的播放器）。
  用法：powershell -ExecutionPolicy Bypass -File tools/build_windows_runtime.ps1 [-Force]
  完成后执行：npx electron-builder --win zip
#>
param([switch]$Force)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root 'build-runtime'
$ffDir = Join-Path $runtime 'ffmpeg\bin'
New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
$exe = Join-Path $ffDir 'ffplay.exe'
if ((Test-Path $exe) -and -not $Force) {
  Write-Host "ffplay.exe 已存在，跳过：$exe"
  exit 0
}

$zip = Join-Path $runtime 'ffmpeg.zip'
$urls = @(
  'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
  'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
)
$downloaded = $false
foreach ($url in $urls) {
  try {
    Write-Host "下载 $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $downloaded = $true
    break
  } catch {
    Write-Warning "下载失败，尝试下一个镜像：$url"
  }
}
if (-not $downloaded) { throw 'ffmpeg 下载失败' }

$extract = Join-Path $runtime 'ffmpeg-extract'
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zip -DestinationPath $extract -Force
$found = Get-ChildItem -Path $extract -Filter 'ffplay.exe' -Recurse | Select-Object -First 1
if (-not $found) { throw '压缩包内未找到 ffplay.exe' }
Copy-Item $found.FullName $exe -Force
# 部分构建把依赖 dll 放在 ffplay.exe 同级，一并带上
$bin = Split-Path $found.FullName -Parent
Get-ChildItem $bin -Filter '*.dll' -ErrorAction SilentlyContinue | Copy-Item -Destination $ffDir -Force
Remove-Item -Recurse -Force $extract
Remove-Item -Force $zip
Write-Host "ffplay 已就绪：$exe"
