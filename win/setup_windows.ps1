# ============================================================================
#  寄方堂人参视频 · Windows 10 一键装环境
#
#  用法（右键“用 PowerShell 运行”，或在 2-人参剪辑 目录里执行）：
#      powershell -ExecutionPolicy Bypass -File win\setup_windows.ps1
#
#  做四件事：
#    1) 建 .venv-win 虚拟环境 + 装 pillow / numpy
#    2) 检查 ffmpeg（没有就提示用 winget 装）
#    3) 打 Windows 兼容补丁（win\patch_for_windows.py）
#    4) 把字体装进当前用户（不需要管理员）
# ============================================================================
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $Here
Set-Location $Root

Write-Host "=== 目录: $Root ===" -ForegroundColor Cyan

# ---------- 1) Python ----------
$py = $null
foreach ($cand in @("py -3", "python", "python3")) {
    try {
        $v = & cmd /c "$cand -c ""import sys;print(sys.version_info[0],sys.version_info[1])""" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    } catch {}
}
if (-not $py) {
    Write-Host "✗ 没找到 Python。装 64 位 Python 3.10~3.12: https://www.python.org/downloads/windows/" -ForegroundColor Red
    Write-Host "  安装时务必勾选 Add python.exe to PATH" -ForegroundColor Red
    exit 1
}
Write-Host "==> Python: $py" -ForegroundColor Green

# ---------- 2) venv + 依赖 ----------
$Venv = Join-Path $Root ".venv-win"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "==> 建虚拟环境 $Venv"
    & cmd /c "$py -m venv ""$Venv"""
}
Write-Host "==> 装依赖（清华镜像，慢的话可去掉 -i 参数）"
& $VenvPy -m pip install --upgrade pip -q
& $VenvPy -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "  镜像失败，改用官方源重试..."
    & $VenvPy -m pip install -r requirements.txt
}

# ---------- 3) ffmpeg ----------
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ff) {
    Write-Host "==> ffmpeg 已就绪: $($ff.Source)" -ForegroundColor Green
} else {
    Write-Host "✗ 没找到 ffmpeg" -ForegroundColor Yellow
    Write-Host "  装法（二选一，装完重开一个 PowerShell）:" -ForegroundColor Yellow
    Write-Host "    winget install Gyan.FFmpeg"
    Write-Host "    或到 https://www.gyan.dev/ffmpeg/builds/ 下 ffmpeg-release-essentials.zip，"
    Write-Host "    解压后把 bin 目录加到系统 PATH"
}

# ---------- 4) 打兼容补丁 ----------
Write-Host "==> 打 Windows 兼容补丁"
& $VenvPy (Join-Path $Here "patch_for_windows.py")

# ---------- 5) 装字体（当前用户，免管理员） ----------
Write-Host "==> 安装中文字体（思源宋体 Medium/Heavy）"
$FontSrc = Join-Path $Root "fonts"
$FontDst = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"
New-Item -ItemType Directory -Force -Path $FontDst | Out-Null
$RegPath = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
foreach ($f in Get-ChildItem $FontSrc -Filter *.otf) {
    $dst = Join-Path $FontDst $f.Name
    Copy-Item $f.FullName $dst -Force
    $label = switch ($f.Name) {
        "SourceHanSerifCN-Medium.otf" { "Source Han Serif CN Medium (TrueType)" }
        "SourceHanSerifCN-Heavy.otf"  { "Source Han Serif CN Heavy (TrueType)" }
        default { "$($f.BaseName) (TrueType)" }
    }
    New-ItemProperty -Path $RegPath -Name $label -Value $dst -PropertyType String -Force | Out-Null
    Write-Host "   装好 $($f.Name)"
}
Write-Host "  ⚠️ 英文 Didot 是 macOS 自带字体，Windows 没有。" -ForegroundColor Yellow
Write-Host "     装一个 Didone 衬线体（如 Playfair Display）后设置两个环境变量：" -ForegroundColor Yellow
Write-Host '       setx JIFANGTANG_FONT_EN "C:\Windows\Fonts\PlayfairDisplay-Regular.ttf"'
Write-Host '       setx JIFANGTANG_FONT_EN_FAMILY "Playfair Display"'
Write-Host "     然后**完全退出并重启达芬奇**（Fusion 只在启动时读字体）。" -ForegroundColor Yellow

# ---------- 6) 体检 ----------
Write-Host ""
Write-Host "==> 环境体检" -ForegroundColor Cyan
& $VenvPy (Join-Path $Here "doctor_windows.py")

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " 完成。日常用法："
Write-Host "   1) 手动启动达芬奇，停在项目页"
Write-Host "   2) 双击 win\一次成片.bat，或："
Write-Host "      .venv-win\Scripts\python.exe run_chapter.py --chapter 01 --media-dir D:\素材\_remux\20260626～39"
Write-Host "============================================================" -ForegroundColor Cyan
