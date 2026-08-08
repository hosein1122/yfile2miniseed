@echo off
setlocal EnableExtensions

rem ================================================================
rem User-editable defaults
rem ================================================================
set "RESULT_ROOT=D:\MSeed_Test\Result"
set "STATION="
set "CHANNEL="
set "PYTHON=python"

rem ================================================================
rem Arguments
rem   %1 = first/reference SDS folder
rem   %2 = second/candidate SDS folder
rem   %3 = optional result folder
rem   %4 = optional station filter, or * / ALL for all stations
rem   %5 = optional channel filter, or * / ALL for all channels
rem ================================================================
if "%~1"=="" goto :usage
if "%~2"=="" goto :usage

set "SDS_A=%~1"
set "SDS_B=%~2"

for %%I in ("%SDS_A%") do set "SDS_A_NAME=%%~nxI"
for %%I in ("%SDS_B%") do set "SDS_B_NAME=%%~nxI"

if "%~3"=="" (
  set "RESULT_DIR=%RESULT_ROOT%\SDS_%SDS_A_NAME%_vs_%SDS_B_NAME%"
) else (
  set "RESULT_DIR=%~3"
)

if not "%~4"=="" if /I not "%~4"=="ALL" if not "%~4"=="*" set "STATION=%~4"
if not "%~5"=="" if /I not "%~5"=="ALL" if not "%~5"=="*" set "CHANNEL=%~5"

set "STATION_ARG="
set "CHANNEL_ARG="
if not "%STATION%"=="" set "STATION_ARG=--station %STATION%"
if not "%CHANNEL%"=="" set "CHANNEL_ARG=--channel %CHANNEL%"

set "REPORT_TMP=%RESULT_DIR%\_availability_tmp"

echo.
echo SDS A / reference : %SDS_A%
echo SDS B / candidate : %SDS_B%
echo Result folder     : %RESULT_DIR%
if "%STATION%"=="" (
  echo Station           : ALL
) else (
  echo Station           : %STATION%
)
if "%CHANNEL%"=="" (
  echo Channel           : ALL
) else (
  echo Channel           : %CHANNEL%
)
echo.

if not exist "%SDS_A%\" (
  echo ERROR: First SDS folder not found: %SDS_A%
  exit /b 2
)
if not exist "%SDS_B%\" (
  echo ERROR: Second SDS folder not found: %SDS_B%
  exit /b 2
)

if not exist "%RESULT_DIR%\" mkdir "%RESULT_DIR%" >nul 2>nul
if errorlevel 1 (
  echo ERROR: Could not create result folder: %RESULT_DIR%
  exit /b 1
)

del /Q "%RESULT_DIR%\sds_a_availability.txt" "%RESULT_DIR%\sds_b_availability.txt" "%RESULT_DIR%\sds_a_vs_sds_b_differences.txt" 2>nul

call :clean_dir "%REPORT_TMP%"
if errorlevel 1 exit /b 1

echo Building first SDS availability report...
call :clean_dir "%REPORT_TMP%\sds_a"
if errorlevel 1 exit /b 1
%PYTHON% "%~dp0sds_availability_report.py" --input "%SDS_A%" --output "%REPORT_TMP%\sds_a" %STATION_ARG% %CHANNEL_ARG% --snap-times
if errorlevel 1 exit /b %ERRORLEVEL%
copy /Y "%REPORT_TMP%\sds_a\availability.txt" "%RESULT_DIR%\sds_a_availability.txt" >nul

echo Building second SDS availability report...
call :clean_dir "%REPORT_TMP%\sds_b"
if errorlevel 1 exit /b 1
%PYTHON% "%~dp0sds_availability_report.py" --input "%SDS_B%" --output "%REPORT_TMP%\sds_b" %STATION_ARG% %CHANNEL_ARG% --snap-times
if errorlevel 1 exit /b %ERRORLEVEL%
copy /Y "%REPORT_TMP%\sds_b\availability.txt" "%RESULT_DIR%\sds_b_availability.txt" >nul

echo Comparing availability reports...
%PYTHON% "%~dp0compare_availability_lines.py" ^
  --center "%RESULT_DIR%\sds_a_availability.txt" ^
  --ours "%RESULT_DIR%\sds_b_availability.txt" ^
  --output "%RESULT_DIR%\sds_a_vs_sds_b_differences.txt" ^
  --center-label "SDS A" ^
  --ours-label "SDS B"
if errorlevel 1 exit /b %ERRORLEVEL%

rd /S /Q "%REPORT_TMP%" 2>nul

echo.
echo Done.
echo Reports:
echo   %RESULT_DIR%\sds_a_availability.txt
echo   %RESULT_DIR%\sds_b_availability.txt
echo   %RESULT_DIR%\sds_a_vs_sds_b_differences.txt
echo.
exit /b 0

:usage
echo Usage:
echo   %~nx0 SDS_A_FOLDER SDS_B_FOLDER [RESULT_FOLDER] [STATION_OR_ALL] [CHANNEL_OR_ALL]
echo.
echo Example:
echo   %~nx0 D:\MSeed_Test\Result\KazTest3\center_output D:\MSeed_Test\Result\KazTest3\our_output
echo   %~nx0 D:\SDS_A D:\SDS_B D:\MSeed_Test\Result\SDS_A_vs_SDS_B KAZ SPE
exit /b 2

:clean_dir
if exist "%~1\" rd /S /Q "%~1"
mkdir "%~1" >nul 2>nul
if errorlevel 1 (
  echo ERROR: Could not prepare folder: %~1
  exit /b 1
)
exit /b 0
