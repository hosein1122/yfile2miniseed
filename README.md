# yfile2miniseed

A robust C++ tool and library for converting **Nanometrics Y-File (ver. 5)** seismology data to standard **MiniSEED** (v2 & v3),  
built on top of the [libmseed](https://github.com/EarthScope/libmseed) library.

Target platform: **Windows (Visual Studio)**.  
Primary audience: **seismology / geophysics** workflows that need reliable, reproducible conversion from raw Y-File data to MiniSEED and SDS.

---

## Project Status

> ⚠️ **Work in progress**

This repository is under active development:

- Existing functional code is being **cleaned up**, refactored and migrated into this repository.
- A clean Visual Studio solution and project structure will be provided.
- Documentation, examples, and tests will be added iteratively.

---

## Key Features

- **Y-File (Nanometrics, v5) → MiniSEED v2/v3**
  - Converts local Nanometrics Y-File (version 5) seismic data to MiniSEED.
  - Supports both **MiniSEED 2** and **MiniSEED 3** output formats via `libmseed`.

- **Batch & Recursive Processing**
  - Processes entire directory trees (nested folders) recursively.
  - Suitable for large archives of Y-File data.

- **ZIP Archive Support**
  - Automatically detects and reads Y-Files that are stored inside **ZIP archives**.
  - Can mix plain Y-Files and zipped Y-Files within the same folder structure.

- **Standard SDS Output**
  - Writes MiniSEED output organized in a standard **SDS (Seismological Data Structure)** directory layout,  
    facilitating integration with existing seismic data centers and workflows.

- **Automatic De-duplication**
  - Assumes input data are raw, unmodified records.
  - Uses sample timestamps to detect overlapping / repeated data segments.
  - Only **new, non-duplicate** portions are written to the final MiniSEED,  
    preserving data integrity and avoiding redundant storage.

- **Tool + Library**
  - Distributed as:
    - A **command-line executable (CLI)** for end users and batch processing.
    - A **reusable C++ library** that can be linked into other scientific or processing pipelines.

---

## Why this project?

To the best of our knowledge, there is no single, integrated tool that:

- Understands **Nanometrics Y-File (v5)**,
- Handles **nested directory trees** and **ZIP-based Y-Files**,
- Produces MiniSEED **v2 and v3** in **SDS** layout, and
- Performs **automatic de-duplication** based on sample timestamps,

all in one place.

`yfile2miniseed` aims to fill this gap for the seismology / geophysics community with a clean, well-documented, and reproducible implementation.

---

## Dependencies

- [**libmseed**](https://github.com/EarthScope/libmseed)  
  Used as the underlying library for reading/writing MiniSEED (v2/v3).

Details on how to build and link `libmseed` (static or dynamic) with this project on Windows / Visual Studio will be documented once the project structure is finalized.

---

## Build (planned, Windows / Visual Studio)

> Detailed build instructions will be added later.

Planned layout:

- **Compiler/IDE**: Microsoft Visual Studio (MSVC)
- **Targets**:
  - `yfile2miniseed-lib` – core C++ library (Y-File → MiniSEED logic, IO, deduplication).
  - `yfile2miniseed-cli` – command-line tool using the library.

---

## License

This project is licensed under the **Apache License 2.0**.  
See the [LICENSE](./LICENSE) file for details.

---

## Acknowledgements

This project builds on:

- [`libmseed`](https://github.com/EarthScope/libmseed) – MiniSEED reading/writing library.

Contributions from the seismology / geophysics community are welcome once the initial refactoring is complete.
