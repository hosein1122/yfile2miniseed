# yfile2miniseed

`yfile2miniseed` converts Nanometrics Y-File version 5 data into MiniSEED and
writes the result in an SDS-style folder layout. It can also read existing
MiniSEED input and rewrite it into the same SDS output tree.

The converter intentionally keeps the C++ conversion path simple: it slices data
into daily MiniSEED files and does not run project-owned overlap cleanup or full
deduplication. Duplicate handling in this stage is limited to libmseed
read/repack behavior for adjacent duplicate records. Any deeper overlap cleanup
or sample-aware deduplication should be done in a separate ObsPy post-processing
step.

The main target platform is Windows with Visual Studio/MSVC.

## Quick Start

From a Visual Studio Developer Command Prompt:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake -S . -B out\build\x64-Release -DCMAKE_BUILD_TYPE=Release
cmake --build out\build\x64-Release --config Release
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe D:\inputYFiles -o D:\OutputStore -V2
```

Use `-V2` when you need MiniSEED 2 output, for example when validating with
ObsPy. Without `-V2`, the converter writes MiniSEED 3.

Use `-V2` for any workflow that uses the ObsPy tools in `tools/`, including
post-conversion duplicate/overlap cleanup. ObsPy's built-in MiniSEED support is
for MiniSEED 2 on this development setup; MiniSEED 3 requires separate plugin
support that is not assumed by this project.

## What This Project Does

- Reads Nanometrics Y-File version 5 records.
- Reads MiniSEED input files as well as Y-Files.
- Scans folders recursively.
- Reads Y-Files stored inside ZIP archives.
- Writes MiniSEED output in SDS layout.
- Uses libmseed read/repack behavior to skip adjacent duplicate MiniSEED
  records.
- Does not perform full deduplication or overlap cleanup in project C++ code.
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
src/mseed_processor.cpp        MiniSEED write/read, SDS export, libmseed repack.
src/yfile_reader.cpp           Y-File reader wrapper.
tools/README.md                Guide to validation and ObsPy cleanup tools.
tools/sds_obspy_cleanup_inplace.py
                               In-place SDS cleanup with ObsPy merge(method=-1).
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

Use a Release build for real conversion work and large datasets. Release is
optimized and is the recommended build for operational validation. Use Debug
when changing code, investigating crashes, or stepping through the program in a
debugger.

### Release Build

Open a Visual Studio Developer Command Prompt and run:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake -S . -B out\build\x64-Release -DCMAKE_BUILD_TYPE=Release
cmake --build out\build\x64-Release --config Release
```

The Release executable will be here:

```text
D:\C++Code\yfile2miniseed\out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe
```

Run the Release test suite:

```bat
cd /d D:\C++Code\yfile2miniseed
ctest --test-dir out\build\x64-Release --output-on-failure
```

### Debug Build

Use this build when developing or debugging:

```bat
cd /d D:\C++Code\yfile2miniseed
cmake -S . -B out\build\x64-Debug -DCMAKE_BUILD_TYPE=Debug
cmake --build out\build\x64-Debug --config Debug
```

The Debug executable will be here:

```text
D:\C++Code\yfile2miniseed\out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe
```

Run the Debug test suite:

```bat
cd /d D:\C++Code\yfile2miniseed
ctest --test-dir out\build\x64-Debug --output-on-failure
```

If you see an error like `cannot open file 'kernel32.lib'`, the command was not
started from the Visual Studio Developer Command Prompt. Start that prompt and
run the build again.

If you explicitly want to use Ninja, install Ninja or use the one bundled with
Visual Studio, then add `-G Ninja` to the configure command.

فارسی کوتاه: برای تبدیل واقعی داده‌ها و حجم بالا از Release استفاده کنید. Debug
برای توسعه و پیدا کردن خطاست و معمولا کندتر اجرا می‌شود.

## Run The Converter

General form:

```bat
yfile2mseed.exe inputPath [-o outputDir] [-R minRamGb] [-V2] [-h]
```

