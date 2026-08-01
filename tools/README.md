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

mseed_availability_report.py  Build availability reports from MiniSEED/SDS.
                              This is a reporting tool, not a cleanup tool.

yfile_availability_report.py  Build availability reports from raw Y-file input
                              using Nanometrics Y5DUMP headers.

compare_mseed_outputs.py      Compare two MiniSEED/SDS trees at header,
                              coverage, or sample level.

compare_availability_lines.py Compare two generated availability text reports
                              line by line.

run_full_availability_case.bat
                              Local workflow for comparing yfile2miniseed with
                              the national-center converter.

availability_report_tools.md  Older Persian notes for the availability report
                              workflow.

compare_mseed_outputs.md      Older Persian notes for comparing MiniSEED output
                              trees.
```

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
