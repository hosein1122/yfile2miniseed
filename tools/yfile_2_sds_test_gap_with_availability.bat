@echo off
cd /d "%~dp0"

set "TestFolderName=shi201001.month"

set "inputYFile=D:\MSeed_Test\Data\%TestFolderName%"
set "SDS_result=D:\MSeed_Test\Result\%TestFolderName%"
set "gap_report=%SDS_result%\gap_report"
set "Availability_report=%SDS_result%\Availability"
set "RawSegment_report=%SDS_result%\RawSegments"
set "SampleCompare_report=%SDS_result%\SampleCompare"

rem Update these three values for the station/component/time window you want to verify sample-by-sample.
set "SampleStreamID=IR.PAR..SPE"
set "SampleStart=2010-01-03T08:17:35"
set "SampleEnd=2010-01-03T09:00:12"

if not exist "%gap_report%" mkdir "%gap_report%"
if not exist "%Availability_report%" mkdir "%Availability_report%"
if not exist "%RawSegment_report%" mkdir "%RawSegment_report%"
if not exist "%SampleCompare_report%" mkdir "%SampleCompare_report%"

echo.
echo ============================================================
echo [1/9] Convert Y-files to SDS with the C++ application
echo ============================================================
echo.

.\yfile2mseed_append.exe "%inputYFile%" -o "%SDS_result%\our" -V2 --workers 4

echo.
pause

echo ============================================================
echo [2-1/9] Build the reference SDS with ObsPy
echo ============================================================
echo.

python .\yfiles_to_mseed_sds_obspy.py ^
  --input-root "%inputYFile%" ^
  --output-root "%SDS_result%\obspy" ^
  --recursive

echo.
pause

echo ============================================================
echo [2-2/9] Build the reference SDS with hybrid (ObsPy+C++)
echo ============================================================
echo.

python .\yfiles_to_mseed_sds_hybrid_cppread.py ^
  --input-root "%inputYFile%" ^
  --output-root "%SDS_result%\hybrid" ^
  --correct-sid .\CorrectSID.txt ^
  --encoding STEIM2 ^
  --record-length 4096 ^
  --pack-workers 4

echo.
pause

echo ============================================================
echo [3/9] Clean SDS archives with ObsPy merge(method=-1)
echo ============================================================
echo.

python.exe .\sds_obspy_cleanup_inplace.py --sds-root "%SDS_result%\our"
python.exe .\sds_obspy_cleanup_inplace.py --sds-root "%SDS_result%\obspy"
python.exe .\sds_obspy_cleanup_inplace.py --sds-root "%SDS_result%\hybrid"
python.exe .\sds_obspy_cleanup_inplace.py --sds-root "%SDS_result%\Any2MSeed.Center"

echo.
echo ============================================================
echo [4/9] Compare gaps and overlaps
echo ============================================================
echo.

python.exe .\compare_sds_gaps.py ^
  --sds-a "%SDS_result%\obspy" ^
  --sds-b "%SDS_result%\our" ^
  --report "%gap_report%\obspy-our" ^
  --a-label obspy ^
  --b-label our

python.exe .\compare_sds_gaps.py ^
  --sds-a "%SDS_result%\obspy" ^
  --sds-b "%SDS_result%\Any2MSeed.Center" ^
  --report "%gap_report%\obspy-center" ^
  --a-label obspy ^
  --b-label center

python.exe .\compare_sds_gaps.py ^
  --sds-a "%SDS_result%\obspy" ^
  --sds-b "%SDS_result%\hybrid" ^
  --report "%gap_report%\obspy-hybrid" ^
  --a-label obspy ^
  --b-label hybrid

echo.
echo ============================================================
echo [5/9] Create raw SDS segment reports without trace merging
echo ============================================================
echo.

python .\sds_raw_segment_report.py ^
  --input "%SDS_result%\our" ^
  --output "%RawSegment_report%\our_raw_segments.txt"

