@echo off
setlocal EnableExtensions

rem ================================================================
rem User-editable defaults
rem ================================================================
set "CENTER_ROOT=D:\MSeed_Test\Any_To_Mseed_Windows"
set "OUR_CONVERTER_DIR=D:\MSeed_Test\yfile2mseed"
set "Y5DUMP=D:\MSeed_Test\NanometricsY5dump\y5dump.exe"
set "RESULT_ROOT=D:\MSeed_Test\Result"
set "STATION="
set "CHANNEL_PREFIX="
set "OUR_EXTRA_ARGS=-V2"
set "PYTHON=python"

rem ================================================================
rem Arguments
rem   %1 = input folder containing Y-files, e.g. D:\MSeed_Test\Data\Kaz_Test2
rem   %2 = optional station filter, or * / ALL for all stations
rem   %3 = optional channel prefix filter, or * / ALL for all channels
rem ================================================================
if "%~1"=="" (
  echo Usage:
  echo   %~nx0 INPUT_YFILE_FOLDER [STATION_OR_ALL] [CHANNEL_PREFIX_OR_ALL]
  echo.
  echo Example:
  echo   %~nx0 D:\MSeed_Test\Data\Kaz_Test2
  echo   %~nx0 D:\MSeed_Test\Data\Kaz_Test2 KAZ SP
  echo   %~nx0 D:\MSeed_Test\Data\Kaz_Test2 ALL ALL
  exit /b 2
)

set "YFILE_INPUT=%~1"
set "INPUT_CASE=%~1"
if not "%~2"=="" if /I not "%~2"=="ALL" if not "%~2"=="*" set "STATION=%~2"
if not "%~3"=="" if /I not "%~3"=="ALL" if not "%~3"=="*" set "CHANNEL_PREFIX=%~3"

for %%I in ("%INPUT_CASE%") do set "CASE_NAME=%%~nxI"
set "RESULT_DIR=%RESULT_ROOT%\%CASE_NAME%"
set "CENTER_FILES_TO_CONVERT=%CENTER_ROOT%\FilesToConvert"
set "CENTER_BUFFER=%CENTER_ROOT%\Buffer"
set "CENTER_CORRUPTED=%CENTER_ROOT%\CorruptedFiles"
set "CENTER_MSEED_DB=%CENTER_ROOT%\MSeedDatabase"
set "CENTER_OUTPUT=%RESULT_DIR%\center_output"
set "OUR_OUTPUT=%RESULT_DIR%\our_output"
set "REPORT_TMP=%RESULT_DIR%\_availability_tmp"

set "OUR_EXE=%OUR_CONVERTER_DIR%\yfile2mseed.exe"
set "STATION_ARG="
set "CHANNEL_PREFIX_ARG="
if not "%STATION%"=="" set "STATION_ARG=--station %STATION%"
if not "%CHANNEL_PREFIX%"=="" set "CHANNEL_PREFIX_ARG=--channel-prefix %CHANNEL_PREFIX%"

echo.
echo Input Y-files   : %YFILE_INPUT%
echo Result folder   : %RESULT_DIR%
if "%STATION%"=="" (
  echo Station         : ALL
) else (
  echo Station         : %STATION%
)
if "%CHANNEL_PREFIX%"=="" (
  echo Channel prefix  : ALL
) else (
  echo Channel prefix  : %CHANNEL_PREFIX%
)
echo Center root     : %CENTER_ROOT%
echo Our converter   : %OUR_EXE%
echo.

if not exist "%YFILE_INPUT%\" (
  echo ERROR: Input Y-file folder not found: %YFILE_INPUT%
  exit /b 2
)
if not exist "%CENTER_ROOT%\" (
  echo ERROR: Center root not found: %CENTER_ROOT%
  exit /b 2
)
if not exist "%CENTER_ROOT%\Run.bat" (
  echo ERROR: Center Run.bat not found: %CENTER_ROOT%\Run.bat
  exit /b 2
)
if not exist "%OUR_EXE%" (
  echo ERROR: Our converter not found: %OUR_EXE%
  exit /b 2
)
if not exist "%Y5DUMP%" (
  echo ERROR: Y5DUMP not found: %Y5DUMP%
  exit /b 2
)

if not exist "%RESULT_ROOT%\" mkdir "%RESULT_ROOT%" >nul 2>nul
if errorlevel 1 (
  echo ERROR: Could not create result root folder: %RESULT_ROOT%
  exit /b 1
)
if not exist "%RESULT_DIR%\" mkdir "%RESULT_DIR%" >nul 2>nul
if errorlevel 1 (
  echo ERROR: Could not create result folder: %RESULT_DIR%
  exit /b 1
)
del /Q "%RESULT_DIR%\yfile_availability.txt" "%RESULT_DIR%\center_availability.txt" "%RESULT_DIR%\center_normalized_availability.txt" "%RESULT_DIR%\our_availability.txt" "%RESULT_DIR%\center_normalized_vs_our_differences.txt" 2>nul
call :clean_dir "%CENTER_FILES_TO_CONVERT%"
if errorlevel 1 exit /b 1
call :clean_dir "%CENTER_BUFFER%"
if errorlevel 1 exit /b 1
call :clean_dir "%CENTER_CORRUPTED%"
if errorlevel 1 exit /b 1
call :clean_dir "%CENTER_MSEED_DB%"
if errorlevel 1 exit /b 1
call :clean_dir "%CENTER_OUTPUT%"
if errorlevel 1 exit /b 1
call :clean_dir "%OUR_OUTPUT%"
if errorlevel 1 exit /b 1
call :clean_dir "%REPORT_TMP%"
if errorlevel 1 exit /b 1

