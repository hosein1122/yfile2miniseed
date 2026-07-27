# yfile2miniseed

`yfile2miniseed` converts Nanometrics Y-File version 5 data into MiniSEED and
writes the result in an SDS-style folder layout. It can also read existing
MiniSEED input and rewrite it into the same SDS output tree.

The main target platform is Windows with Visual Studio/MSVC.

## Quick Start

From a Visual Studio Developer Command Prompt:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake --build out\build\x64-Debug --config Debug
out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe D:\inputYFiles -o D:\OutputStore -V2
```

Use `-V2` when you need MiniSEED 2 output, for example when validating with
ObsPy. Without `-V2`, the converter writes MiniSEED 3.

## What This Project Does

- Reads Nanometrics Y-File version 5 records.
- Reads MiniSEED input files as well as Y-Files.
- Scans folders recursively.
- Reads Y-Files stored inside ZIP archives.
- Writes MiniSEED output in SDS layout.
- Avoids writing duplicate samples when output files already contain the same
  time range.
- Builds a reusable static library plus a command-line executable.

## Folder Guide

```text
apps/yfile2mseed_cli/          Command-line application.
include/yfile2miniseed/        Public library headers.
include/yfile2miniseed/detail/ Y-File v5 parser internals.
src/                           Library implementation.
tests/                         CTest tests, including ObsPy/STEIM1 validation.
cmake/modules/                 Local CMake find modules.
out/                           Local build output. Ignored by git.
logs/                          Runtime logs. Ignored by git.
```

Useful files:

```text
CMakeLists.txt                 Main CMake project.
apps/yfile2mseed_cli/main.cpp  CLI entry point and argument handling.
src/mseed_processor.cpp        MiniSEED write/read, SDS export, dedup logic.
src/yfile_reader.cpp           Y-File reader wrapper.
CorrectSID.txt                 Optional local station/channel correction file.
error_files.txt                Runtime list of files that could not be processed.
```

`CorrectSID.txt`, `error_files.txt`, `logs/`, and build outputs are intentionally
ignored by git because they are machine/runtime-specific.

## Build

The project uses CMake and downloads/builds dependencies with `FetchContent`:

- libmseed
- spdlog
- zlib
- libzip

Open a Visual Studio Developer Command Prompt and run:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake --build out\build\x64-Debug --config Debug
```

If the build directory does not exist yet, configure first:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake -S . -B out\build\x64-Debug -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build out\build\x64-Debug --config Debug
```

If you see an error like `cannot open file 'kernel32.lib'`, the command was not
started from the Visual Studio Developer Command Prompt. Start that prompt and
run the build again.

## Run The Converter

General form:

```bat
yfile2mseed.exe inputPath [-o outputDir] [-R minRamGb] [-V2] [-h]
```

Examples:

```bat
out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe D:\YFiles -o D:\SDS
out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe D:\YFiles -o D:\SDS -V2
out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe D:\one_file.mseed -o D:\SDS -V2
```

Arguments:

```text
inputPath      Input folder or single file.
-o outputDir   Output SDS root. Default: OutPutStore
-R minRamGb    Approximate RAM threshold before flushing data. Default: 2
-V2            Write MiniSEED 2 instead of MiniSEED 3.
-h             Show help.
```

The output path uses SDS-style folders:

```text
outputRoot/YYYY/NET/STA/CHA.D/NET.STA.LOC.CHA.D.YYYY.DDD.mseed
```

Example:

```text
D:\SDS\2020\IR\TST\BHZ.D\IR.TST..BHZ.D.2020.001.mseed
```

## CorrectSID.txt

The CLI uses `CorrectSID.txt` for local source identifier corrections. Keep this
file next to the executable or run the converter from a folder where the file is
available.

The file is ignored by git because it is local/operator-specific.

## Tests

Run all tests:

```bat
cd /d D:\C++Code\yfile2miniseed
ctest --test-dir out\build\x64-Debug --output-on-failure
```

Current test groups:

```text
compute_okseg       C++ property/simulation tests for duplicate/overlap logic.
obspy_steim1_cli    Python/ObsPy CLI validation using STEIM1 MiniSEED input.
```

The ObsPy test creates synthetic STEIM1 MiniSEED input, runs the CLI with `-V2`,
then reads the output with ObsPy and compares timing and sample values. It covers:

- single-file round trip
- rerunning the same input into an existing output folder
- gap
- overlap
- duplicate input
- same start time with different lengths
- reverse input order
- midnight/day split
- cross-day overlap
- many small segments

If ObsPy is not installed, the ObsPy test is skipped with CTest skip code `77`.
On this development machine, ObsPy 1.5.0 is installed and the test passes.

## Real Data Validation Against A Reference Converter

For operational confidence, convert the same Y-File dataset with two independent
paths:

```text
reference output  National-center converter output
candidate output  yfile2miniseed output
judge             tools/compare_mseed_outputs.py using ObsPy
```

Recommended folder layout:

```text
D:\MSeed_Test\Validation
  input_yfiles_copy\
  output_center\
  output_new\
  reports\
```

Important: do not point `yfile2miniseed` at a live `FilesToConvert` or `Buffer`
folder from the center converter. That workflow moves and deletes files while it
runs. Use a separate, immutable copy of the raw Y-File month.

Run the new converter on the copied input:

```bat
out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe ^
  D:\MSeed_Test\Validation\input_yfiles_copy ^
  -o D:\MSeed_Test\Validation\output_new ^
  -V2
```

After the center converter has finished, compare the two MiniSEED outputs.

Fast header-level check for a full month:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\simple ^
  --level simple ^
  --id-mode component ^
  --default-network IR
```

Medium coverage/gap/overlap check:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\medium ^
  --level medium ^
  --id-mode component ^
  --default-network IR
```

Deep sample-by-sample check for a smaller subset:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\deep_SHI ^
  --level deep ^
  --station SHI ^
  --id-mode component ^
  --default-network IR ^
  --max-deep-samples 2000000
```

Report files:

```text
comparison_summary.csv   Spreadsheet-friendly per-trace-key result.
comparison_summary.json  Full structured report.
mismatches.txt           Short list of differences.
```

Use `--id-mode strict` when metadata must match exactly. Use
`--id-mode component` when comparing against the old center converter, because
that code may rewrite channels such as `BHZ` or `SPZ` to `SHZ`; component mode
matches by network, station, location, and final channel letter.

## Sanity Checklist Before Push

Run these before committing important changes:

```bat
cmake --build out\build\x64-Debug --config Debug
ctest --test-dir out\build\x64-Debug --output-on-failure
git status --short --branch
```

Expected result:

```text
100% tests passed
```

Also check that no generated runtime files are accidentally staged:

```text
out/
logs/
CorrectSID.txt
error_files.txt
*.exe
*.dll
*.lib
```

## Migration Notes

This repository is the cleaned-up CMake version of older local Visual Studio
work. During migration, the useful historical references were:

```text
D:\C++Code\HF_Pars_YFile
D:\C++Code\Obspy\Test_HF_Y2MSeed
D:\C++Code\Y5Dump
```

The old ObsPy test scenarios were folded into the current `tests/` suite in a
cleaner, assert-based form.

## License

This project is licensed under the Apache License 2.0. See `LICENSE`.