Examples:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe D:\YFiles -o D:\SDS
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe D:\YFiles -o D:\SDS -V2
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe D:\one_file.mseed -o D:\SDS -V2
```

Arguments:

```text
inputPath      Input folder or single file.
-o outputDir   Output SDS root. Default: OutPutStore
-R minRamGb    Approximate RAM threshold before flushing data. Default: 2
-V2            Write MiniSEED 2 instead of MiniSEED 3. Recommended before
               running ObsPy tools.
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

## ObsPy SDS Cleanup

After conversion, use `tools/sds_obspy_cleanup_inplace.py` when you want ObsPy
to remove compatible duplicates and safe overlaps in the SDS archive itself:

```bat
python tools\sds_obspy_cleanup_inplace.py ^
  --sds-root D:\SDS ^
  --workers 8 ^
  --encoding STEIM2 ^
  --reclen 4096 ^
  --report D:\SDS_cleanup_report
```

Start with `--dry-run` on a new dataset. The cleanup tool rewrites files in
place; it does not copy the SDS tree to a different output folder. Use
`--backup-suffix .bak` if you want original files preserved next to the cleaned
files.

This tool uses ObsPy `Stream.merge(method=-1)`, which is conservative. It does
not force conflicting overlap sample values into a single trace. For this
workflow, generate MiniSEED 2 with `-V2`; plain ObsPy does not handle MiniSEED 3
in this project setup.

## CorrectSID.txt

The CLI uses `CorrectSID.txt` for local source identifier corrections. Keep this
file next to the executable or run the converter from a folder where the file is
available.

The file is ignored by git because it is local/operator-specific.

## Tests

Run all tests:

```bat
cd /d D:\C++Code\yfile2miniseed
ctest --test-dir out\build\x64-Release --output-on-failure
```

Current test groups:

```text
compute_okseg       Legacy C++ property/simulation tests for the disabled
                    duplicate/overlap clipping helper.
obspy_steim1_cli    Python/ObsPy CLI validation using STEIM1 MiniSEED input.
```

The ObsPy test creates synthetic STEIM1 MiniSEED input, runs the CLI with `-V2`,
then reads the output with ObsPy and compares timing and sample values. These
tests are regression checks for conversion output, not a claim that the C++
converter performs complete overlap cleanup. They cover:

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

For repeatable case-by-case validation against the national-center converter,
use the batch workflow in `tools/run_full_availability_case.bat`.

Example:

```bat
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2
```

This processes every Y-File under the input folder, runs both converters, and
writes the case result under:

```text
D:\MSeed_Test\Result\Kaz_Test2
```

Main outputs:

```text
center_output\
our_output\
yfile_availability.txt
center_availability.txt
center_normalized_availability.txt
our_availability.txt
center_normalized_vs_our_differences.txt
```

Use optional filters when needed:

```bat
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2 KAZ SP
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2 ALL ALL
```

See `tools/availability_report_tools.md` for the Persian guide.

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
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe ^
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
cmake --build out\build\x64-Release --config Release
ctest --test-dir out\build\x64-Release --output-on-failure
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

## GitHub Actions And Releases

The repository has a Windows CI workflow at
`.github/workflows/windows-ci.yml`.

For each push to `main` and each pull request, GitHub Actions builds the
Release configuration and runs the full CTest suite, including the ObsPy-based
validation tests.

Build outputs are not committed to git. Instead, the workflow publishes a
downloadable artifact named `yfile2miniseed-windows-x64.zip`. The package
contains:

```text
yfile2mseed.exe
yfile2miniseed_lib.lib
include/
tools/
README.md
LICENSE
```

To create an official downloadable GitHub Release, tag a tested commit with a
version tag such as:

```bat
git tag v1.2.0
git push origin v1.2.0
```

When the tag starts with `v`, GitHub Actions builds the Windows x64 Release
package and attaches the zip file to that GitHub Release.

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
