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
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed_sid_scan.exe D:\inputYFiles -o CorrectSID.txt
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
- Reads MiniSEED input files as well as Y-Files. For example, you can pass a
  folder of standalone MiniSEED files and rewrite them into the SDS layout.
- Scans folders recursively.
- Reads Y-Files stored inside ZIP archives.
- Writes MiniSEED output in SDS layout.
- Builds a `CorrectSID.txt` template from input Y-file headers.
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
tools/                         Validation, reporting, and ObsPy cleanup tools.
docs/                          Development, CI, and maintenance notes.
tests/                         CTest tests, including ObsPy/STEIM1 validation.
cmake/modules/                 Local CMake find modules.
out/                           Local build output. Ignored by git.
logs/                          Runtime logs. Ignored by git.
```

Useful files:

```text
CMakeLists.txt                 Main CMake project.
apps/yfile2mseed_cli/main.cpp  CLI entry point and argument handling.
apps/yfile2mseed_cli/sid_inventory_main.cpp
                               CorrectSID.txt inventory/template builder.
src/mseed_processor.cpp        MiniSEED write/read, SDS export, libmseed repack.
src/yfile_reader.cpp           Y-File reader wrapper.
tools/README.md                Guide to validation and ObsPy cleanup tools.
tools/sds_obspy_cleanup_inplace.py
                               In-place SDS cleanup with ObsPy merge(method=-1).
CorrectSID.txt                 Required local station/channel correction file.
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

## Recommended Workflow

0. Choose the input folder that contains Y-files, ZIP files, or MiniSEED files.
1. Build or update `CorrectSID.txt`, then review it manually:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed_sid_scan.exe ^
  D:\YFiles ^
  -o CorrectSID.txt
```

2. Run the converter:

```bat
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe ^
  D:\YFiles ^
  -o D:\SDS ^
  -V2
```

3. Run ObsPy cleanup with `merge(method=-1)`:

```bat
python tools\sds_obspy_cleanup_inplace.py ^
  --sds-root D:\SDS ^
  --workers 8 ^
  --encoding STEIM2 ^
  --reclen 4096 ^
  --report D:\SDS_cleanup_report
```

4. Optionally compare against another SDS archive:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\ExistingSDS ^
  --candidate D:\SDS ^
  --report D:\SDS_compare_report ^
  --level medium ^
  --id-mode strict
```

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
out\build\x64-Release\apps\yfile2mseed_cli\yfile2mseed.exe D:\LooseMSeedFiles -o D:\SDS -V2
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

## Compare Two SDS Archives

Use `tools/compare_mseed_outputs.py` when you want to compare two SDS folders,
for example one generated by this project and another generated by a different
program:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\ExistingSDS ^
  --candidate D:\OurSDS ^
  --report D:\SDS_compare_report ^
  --level medium ^
  --id-mode strict
```

For a smaller subset, use `--level deep`. Deep comparison reads sample data and
compares ObsPy-cleaned streams using `Stream.merge(method=-1)`:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\ExistingSDS ^
  --candidate D:\OurSDS ^
  --report D:\SDS_compare_deep ^
  --level deep ^
  --station TST ^
  --max-deep-samples 2000000
```

The report folder contains:

```text
comparison_summary.csv
comparison_summary.json
mismatches.txt
```

See [tools/README.md](tools/README.md) for the full tools guide.

## CorrectSID.txt

The converter uses `CorrectSID.txt` for local source identifier corrections.
Run `yfile2mseed_sid_scan` before conversion to build a template from the input
Y-file headers. Existing entries are preserved, so you can safely rerun the scan
after adding new input files.

Each line maps the raw SID fields from the input file to the corrected values:

```text
RAWNET_RAWSTA_RAWLOC_RAWCHA => NET_STA_LOC_CHA
```

The converter no longer waits for keyboard input when it sees a new SID. If
`CorrectSID.txt` is missing, empty, or does not contain a needed mapping, it
prints an error and stops. This prevents long unattended runs from silently
waiting for user input.

The file is ignored by git because it is local/operator-specific.

## More Documentation

- [tools/README.md](tools/README.md): validation, comparison, and ObsPy cleanup
  tools.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): tests, CI, release packaging, and
  maintenance notes.

## License

This project is licensed under the Apache License 2.0. See `LICENSE`.
