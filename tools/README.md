# Tools Guide

The files in this folder are still useful, but they now have clearer roles:
the C++ converter writes daily SDS MiniSEED, while ObsPy-based tools are used
for validation and post-processing.

## Recommended Conversion And Cleanup Flow

Use MiniSEED 2 output when you plan to run the ObsPy tools:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe ^
  D:\YFiles ^
  -o D:\SDS ^
  -V2
```

ObsPy's built-in MiniSEED support targets MiniSEED 2. MiniSEED 3 support is not
part of the normal ObsPy MiniSEED path on this development setup, so write `-V2`
before using these tools unless you have installed and tested a separate
MiniSEED 3 ObsPy plugin.

After conversion, run conservative in-place cleanup:

```bat
python tools\sds_obspy_cleanup_inplace.py ^
  --sds-root D:\SDS ^
  --workers 8 ^
  --encoding STEIM2 ^
  --reclen 4096 ^
  --report D:\SDS_cleanup_report
```

This rewrites the SDS files in the same location. Use `--dry-run` first if you
only want to inspect what would be processed, or `--backup-suffix .bak` if you
want a copy of each original file before replacement.

Start with a dry run for new datasets:

```bat
python tools\sds_obspy_cleanup_inplace.py ^
  --sds-root D:\SDS ^
  --workers 8 ^
  --dry-run ^
  --report D:\SDS_cleanup_dry_run
```

## Tool Inventory

```text
sds_obspy_cleanup_inplace.py  Post-process a standard SDS tree in place with
                              ObsPy Stream.merge(method=-1).

sds_availability_report.py  Build availability reports from MiniSEED/SDS.
                              This is a reporting tool, not a cleanup tool.
                              Its normalized report uses ObsPy method=-1.

sds_raw_segment_report.py   Build a TXT report from raw MiniSEED records in an
                              SDS tree without merging traces.

yfiles_to_mseed_sds_hybrid_cppread.py
                              Use the C++ Y-file reader bridge, then reuse the
                              ObsPy merge/pack/SDS path.

compare_append_vs_hybrid_sds.bat
                              Build SDS with yfile2mseed_append.exe and the
                              hybrid converter, then compare the two outputs.

yfile_availability_report.py  Build availability reports from raw Y-file input
                              using Nanometrics Y5DUMP headers.

compare_mseed_outputs.py      Compare two MiniSEED/SDS trees at header,
                              coverage, or sample level. Deep comparison uses
                              ObsPy Stream.merge(method=-1).

compare_sds_gaps.py           Save ordered gap/overlap lists for two SDS trees
                              and compare the two lists using ObsPy get_gaps().

compare_sds_sample_window.py  Fast sample-by-sample comparison for one NSLC
                              stream and one short time window.

compare_availability_lines.py Compare two generated availability text reports
                              line by line.

run_full_availability_case.bat
                              Local lab workflow for comparing yfile2miniseed
                              with another converter in one specific setup.

compare_sds_availability.bat  Build availability reports for two existing SDS
                              folders and compare the reports line by line.

availability_report_tools.md  Older Persian notes for the availability report
                              workflow.

compare_mseed_outputs.md      Guide for comparing two arbitrary SDS archives.
```

See [compare_mseed_outputs.md](compare_mseed_outputs.md) for comparison examples
and report meanings.

## Compare SDS Gaps

Use `compare_sds_gaps.py` when you only want the gaps/overlaps in two SDS trees:

```bat
python tools\compare_sds_gaps.py ^
  --sds-a D:\ReferenceSDS ^
  --sds-b D:\OurSDS ^
  --a-label reference ^
  --b-label ours ^
  --report D:\SDS_gap_compare
```

The tool reads MiniSEED headers with ObsPy, sorts traces by stream and time, and
uses `Stream.get_gaps()`. It writes the full ordered gap/overlap list for each
SDS tree, then writes only differences to the final comparison CSV:

```text
gaps_reference.csv
gaps_ours.csv
gap_comparison.csv
report.json
```

`gap_comparison.csv` contains only gaps/overlaps that exist in one SDS tree but
not the other. A gap is considered shared when stream id, gap/overlap kind, start
boundary, and end boundary match within `--time-tolerance-ns`.

Use `--station KAZ` or `--channel SPE` to limit the comparison.

## Raw SDS Segment Report

Use `sds_raw_segment_report.py` when you want to inspect what is stored inside
one SDS tree without ObsPy trace merging:

```bat
python tools\sds_raw_segment_report.py ^
  --input D:\SDS ^
  --output D:\SDS_raw_segments.txt ^
  --station SHI ^
  --channel BHE
