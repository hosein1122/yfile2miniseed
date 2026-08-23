#!/usr/bin/env python3
"""Hybrid SDS builder: C++ reads Y-files, ObsPy handles merge/pack/SDS."""

from __future__ import annotations

import argparse
import copy
import gc
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import obspy
from obspy import Stream, Trace, UTCDateTime

import yfiles_to_mseed_sds_obspy as base


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PY_BINDINGS_SRC = REPO_ROOT / "python" / "yfile2obspy_cpp" / "src"
DEFAULT_CORRECT_SID = SCRIPT_DIR / "CorrectSID.txt"


BRIDGE_MAGIC = b"Y2OBSBR1\n"

try:
    import yfile2obspy_cpp
except ImportError:
    if PY_BINDINGS_SRC.exists():
        sys.path.insert(0, str(PY_BINDINGS_SRC))
    try:
        import yfile2obspy_cpp
    except ImportError:
        yfile2obspy_cpp = None


def default_bridge_exe() -> Path:
    local = SCRIPT_DIR / "yfile2obspy_bridge.exe"
    if local.exists():
        return local
    return Path("yfile2obspy_bridge.exe")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read Nanometrics Y-files with the C++ reader, then use ObsPy to "
            "merge, pack, and write strict SDS MiniSEED."
        )
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--bridge-exe",
        type=Path,
        default=default_bridge_exe(),
        help="Compatibility fallback used only when yfile2obspy_cpp is not importable.",
    )
    parser.add_argument("--network", help="Fallback network if --no-correct-sid is used.")
    parser.add_argument("--encoding", default="STEIM2", choices=("STEIM1", "STEIM2", "INT32"))
    parser.add_argument("--record-length", type=int, default=4096)
    parser.add_argument("--pattern", default="*")
    parser.add_argument("--station", help="Override station on all traces.")
    parser.add_argument("--location", help="Override location on all traces. Use --location= for empty.")
    parser.add_argument("--channel", help="Override channel on all traces.")
    parser.add_argument("--correct-sid", type=Path, default=DEFAULT_CORRECT_SID)
    parser.add_argument("--no-correct-sid", action="store_true")
    parser.add_argument("--report", type=Path, help="Optional JSON report path outside output-root.")
    parser.add_argument(
        "--error-log",
        type=Path,
        help=(
            "Path for skipped Y-file read errors. Default: "
            "<output-root>/_yfile_read_errors.log"
        ),
    )
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Print selected stage timings to the console. JSON reports remain detailed.",
    )
    parser.add_argument("--pack-backend", choices=("auto", "memory", "disk"), default="auto")
    parser.add_argument("--pack-workers", type=int, default=4)
    parser.add_argument(
        "--memory-reserve-mb",
        type=int,
        default=0,
        help=(
            "Physical RAM to keep available before automatic spill-to-disk. "
            "0 = auto: max(2048 MB, 10%% of physical RAM), capped at 50%%."
        ),
    )
    parser.add_argument(
        "--memory-spill-chunk-mb",
        type=int,
        default=256,
        help=(
            "Maximum raw int32 trace bytes drained per spill/merge chunk. "
            "Default: 256 MB."
        ),
    )
    args = parser.parse_args()

    if args.progress_every < 1:
        parser.error("--progress-every must be at least 1")
    if args.pack_workers < 1:
        parser.error("--pack-workers must be at least 1")
    if args.pack_backend == "disk" and args.pack_workers > 1:
        parser.error("--pack-workers greater than 1 requires memory/auto packing")
    if args.memory_reserve_mb < 0:
        parser.error("--memory-reserve-mb must be >= 0")
    if args.memory_spill_chunk_mb < 16:
        parser.error("--memory-spill-chunk-mb must be at least 16")
    return args


