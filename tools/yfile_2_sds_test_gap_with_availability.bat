@echo off
cd /d "%~dp0"

set "inputYFile=D:\MSeed_Test\Data\shi201001.month"
set "SDS_our=D:\MSeed_Test\Result\shi201001.month\our"
set "SDS_obspy=D:\MSeed_Test\Result\shi201001.month\obspy"
set "gap_report=D:\MSeed_Test\Result\shi201001.month\gap_report"
set "Availability_report=D:\MSeed_Test\Result\shi201001.month\Availability"

echo.
echo ============================================================
echo [1/7] Convert Y-files to SDS with the C++ application
echo ============================================================
echo.

.\yfile2mseed.exe "%inputYFile%" -o "%SDS_our%" -V2

echo.
pause
echo ============================================================
echo [2/7] Build the reference SDS with ObsPy
echo ============================================================
echo.

python .\yfiles_to_mseed_sds_obspy.py ^
  --input-root "%inputYFile%" ^
  --output-root "%SDS_obspy%" ^
  --recursive

echo.
pause

echo ============================================================
echo [3/7] Clean both SDS archives with ObsPy
echo ============================================================
echo.

python.exe .\sds_obspy_cleanup_inplace.py --sds-root "%SDS_our%"
python.exe .\sds_obspy_cleanup_inplace.py --sds-root "%SDS_obspy%"

echo.
echo ============================================================
echo [4/7] Compare gaps and overlaps
echo ============================================================
echo.

python.exe .\compare_sds_gaps.py ^
  --sds-a "%SDS_obspy%" ^
  --sds-b "%SDS_our%" ^
  --report "%gap_report%" ^
  --a-label obspy ^
  --b-label our

echo.
echo ============================================================
echo [5/7] Create the availability report from the original Y-files
echo ============================================================
echo.

python .\yfile_availability_report.py ^
  --input "%inputYFile%" ^
  --output "%Availability_report%" ^
  --y5dump ".\y5dump.exe"

echo.
echo ============================================================
echo [6/7] Create availability reports for both SDS archives
echo ============================================================
echo.

python .\sds_availability_report.py ^
  --input "%SDS_obspy%" ^
  --output "%Availability_report%\obspy"

python .\sds_availability_report.py ^
  --input "%SDS_our%" ^
  --output "%Availability_report%\our"

echo.
echo ============================================================
echo [7/7] Compare the generated availability reports
echo ============================================================
echo.

python .\compare_availability_lines.py ^
  --center "%Availability_report%\obspy\availability.txt" ^
  --ours "%Availability_report%\our\availability.txt" ^
  --output "%Availability_report%\obspy-vs-our.txt"

python .\compare_availability_lines.py ^
  --center "%Availability_report%\y-availability.txt" ^
  --ours "%Availability_report%\our\availability.txt" ^
  --output "%Availability_report%\yfile-vs-our.txt"

echo.
echo ============================================================
echo Workflow completed
echo Results: D:\MSeed_Test\Result\shi201001.month
echo ============================================================
echo.

pause
