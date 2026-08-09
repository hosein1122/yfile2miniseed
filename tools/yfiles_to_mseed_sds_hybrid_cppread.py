#!/usr/bin/env python3
"""Hybrid SDS builder: C++ reads Y-files, ObsPy handles merge/pack/SDS."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import obspy
from obspy import Stream, Trace, UTCDateTime

import yfiles_to_mseed_sds_obspy as base


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CORRECT_SID = SCRIPT_DIR / "CorrectSID.txt"


BRIDGE_MAGIC = b"Y2OBSBR1\n"


def default_bridge_exe() -> Path:
    local = SCRIPT_DIR / "yfile2obspy_bridge.exe"
    if local.exists():
        return local
    return Path("yfile2obspy_bridge.exe")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read Nanometrics Y-files with the C++ bridge, then use ObsPy to "
            "merge, pack, and write strict SDS MiniSEED."
        )
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--bridge-exe", type=Path, default=default_bridge_exe())
    parser.add_argument("--network", help="Fallback network if --no-correct-sid is used.")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--encoding", default="STEIM2", choices=("STEIM1", "STEIM2", "INT32"))
    parser.add_argument("--record-length", type=int, default=4096)
    parser.add_argument("--pattern", default="*")
    parser.add_argument("--station", help="Override station on all traces.")
    parser.add_argument("--location", help="Override location on all traces. Use --location= for empty.")
    parser.add_argument("--channel", help="Override channel on all traces.")
    parser.add_argument("--correct-sid", type=Path, default=DEFAULT_CORRECT_SID)
    parser.add_argument("--no-correct-sid", action="store_true")
    parser.add_argument("--report", type=Path, help="Optional JSON report path outside output-root.")
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
    if args.recursive:
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
        "samples_read": sample_count,
        "total_source_bytes": total_source_bytes,
    }


def pack_and_write_sds(
    stream: Stream,
    args: argparse.Namespace,
    output_root: Path,
    timings: dict[str, float],
) -> tuple[dict, base.PackBackendState]:
    record_counts: dict[Path, int] = defaultdict(int)
    output_handles = {}
    total_records = 0

    backend_state = base.PackBackendState(
        requested=args.pack_backend,
        active="disk" if args.pack_backend == "disk" else "memory",
        used=set(),
    )

    pack_pipeline_started = time.perf_counter()
    parallel_results: list[base.PackedTraceResult] | None = None

    if args.pack_workers > 1 and backend_state.active == "memory":
        try:
            parallel_results = base.pack_stream_in_parallel(stream, args, timings)
        except Exception as exc:
            if backend_state.requested == "memory":
                raise
            backend_state.active = "disk"
            backend_state.fallback_reason = f"{type(exc).__name__}: {exc}"
            print(
                "WARNING: parallel RAM packing failed; switching to disk backend: "
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
            with tempfile.TemporaryDirectory(prefix="yfile_hybrid_pack_") as temp:
                temp_dir = Path(temp)
                for trace_index, trace in enumerate(stream, start=1):
                    trace_record_count = 0
                    for record_start, record in base.pack_trace_records(
                        trace,
                        args,
                        temp_dir,
                        trace_index,
                        timings,
                        backend_state,
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
        "parallel_pack_payload_bytes": (
            sum(len(result.payload) for result in parallel_results)
            if parallel_results is not None
            else 0
        ),
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
    timings["bridge_pipeline_unmeasured"] = max(
        0.0,
        timings["bridge_read_wall"]
        - timings["bridge_payload_read"]
        - timings["bridge_trace_build"]
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
        "bridge_pipeline_unmeasured",
        "pack_pipeline_unmeasured",
        "pack_thread_mseed_write_sum",
        "pack_thread_buffer_copy_sum",
        "pack_thread_record_slice_sum",
        "pack_thread_header_parse_sum",
        "measured_stage_total",
        "unmeasured_overhead",
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
    if args.report and output_root in args.report.resolve().parents:
        raise RuntimeError("--report must be outside output-root")
    if args.no_correct_sid and not args.network:
        raise RuntimeError("--network is required when --no-correct-sid is used")
    base.add_timing(timings, "argument_and_path_validation", stage_started)

    stage_started = time.perf_counter()
    base.ensure_empty_output(output_root)
    base.add_timing(timings, "output_prepare", stage_started)

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")
    print(f"C++ bridge: {args.bridge_exe}")

    stage_started = time.perf_counter()
    corrections = None if args.no_correct_sid else base.load_correct_sid(args.correct_sid.resolve())
    base.add_timing(timings, "correct_sid_load", stage_started)
    if corrections is not None:
        print(f"Using CorrectSID: {args.correct_sid.resolve()} ({len(corrections)} entries)")

    stream, read_stats = read_stream_from_bridge(args, input_root, corrections, timings)
    if not stream:
        raise RuntimeError("C++ bridge returned no traces")

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

    write_stats, backend_state = pack_and_write_sds(stream, args, output_root, timings)
    elapsed_seconds = time.perf_counter() - started
    finalize_timing_aggregates(
        timings,
        elapsed_seconds,
        write_stats["pack_execution_mode"] == "thread_pool_memory",
    )

    report = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "bridge_exe": str(args.bridge_exe),
        "obspy": obspy.__version__,
        "numpy": np.__version__,
        "files_read": read_stats["files_read"],
        "plain_files_read": read_stats["plain_files_read"],
        "zip_members_read": read_stats["zip_members_read"],
        "samples_read": read_stats["samples_read"],
        "total_source_bytes": read_stats["total_source_bytes"],
        "sds_files_written": write_stats["sds_files_written"],
        "records_written": write_stats["records_written"],
        "samples_written": total_samples,
        "encoding": args.encoding,
        "record_length": args.record_length,
        "correct_sid": str(args.correct_sid.resolve()) if corrections is not None else None,
        "progress_every": args.progress_every,
        "quiet": args.quiet,
        "benchmark": args.benchmark,
        "pack_workers": args.pack_workers,
        "merged_trace_count": len(stream),
        "parallel_pack_payload_bytes": write_stats["parallel_pack_payload_bytes"],
        "pack_execution_mode": write_stats["pack_execution_mode"],
        "input_decode_mode": "cpp_bridge",
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
            "bridge_read_wall",
            "merge_method_minus_1",
            "parallel_pack_wall",
            "pack_total",
            "sds_output_total",
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

    print("Completed successfully")
    print(
        "Summary: "
        f"elapsed={report['elapsed_seconds']:.6f}s, "
        f"files={report['files_read']}, "
        f"samples={report['samples_written']}, "
        f"records={report['records_written']}, "
        f"sds_files={report['sds_files_written']}"
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