echo Copying Y-files to center FilesToConvert...
robocopy "%YFILE_INPUT%" "%CENTER_FILES_TO_CONVERT%" /E /NFL /NDL /NJH /NJS /NP
call :check_robocopy "Copy Y-files to center"
if errorlevel 1 exit /b %ERRORLEVEL%

echo.
echo Running center converter...
pushd "%CENTER_ROOT%"
call Run.bat
set "CENTER_RUN_RC=%ERRORLEVEL%"
popd
if not "%CENTER_RUN_RC%"=="0" (
  echo ERROR: Center Run.bat failed with exit code %CENTER_RUN_RC%.
  exit /b %CENTER_RUN_RC%
)

echo Moving center MSeedDatabase to case output...
robocopy "%CENTER_MSEED_DB%" "%CENTER_OUTPUT%" /E /MOVE /NFL /NDL /NJH /NJS /NP
call :check_robocopy "Copy center output"
if errorlevel 1 exit /b %ERRORLEVEL%

echo.
echo Running our converter...
pushd "%OUR_CONVERTER_DIR%"
"%OUR_EXE%" "%YFILE_INPUT%" -o "%OUR_OUTPUT%" %OUR_EXTRA_ARGS%
set "OUR_RUN_RC=%ERRORLEVEL%"
popd
if not "%OUR_RUN_RC%"=="0" (
  echo ERROR: Our converter failed with exit code %OUR_RUN_RC%.
  exit /b %OUR_RUN_RC%
)

echo.
echo Building Y-file availability report...
call :clean_dir "%REPORT_TMP%\yfile"
if errorlevel 1 exit /b 1
%PYTHON% "%~dp0yfile_availability_report.py" --input "%YFILE_INPUT%" --output "%REPORT_TMP%\yfile" --y5dump "%Y5DUMP%" %STATION_ARG% %CHANNEL_PREFIX_ARG%
if errorlevel 1 exit /b %ERRORLEVEL%
copy /Y "%REPORT_TMP%\yfile\availability.txt" "%RESULT_DIR%\yfile_availability.txt" >nul

echo Building center availability reports...
call :clean_dir "%REPORT_TMP%\center"
if errorlevel 1 exit /b 1
%PYTHON% "%~dp0mseed_availability_report.py" --input "%CENTER_OUTPUT%" --output "%REPORT_TMP%\center" %STATION_ARG% --write-normalized --snap-times
if errorlevel 1 exit /b %ERRORLEVEL%
copy /Y "%REPORT_TMP%\center\availability.txt" "%RESULT_DIR%\center_availability.txt" >nul
copy /Y "%REPORT_TMP%\center\normalized\availability.txt" "%RESULT_DIR%\center_normalized_availability.txt" >nul

echo Building our availability report...
call :clean_dir "%REPORT_TMP%\ours"
if errorlevel 1 exit /b 1
%PYTHON% "%~dp0mseed_availability_report.py" --input "%OUR_OUTPUT%" --output "%REPORT_TMP%\ours" %STATION_ARG% --snap-times
if errorlevel 1 exit /b %ERRORLEVEL%
copy /Y "%REPORT_TMP%\ours\availability.txt" "%RESULT_DIR%\our_availability.txt" >nul

echo Comparing center normalized report with our report...
%PYTHON% "%~dp0compare_availability_lines.py" ^
  --center "%RESULT_DIR%\center_normalized_availability.txt" ^
  --ours "%RESULT_DIR%\our_availability.txt" ^
  --output "%RESULT_DIR%\center_normalized_vs_our_differences.txt"
if errorlevel 1 exit /b %ERRORLEVEL%

rd /S /Q "%REPORT_TMP%" 2>nul

echo.
echo Done.
echo Reports:
echo   %RESULT_DIR%\yfile_availability.txt
echo   %RESULT_DIR%\center_availability.txt
echo   %RESULT_DIR%\center_normalized_availability.txt
echo   %RESULT_DIR%\our_availability.txt
echo   %RESULT_DIR%\center_normalized_vs_our_differences.txt
echo.
exit /b 0

:clean_dir
if exist "%~1\" rd /S /Q "%~1"
mkdir "%~1" >nul 2>nul
if errorlevel 1 (
  echo ERROR: Could not prepare folder: %~1
  exit /b 1
)
exit /b 0

:check_robocopy
if %ERRORLEVEL% GEQ 8 (
  echo ERROR: %~1 failed. Robocopy exit code: %ERRORLEVEL%
  exit /b %ERRORLEVEL%
)
exit /b 0
