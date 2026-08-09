@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

if "%~1"=="" goto :usage

set "INPUT_ROOT=%~f1"
if not exist "%INPUT_ROOT%" (
  echo Input folder/file not found: "%INPUT_ROOT%"
  exit /b 2
)

if "%~2"=="" (
  for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "RUN_STAMP=%%I"
  set "RESULT_ROOT=%REPO_ROOT%\out\append_vs_hybrid_%RUN_STAMP%"
) else (
  set "RESULT_ROOT=%~f2"
)

if "%~3"=="" (
  if exist "%REPO_ROOT%\CorrectSID.txt" (
    set "CORRECT_SID=%REPO_ROOT%\CorrectSID.txt"
  ) else (
    set "CORRECT_SID=%SCRIPT_DIR%CorrectSID.txt"
  )
) else (
  set "CORRECT_SID=%~f3"
)

if "%~4"=="" (
  set "STREAM_ID="
) else (
  set "STREAM_ID=%~4"
)
set "WINDOW_START=%~5"
set "WINDOW_END=%~6"

set "BUILD_EXE_DIR=%REPO_ROOT%\out\build\x64-Release\apps\yfile2mseed_cli"

if exist "%SCRIPT_DIR%yfile2mseed_append.exe" (
  set "APPEND_EXE=%SCRIPT_DIR%yfile2mseed_append.exe"
) else (
  set "APPEND_EXE=%BUILD_EXE_DIR%\yfile2mseed_append.exe"
)

if exist "%SCRIPT_DIR%yfile2obspy_bridge.exe" (
  set "BRIDGE_EXE=%SCRIPT_DIR%yfile2obspy_bridge.exe"
) else (
  set "BRIDGE_EXE=%BUILD_EXE_DIR%\yfile2obspy_bridge.exe"
)

if not exist "%APPEND_EXE%" (
  echo yfile2mseed_append.exe not found:
  echo   "%APPEND_EXE%"
  echo Build the Release target first, or copy the exe next to this batch file.
  exit /b 2
)

if not exist "%BRIDGE_EXE%" (
  echo yfile2obspy_bridge.exe not found:
  echo   "%BRIDGE_EXE%"
  echo Build the Release target first, or copy the exe next to this batch file.
  exit /b 2
)

if not exist "%CORRECT_SID%" (
  echo CorrectSID file not found:
  echo   "%CORRECT_SID%"
  exit /b 2
)

for %%I in ("%CORRECT_SID%") do set "CORRECT_SID_DIR=%%~dpI"

set "HYBRID_SCRIPT=%SCRIPT_DIR%yfiles_to_mseed_sds_hybrid_cppread.py"
set "CLEANUP_SCRIPT=%SCRIPT_DIR%sds_obspy_cleanup_inplace.py"
set "SDS_AVAIL_SCRIPT=%SCRIPT_DIR%sds_availability_report.py"
set "RAW_REPORT_SCRIPT=%SCRIPT_DIR%sds_raw_segment_report.py"
set "COMPARE_AVAIL_SCRIPT=%SCRIPT_DIR%compare_availability_lines.py"
set "COMPARE_GAPS_SCRIPT=%SCRIPT_DIR%compare_sds_gaps.py"
set "COMPARE_ARCHIVE_SCRIPT=%SCRIPT_DIR%compare_sds_archives.py"
set "COMPARE_WINDOW_SCRIPT=%SCRIPT_DIR%compare_sds_sample_window.py"

if not exist "%HYBRID_SCRIPT%" goto :missing_tools
if not exist "%CLEANUP_SCRIPT%" goto :missing_tools
if not exist "%SDS_AVAIL_SCRIPT%" goto :missing_tools
if not exist "%RAW_REPORT_SCRIPT%" goto :missing_tools
if not exist "%COMPARE_AVAIL_SCRIPT%" goto :missing_tools
if not exist "%COMPARE_GAPS_SCRIPT%" goto :missing_tools
if not exist "%COMPARE_ARCHIVE_SCRIPT%" goto :missing_tools

if exist "%RESULT_ROOT%" (
  echo Result folder already exists:
  echo   "%RESULT_ROOT%"
  echo Choose a new output folder to avoid mixing old and new SDS files.
  exit /b 2
)

set "SDS_APPEND=%RESULT_ROOT%\append_cpp"
set "SDS_HYBRID=%RESULT_ROOT%\hybrid_cppread_obspy"
set "REPORTS=%RESULT_ROOT%\reports"

mkdir "%SDS_APPEND%" "%SDS_HYBRID%" "%REPORTS%" >nul 2>nul

echo Input:
echo   "%INPUT_ROOT%"
echo Output:
echo   "%RESULT_ROOT%"
echo.

echo [1/8] Building SDS with current C++ append converter...
pushd "%CORRECT_SID_DIR%"
"%APPEND_EXE%" "%INPUT_ROOT%" -o "%SDS_APPEND%" -V2
set "APPEND_STATUS=%errorlevel%"
popd
if not "%APPEND_STATUS%"=="0" exit /b %APPEND_STATUS%

echo.
echo [2/8] Building SDS with hybrid C++ reader + ObsPy writer...
python "%HYBRID_SCRIPT%" ^
  --input-root "%INPUT_ROOT%" ^
  --output-root "%SDS_HYBRID%" ^
  --bridge-exe "%BRIDGE_EXE%" ^
  --correct-sid "%CORRECT_SID%" ^
  --encoding STEIM2 ^
  --record-length 4096 ^
  --pack-workers 4 ^
  --recursive ^
  --benchmark ^
  --report "%REPORTS%\hybrid_report.json"
