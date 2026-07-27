@echo off
setlocal

if "%~1"=="" (
  echo Usage:
  echo   %~nx0 CASE_ROOT STATION [CHANNEL_PREFIX] [Y5DUMP_EXE]
  echo.
  echo Example:
  echo   %~nx0 D:\MSeed_Test\Focused_KAZ_20100107_08 KAZ SP D:\MSeed_Test\Tools\Nanometrics\y5dump.exe
  exit /b 2
)

set "CASE_ROOT=%~1"
set "STATION=%~2"
if "%STATION%"=="" set "STATION=KAZ"
set "CHANNEL_PREFIX=%~3"
if "%CHANNEL_PREFIX%"=="" set "CHANNEL_PREFIX=SP"
set "Y5DUMP=%~4"
if "%Y5DUMP%"=="" set "Y5DUMP=D:\MSeed_Test\Tools\Nanometrics\y5dump.exe"

python "%~dp0focused_yfile_case.py" ^
  --case-root "%CASE_ROOT%" ^
  --station "%STATION%" ^
  --channel-prefix "%CHANNEL_PREFIX%" ^
  --y5dump "%Y5DUMP%" ^
  --clean
