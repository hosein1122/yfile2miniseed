#!/usr/bin/env python3
"""Hybrid SDS builder: C++ reads Y-files, ObsPy handles merge/pack/SDS."""

from __future__ import annotations

import argparse
import io
import json
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
    args = parser.parse_args()

    if args.progress_every < 1:
        parser.error("--progress-every must be at least 1")
    if args.pack_workers < 1:
        parser.error("--pack-workers must be at least 1")
    if args.pack_backend == "disk" and args.pack_workers > 1:
        parser.error("--pack-workers greater than 1 requires memory/auto packing")
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


def read_stream_from_extension(
    args: argparse.Namespace,
    input_root: Path,
    corrections: dict[str, tuple[str, str, str, str]] | None,
    timings: dict[str, float],
) -> tuple[Stream, dict]:
    if yfile2obspy_cpp is None:
        raise RuntimeError("yfile2obspy_cpp is not importable")

    sources = base.iter_input_sources(input_root, args.pattern, True)
    if not sources:
        raise RuntimeError(f"no input files matched {args.pattern!r} under {input_root}")

    stream = Stream()
    sample_count = 0
    source_bytes_done = 0
    successful_source_bytes = 0
    plain_count = 0
    zip_member_count = 0
    rar_member_count = 0
    read_errors: list[dict] = []
    total_source_bytes = sum(source.uncompressed_size for source in sources)

    started = time.perf_counter()
    with base.InputSourceReader() as source_reader:
        for index, source in enumerate(sources, start=1):
            stage_started = time.perf_counter()
            try:
                if source.is_archive_member:
                    payload, decompress_elapsed = source_reader.read_archive_payload(source)
                    timings["archive_decompress"] += decompress_elapsed
                    record = yfile2obspy_cpp.read_yfile_bytes(payload)
                else:
                    record = yfile2obspy_cpp.read_yfile_path(str(source.container))
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
                continue

            base.add_timing(timings, "cpp_extension_read", stage_started)

            if source.is_archive_member:
                if source.is_zip_member:
                    zip_member_count += 1
                elif source.is_rar_member:
                    rar_member_count += 1
            else:
                plain_count += 1

            # Metadata/configuration errors are intentionally NOT skipped.
            # A missing CorrectSID entry is a configuration problem, not a bad Y-file.
            stage_started = time.perf_counter()
            current = Stream([trace_from_cpp_record(record)])
            base.add_timing(timings, "cpp_extension_trace_build", stage_started)

            stage_started = time.perf_counter()
            base.apply_metadata(current, args, source.display_name, corrections)
            base.add_timing(timings, "metadata_apply", stage_started)

            stage_started = time.perf_counter()
            stream += current
            base.add_timing(timings, "stream_append", stage_started)

            npts = int(record["npts"])
            sample_count += npts
            source_bytes_done += source.uncompressed_size
            successful_source_bytes += source.uncompressed_size
            print_bridge_progress(
                args,
                timings,
                index,
                len(sources),
                source_bytes_done,
                total_source_bytes,
                f"C++ extension read {source.display_name}",
            )

    timings["cpp_extension_read_wall"] = time.perf_counter() - started
    return stream, {
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
    Prefer per-trace RAM packing.

    If STEIM compression cannot represent a sample difference, retry ONLY this
    Trace as INT32. Disk fallback is reserved for non-encoding RAM failures.
    """
    if backend_state.active == "memory":
        try:
            payload = base.pack_trace_to_memory(trace, args, timings)
        except Exception as exc:
            # A disk retry with the same STEIM encoding cannot fix an encoding
            # range error. Preserve every sample by writing this Trace as INT32.
            if (
                args.encoding in ("STEIM1", "STEIM2")
                and is_steim_range_error(exc)
            ):
                diagnostics = steim2_jump_diagnostics(trace)

                try:
                    payload = pack_trace_to_memory_with_encoding(
                        trace,
                        args,
                        "INT32",
                        timings,
                    )
                except Exception as int32_exc:
                    raise RuntimeError(
                        f"{trace.id}: {args.encoding} packing failed and INT32 "
                        f"fallback also failed. Original={type(exc).__name__}: {exc}; "
                        f"INT32={type(int32_exc).__name__}: {int32_exc}"
                    ) from int32_exc

                fallback = {
                    "trace_index": trace_index,
                    "trace_id": trace.id,
                    "requested_encoding": args.encoding,
                    "actual_encoding": "INT32",
                    "fallback_reason": "steim_difference_range",
                    "packing_error": f"{type(exc).__name__}: {exc}",
                    **diagnostics,
                }
                encoding_fallbacks.append(fallback)

                print(
                    "WARNING: "
                    f"{args.encoding} cannot encode Trace [{trace_index}] {trace.id}; "
                    "writing this Trace as INT32 instead. "
                    f"largest_jump={diagnostics['largest_jump']} "
                    f"({diagnostics['sample_before']} -> {diagnostics['sample_after']}) "
                    f"at {diagnostics['jump_time']}",
                    file=sys.stderr,
                )

                backend_state.used.add("memory")
                yield from base.iter_packed_records(payload, trace, args, timings)
                return

            if backend_state.requested == "memory":
                raise

            # Non-encoding RAM failures may still use the original disk backend.
            backend_state.active = "disk"
            backend_state.fallback_reason = (
                f"trace {trace_index} {trace.id}: {type(exc).__name__}: {exc}"
            )
            print(
                "WARNING: sequential RAM MiniSEED packing failed for a "
                "non-encoding reason; switching to robust disk backend: "
                f"{backend_state.fallback_reason}",
                file=sys.stderr,
            )
        else:
            backend_state.used.add("memory")
            yield from base.iter_packed_records(payload, trace, args, timings)
            return

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
        timings["pack_and_sds_pipeline_wall"] = time.perf_counter() - pack_pipeline_started

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

    input_decode_mode = "cpp_extension"
    if yfile2obspy_cpp is not None:
        stream, read_stats = read_stream_from_extension(args, input_root, corrections, timings)
    else:
        input_decode_mode = "cpp_bridge"
        stream, read_stats = read_stream_from_bridge(args, input_root, corrections, timings)
    read_errors = read_stats.get("read_errors", [])
    error_log = (
        args.error_log.resolve()
        if args.error_log is not None
        else output_root / "_yfile_read_errors.log"
    )
    write_read_error_log(error_log, input_root, read_errors)

    if not stream:
        raise RuntimeError(
            "C++ reader returned no usable traces"
            + (f"; skipped={len(read_errors)}; see {error_log}" if read_errors else "")
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
        write_stats, backend_state = pack_and_write_sds(stream, args, staging_root, timings)
        replaced_files = replace_staged_sds_files(
            staging_root,
            output_root,
            write_stats["record_counts"],
            timings,
        )

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
        write_stats["pack_execution_mode"] == "thread_pool_memory",
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
        "existing_sds_candidate_files": len(touched_paths),
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
        "merged_trace_count": len(stream),
        "parallel_pack_payload_bytes": write_stats["parallel_pack_payload_bytes"],
        "pack_execution_mode": write_stats["pack_execution_mode"],
        "input_decode_mode": input_decode_mode,
        "pack_backend_requested": backend_state.requested,
        "pack_backends_used": sorted(backend_state.used),
        "pack_backend_final": backend_state.active,
        "pack_backend_fallback_reason": backend_state.fallback_reason,
        "timings_seconds": base.rounded_timings(timings),
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

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

    print("Completed with warnings" if read_errors else "Completed successfully")
    print(
        "Summary: "
        f"elapsed={report['elapsed_seconds']:.6f}s, "
        f"files={report['files_read']}/{report['sources_found']}, "
        f"skipped={report['files_skipped']}, "
        f"samples={report['samples_written']}, "
        f"records={report['records_written']}, "
        f"sds_files={report['sds_files_replaced']}"
    )
    if read_errors:
        print(f"WARNING: skipped {len(read_errors)} unreadable source(s)", file=sys.stderr)
        print(f"Read error log: {error_log}", file=sys.stderr)
    if write_stats.get("encoding_fallback_count", 0):
        print(
            "WARNING: "
            f"{write_stats['encoding_fallback_count']} Trace(s) required "
            f"INT32 fallback after {args.encoding} packing problems.",
            file=sys.stderr,
        )
        print(f"Encoding fallback log: {encoding_fallback_log}", file=sys.stderr)
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