def read_exact(handle, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = handle.read(remaining)
        if not chunk:
            raise EOFError(f"bridge output ended while reading {size} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_u32(handle) -> int:
    return int.from_bytes(read_exact(handle, 4), "little", signed=False)


def read_u64(handle) -> int:
    return int.from_bytes(read_exact(handle, 8), "little", signed=False)


def should_print_bridge_progress(args: argparse.Namespace, index: int, total: int) -> bool:
    if args.quiet:
        return False
    return index == 1 or index == total or index % args.progress_every == 0


def print_bridge_progress(
    args: argparse.Namespace,
    timings: dict[str, float],
    index: int,
    total_sources: int,
    bytes_done: int,
    total_bytes: int,
    message: str,
) -> None:
    if not should_print_bridge_progress(args, index, total_sources):
        return

    if total_bytes > 0:
        percentage = max(0.0, min(100.0, bytes_done * 100.0 / total_bytes))
    elif total_sources > 0:
        percentage = max(0.0, min(100.0, index * 100.0 / total_sources))
    else:
        percentage = 100.0

    stage_started = time.perf_counter()
    print(f"[{percentage:6.2f}%] [{index}/{total_sources}] {message}")
    base.add_timing(timings, "console_progress", stage_started)


def bridge_command(args: argparse.Namespace, input_root: Path) -> list[str]:
    command = [
        str(args.bridge_exe),
        "--input-root",
        str(input_root),
        "--pattern",
        args.pattern,
    ]
    command.append("--recursive")
    return command


def stderr_text(process: subprocess.Popen) -> str:
    data = process.stderr.read() if process.stderr else b""
    return data.decode("utf-8", errors="replace")


def read_stream_from_bridge(
    args: argparse.Namespace,
    input_root: Path,
    corrections: dict[str, tuple[str, str, str, str]] | None,
    timings: dict[str, float],
) -> tuple[Stream, dict[str, int]]:
    command = bridge_command(args, input_root)
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"C++ bridge executable not found: {args.bridge_exe}. "
            "Build yfile2obspy_bridge or pass --bridge-exe."
        ) from exc

    if process.stdout is None:
        raise RuntimeError("bridge stdout pipe was not created")

    stream = Stream()
    trace_count = 0
    sample_count = 0
    zip_member_count = 0
    plain_count = 0
    total_sources = 0
    total_source_bytes = 0
    source_bytes_done = 0

    try:
        magic = read_exact(process.stdout, len(BRIDGE_MAGIC))
        if magic != BRIDGE_MAGIC:
            raise RuntimeError(f"unexpected bridge magic: {magic!r}")

        header_len = read_u32(process.stdout)
        if header_len == 0:
            raise RuntimeError("C++ bridge returned no traces")
        first_header = json.loads(read_exact(process.stdout, header_len).decode("utf-8"))

        pending_header = None
        if first_header.get("kind") == "run":
            total_sources = int(first_header.get("total_sources", 0))
            total_source_bytes = int(first_header.get("total_source_bytes", 0))
        else:
            pending_header = first_header
            total_sources = 1

        while True:
            if pending_header is not None:
                header = pending_header
                pending_header = None
            else:
                header_len = read_u32(process.stdout)
                if header_len == 0:
                    break
                header = json.loads(read_exact(process.stdout, header_len).decode("utf-8"))

            stage_started = time.perf_counter()
            sample_bytes = read_u64(process.stdout)
            payload = read_exact(process.stdout, sample_bytes)
            base.add_timing(timings, "bridge_payload_read", stage_started)

            npts = int(header["npts"])
            expected_bytes = npts * np.dtype("<i4").itemsize
            if sample_bytes != expected_bytes:
                raise RuntimeError(
                    f"{header.get('source', '<unknown>')}: sample byte count mismatch: "
                    f"{sample_bytes} != {expected_bytes}"
                )

            stage_started = time.perf_counter()
            data = np.frombuffer(payload, dtype="<i4", count=npts).copy()
            trace = Trace(data=data)
            trace.stats.network = str(header.get("network", ""))
            trace.stats.station = str(header.get("station", ""))
            trace.stats.location = str(header.get("location", ""))
            trace.stats.channel = str(header.get("channel", ""))
            trace.stats.starttime = UTCDateTime(ns=int(header["start_ns"]), precision=9)
            trace.stats.sampling_rate = float(header["sample_rate"])
            current = Stream([trace])
            base.add_timing(timings, "bridge_trace_build", stage_started)

            stage_started = time.perf_counter()
            base.apply_metadata(current, args, str(header.get("source", "")), corrections)
            base.add_timing(timings, "metadata_apply", stage_started)

            stage_started = time.perf_counter()
            stream += current
            base.add_timing(timings, "stream_append", stage_started)

            source = str(header.get("source", ""))
            if "!/" in source:
                zip_member_count += 1
            else:
                plain_count += 1

            trace_count += 1
            sample_count += npts
            source_bytes_done += int(header.get("source_bytes", 0))
            if total_sources <= 0:
                total_sources = trace_count
            print_bridge_progress(
                args,
                timings,
                trace_count,
                total_sources,
                source_bytes_done,
                total_source_bytes,
                f"C++ read {source}",
            )

    except Exception:
        process.kill()
        process.wait()
        err = stderr_text(process)
        if err:
            raise RuntimeError(err.strip()) from None
        raise

    returncode = process.wait()
    err = stderr_text(process)
    if returncode != 0:
        raise RuntimeError(err.strip() or f"bridge failed with exit code {returncode}")

    timings["bridge_read_wall"] = time.perf_counter() - started
    return stream, {
        "files_read": trace_count,
        "plain_files_read": plain_count,
        "zip_members_read": zip_member_count,
        "rar_members_read": 0,
        "samples_read": sample_count,
        "total_source_bytes": total_source_bytes,
    }


def trace_from_cpp_record(record: dict) -> Trace:
    data = np.asarray(record["samples"], dtype=np.int32)
    if not data.flags.c_contiguous:
        data = np.ascontiguousarray(data)

    trace = Trace(data=data)
    trace.stats.network = str(record.get("network", ""))
    trace.stats.station = str(record.get("station", ""))
    trace.stats.location = str(record.get("location", ""))
    trace.stats.channel = str(record.get("channel", ""))
    trace.stats.starttime = UTCDateTime(ns=int(record["start_ns"]), precision=9)
    trace.stats.sampling_rate = float(record["sample_rate"])
    return trace



GIB = 1024 ** 3
MIB = 1024 ** 2


def physical_memory_status() -> tuple[int, int]:
    """
    Return (total_physical_bytes, available_physical_bytes).

    Windows uses GlobalMemoryStatusEx directly, so no third-party dependency
    such as psutil is required. A POSIX fallback is included for portability.
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("GlobalMemoryStatusEx failed")
        return int(status.ullTotalPhys), int(status.ullAvailPhys)

    # Portable fallback for Linux/macOS-like environments.
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        total_pages = int(os.sysconf("SC_PHYS_PAGES"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        return page_size * total_pages, page_size * available_pages
    except (AttributeError, ValueError, OSError):
        # Last-resort conservative values; memory spill still has the
        # MemoryError safety net.
        return 8 * GIB, 4 * GIB


def resolve_memory_reserve_bytes(args: argparse.Namespace, total_bytes: int) -> int:
    if args.memory_reserve_mb > 0:
        requested = int(args.memory_reserve_mb) * MIB
    else:
        requested = max(2 * GIB, int(total_bytes * 0.10))

    # Never reserve more than half of physical RAM, and keep at least
    # 512 MB on very small systems.
    return min(requested, max(512 * MIB, total_bytes // 2))


def format_bytes(value: int) -> str:
    value = int(value)
    if value >= GIB:
        return f"{value / GIB:.2f} GiB"
    if value >= MIB:
        return f"{value / MIB:.1f} MiB"
    return f"{value} B"


def stream_raw_bytes(stream: Stream) -> int:
    return sum(int(getattr(trace.data, "nbytes", 0)) for trace in stream)


def make_spill_args(args: argparse.Namespace) -> argparse.Namespace:
    """
    Temporary spill SDS is intentionally INT32 and sequential.

    It is lossless, avoids STEIM range failures during emergency spilling, and
    avoids creating a second large parallel packed-result list while RAM is low.
    """
    spill_args = copy.copy(args)
    spill_args.encoding = "INT32"
    spill_args.pack_workers = 1
    spill_args.pack_backend = "auto"
    return spill_args


def load_overlay_existing_for_new_data(
    overlay_root: Path,
    fallback_root: Path | None,
    new_stream: Stream,
    timings: dict[str, float],
) -> tuple[Stream, set[Path], set[Path], int]:
    """
    Copy-on-write SDS read.

    Prefer an already modified file in overlay_root. If it does not exist yet,
    read the corresponding file from fallback_root (normally the real SDS
    output tree).
    """
    overlay_paths = affected_sds_paths(overlay_root, new_stream)
    existing = Stream()
    fallback_files_read: set[Path] = set()
    traces_read = 0

    stage_started = time.perf_counter()
    for overlay_path in sorted(overlay_paths):
        relative = overlay_path.relative_to(overlay_root)

        if overlay_path.exists():
            source_path = overlay_path
        elif fallback_root is not None:
            candidate = fallback_root / relative
            if candidate.exists():
                source_path = candidate
                fallback_files_read.add(candidate)
            else:
                continue
        else:
            continue

        part = obspy.read(str(source_path), format="MSEED", check_compression=False)
        existing += part
        traces_read += len(part)

    base.add_timing(timings, "existing_sds_read", stage_started)
    return existing, overlay_paths, fallback_files_read, traces_read


def flush_stream_to_overlay(
    stream: Stream,
    overlay_root: Path,
    args: argparse.Namespace,
    timings: dict[str, float],
    fallback_root: Path | None = None,
) -> dict:
    """
    Merge one manageable Stream chunk into a copy-on-write SDS overlay.

    The function only commits complete staged files, so a failed chunk does not
    partially replace the working SDS files.
    """
    if not stream:
        return {
            "write_stats": None,
            "backend_state": None,
            "replaced_files": 0,
            "fallback_files_read": set(),
            "existing_traces_read": 0,
            "candidate_paths": set(),
            "merged_samples": 0,
            "merged_trace_count": 0,
        }

    existing, candidate_paths, fallback_files_read, existing_traces_read = (
        load_overlay_existing_for_new_data(
            overlay_root,
            fallback_root,
            stream,
            timings,
        )
    )

    # A new Stream container avoids altering the caller's trace list. No sample
    # array copy is made here.
    combined = Stream(traces=list(stream.traces))
    if existing:
        combined += existing

    stage_started = time.perf_counter()
    combined.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    base.add_timing(timings, "sort_before_merge", stage_started)

    stage_started = time.perf_counter()
    combined.merge(method=-1)
    base.add_timing(timings, "merge_method_minus_1", stage_started)

    stage_started = time.perf_counter()
    combined.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    base.add_timing(timings, "sort_after_merge", stage_started)

    merged_samples = sum(int(trace.stats.npts) for trace in combined)

    with tempfile.TemporaryDirectory(prefix="yfile_hybrid_overlay_stage_") as stage_temp:
        staging_root = Path(stage_temp)
        write_stats, backend_state = pack_and_write_sds(
            combined,
            args,
            staging_root,
            timings,
        )
        replaced_files = replace_staged_sds_files(
            staging_root,
            overlay_root,
            write_stats["record_counts"],
            timings,
        )

    result = {
        "write_stats": write_stats,
        "backend_state": backend_state,
        "replaced_files": replaced_files,
        "fallback_files_read": fallback_files_read,
        "existing_traces_read": existing_traces_read,
        "candidate_paths": candidate_paths,
        "merged_samples": merged_samples,
        "merged_trace_count": len(combined),
    }

    del combined
    del existing
    return result


def drain_batch_to_overlay(
    batch: Stream,
    overlay_root: Path,
    args: argparse.Namespace,
    timings: dict[str, float],
    fallback_root: Path | None = None,
) -> dict:
    """
    Destructively drain a large in-RAM batch into the SDS overlay in bounded
    raw-data chunks. Trace objects are removed from batch before each flush so
    RAM is released progressively.
    """
    max_chunk_bytes = int(args.memory_spill_chunk_mb) * MIB
    spill_args = make_spill_args(args) if fallback_root is None else copy.copy(args)

    # Final copy-on-write integration also stays sequential to keep RAM bounded.
    if fallback_root is not None:
        spill_args.pack_workers = 1

    stats = {
        "flush_chunks": 0,
        "traces_flushed": 0,
        "raw_bytes_flushed": 0,
        "records_written_sum": 0,
        "encoding_fallbacks": [],
        "fallback_files_read": set(),
        "existing_traces_read": 0,
        "candidate_paths": set(),
        "backends_used": set(),
        "backend_final": None,
        "backend_fallback_reason": None,
    }

    while batch.traces:
        chunk_count = 0
        chunk_bytes = 0

        for trace in batch.traces:
            trace_bytes = int(getattr(trace.data, "nbytes", 0))
            if chunk_count > 0 and chunk_bytes + trace_bytes > max_chunk_bytes:
                break
            chunk_count += 1
            chunk_bytes += trace_bytes
            if chunk_bytes >= max_chunk_bytes:
                break

        if chunk_count <= 0:
            chunk_count = 1

        chunk_traces = batch.traces[:chunk_count]
        del batch.traces[:chunk_count]
        chunk = Stream(traces=chunk_traces)

        try:
            result = flush_stream_to_overlay(
                chunk,
                overlay_root,
                spill_args,
                timings,
                fallback_root=fallback_root,
            )
        except MemoryError as exc:
            # The chunk is already intentionally small. One forced collection
            # and retry protects against a transient allocator failure.
            gc.collect()
            try:
                result = flush_stream_to_overlay(
                    chunk,
                    overlay_root,
                    spill_args,
                    timings,
                    fallback_root=fallback_root,
                )
            except MemoryError as retry_exc:
                # Put the not-yet-committed traces back at the front. Never
                # silently lose a source because RAM is exhausted.
                batch.traces[0:0] = chunk.traces
                raise RuntimeError(
                    "Out of physical memory while spilling a bounded trace "
                    f"chunk ({format_bytes(chunk_bytes)}). No input trace was "
                    "discarded. Increase --memory-reserve-mb or reduce "
                    "--memory-spill-chunk-mb."
                ) from retry_exc

        write_stats = result["write_stats"]
        backend_state = result["backend_state"]

        stats["flush_chunks"] += 1
        stats["traces_flushed"] += len(chunk)
        stats["raw_bytes_flushed"] += chunk_bytes
        stats["fallback_files_read"].update(result["fallback_files_read"])
        stats["existing_traces_read"] += result["existing_traces_read"]
        stats["candidate_paths"].update(result["candidate_paths"])

        if write_stats is not None:
            stats["records_written_sum"] += int(write_stats["records_written"])
            stats["encoding_fallbacks"].extend(
                write_stats.get("encoding_fallbacks", [])
            )

        if backend_state is not None:
            stats["backends_used"].update(backend_state.used)
            stats["backend_final"] = backend_state.active
            if backend_state.fallback_reason:
                stats["backend_fallback_reason"] = backend_state.fallback_reason

        del chunk
        gc.collect()

    return stats


def read_stream_from_extension(
    args: argparse.Namespace,
    input_root: Path,
    corrections: dict[str, tuple[str, str, str, str]] | None,
    timings: dict[str, float],
    spill_root: Path | None = None,
) -> tuple[Stream, dict]:
    if yfile2obspy_cpp is None:
        raise RuntimeError("yfile2obspy_cpp is not importable")

    sources = base.iter_input_sources(input_root, args.pattern, True)
    if not sources:
        raise RuntimeError(f"no input files matched {args.pattern!r} under {input_root}")

    total_physical, available_physical = physical_memory_status()
    reserve_bytes = resolve_memory_reserve_bytes(args, total_physical)

    print(
        "Memory policy: "
        f"physical={format_bytes(total_physical)}, "
        f"available={format_bytes(available_physical)}, "
        f"reserve={format_bytes(reserve_bytes)}, "
        f"spill-chunk={args.memory_spill_chunk_mb} MiB"
    )

    batch = Stream()
    batch_raw_bytes = 0
    sample_count = 0
    source_bytes_done = 0
    successful_source_bytes = 0
    plain_count = 0
    zip_member_count = 0
    rar_member_count = 0
    read_errors: list[dict] = []
    total_source_bytes = sum(source.uncompressed_size for source in sources)

    spill_used = False
    memory_spill_events = 0
    spill_flush_chunks = 0
    spilled_trace_count = 0
    spilled_raw_bytes = 0
    min_available_bytes = available_physical
    peak_batch_raw_bytes = 0

    if spill_root is None:
        # The caller normally supplies a TemporaryDirectory-backed root whose
        # lifetime covers reading and finalization.
        spill_root = Path(tempfile.mkdtemp(prefix="yfile_hybrid_newdata_"))

    spill_root.mkdir(parents=True, exist_ok=True)

    def current_available() -> int:
        nonlocal min_available_bytes
        _, available = physical_memory_status()
        min_available_bytes = min(min_available_bytes, available)
        return available

    def spill_batch(reason: str) -> None:
        nonlocal spill_used
        nonlocal memory_spill_events
        nonlocal spill_flush_chunks
        nonlocal spilled_trace_count
        nonlocal spilled_raw_bytes
        nonlocal batch_raw_bytes

        if not batch:
            return

        before_bytes = batch_raw_bytes
        available = current_available()

        print(
            "MEMORY SPILL: "
            f"{reason}; batch_traces={len(batch)}, "
            f"batch_raw={format_bytes(before_bytes)}, "
            f"available={format_bytes(available)}, "
            f"reserve={format_bytes(reserve_bytes)}"
        )

        spill_stats = drain_batch_to_overlay(
            batch,
            spill_root,
            args,
            timings,
            fallback_root=None,
        )

        spill_used = True
        memory_spill_events += 1
        spill_flush_chunks += int(spill_stats["flush_chunks"])
        spilled_trace_count += int(spill_stats["traces_flushed"])
        spilled_raw_bytes += int(spill_stats["raw_bytes_flushed"])
        batch_raw_bytes = 0
        gc.collect()

        available_after = current_available()
        print(
            "MEMORY SPILL completed: "
            f"available={format_bytes(available_after)}"
        )

    started = time.perf_counter()
    with base.InputSourceReader() as source_reader:
        for index, source in enumerate(sources, start=1):
            # Proactive spill: do not wait for the next allocation to fail.
            if batch and current_available() <= reserve_bytes:
                spill_batch("available physical RAM reached reserve threshold")

            # ----------------------- source read -----------------------
            record = None
            read_retry = 0

            while True:
                stage_started = time.perf_counter()
                try:
                    if source.is_archive_member:
                        payload, decompress_elapsed = source_reader.read_archive_payload(source)
                        timings["archive_decompress"] += decompress_elapsed
                        try:
                            record = yfile2obspy_cpp.read_yfile_bytes(payload)
                        finally:
                            del payload
                    else:
                        record = yfile2obspy_cpp.read_yfile_path(str(source.container))
                except MemoryError:
                    base.add_timing(timings, "cpp_extension_read", stage_started)

                    if batch:
                        spill_batch(
                            f"MemoryError while reading source [{index}/{len(sources)}]; "
                            "retrying the same source"
                        )
                        read_retry += 1
                        gc.collect()
                        if read_retry <= 2:
                            continue

                    if read_retry == 0:
                        read_retry += 1
                        gc.collect()
                        continue

                    raise RuntimeError(
                        "Out of memory while reading one Y source even after "
                        "spilling all buffered traces. The source was NOT skipped: "
                        f"{source.display_name}"
                    )
                except Exception as exc:
                    base.add_timing(timings, "cpp_extension_read", stage_started)
                    source_bytes_done += source.uncompressed_size
                    read_errors.append(
                        {
                            "index": index,
                            "total": len(sources),
                            "source": source.display_name,
                            "source_bytes": int(source.uncompressed_size),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                    print(
                        f"WARNING: skipped unreadable Y source [{index}/{len(sources)}] "
                        f"{source.display_name}: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
                    print_bridge_progress(
                        args,
                        timings,
                        index,
                        len(sources),
                        source_bytes_done,
                        total_source_bytes,
                        f"skipped unreadable source {source.display_name}",
                    )
                    record = None
                    break
                else:
                    base.add_timing(timings, "cpp_extension_read", stage_started)
                    break

            if record is None:
                continue

            # ----------------------- Trace build -----------------------
            build_retry = 0
            while True:
                try:
                    stage_started = time.perf_counter()
                    current = Stream([trace_from_cpp_record(record)])
                    base.add_timing(timings, "cpp_extension_trace_build", stage_started)

                    stage_started = time.perf_counter()
                    base.apply_metadata(current, args, source.display_name, corrections)
                    base.add_timing(timings, "metadata_apply", stage_started)
                    break
                except MemoryError:
                    if batch:
                        spill_batch(
                            f"MemoryError while building Trace [{index}/{len(sources)}]; "
                            "retrying without rereading the source"
                        )
                        build_retry += 1
                        gc.collect()
                        if build_retry <= 2:
                            continue

                    if build_retry == 0:
                        build_retry += 1
                        gc.collect()
                        continue

                    raise RuntimeError(
                        "Out of memory while constructing one Trace even after "
                        "spilling all buffered traces. The source was NOT skipped: "
                        f"{source.display_name}"
                    )

            # ----------------------- append ----------------------------
            append_retry = 0
            while True:
                try:
                    stage_started = time.perf_counter()
                    batch += current
                    base.add_timing(timings, "stream_append", stage_started)
                    break
                except MemoryError:
                    if batch:
                        spill_batch(
                            f"MemoryError while appending Trace [{index}/{len(sources)}]; "
                            "retrying the same Trace"
                        )
                        append_retry += 1
                        gc.collect()
                        if append_retry <= 2:
                            continue

                    raise RuntimeError(
                        "Out of memory while appending a single Trace after "
                        "spilling all buffered data. The source was NOT skipped: "
                        f"{source.display_name}"
                    )

            if source.is_archive_member:
                if source.is_zip_member:
                    zip_member_count += 1
                elif source.is_rar_member:
                    rar_member_count += 1
            else:
                plain_count += 1

            npts = int(record["npts"])
            sample_count += npts
            source_bytes_done += source.uncompressed_size
            successful_source_bytes += source.uncompressed_size

            trace_bytes = int(getattr(current[0].data, "nbytes", 0))
            batch_raw_bytes += trace_bytes
            peak_batch_raw_bytes = max(peak_batch_raw_bytes, batch_raw_bytes)

            del record
            del current

            print_bridge_progress(
                args,
                timings,
                index,
                len(sources),
                source_bytes_done,
                total_source_bytes,
                f"C++ extension read {source.display_name}",
            )

            # Post-read check catches a large source that crossed the threshold.
            if batch and current_available() <= reserve_bytes:
                spill_batch("available physical RAM fell below reserve after source read")

    # If spill mode was ever needed, keep the whole run in bounded-memory mode.
    if spill_used and batch:
        spill_batch("final buffered traces after input scan")

    timings["cpp_extension_read_wall"] = time.perf_counter() - started

    return batch, {
        "sources_found": len(sources),
        "files_read": len(sources) - len(read_errors),
        "files_skipped": len(read_errors),
        "plain_files_read": plain_count,
        "zip_members_read": zip_member_count,
        "rar_members_read": rar_member_count,
        "samples_read": sample_count,
        "total_source_bytes": total_source_bytes,
        "successful_source_bytes": successful_source_bytes,
        "read_errors": read_errors,
        "memory_spill_used": spill_used,
        "memory_spill_events": memory_spill_events,
        "memory_spill_flush_chunks": spill_flush_chunks,
        "memory_spilled_trace_count": spilled_trace_count,
        "memory_spilled_raw_bytes": spilled_raw_bytes,
        "memory_total_physical_bytes": total_physical,
        "memory_reserve_bytes": reserve_bytes,
        "memory_min_available_bytes": min_available_bytes,
        "memory_peak_batch_raw_bytes": peak_batch_raw_bytes,
        "spill_root": str(spill_root) if spill_used else None,
    }


def write_read_error_log(
    path: Path,
    input_root: Path,
    errors: list[dict],
) -> None:
    """Append a human-readable list of skipped unreadable Y sources."""
    if not errors:
        return

    lines = [
        "=" * 80,
        f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "Nanometrics Y-file read errors",
        f"Input root: {input_root}",
        f"Skipped sources: {len(errors)}",
        "",
    ]
    for item in errors:
        lines.extend(
            [
                f"[{item['index']}/{item['total']}] {item['source']}",
                f"Size: {item['source_bytes']} bytes",
                f"Error: {item['error_type']}: {item['error']}",
                "",
            ]
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if path.stat().st_size > 0:
            handle.write("\n")
        handle.write("\n".join(lines))
        handle.write("\n")


def iter_trace_days(trace: Trace):
    current = base.day_start(trace.stats.starttime)
    end_day = base.day_start(trace.stats.endtime)
    while current <= end_day:
        yield current
        current = current + 86400


def affected_sds_paths(output_root: Path, stream: Stream) -> set[Path]:
    paths: set[Path] = set()
    for trace in stream:
        for day in iter_trace_days(trace):
            paths.add(base.sds_path(output_root, trace, day))
    return paths


def load_existing_sds_for_new_data(
    output_root: Path,
    new_stream: Stream,
    timings: dict[str, float],
) -> tuple[Stream, set[Path], int, int]:
    paths = affected_sds_paths(output_root, new_stream)
    existing = Stream()
    files_read = 0
    traces_read = 0

    stage_started = time.perf_counter()
    for path in sorted(paths):
        if not path.exists():
            continue
        part = obspy.read(str(path), format="MSEED", check_compression=False)
        existing += part
        files_read += 1
        traces_read += len(part)
    base.add_timing(timings, "existing_sds_read", stage_started)

    return existing, paths, files_read, traces_read


def replace_staged_sds_files(
    staging_root: Path,
    output_root: Path,
    record_counts: dict[Path, int],
    timings: dict[str, float],
) -> int:
    replaced = 0
    stage_started = time.perf_counter()
    for staging_path in sorted(record_counts):
        relative = staging_path.relative_to(staging_root)
        final_path = output_root / relative
        final_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = final_path.with_name(
            f"{final_path.name}.tmp-{int(time.time() * 1000)}"
        )
        if temp_path.exists():
            temp_path.unlink()
        shutil.move(str(staging_path), str(temp_path))
        temp_path.replace(final_path)
        replaced += 1
    base.add_timing(timings, "sds_atomic_replace", stage_started)
    return replaced


def remove_temp_file_with_retry(
    path: Path,
    timings: dict[str, float],
    attempts: int = 20,
) -> bool:
    """
    Best-effort removal of a temporary MiniSEED file on Windows.

    Antivirus/indexing software can briefly hold a file after it has been closed,
    producing WinError 32. Retry with a short bounded backoff. A cleanup failure
    must not abort an otherwise valid SDS conversion.
    """
    stage_started = time.perf_counter()
    last_error: OSError | None = None

    for attempt in range(attempts):
        try:
            path.unlink(missing_ok=True)
            base.add_timing(timings, "pack_disk_file_delete", stage_started)
            return True
        except OSError as exc:
            is_sharing_violation = (
                isinstance(exc, PermissionError)
                or getattr(exc, "winerror", None) in (5, 32)
            )
            if not is_sharing_violation:
                base.add_timing(timings, "pack_disk_file_delete", stage_started)
                raise

            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.50, 0.02 * (attempt + 1)))

    base.add_timing(timings, "pack_disk_file_delete", stage_started)
    print(
        "WARNING: could not delete temporary MiniSEED file after retries; "
        f"continuing and leaving it for OS cleanup: {path}: {last_error}",
        file=sys.stderr,
    )
    return False


def pack_trace_records_disk_robust(
    trace: Trace,
    args: argparse.Namespace,
    temp_dir: Path,
    trace_index: int,
    timings: dict[str, float],
):
    """Disk packing fallback with Windows-safe temporary-file cleanup."""
    temp_path = temp_dir / f"trace_{trace_index:08d}.mseed"

    try:
        stage_started = time.perf_counter()
        Stream([trace]).write(
            str(temp_path),
            format="MSEED",
            encoding=args.encoding,
            reclen=args.record_length,
            flush=True,
        )
        base.add_timing(timings, "pack_disk_mseed_write", stage_started)

        stage_started = time.perf_counter()
        file_size = temp_path.stat().st_size
        base.add_timing(timings, "pack_disk_file_stat", stage_started)
        base.validate_packed_size(file_size, trace, args.record_length)

        stage_started = time.perf_counter()
        handle = temp_path.open("rb")
        base.add_timing(timings, "pack_disk_file_open", stage_started)

        try:
            record_index = 0
            while True:
                stage_started = time.perf_counter()
                record = handle.read(args.record_length)
                base.add_timing(timings, "pack_disk_record_read", stage_started)

                if not record:
                    break

                record_index += 1
                if len(record) != args.record_length:
                    raise RuntimeError(
                        f"short MiniSEED record for {trace.id}: "
                        f"record={record_index}, bytes={len(record)}"
                    )

                stage_started = time.perf_counter()
                info = base.get_record_information(io.BytesIO(record))
                base.add_timing(timings, "pack_record_header_parse", stage_started)

                actual_length = int(info.get("record_length", args.record_length))
                if actual_length != args.record_length:
                    raise RuntimeError(
                        f"unexpected MiniSEED record length for {trace.id}: "
                        f"record={record_index}, expected={args.record_length}, "
                        f"actual={actual_length}"
                    )

                record_start = info.get("starttime")
                if record_start is None:
                    raise RuntimeError(
                        f"MiniSEED record start time is missing for {trace.id}: "
                        f"record={record_index}"
                    )

                yield record_start, record
        finally:
            stage_started = time.perf_counter()
            handle.close()
            base.add_timing(timings, "pack_disk_file_close", stage_started)
    finally:
        remove_temp_file_with_retry(temp_path, timings)



def steim2_jump_diagnostics(trace: Trace) -> dict:
    """
    Find the largest consecutive int32 sample jump.

    STEIM2's largest single difference is a signed 30-bit value:
    -2^29 .. 2^29-1.  Use int64 arithmetic so np.diff cannot overflow.
    """
    data = np.asarray(trace.data, dtype=np.int64)
    info = {
        "npts": int(trace.stats.npts),
        "starttime": str(trace.stats.starttime),
        "endtime": str(trace.stats.endtime),
        "sampling_rate": float(trace.stats.sampling_rate),
        "min_sample": int(data.min()) if data.size else None,
        "max_sample": int(data.max()) if data.size else None,
        "largest_jump": None,
        "largest_jump_abs": None,
        "sample_index_before": None,
        "sample_index_after": None,
        "sample_before": None,
        "sample_after": None,
        "jump_time": None,
        "exceeds_steim2_30bit": False,
    }

    if data.size < 2:
        return info

    diffs = np.diff(data)
    abs_diffs = np.abs(diffs)
    pos = int(np.argmax(abs_diffs))
    jump = int(diffs[pos])

    info["largest_jump"] = jump
    info["largest_jump_abs"] = int(abs_diffs[pos])
    info["sample_index_before"] = pos
    info["sample_index_after"] = pos + 1
    info["sample_before"] = int(data[pos])
    info["sample_after"] = int(data[pos + 1])
    info["jump_time"] = str(
        trace.stats.starttime + (pos + 1) / float(trace.stats.sampling_rate)
    )
    info["exceeds_steim2_30bit"] = (
        jump < -(1 << 29) or jump > ((1 << 29) - 1)
    )
    return info


def pack_trace_to_memory_with_encoding(
    trace: Trace,
    args: argparse.Namespace,
    encoding: str,
    timings: dict[str, float],
) -> bytes:
    """Pack one Trace to a MiniSEED bytes buffer with an explicit encoding."""
    buffer = io.BytesIO()

    stage_started = time.perf_counter()
    Stream([trace]).write(
        buffer,
        format="MSEED",
        encoding=encoding,
        reclen=args.record_length,
        flush=True,
    )
    base.add_timing(timings, "pack_memory_mseed_write", stage_started)

    stage_started = time.perf_counter()
    payload = buffer.getvalue()
    base.add_timing(timings, "pack_memory_buffer_copy", stage_started)

    base.validate_packed_size(len(payload), trace, args.record_length)
    return payload


def is_steim_range_error(exc: Exception) -> bool:
    """
    Return True only for the specific STEIM sample-difference range failure.

    Other libmseed errors (for example an msr_free()/packing failure) must not
    be treated as proof that STEIM cannot represent the samples.
    """
    message = str(exc)
    return "Unable to represent difference" in message


def write_encoding_fallback_log(
    path: Path,
    input_root: Path,
    fallbacks: list[dict],
) -> None:
    """Append Trace-level STEIM -> INT32 fallbacks to a diagnostic log."""
    if not fallbacks:
        return

    lines = [
        "=" * 80,
        f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "MiniSEED encoding fallbacks",
        f"Input root: {input_root}",
        f"Fallback traces: {len(fallbacks)}",
        "",
    ]

    for item in fallbacks:
        lines.extend(
            [
                f"Trace [{item['trace_index']}] {item['trace_id']}",
                f"Time: {item['starttime']} -> {item['endtime']}",
                f"Requested encoding: {item['requested_encoding']}",
                f"Actual encoding: {item['actual_encoding']}",
                f"Fallback reason: {item.get('fallback_reason', 'unspecified')}",
                f"Samples: npts={item['npts']}, min={item['min_sample']}, max={item['max_sample']}",
                (
                    "Largest jump: "
                    f"{item['sample_before']} -> {item['sample_after']} "
                    f"(diff={item['largest_jump']}, abs={item['largest_jump_abs']})"
                ),
                (
                    "Jump location: "
                    f"index {item['sample_index_before']} -> {item['sample_index_after']}, "
                    f"time={item['jump_time']}"
                ),
                f"Exceeds STEIM2 signed-30-bit range: {item['exceeds_steim2_30bit']}",
                f"Original packing error: {item['packing_error']}",
                *(
                    [f"Sequential retry error: {item['sequential_retry_error']}"]
                    if item.get("sequential_retry_error")
                    else []
                ),
                "",
            ]
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if path.stat().st_size > 0:
            handle.write("\n")
        handle.write("\n".join(lines))
        handle.write("\n")



def pack_trace_memory_worker_robust(task):
    """
    Thread worker.

    - A proven STEIM difference-range failure is retried as INT32 immediately.
    - Any other packing exception is returned to the main thread so that the
      same Trace can be retried once sequentially with the requested encoding.
    """
    trace_index, trace, encoding, record_length = task

    try:
        result = base.pack_trace_memory_worker(task)
        return result, None, None
    except Exception as exc:
        if (
            encoding in ("STEIM1", "STEIM2")
            and is_steim_range_error(exc)
        ):
            diagnostics = steim2_jump_diagnostics(trace)
            int32_task = (trace_index, trace, "INT32", record_length)

            try:
                result = base.pack_trace_memory_worker(int32_task)
            except Exception as int32_exc:
                raise RuntimeError(
                    f"{trace.id}: {encoding} range failure and INT32 fallback "
                    f"also failed. Original={type(exc).__name__}: {exc}; "
                    f"INT32={type(int32_exc).__name__}: {int32_exc}"
                ) from int32_exc

            fallback = {
                "trace_index": trace_index,
                "trace_id": trace.id,
                "requested_encoding": encoding,
                "actual_encoding": "INT32",
                "fallback_reason": "steim_difference_range",
                "packing_error": f"{type(exc).__name__}: {exc}",
                **diagnostics,
            }
            return result, fallback, None

        # Do not classify other libmseed failures as STEIM range failures.
        # Retry this Trace sequentially in the main thread.
        retry = {
            "trace_index": trace_index,
            "trace": trace,
            "encoding": encoding,
            "record_length": record_length,
            "packing_error": f"{type(exc).__name__}: {exc}",
        }
        return None, None, retry


def pack_stream_in_parallel_robust(
    stream: Stream,
    args: argparse.Namespace,
    timings: dict[str, float],
) -> tuple[list[base.PackedTraceResult], list[dict]]:
    """
    Parallel RAM packing with precise per-Trace fallback behavior.

    Proven STEIM range failure:
        parallel STEIM -> INT32 for that Trace only.

    Any other worker/libmseed failure:
        retry once sequentially with the requested encoding;
        if that also fails, preserve the Trace as INT32 and log the reason.
    """
    tasks = [
        (
            trace_index,
            trace,
            args.encoding,
            args.record_length,
        )
        for trace_index, trace in enumerate(stream, start=1)
    ]

    started = time.perf_counter()
    with ThreadPoolExecutor(
        max_workers=min(args.pack_workers, max(1, len(tasks))),
        thread_name_prefix="mseed-pack",
    ) as executor:
        triples = list(executor.map(pack_trace_memory_worker_robust, tasks))
    timings["parallel_pack_wall"] += time.perf_counter() - started

    results: list[base.PackedTraceResult] = []
    fallbacks: list[dict] = []

    for expected_index, (result, fallback, retry) in enumerate(triples, start=1):
        if retry is not None:
            trace = retry["trace"]
            requested_encoding = retry["encoding"]
            original_error = retry["packing_error"]

            # First retry the exact same encoding sequentially. This separates
            # transient/thread-related libmseed failures from true data-range
            # failures.
            try:
                sequential_task = (
                    retry["trace_index"],
                    trace,
                    requested_encoding,
                    retry["record_length"],
                )
                result = base.pack_trace_memory_worker(sequential_task)
            except Exception as sequential_exc:
                if (
                    requested_encoding in ("STEIM1", "STEIM2")
                    and is_steim_range_error(sequential_exc)
                ):
                    fallback_reason = "steim_difference_range_after_sequential_retry"
                else:
                    fallback_reason = "non_range_pack_error_after_sequential_retry"

                diagnostics = steim2_jump_diagnostics(trace)

                try:
                    int32_task = (
                        retry["trace_index"],
                        trace,
                        "INT32",
                        retry["record_length"],
                    )
                    result = base.pack_trace_memory_worker(int32_task)
                except Exception as int32_exc:
                    raise RuntimeError(
                        f"{trace.id}: parallel {requested_encoding} packing failed, "
                        f"sequential retry failed, and INT32 fallback also failed. "
                        f"Parallel={original_error}; "
                        f"Sequential={type(sequential_exc).__name__}: {sequential_exc}; "
                        f"INT32={type(int32_exc).__name__}: {int32_exc}"
                    ) from int32_exc

                fallback = {
                    "trace_index": retry["trace_index"],
                    "trace_id": trace.id,
                    "requested_encoding": requested_encoding,
                    "actual_encoding": "INT32",
                    "fallback_reason": fallback_reason,
                    "packing_error": original_error,
                    "sequential_retry_error": (
                        f"{type(sequential_exc).__name__}: {sequential_exc}"
                    ),
                    **diagnostics,
                }

                print(
                    "WARNING: "
                    f"{requested_encoding} failed for Trace "
                    f"[{retry['trace_index']}] {trace.id} in parallel and again "
                    "during sequential retry; writing this Trace as INT32. "
                    f"reason={fallback_reason}",
                    file=sys.stderr,
                )
            else:
                print(
                    "WARNING: "
                    f"parallel {requested_encoding} packing transiently failed for "
                    f"Trace [{retry['trace_index']}] {trace.id}; "
                    "sequential retry succeeded with the original encoding.",
                    file=sys.stderr,
                )

        if result is None:
            raise RuntimeError(
                f"internal error: missing packed result for Trace {expected_index}"
            )

        if result.trace_index != expected_index:
            raise RuntimeError(
                "parallel pack result order mismatch after fallback handling: "
                f"expected={expected_index}, actual={result.trace_index}"
            )

        results.append(result)
        timings["pack_thread_mseed_write_sum"] += result.mseed_write_seconds
        timings["pack_thread_buffer_copy_sum"] += result.buffer_copy_seconds
        timings["pack_thread_record_slice_sum"] += result.record_slice_seconds
        timings["pack_thread_header_parse_sum"] += result.header_parse_seconds

        if fallback is not None:
            fallbacks.append(fallback)

            if fallback["fallback_reason"].startswith("steim_difference_range"):
                print(
                    "WARNING: "
                    f"{fallback['requested_encoding']} cannot represent a sample "
                    f"difference in Trace [{fallback['trace_index']}] "
                    f"{fallback['trace_id']}; packed this Trace as INT32. "
                    f"largest_jump={fallback['largest_jump']} "
                    f"({fallback['sample_before']} -> {fallback['sample_after']}) "
                    f"at {fallback['jump_time']}",
                    file=sys.stderr,
                )

    return results, fallbacks


def pack_trace_records_robust(
    trace: Trace,
    args: argparse.Namespace,
    temp_dir: Path,
    trace_index: int,
    timings: dict[str, float],
    backend_state: base.PackBackendState,
    encoding_fallbacks: list[dict],
):
    """
    Bounded-memory per-Trace packing.

    STEIM range failure:
        immediately preserve this Trace as INT32.

    Other non-memory libmseed failure:
        retry once with the same requested encoding; if it fails again,
        preserve this Trace as INT32 and log the exact reason.

    MemoryError:
        in auto mode switch to the disk backend, because retrying another
        in-memory encoding would not solve physical-memory exhaustion.
    """

    def emit_int32_fallback(
        original_exc: Exception,
        reason: str,
        retry_exc: Exception | None = None,
    ):
        diagnostics = steim2_jump_diagnostics(trace)

        try:
            payload_ = pack_trace_to_memory_with_encoding(
                trace,
                args,
                "INT32",
                timings,
            )
        except MemoryError:
            # INT32 memory packing can itself be impossible under genuine RAM
            # pressure. Let the caller use disk rather than losing the Trace.
            raise
        except Exception as int32_exc:
            raise RuntimeError(
                f"{trace.id}: {args.encoding} packing failed and INT32 "
                f"fallback also failed. Original={type(original_exc).__name__}: "
                f"{original_exc}; INT32={type(int32_exc).__name__}: {int32_exc}"
            ) from int32_exc

        fallback = {
            "trace_index": trace_index,
            "trace_id": trace.id,
            "requested_encoding": args.encoding,
            "actual_encoding": "INT32",
            "fallback_reason": reason,
            "packing_error": f"{type(original_exc).__name__}: {original_exc}",
            **diagnostics,
        }
        if retry_exc is not None:
            fallback["sequential_retry_error"] = (
                f"{type(retry_exc).__name__}: {retry_exc}"
            )

        encoding_fallbacks.append(fallback)
        backend_state.used.add("memory")

        print(
            "WARNING: "
            f"{args.encoding} packing fallback for Trace [{trace_index}] "
            f"{trace.id}; writing this Trace as INT32. reason={reason}",
            file=sys.stderr,
        )

        yield from base.iter_packed_records(payload_, trace, args, timings)

    if backend_state.active == "memory":
        try:
            payload = base.pack_trace_to_memory(trace, args, timings)
        except MemoryError as exc:
            if backend_state.requested == "memory":
                raise

            backend_state.active = "disk"
            backend_state.fallback_reason = (
                f"trace {trace_index} {trace.id}: MemoryError: {exc}"
            )
            print(
                "WARNING: in-memory MiniSEED packing ran out of RAM; "
                "switching to robust disk backend for subsequent Trace(s): "
                f"{backend_state.fallback_reason}",
                file=sys.stderr,
            )

        except Exception as exc:
            if (
                args.encoding in ("STEIM1", "STEIM2")
                and is_steim_range_error(exc)
            ):
                yield from emit_int32_fallback(
                    exc,
                    "steim_difference_range",
                )
                return

            # This covers transient libmseed failures such as the previously
            # observed msr_free() error. Retry once with the same encoding
            # before changing representation.
            gc.collect()
            try:
                payload = base.pack_trace_to_memory(trace, args, timings)
            except MemoryError as retry_exc:
                if backend_state.requested == "memory":
                    raise

                backend_state.active = "disk"
                backend_state.fallback_reason = (
                    f"trace {trace_index} {trace.id}: MemoryError on retry: "
                    f"{retry_exc}"
                )
                print(
                    "WARNING: sequential packing retry ran out of RAM; "
                    "switching to robust disk backend: "
                    f"{backend_state.fallback_reason}",
                    file=sys.stderr,
                )

            except Exception as retry_exc:
                if (
                    args.encoding in ("STEIM1", "STEIM2")
                    and is_steim_range_error(retry_exc)
                ):
                    reason = "steim_difference_range_after_sequential_retry"
                else:
                    reason = "non_range_pack_error_after_sequential_retry"

                yield from emit_int32_fallback(
                    exc,
                    reason,
                    retry_exc=retry_exc,
                )
                return

            else:
                backend_state.used.add("memory")
                print(
                    "WARNING: transient MiniSEED packing failure recovered by "
                    f"sequential retry for Trace [{trace_index}] {trace.id}.",
                    file=sys.stderr,
                )
                yield from base.iter_packed_records(
                    payload,
                    trace,
                    args,
                    timings,
                )
                return

        else:
            backend_state.used.add("memory")
            yield from base.iter_packed_records(payload, trace, args, timings)
            return

    # Genuine RAM-pressure fallback. The disk path uses only a bounded record
    # buffer and therefore remains available when the in-memory payload cannot
    # be allocated.
    backend_state.used.add("disk")
    yield from pack_trace_records_disk_robust(
        trace,
        args,
        temp_dir,
        trace_index,
        timings,
    )


def pack_and_write_sds(
    stream: Stream,
    args: argparse.Namespace,
    output_root: Path,
    timings: dict[str, float],
) -> tuple[dict, base.PackBackendState]:
    record_counts: dict[Path, int] = defaultdict(int)
    output_handles = {}
    total_records = 0
    encoding_fallbacks: list[dict] = []

    backend_state = base.PackBackendState(
        requested=args.pack_backend,
        active="disk" if args.pack_backend == "disk" else "memory",
        used=set(),
    )

    pack_pipeline_started = time.perf_counter()
    parallel_results: list[base.PackedTraceResult] | None = None

    if args.pack_workers > 1 and backend_state.active == "memory":
        try:
            parallel_results, parallel_fallbacks = pack_stream_in_parallel_robust(
                stream,
                args,
                timings,
            )
            encoding_fallbacks.extend(parallel_fallbacks)
        except Exception as exc:
            if backend_state.requested == "memory":
                raise
            # STEIM range failures are already handled per Trace inside the
            # parallel workers. Any error reaching here is a different failure;
            # retry sequentially before considering disk.
            backend_state.active = "memory"
            backend_state.fallback_reason = f"{type(exc).__name__}: {exc}"
            print(
                "WARNING: parallel RAM packing failed; retrying with "
                "sequential per-trace RAM packing: "
                f"{backend_state.fallback_reason}",
                file=sys.stderr,
            )
        else:
            backend_state.used.add("memory")

    try:
        if parallel_results is not None:
            for expected_trace_index, (trace, result) in enumerate(
                zip(stream, parallel_results),
                start=1,
            ):
                if result.trace_index != expected_trace_index:
                    raise RuntimeError(
                        "parallel pack result order mismatch: "
                        f"expected={expected_trace_index}, actual={result.trace_index}"
                    )
                payload_view = memoryview(result.payload)
                trace_record_count = 0
                for record_index, record_start in enumerate(result.record_starts):
                    offset = record_index * args.record_length

                    stage_started = time.perf_counter()
                    record = payload_view[offset : offset + args.record_length]
                    base.add_timing(timings, "pack_parallel_route_record_slice", stage_started)

                    stage_started = time.perf_counter()
                    day = base.day_start(record_start)
                    path = base.sds_path(output_root, trace, day)
                    base.add_timing(timings, "sds_route_path", stage_started)

                    if path not in output_handles:
                        stage_started = time.perf_counter()
                        path.parent.mkdir(parents=True, exist_ok=True)
                        output_handles[path] = path.open("ab")
                        base.add_timing(timings, "sds_open_output", stage_started)

                    stage_started = time.perf_counter()
                    output_handles[path].write(record)
                    base.add_timing(timings, "sds_record_write", stage_started)

                    record_counts[path] += 1
                    total_records += 1
                    trace_record_count += 1

                base.print_progress(
                    args,
                    timings,
                    result.trace_index,
                    len(stream),
                    f"parallel packed {trace.id} records={trace_record_count}",
                )
        else:
            with tempfile.TemporaryDirectory(prefix="yfile_hybrid_pack_", ignore_cleanup_errors=True) as temp:
                temp_dir = Path(temp)
                for trace_index, trace in enumerate(stream, start=1):
                    trace_record_count = 0
                    for record_start, record in pack_trace_records_robust(
                        trace,
                        args,
                        temp_dir,
                        trace_index,
                        timings,
                        backend_state,
                        encoding_fallbacks,
                    ):
                        stage_started = time.perf_counter()
                        day = base.day_start(record_start)
                        path = base.sds_path(output_root, trace, day)
                        base.add_timing(timings, "sds_route_path", stage_started)

                        if path not in output_handles:
                            stage_started = time.perf_counter()
                            path.parent.mkdir(parents=True, exist_ok=True)
                            output_handles[path] = path.open("ab")
                            base.add_timing(timings, "sds_open_output", stage_started)

                        stage_started = time.perf_counter()
                        output_handles[path].write(record)
                        base.add_timing(timings, "sds_record_write", stage_started)

                        record_counts[path] += 1
                        total_records += 1
                        trace_record_count += 1

                    base.print_progress(
                        args,
                        timings,
                        trace_index,
                        len(stream),
                        f"packed {trace.id} records={trace_record_count}",
                    )
    finally:
        stage_started = time.perf_counter()
        for handle in output_handles.values():
            handle.close()
        base.add_timing(timings, "sds_close_outputs", stage_started)
        timings["pack_and_sds_pipeline_wall"] += time.perf_counter() - pack_pipeline_started

    stage_started = time.perf_counter()
    for path in sorted(record_counts):
        expected_size = record_counts[path] * args.record_length
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                f"SDS file size mismatch: {path}: expected={expected_size}, actual={actual_size}"
            )
    base.add_timing(timings, "output_validation", stage_started)

    return {
        "sds_files_written": len(record_counts),
        "records_written": total_records,
        "record_counts": dict(record_counts),
        "parallel_pack_payload_bytes": (
            sum(len(result.payload) for result in parallel_results)
            if parallel_results is not None
            else 0
        ),
        "encoding_fallbacks": encoding_fallbacks,
        "encoding_fallback_count": len(encoding_fallbacks),
        "pack_execution_mode": (
            "thread_pool_memory"
            if parallel_results is not None
            else (
                "sequential_disk"
                if backend_state.active == "disk"
                else "sequential_memory"
            )
        ),
    }, backend_state



def list_sds_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def summarize_staged_sds(
    root: Path,
    record_length: int,
) -> tuple[int, int, int]:
    """
    Return (records, samples, trace_segments) without loading waveform samples.
    """
    records = 0
    samples = 0
    trace_segments = 0

    for path in list_sds_files(root):
        size = path.stat().st_size
        if size % record_length != 0:
            raise RuntimeError(
                f"staged SDS size is not divisible by record length: {path}"
            )
        records += size // record_length

        head = obspy.read(
            str(path),
            format="MSEED",
            headonly=True,
            check_compression=False,
        )
        samples += sum(int(trace.stats.npts) for trace in head)
        trace_segments += len(head)

    return records, samples, trace_segments


def finalize_spilled_new_data(
    new_work_root: Path,
    output_root: Path,
    args: argparse.Namespace,
    timings: dict[str, float],
) -> tuple[dict, base.PackBackendState]:
    """
    Merge temporary new-data SDS into the real SDS tree using a second
    copy-on-write overlay. Only one manageable work SDS file is loaded at a
    time, so the full year is never reconstructed in RAM.
    """
    new_files = list_sds_files(new_work_root)
    if not new_files:
        raise RuntimeError("memory spill mode produced no temporary SDS files")

    aggregate_fallbacks: list[dict] = []
    fallback_files_read: set[Path] = set()
    candidate_paths: set[Path] = set()
    existing_traces_read = 0
    backends_used: set[str] = set()
    backend_final = "memory"
    backend_fallback_reason = None

    final_args = copy.copy(args)
    # Bounded-memory finalization: per-work-file parallelism provides little
    # benefit and can duplicate payload memory.
    final_args.pack_workers = 1

    with tempfile.TemporaryDirectory(prefix="yfile_hybrid_final_overlay_") as final_temp:
        final_overlay = Path(final_temp)

        total = len(new_files)
        for index, work_path in enumerate(new_files, start=1):
            try:
                part = obspy.read(
                    str(work_path),
                    format="MSEED",
                    check_compression=False,
                )
            except MemoryError as exc:
                gc.collect()
                try:
                    part = obspy.read(
                        str(work_path),
                        format="MSEED",
                        check_compression=False,
                    )
                except MemoryError as retry_exc:
                    raise RuntimeError(
                        "Out of memory while reading one temporary SDS work file. "
                        "No final SDS file was partially committed: "
                        f"{work_path}"
                    ) from retry_exc

            # This Stream is normally one NET.STA.LOC.CHA.DAY file and is small.
            merge_result = flush_stream_to_overlay(
                part,
                final_overlay,
                final_args,
                timings,
                fallback_root=output_root,
            )

            write_stats = merge_result["write_stats"]
            state = merge_result["backend_state"]

            aggregate_fallbacks.extend(
                write_stats.get("encoding_fallbacks", [])
                if write_stats is not None
                else []
            )
            fallback_files_read.update(merge_result["fallback_files_read"])
            candidate_paths.update(merge_result["candidate_paths"])
            existing_traces_read += int(merge_result["existing_traces_read"])

            if state is not None:
                backends_used.update(state.used)
                backend_final = state.active
                if state.fallback_reason:
                    backend_fallback_reason = state.fallback_reason

            base.print_progress(
                args,
                timings,
                index,
                total,
                f"low-memory finalized {work_path.relative_to(new_work_root)}",
            )

            del part
            gc.collect()

        final_files = list_sds_files(final_overlay)
        record_counts = {
            path: path.stat().st_size // args.record_length
            for path in final_files
        }

        records_written, samples_written, merged_trace_count = summarize_staged_sds(
            final_overlay,
            args.record_length,
        )

        replaced_files = replace_staged_sds_files(
            final_overlay,
            output_root,
            record_counts,
            timings,
        )

    # Deduplicate repeated fallback diagnostics if an overlay file happened to
    # be rewritten more than once.
    deduped_fallbacks: list[dict] = []
    seen = set()
    for item in aggregate_fallbacks:
        key = (
            item.get("trace_id"),
            item.get("starttime"),
            item.get("endtime"),
            item.get("fallback_reason"),
            item.get("jump_time"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped_fallbacks.append(item)

    state = base.PackBackendState(
        requested=args.pack_backend,
        active=backend_final,
        used=backends_used or {"memory"},
        fallback_reason=backend_fallback_reason,
    )

    return {
        "sds_files_written": replaced_files,
        "sds_files_replaced": replaced_files,
        "records_written": records_written,
        "samples_written": samples_written,
        "merged_trace_count": merged_trace_count,
        "parallel_pack_payload_bytes": 0,
        "encoding_fallbacks": deduped_fallbacks,
        "encoding_fallback_count": len(deduped_fallbacks),
        "pack_execution_mode": "memory_spill_copy_on_write",
        "existing_sds_candidate_files": len(candidate_paths),
        "existing_sds_files_read": len(fallback_files_read),
        "existing_sds_traces_read": existing_traces_read,
    }, state


def finalize_timing_aggregates(
    timings: dict[str, float],
    elapsed_seconds: float,
    parallel_results_used: bool,
) -> None:
    sequential_pack_component_names = (
        "pack_memory_mseed_write",
        "pack_memory_buffer_copy",
        "pack_memory_record_slice",
        "pack_disk_mseed_write",
        "pack_disk_file_stat",
        "pack_disk_file_open",
        "pack_disk_record_read",
        "pack_disk_file_close",
        "pack_disk_file_delete",
        "pack_record_header_parse",
    )
    if parallel_results_used:
        timings["pack_total"] = (
            timings["parallel_pack_wall"]
            + timings["pack_parallel_route_record_slice"]
        )
    else:
        timings["pack_total"] = sum(timings[name] for name in sequential_pack_component_names)

    timings["sds_output_total"] = (
        timings["sds_route_path"]
        + timings["sds_open_output"]
        + timings["sds_record_write"]
        + timings["sds_close_outputs"]
        + timings["output_validation"]
    )
    input_wall = timings["cpp_extension_read_wall"] or timings["bridge_read_wall"]
    timings["input_reader_pipeline_unmeasured"] = max(
        0.0,
        input_wall
        - timings["bridge_payload_read"]
        - timings["bridge_trace_build"]
        - timings["cpp_extension_read"]
        - timings["cpp_extension_trace_build"]
        - timings["archive_decompress"]
        - timings["metadata_apply"]
        - timings["stream_append"]
        - timings["console_progress"],
    )
    timings["pack_pipeline_unmeasured"] = max(
        0.0,
        timings["pack_and_sds_pipeline_wall"]
        - timings["pack_total"]
        - (
            timings["sds_route_path"]
            + timings["sds_open_output"]
            + timings["sds_record_write"]
            + timings["sds_close_outputs"]
        )
        - timings["console_progress"],
    )

    aggregate_names = {
        "pack_total",
        "sds_output_total",
        "pack_and_sds_pipeline_wall",
        "bridge_read_wall",
        "cpp_extension_read_wall",
        "pack_pipeline_unmeasured",
        "pack_thread_mseed_write_sum",
        "pack_thread_buffer_copy_sum",
        "pack_thread_record_slice_sum",
        "pack_thread_header_parse_sum",
        "measured_stage_total",
        "unmeasured_overhead",
        "input_reader_pipeline_unmeasured",
    }
    timings["measured_stage_total"] = sum(
        seconds
        for name, seconds in timings.items()
        if name not in aggregate_names
    )
    timings["unmeasured_overhead"] = max(0.0, elapsed_seconds - timings["measured_stage_total"])


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    timings: dict[str, float] = defaultdict(float)

    stage_started = time.perf_counter()
    input_root = args.input_root.resolve()
    output_root = args.output_root.resolve()
    if not input_root.exists():
        raise RuntimeError(f"input-root does not exist: {input_root}")
    if input_root == output_root:
        raise RuntimeError("input-root and output-root must be different")
    if output_root.exists() and not output_root.is_dir():
        raise RuntimeError(f"output-root is not a directory: {output_root}")
    if args.report and output_root in args.report.resolve().parents:
        raise RuntimeError("--report must be outside output-root")
    if args.no_correct_sid and not args.network:
        raise RuntimeError("--network is required when --no-correct-sid is used")
    base.add_timing(timings, "argument_and_path_validation", stage_started)

    stage_started = time.perf_counter()
    output_root.mkdir(parents=True, exist_ok=True)
    base.add_timing(timings, "output_prepare", stage_started)

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")
    if yfile2obspy_cpp is not None:
        print("C++ reader: yfile2obspy_cpp native module")
    else:
        print(f"C++ reader: bridge fallback {args.bridge_exe}")

    stage_started = time.perf_counter()
    corrections = None if args.no_correct_sid else base.load_correct_sid(args.correct_sid.resolve())
    base.add_timing(timings, "correct_sid_load", stage_started)
    if corrections is not None:
        print(f"Using CorrectSID: {args.correct_sid.resolve()} ({len(corrections)} entries)")

    with tempfile.TemporaryDirectory(prefix="yfile_hybrid_newdata_") as newdata_temp:
        new_work_root = Path(newdata_temp)

        input_decode_mode = "cpp_extension"
        if yfile2obspy_cpp is not None:
            stream, read_stats = read_stream_from_extension(
                args,
                input_root,
                corrections,
                timings,
                spill_root=new_work_root,
            )
        else:
            # The legacy bridge fallback is retained for compatibility. Native
            # extension mode is the fully memory-aware path.
            input_decode_mode = "cpp_bridge"
            print(
                "WARNING: bridge fallback does not support read-stage automatic "
                "memory spilling; native yfile2obspy_cpp is recommended.",
                file=sys.stderr,
            )
            stream, read_stats = read_stream_from_bridge(
                args,
                input_root,
                corrections,
                timings,
            )

        read_errors = read_stats.get("read_errors", [])
        error_log = (
            args.error_log.resolve()
            if args.error_log is not None
            else output_root / "_yfile_read_errors.log"
        )
        write_read_error_log(error_log, input_root, read_errors)

        spill_mode = bool(read_stats.get("memory_spill_used", False))

        if spill_mode:
            if stream:
                # Defensive: the memory-aware reader normally drains the final
                # batch once spill mode has started.
                drain_batch_to_overlay(
                    stream,
                    new_work_root,
                    args,
                    timings,
                    fallback_root=None,
                )

            print(
                "Low-memory finalization: merging temporary new-data SDS into "
                "the destination one work file at a time."
            )

            write_stats, backend_state = finalize_spilled_new_data(
                new_work_root,
                output_root,
                args,
                timings,
            )

            replaced_files = write_stats["sds_files_replaced"]
            existing_files_read = write_stats["existing_sds_files_read"]
            existing_traces_read = write_stats["existing_sds_traces_read"]
            touched_path_count = write_stats["existing_sds_candidate_files"]
            total_samples = write_stats["samples_written"]
            merged_trace_count = write_stats["merged_trace_count"]
            new_trace_count = read_stats["files_read"]

        else:
            # Fast path: exactly the previous all-in-memory behavior.
            if not stream:
                raise RuntimeError(
                    "C++ reader returned no usable traces"
                    + (
                        f"; skipped={len(read_errors)}; see {error_log}"
                        if read_errors
                        else ""
                    )
                )

            new_trace_count = len(stream)
            existing_stream, touched_paths, existing_files_read, existing_traces_read = (
                load_existing_sds_for_new_data(output_root, stream, timings)
            )
            if existing_stream:
                stream += existing_stream

            stage_started = time.perf_counter()
            stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
            base.add_timing(timings, "sort_before_merge", stage_started)

            stage_started = time.perf_counter()
            stream.merge(method=-1)
            base.add_timing(timings, "merge_method_minus_1", stage_started)

            stage_started = time.perf_counter()
            stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
            base.add_timing(timings, "sort_after_merge", stage_started)

            stage_started = time.perf_counter()
            total_samples = sum(int(trace.stats.npts) for trace in stream)
            base.add_timing(timings, "sample_count", stage_started)

            with tempfile.TemporaryDirectory(prefix="yfile_hybrid_sds_stage_") as stage_temp:
                staging_root = Path(stage_temp)
                write_stats, backend_state = pack_and_write_sds(
                    stream,
                    args,
                    staging_root,
                    timings,
                )
                replaced_files = replace_staged_sds_files(
                    staging_root,
                    output_root,
                    write_stats["record_counts"],
                    timings,
                )

            touched_path_count = len(touched_paths)
            merged_trace_count = len(stream)

        encoding_fallback_log = output_root / "_mseed_encoding_fallbacks.log"
        write_encoding_fallback_log(
            encoding_fallback_log,
            input_root,
            write_stats.get("encoding_fallbacks", []),
        )

    elapsed_seconds = time.perf_counter() - started
    finalize_timing_aggregates(
        timings,
        elapsed_seconds,
        (
            not spill_mode
            and write_stats["pack_execution_mode"] == "thread_pool_memory"
        ),
    )

    report = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "bridge_exe": str(args.bridge_exe) if input_decode_mode == "cpp_bridge" else None,
        "obspy": obspy.__version__,
        "numpy": np.__version__,
        "sources_found": read_stats.get("sources_found", read_stats["files_read"]),
        "files_read": read_stats["files_read"],
        "files_skipped": read_stats.get("files_skipped", 0),
        "read_error_log": str(error_log) if read_errors else None,
        "read_errors": read_errors,
        "plain_files_read": read_stats["plain_files_read"],
        "zip_members_read": read_stats["zip_members_read"],
        "rar_members_read": read_stats.get("rar_members_read", 0),
        "samples_read": read_stats["samples_read"],
        "total_source_bytes": read_stats["total_source_bytes"],
        "sds_files_written": write_stats["sds_files_written"],
        "sds_files_replaced": replaced_files,
        "existing_sds_candidate_files": touched_path_count,
        "existing_sds_files_read": existing_files_read,
        "existing_sds_traces_read": existing_traces_read,
        "records_written": write_stats["records_written"],
        "samples_written": total_samples,
        "encoding_fallback_count": write_stats.get("encoding_fallback_count", 0),
        "encoding_fallback_log": (
            str(encoding_fallback_log)
            if write_stats.get("encoding_fallback_count", 0)
            else None
        ),
        "encoding_fallbacks": write_stats.get("encoding_fallbacks", []),
        "encoding": args.encoding,
        "record_length": args.record_length,
        "correct_sid": str(args.correct_sid.resolve()) if corrections is not None else None,
        "progress_every": args.progress_every,
        "quiet": args.quiet,
        "benchmark": args.benchmark,
        "pack_workers": args.pack_workers,
        "new_trace_count": new_trace_count,
        "merged_trace_count": merged_trace_count,
        "parallel_pack_payload_bytes": write_stats["parallel_pack_payload_bytes"],
        "pack_execution_mode": write_stats["pack_execution_mode"],
        "input_decode_mode": input_decode_mode,
        "pack_backend_requested": backend_state.requested,
        "pack_backends_used": sorted(backend_state.used),
        "pack_backend_final": backend_state.active,
        "pack_backend_fallback_reason": backend_state.fallback_reason,
        "memory_spill_used": spill_mode,
        "memory_spill_events": read_stats.get("memory_spill_events", 0),
        "memory_spill_flush_chunks": read_stats.get("memory_spill_flush_chunks", 0),
        "memory_spilled_trace_count": read_stats.get("memory_spilled_trace_count", 0),
        "memory_spilled_raw_bytes": read_stats.get("memory_spilled_raw_bytes", 0),
        "memory_total_physical_bytes": read_stats.get("memory_total_physical_bytes"),
        "memory_reserve_bytes": read_stats.get("memory_reserve_bytes"),
        "memory_min_available_bytes": read_stats.get("memory_min_available_bytes"),
        "memory_peak_batch_raw_bytes": read_stats.get("memory_peak_batch_raw_bytes"),
        "memory_spill_chunk_mb": args.memory_spill_chunk_mb,
        "timings_seconds": base.rounded_timings(timings),
        "elapsed_seconds": round(elapsed_seconds, 6),
    }

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if args.benchmark:
        print()
        print("Benchmark timings:")
        benchmark_names = [
            "cpp_extension_read_wall",
            "bridge_read_wall",
            "merge_method_minus_1",
            "parallel_pack_wall",
            "pack_total",
            "sds_output_total",
            "sds_atomic_replace",
            "pack_and_sds_pipeline_wall",
            "elapsed_seconds",
        ]
        for name in benchmark_names:
            if name == "elapsed_seconds":
                seconds = report["elapsed_seconds"]
            elif name in report["timings_seconds"]:
                seconds = report["timings_seconds"][name]
            else:
                continue
            print(f"  {name:30s} {seconds:12.6f} s")

    warning_state = bool(read_errors) or bool(
        write_stats.get("encoding_fallback_count", 0)
    )
    print("Completed with warnings" if warning_state else "Completed successfully")
    print(
        "Summary: "
        f"elapsed={report['elapsed_seconds']:.6f}s, "
        f"files={report['files_read']}/{report['sources_found']}, "
        f"skipped={report['files_skipped']}, "
        f"samples={report['samples_written']}, "
        f"records={report['records_written']}, "
        f"sds_files={report['sds_files_replaced']}, "
        f"memory_spill={'yes' if spill_mode else 'no'}"
    )

    if spill_mode:
        print(
            "Memory spill summary: "
            f"events={report['memory_spill_events']}, "
            f"chunks={report['memory_spill_flush_chunks']}, "
            f"spilled_raw={format_bytes(report['memory_spilled_raw_bytes'])}, "
            f"min_available={format_bytes(report['memory_min_available_bytes'])}"
        )

    if read_errors:
        print(
            f"WARNING: skipped {len(read_errors)} unreadable source(s)",
            file=sys.stderr,
        )
        print(f"Read error log: {error_log}", file=sys.stderr)

    if write_stats.get("encoding_fallback_count", 0):
        print(
            "WARNING: "
            f"{write_stats['encoding_fallback_count']} Trace(s) required "
            f"INT32 fallback after {args.encoding} packing problems.",
            file=sys.stderr,
        )
        print(
            f"Encoding fallback log: {encoding_fallback_log}",
            file=sys.stderr,
        )

    if args.report:
        print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
