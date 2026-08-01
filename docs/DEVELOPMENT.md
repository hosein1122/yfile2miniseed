# Development Notes

This file keeps developer-facing information out of the main README.

## Tests

Run all tests from a Visual Studio Developer Command Prompt:

```bat
cd /d D:\C++Code\yfile2miniseed
ctest --test-dir out\build\x64-Release --output-on-failure
```

Current test groups:

```text
compute_okseg       Legacy C++ property/simulation tests for the disabled
                    duplicate/overlap clipping helper.
obspy_steim1_cli    Python/ObsPy CLI validation using STEIM1 MiniSEED input.
compare_mseed_outputs
                    Self-test for the SDS comparison tool.
```

The ObsPy test creates synthetic STEIM1 MiniSEED input, runs the CLI with `-V2`,
then reads the output with ObsPy and compares timing and sample values. These
tests are regression checks for conversion output, not a claim that the C++
converter performs complete overlap cleanup.

If ObsPy is not installed, the ObsPy test is skipped with CTest skip code `77`.

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

## Validation Workflows

For repeatable local validation against another converter or another SDS
archive, prefer the generic comparison tool:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\ExistingSDS ^
  --candidate D:\OurSDS ^
  --report D:\SDS_compare_report ^
  --level medium ^
  --id-mode strict
```

`tools/run_full_availability_case.bat` is a local workflow for one specific
environment. It prepares input folders, runs another converter, runs
`yfile2mseed`, builds availability reports, and compares text reports. Treat it
as a lab workflow, not as the main user-facing interface.

Additional tool notes are kept here:

```text
tools/availability_report_tools.md
tools/compare_mseed_outputs.md
```

## GitHub Actions And Releases

The repository has a Windows CI workflow at:

```text
.github/workflows/windows-ci.yml
```

For each push to `main` and each pull request, GitHub Actions builds the Release
configuration and runs the CTest suite, including ObsPy-based validation tests.

Build outputs are not committed to git. The workflow publishes a downloadable
artifact named:

```text
yfile2miniseed-windows-x64.zip
```

The package contains:

```text
yfile2mseed.exe
yfile2miniseed_lib.lib
include/
tools/
docs/
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
