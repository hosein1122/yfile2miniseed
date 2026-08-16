# Tools Guide

The main user-facing converter in this folder is:

```text
yfiles_to_mseed_sds_hybrid_cppread.py
```

It reads Y-files with the project C++ reader exposed as the Python package
`yfile2obspy_cpp`, then uses ObsPy for sorting, conservative
`Stream.merge(method=-1)`, MiniSEED packing, and SDS routing.

## Primary Hybrid Converter

Install the native reader once:

```bat
cd /d D:\C++Code\yfile2miniseed
python -m pip install --upgrade pip setuptools wheel build numpy obspy
python -m pip install --force-reinstall .\python\yfile2obspy_cpp
```

Run:

```bat
python tools\yfiles_to_mseed_sds_hybrid_cppread.py ^
  --input-root D:\YFiles ^
  --output-root D:\MainSDS ^
  --correct-sid D:\C++Code\yfile2miniseed\CorrectSID.txt ^
  --encoding STEIM2 ^
  --record-length 4096 ^
  --pack-workers 4 ^
  --quiet ^
  --report D:\MainSDS_hybrid_report.json
```

Notes:

- Input scanning is recursive by default.
- Existing SDS files under `--output-root` are considered automatically for
  affected station/day/channel paths.
- Plain Y-files, ZIP archives, and RAR archives are supported.
- ZIP/RAR members are decompressed one member at a time in memory.
- RAR support requires `rarfile` plus an installed backend such as 7-Zip.
- `yfile2obspy_bridge.exe` remains only as a compatibility fallback when the
  Python extension is not importable.

Optional RAR setup:

```bat
python -m pip install rarfile
winget install 7zip.7zip
setx PATH "%PATH%;C:\Program Files\7-Zip"
```

Open a new terminal after changing `PATH`.

## Pure ObsPy Reference Builder

Use `yfiles_to_mseed_sds_obspy.py` when you want a reference path that reads
Y-files through ObsPy/Python instead of the C++ extension:

```bat
python tools\yfiles_to_mseed_sds_obspy.py ^
  --input-root D:\YFiles ^
  --output-root D:\ReferenceSDS ^
  --recursive
```

This tool is useful as a baseline. It is usually slower than the hybrid path.

## Direct C++ Append Tool

`yfile2mseed_append.exe` is kept for direct C++ output, benchmarks, and
diagnostics:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed_append.exe ^
  D:\YFiles ^
  -o D:\SDS_append ^
  -V2 ^
  --workers 4
```

It appends records into SDS files. It intentionally does not implement custom
sample deduplication or overlap rewriting. Prefer the hybrid converter for final
SDS generation when duplicate/overlap behavior matters.

## Existing SDS Behavior

Hybrid updates are designed for a destination that may already contain SDS data:

```text
read new Y-file traces
read affected existing SDS files
sort by stream/time
ObsPy merge(method=-1)
write staged SDS files
replace touched output files
```

This is cleaner than direct append for repeated runs, but it is still not a
guarantee that rerunning the same source files will be perfectly idempotent.
For production ingestion, keep a manifest of already-ingested source files and
skip them before conversion.

## Reports And Comparisons

Raw MiniSEED record segments:

```bat
python tools\sds_raw_segment_report.py ^
  --input D:\MainSDS ^
  --output D:\Reports\raw_segments.txt ^
  --no-split-on-sequence-reset
```

Availability:

```bat
python tools\sds_availability_report.py ^
  --input D:\MainSDS ^
  --output D:\Reports\availability
```

Availability diff:

```bat
python tools\compare_availability_lines.py ^
  --center D:\Reports\availability_a\availability.txt ^
  --ours D:\Reports\availability_b\availability.txt ^
  --output D:\Reports\availability_diff.txt
```

Gap/overlap comparison:

```bat
python tools\compare_sds_gaps.py ^
  --sds-a D:\ReferenceSDS ^
  --sds-b D:\MainSDS ^
  --a-label reference ^
  --b-label hybrid ^
  --report D:\Reports\gap_compare ^
  --allow-differences
```

Sample-window comparison:

```bat
python tools\compare_sds_sample_window.py ^
  --sds-a D:\ReferenceSDS ^
  --sds-b D:\MainSDS ^
  --label-a reference ^
  --label-b hybrid ^
  --stream-id IR.SHI..BHE ^
  --start 2010-01-07T00:00:00 ^
  --duration-seconds 30 ^
  --report D:\Reports\sample_window_compare.json ^
  --allow-differences
```

## Benchmark Workflows

Append vs hybrid repeated-ingestion benchmark:

```bat
python tools\benchmark_append_vs_hybrid_existing_sds.py ^
  --input-root D:\MSeed_Test\Data\shi201001.month ^
  --result-root D:\MSeed_Test\Result\shi201001_benchmark ^
  --iterations 5
```

Matrix comparison across several input folders:

```bat
python tools\run_raw_sds_matrix_5cases.py ^
  --data-root D:\MSeed_Test\Data ^
  --result-root D:\MSeed_Test\Result\raw_sds_matrix ^
  --limit 14
```

Despite the historical filename, `run_raw_sds_matrix_5cases.py` accepts any
limit and repeatable `--case` arguments.

## Tool Inventory

```text
yfiles_to_mseed_sds_hybrid_cppread.py  Primary hybrid SDS builder.
yfiles_to_mseed_sds_obspy.py           Pure ObsPy reference SDS builder.
sds_obspy_cleanup_inplace.py           Conservative in-place SDS cleanup.
sds_availability_report.py             Availability reports from SDS.
sds_raw_segment_report.py              Raw MiniSEED record segment report.
compare_availability_lines.py          Compare availability TXT reports.
compare_sds_gaps.py                    Compare ObsPy gap/overlap lists.
compare_sds_sample_window.py           Compare one exact sample window.
compare_mseed_outputs.py               Compare two MiniSEED/SDS trees.
benchmark_append_vs_hybrid_existing_sds.py
                                      Benchmark append vs hybrid reruns.
run_raw_sds_matrix_5cases.py           Multi-folder fresh/existing comparison.
compare_append_vs_hybrid_sds.bat       Legacy local comparison wrapper.
availability_report_tools.md           Older availability notes.
```
