@echo off
chcp 936 >nul
rem ===========================================================================
rem  寄方堂人参视频 · 一键成片（Windows 版，对应 macOS 的 一次成片.sh）
rem
rem   一次成片.bat 01
rem   一次成片.bat 02 D:\素材\_remux\20260626～25
rem   一次成片.bat 02 D:\素材\_remux\20260626～25 --tts
rem   一次成片.bat 02 D:\素材\_remux\20260626～25 --card-zh "23道芦碗·约26年" --card-en "23 crown-scars ·c. 26yrs" --card-name "一阴一阳" --citation "周易·系辞上"
rem
rem  前置：先跑过一次 win\setup_windows.ps1；达芬奇要**手动启动**并停在项目页。
rem ===========================================================================
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

set "PY=%~dp0..\.venv-win\Scripts\python.exe"
if not exist "%PY%" (
  echo [提示] 没找到 .venv-win，改用系统 python。建议先跑 win\setup_windows.ps1
  set "PY=python"
)

set "CH=%~1"
if "%CH%"=="" (
  echo 用法: 一次成片.bat ^<章号^> [素材目录] [--tts] [--card-zh ... --card-en ... --card-name ... --citation ...]
  exit /b 1
)
shift

set "MED="
set "TTS=0"
set "CARDS="
:parse
if "%~1"=="" goto done
if /i "%~1"=="--tts" ( set "TTS=1" & shift & goto parse )
if /i "%~1"=="--card-zh"   ( set "CARDS=%CARDS% --card-zh %~2"   & shift & shift & goto parse )
if /i "%~1"=="--card-en"   ( set "CARDS=%CARDS% --card-en %~2"   & shift & shift & goto parse )
if /i "%~1"=="--card-name" ( set "CARDS=%CARDS% --card-name %~2" & shift & shift & goto parse )
if /i "%~1"=="--citation"  ( set "CARDS=%CARDS% --citation %~2"  & shift & shift & goto parse )
set "MED=%~1"
shift
goto parse
:done

echo ==============================================
echo   第 %CH% 章   素材: %MED%
echo ==============================================

if "%TTS%"=="1" (
  echo.
  echo ^>^>^> 合成配音（需要本机 127.0.0.1:3900 上的 TTS 服务；Windows 上没有 VoiceStudio，
  echo     要么自己跑一个兼容 /v1/audio/speech 的服务，要么把 Mac 上合成好的 voice_sent\*.wav 拷过来）
  "%PY%" synth_sentences.py --chapter %CH%
  if errorlevel 1 echo [警告] 配音合成失败，继续用现有的 voice_sent\
)

"%PY%" prep_shots.py --chapter %CH%
"%PY%" run_chapter.py --chapter %CH% --replace --media-dir "%MED%" %CARDS%
echo.
echo   工程名: 
type out\last_project.txt 2>nul
endlocal