if errorlevel 1 exit /b %errorlevel%

echo.
echo [3/8] Running identical ObsPy cleanup on both SDS folders...
python "%CLEANUP_SCRIPT%" --sds-root "%SDS_APPEND%" --workers 4 --encoding STEIM2 --reclen 4096 --report "%REPORTS%\cleanup_append"
if errorlevel 1 exit /b %errorlevel%
python "%CLEANUP_SCRIPT%" --sds-root "%SDS_HYBRID%" --workers 4 --encoding STEIM2 --reclen 4096 --report "%REPORTS%\cleanup_hybrid"
if errorlevel 1 exit /b %errorlevel%

echo.
echo [4/8] Writing raw MiniSEED record segment reports...
python "%RAW_REPORT_SCRIPT%" --input "%SDS_APPEND%" --output "%REPORTS%\append_raw_segments.txt"
if errorlevel 1 exit /b %errorlevel%
python "%RAW_REPORT_SCRIPT%" --input "%SDS_HYBRID%" --output "%REPORTS%\hybrid_raw_segments.txt"
if errorlevel 1 exit /b %errorlevel%

echo.
echo [5/8] Writing normalized availability reports...
python "%SDS_AVAIL_SCRIPT%" --input "%SDS_APPEND%" --output "%REPORTS%\append_availability" --write-normalized
if errorlevel 1 exit /b %errorlevel%
python "%SDS_AVAIL_SCRIPT%" --input "%SDS_HYBRID%" --output "%REPORTS%\hybrid_availability" --write-normalized
if errorlevel 1 exit /b %errorlevel%

echo.
echo [6/8] Comparing normalized availability reports...
python "%COMPARE_AVAIL_SCRIPT%" ^
  --center "%REPORTS%\append_availability\availability_normalized.txt" ^
  --ours "%REPORTS%\hybrid_availability\availability_normalized.txt" ^
  --center-label append_cpp ^
  --ours-label hybrid_cppread_obspy ^
  --output "%REPORTS%\availability_diff_append_vs_hybrid.txt"
if errorlevel 1 exit /b %errorlevel%

echo.
echo [7/8] Comparing ObsPy gap/overlap lists...
python "%COMPARE_GAPS_SCRIPT%" ^
  --sds-a "%SDS_APPEND%" ^
  --sds-b "%SDS_HYBRID%" ^
  --a-label append_cpp ^
  --b-label hybrid_cppread_obspy ^
  --report "%REPORTS%\gap_compare" ^
  --allow-differences
if errorlevel 1 exit /b %errorlevel%

echo.
echo [8/8] Comparing complete SDS sample content with ObsPy...
python "%COMPARE_ARCHIVE_SCRIPT%" ^
  --reference-sds "%SDS_APPEND%" ^
  --cpp-sds "%SDS_HYBRID%" ^
  --report "%REPORTS%\archive_compare" ^
  --allow-differences
if errorlevel 1 exit /b %errorlevel%

if not "%STREAM_ID%"=="" (
  if "%WINDOW_START%"=="" goto :bad_window
  if "%WINDOW_END%"=="" goto :bad_window
  if not exist "%COMPARE_WINDOW_SCRIPT%" goto :missing_tools
  echo.
  echo Extra: comparing requested sample window...
  python "%COMPARE_WINDOW_SCRIPT%" ^
    --sds-a "%SDS_APPEND%" ^
    --sds-b "%SDS_HYBRID%" ^
    --label-a append_cpp ^
    --label-b hybrid_cppread_obspy ^
    --stream-id "%STREAM_ID%" ^
    --start "%WINDOW_START%" ^
    --end "%WINDOW_END%" ^
    --report "%REPORTS%\sample_window_compare.json" ^
    --allow-differences
  if errorlevel 1 exit /b %errorlevel%
)

echo.
echo Done.
echo SDS append:
echo   "%SDS_APPEND%"
echo SDS hybrid:
echo   "%SDS_HYBRID%"
echo Reports:
echo   "%REPORTS%"
echo Key files:
echo   "%REPORTS%\append_raw_segments.txt"
echo   "%REPORTS%\hybrid_raw_segments.txt"
echo   "%REPORTS%\availability_diff_append_vs_hybrid.txt"
echo   "%REPORTS%\gap_compare\gap_comparison.csv"
echo   "%REPORTS%\archive_compare\comparison_summary.json"
exit /b 0

:usage
echo Usage:
echo   %~nx0 INPUT_Y_FOLDER [RESULT_ROOT] [CorrectSID.txt] [NET.STA.LOC.CHA START END]
echo.
echo Example:
echo   %~nx0 D:\MSeed_Test\Data\Shi_20100107_08.day D:\MSeed_Test\Bench\append_vs_hybrid D:\MSeed_Test\app\CorrectSID.txt IR.SHI..BHE 2010-01-07T00:00:00 2010-01-07T00:10:00
exit /b 2

:missing_tools
echo One or more Python tools are missing next to this batch file.
exit /b 2

:bad_window
echo When STREAM_ID is provided, START and END are required too.
exit /b 2