python .\sds_raw_segment_report.py ^
  --input "%SDS_result%\obspy" ^
  --output "%RawSegment_report%\obspy_raw_segments.txt"

python .\sds_raw_segment_report.py ^
  --input "%SDS_result%\hybrid" ^
  --output "%RawSegment_report%\hybrid_raw_segments.txt"

python .\sds_raw_segment_report.py ^
  --input "%SDS_result%\Any2MSeed.Center" ^
  --output "%RawSegment_report%\center_raw_segments.txt"

echo.
echo ============================================================
echo [6/9] Compare sample values in one selected time window
echo ============================================================
echo.

echo Sample stream: %SampleStreamID%
echo Sample window: %SampleStart% to %SampleEnd%
echo.

python .\compare_sds_sample_window.py ^
  --sds-a "%SDS_result%\obspy" ^
  --sds-b "%SDS_result%\our" ^
  --label-a obspy ^
  --label-b our ^
  --stream-id "%SampleStreamID%" ^
  --start "%SampleStart%" ^
  --end "%SampleEnd%" ^
  --report "%SampleCompare_report%\obspy_vs_our.json" ^
  --allow-differences

python .\compare_sds_sample_window.py ^
  --sds-a "%SDS_result%\obspy" ^
  --sds-b "%SDS_result%\hybrid" ^
  --label-a obspy ^
  --label-b hybrid ^
  --stream-id "%SampleStreamID%" ^
  --start "%SampleStart%" ^
  --end "%SampleEnd%" ^
  --report "%SampleCompare_report%\obspy_vs_hybrid.json" ^
  --allow-differences

python .\compare_sds_sample_window.py ^
  --sds-a "%SDS_result%\obspy" ^
  --sds-b "%SDS_result%\Any2MSeed.Center" ^
  --label-a obspy ^
  --label-b center ^
  --stream-id "%SampleStreamID%" ^
  --start "%SampleStart%" ^
  --end "%SampleEnd%" ^
  --report "%SampleCompare_report%\obspy_vs_center.json" ^
  --allow-differences

echo.
echo ============================================================
echo [7/9] Create the availability report from the original Y-files
echo ============================================================
echo.

python .\yfile_availability_report.py ^
  --input "%inputYFile%" ^
  --output "%Availability_report%" ^
  --y5dump ".\y5dump.exe"

echo.
echo ============================================================
echo [8/9] Create availability reports for SDS archives
echo ============================================================
echo.

python .\sds_availability_report.py ^
  --input "%SDS_result%\obspy" ^
  --output "%Availability_report%\obspy"

python .\sds_availability_report.py ^
  --input "%SDS_result%\our" ^
  --output "%Availability_report%\our"

python .\sds_availability_report.py ^
  --input "%SDS_result%\hybrid" ^
  --output "%Availability_report%\hybrid"

python .\sds_availability_report.py ^
  --input "%SDS_result%\Any2MSeed.Center" ^
  --output "%Availability_report%\center"

echo.
echo ============================================================
echo [9/9] Compare the generated availability reports
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

python .\compare_availability_lines.py ^
  --center "%Availability_report%\center\availability.txt" ^
  --ours "%Availability_report%\obspy\availability.txt" ^
  --output "%Availability_report%\center-vs-obspy.txt"

python .\compare_availability_lines.py ^
  --center "%Availability_report%\center\availability.txt" ^
  --ours "%Availability_report%\our\availability.txt" ^
  --output "%Availability_report%\center-vs-our.txt"

python .\compare_availability_lines.py ^
  --center "%Availability_report%\hybrid\availability.txt" ^
  --ours "%Availability_report%\obspy\availability.txt" ^
  --output "%Availability_report%\hybrid-vs-obspy.txt"

echo.
echo ============================================================
echo Workflow completed
echo Results: %SDS_result%
echo Raw segment reports: %RawSegment_report%
echo Sample compare reports: %SampleCompare_report%
echo ============================================================
echo.

pause
