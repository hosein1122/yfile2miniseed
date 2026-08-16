# yfile2miniseed

Fast Nanometrics Y-file tooling for ObsPy and SDS MiniSEED workflows.

The recommended product path is the **hybrid converter**:

```text
Y-file / ZIP / RAR input
  -> C++ Y-file reader exposed as a Python extension
  -> ObsPy Stream.sort() + Stream.merge(method=-1)
  -> strict SDS MiniSEED output
```

The repository also keeps the direct C++ append converter and comparison tools.
Those are useful for speed tests, low-level experiments, and regression checks,
but they are not the preferred final SDS production path when duplicate or
overlapping Y-file segments may be present.

The main target platform is Windows with Visual Studio/MSVC.

## Recommended Quick Start

Build the C++ tools:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake -S . -B out\build\x64-Release -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build out\build\x64-Release --config Release
```

Install the Python C++ reader extension:

```bat
python -m pip install --upgrade pip setuptools wheel build numpy obspy
python -m pip install --force-reinstall .\python\yfile2obspy_cpp
```

Optional RAR support:

```bat
python -m pip install rarfile
winget install 7zip.7zip
setx PATH "%PATH%;C:\Program Files\7-Zip"
```

Open a new terminal after changing `PATH`.

Build or update the local SID correction file:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed_sid_scan.exe ^
  D:\YFiles ^
  -o CorrectSID.txt
```

Create or update an SDS archive with the hybrid converter:

```bat
python tools\yfiles_to_mseed_sds_hybrid_cppread.py ^
  --input-root D:\YFiles ^
  --output-root D:\MainSDS ^
  --correct-sid CorrectSID.txt ^
  --encoding STEIM2 ^
  --record-length 4096 ^
  --pack-workers 4 ^
  --report D:\MainSDS_hybrid_report.json
```

The hybrid converter scans input recursively by default. It reads plain Y-files,
Y-files inside ZIP archives, and Y-files inside RAR archives. ZIP/RAR members
are decompressed one member at a time in memory and passed to the C++ reader with
no extraction directory.

## What To Trust For Final SDS

Use `tools/yfiles_to_mseed_sds_hybrid_cppread.py` as the primary SDS builder.
It keeps the fast project C++ Y-file decoder, then lets ObsPy do the conservative
merge and MiniSEED writing work.

Use `apps/yfile2mseed_cli/yfile2mseed_append.exe` as a direct C++ append tool,
benchmark target, or diagnostic path. It appends packed MiniSEED records into
daily SDS files and intentionally does not implement project-owned sample
deduplication or overlap rewriting.

Important limitation: ObsPy `merge(method=-1)` is conservative. It is reliable
for compatible overlaps and exact data continuity, but it is not a force-delete
policy for every possible duplicate that already exists inside an SDS archive.
For fully idempotent production ingestion, keep an external manifest/ingest log
so the same source file is not submitted again.

## Python Y-file Reader

The package in `python/yfile2obspy_cpp` exposes the C++ Y-file reader to Python:

```python
import yfile2obspy_cpp

record = yfile2obspy_cpp.read_yfile_path(r"D:\YFiles\YPARSPE.20100107.095933")
record = yfile2obspy_cpp.read_yfile_bytes(payload_from_zip_or_rar)
```

Returned records contain metadata plus a NumPy `int32` sample array:

```text
network, station, location, channel, start_ns, end_ns,
sample_rate, npts, samples
```

This package is intentionally small. Archive discovery, SID correction, ObsPy
merge, MiniSEED packing, SDS routing, and existing-SDS update logic live in the
Python tools.

## Existing SDS Updates

When `--output-root` already contains SDS files, the hybrid converter reads only
the SDS files affected by the new input, combines existing traces with the new
Y-file traces, sorts, runs `Stream.merge(method=-1)`, writes to a staging
directory, and replaces the touched SDS files.

This usually produces much cleaner reruns than direct append. It is still not a
substitute for a source-file manifest because previously packed SDS trace
boundaries can differ from the original Y-file trace boundaries.

## C++ Append Tool

General form:

```bat
yfile2mseed_append.exe inputPath [-o outputDir] [-R minRamGb] [-V2] [--workers N] [-h]
```

Examples:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed_append.exe D:\YFiles -o D:\SDS_append -V2 --workers 4
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed_append.exe D:\one_file.mseed -o D:\SDS_append -V2
```

Use `-V2` for workflows that will be read by the included ObsPy tools. Without
`-V2`, the C++ converter writes MiniSEED 3.

## Validation And Benchmarks

Compare append, ObsPy, and hybrid output across many input folders:

```bat
python tools\run_raw_sds_matrix_5cases.py ^
  --data-root D:\MSeed_Test\Data ^
  --result-root D:\MSeed_Test\Result\raw_sds_matrix ^
  --limit 14
```

Benchmark repeated ingestion into an existing SDS:

```bat
python tools\benchmark_append_vs_hybrid_existing_sds.py ^
  --input-root D:\MSeed_Test\Data\shi201001.month ^
  --result-root D:\MSeed_Test\Result\shi201001_benchmark ^
  --iterations 5
```

Compare two existing SDS archives:

```bat
python tools\compare_sds_gaps.py ^
  --sds-a D:\ReferenceSDS ^
  --sds-b D:\CandidateSDS ^
  --a-label reference ^
  --b-label candidate ^
  --report D:\SDS_gap_compare ^
  --allow-differences
```

See [tools/README.md](tools/README.md) for the full tools guide.

## Folder Guide

```text
apps/yfile2mseed_cli/          C++ command-line tools.
include/yfile2miniseed/        Public C++ library headers.
src/                           C++ library implementation.
python/yfile2obspy_cpp/        Python extension wrapping the C++ Y-file reader.
tools/                         Hybrid converter, ObsPy builders, reports, benchmarks.
docs/                          Development, CI, and release notes.
tests/                         CTest and ObsPy validation tests.
out/                           Local build output. Ignored by git.
logs/                          Runtime logs. Ignored by git.
```

Useful files:

```text
tools/yfiles_to_mseed_sds_hybrid_cppread.py  Primary SDS builder.
tools/yfiles_to_mseed_sds_obspy.py           Pure Python/ObsPy reference builder.
tools/sds_obspy_cleanup_inplace.py           Conservative in-place SDS cleanup.
tools/sds_raw_segment_report.py              Raw MiniSEED record segment report.
tools/sds_availability_report.py             SDS availability report.
CorrectSID.txt                               Local SID correction file.
```

`CorrectSID.txt`, runtime logs, build outputs, and generated SDS results are
local/operator-specific and should not be committed.

## Build Notes

Use a Release build for real conversion work and large datasets:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake -S . -B out\build\x64-Release -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF
cmake --build out\build\x64-Release --config Release
```

Run tests:

```bat
ctest --test-dir out\build\x64-Release --output-on-failure
```

Use an x64 Visual Studio Developer Command Prompt. If MSVC reports x86/x64
library conflicts, reopen the x64 prompt, delete the stale build directory, and
configure again.

## More Documentation

- [tools/README.md](tools/README.md): hybrid converter, reports, comparisons,
  and benchmark workflows.
- [python/yfile2obspy_cpp/README.md](python/yfile2obspy_cpp/README.md): Python
  extension install and API notes.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): tests, CI, releases, and
  maintenance notes.

## License

This project is licensed under the Apache License 2.0. See `LICENSE`.