```

The report is built from MiniSEED record headers. Consecutive records are kept
as one segment only while their times are contiguous and their MiniSEED2 sequence
numbers continue. If a converter appended a new packed Y-file with sequence
numbers starting again, the report starts a new segment even when there is no
time gap.

## Hybrid C++ Read And ObsPy Write

Use `yfiles_to_mseed_sds_hybrid_cppread.py` when you want the C++ Y-file/ZIP
reader speed but the same ObsPy merge and SDS writing logic as
`yfiles_to_mseed_sds_obspy.py`:

```bat
python tools\yfiles_to_mseed_sds_hybrid_cppread.py ^
  --input-root D:\YFiles ^
  --output-root D:\SDS_hybrid ^
  --bridge-exe out\build\x64-Release\apps\yfile2mseed_cli\yfile2obspy_bridge.exe ^
  --correct-sid D:\MSeed_Test\app\CorrectSID.txt ^
  --encoding STEIM2 ^
  --record-length 4096 ^
  --pack-workers 4 ^
  --quiet ^
  --report D:\SDS_hybrid_report.json ^
  --recursive
```

Build the bridge target first:

```bat
cmake --build out\build\x64-Release --target yfile2obspy_bridge --config Release
```

The bridge executable is a quiet binary backend. It does not print a progress
bar by itself; the Python hybrid wrapper prints conversion progress from the
input byte totals reported by the bridge. Add `--benchmark` only when you want
selected timings printed on the console.

## Compare Append C++ And Hybrid C++/ObsPy

Use `compare_append_vs_hybrid_sds.bat` when you want one command that takes a
Y-file folder, creates SDS output with both recommended methods, and shows where
the two results differ:

```bat
tools\compare_append_vs_hybrid_sds.bat ^
  D:\YFiles ^
  D:\CompareRuns\append_vs_hybrid ^
  D:\MSeed_Test\app\CorrectSID.txt
```

The batch file writes:

```text
append_cpp\                         SDS from yfile2mseed_append.exe
hybrid_cppread_obspy\               SDS from the C++ bridge + ObsPy path
reports\append_raw_segments.txt     MiniSEED record segments without merging
reports\hybrid_raw_segments.txt     MiniSEED record segments without merging
reports\availability_diff_append_vs_hybrid.txt
reports\gap_compare\gap_comparison.csv
reports\archive_compare\comparison_summary.json
```

You can also add one exact sample-window comparison at the end of the command:

```bat
tools\compare_append_vs_hybrid_sds.bat ^
  D:\YFiles ^
  D:\CompareRuns\append_vs_hybrid ^
  D:\MSeed_Test\app\CorrectSID.txt ^
  IR.SHI..BHE ^
  2010-01-07T00:00:00 ^
  2010-01-07T00:10:00
```

## Compare One Sample Window

Use `compare_sds_sample_window.py` when you want a quick exact check for one
station component instead of comparing a complete SDS archive:

```bat
python tools\compare_sds_sample_window.py ^
  --sds-a D:\Bench\obspy ^
  --sds-b D:\Bench\cpp ^
  --label-a obspy ^
  --label-b cpp ^
  --stream-id IR.TST..BHZ ^
  --start 2020-01-01T00:00:00 ^
  --duration-seconds 30 ^
  --report D:\Bench\sample_window_compare.json
```

The tool reads only that SDS stream and the half-open time window
`start <= sample_time < end`, aligns samples on the requested start-time grid,
then reports samples that exist on only one side and samples whose values
differ. Use `--max-diffs` to control how many individual differences are
printed.

## In-Place SDS Cleanup Details

`sds_obspy_cleanup_inplace.py` assumes this SDS layout:

```text
SDS_ROOT/YEAR/NET/STA/CHAN.TYPE/NET.STA.LOC.CHAN.TYPE.YEAR.DOY[.mseed]
```

It does not scan all headers before building work. It parses metadata from the
SDS path, groups files by:

```text
Network + Station + Year + DayOfYear
```

Then each worker:

1. Reads all waveform channel files for that station-day.
2. Validates that every loaded trace matches the NSLC claimed by its SDS path.
3. Runs `Stream.merge(method=-1)`.
4. Writes each cleaned NSLC stream back to the same SDS file, using a temporary
   file in the same folder and then replacing the original file.

`method=-1` is ObsPy's conservative cleanup merge. It can remove compatible
duplicates and safe overlaps, but it is not a force-merge for conflicting
overlap sample values. Conflicts are left for review instead of being silently
filled or overwritten.

For safer first runs, use:

```bat
python tools\sds_obspy_cleanup_inplace.py ^
  --sds-root D:\SDS ^
  --dry-run ^
  --report D:\SDS_cleanup_dry_run
```

For an in-place run with original-file backups:

```bat
python tools\sds_obspy_cleanup_inplace.py ^
  --sds-root D:\SDS ^
  --backup-suffix .bak ^
  --report D:\SDS_cleanup_report
```

Backups stay next to the original files and are not part of the SDS cleanup
output.
