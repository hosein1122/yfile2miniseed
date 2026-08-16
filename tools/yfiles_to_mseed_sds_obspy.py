#!/usr/bin/env python3
"""Optimized strict SDS builder for plain and ZIP-contained Nanometrics Y-files."""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import numpy as np
import obspy
from obspy import Stream, Trace, UTCDateTime, read
from obspy.clients.filesystem.sds import SDS_FMTSTR
from obspy.io.mseed.util import get_record_information


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CORRECT_SID = SCRIPT_DIR / "CorrectSID.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read plain Nanometrics Y-files and Y-files stored inside ZIP archives, "
            "then write daily strict SDS MiniSEED."
        )
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
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
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help=(
            "Print progress percentage every N input sources/traces/files. "
            "The first and final item are always printed. Default: 100."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-item progress messages while keeping the final summary.",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Print selected stage timings to the console. JSON reports remain detailed.",
    )
    parser.add_argument(
        "--pack-backend",
        choices=("auto", "memory", "disk"),
        default="auto",
        help=(
            "MiniSEED packing backend. 'auto' tries RAM first and falls back "
            "to the original temporary-file method if RAM packing is not "
            "supported. Default: auto."
        ),
    )
    parser.add_argument(
        "--zip-prefetch",
        type=int,
        choices=(0, 1),
        default=1,
        help=(
            "Prefetch and decompress one upcoming ZIP member in a background "
            "thread while ObsPy decodes the current source. "
            "Used only when --y-workers=1. Default: 1."
        ),
    )
    parser.add_argument(
        "--y-workers",
        type=int,
        default=4,
        help=(
            "Number of worker processes used to read and decode independent "
            "Y-files. Use 1 for the P4 sequential/prefetch path. "
            "Measured recommended default: 4."
        ),
    )
    parser.add_argument(
        "--y-chunk-size",
        type=int,
        default=100,
        help=(
            "Number of consecutive input sources decoded by each process task. "
            "Larger chunks reduce process overhead but use more RAM. Default: 100."
        ),
    )
    parser.add_argument(
        "--pack-workers",
        type=int,
        default=4,
        help=(
            "Number of threads used to pack independent merged Traces into "
            "MiniSEED memory buffers. SDS routing and writing remain single-"
            "threaded and deterministic. Use 1 for the P5 sequential path. "
            "Measured recommended default: 4."
        ),
    )
    args = parser.parse_args()
    if args.progress_every < 1:
        parser.error("--progress-every must be at least 1")
    if args.y_workers < 1:
        parser.error("--y-workers must be at least 1")
    if args.y_chunk_size < 1:
        parser.error("--y-chunk-size must be at least 1")
    if args.pack_workers < 1:
        parser.error("--pack-workers must be at least 1")
    if args.pack_backend == "disk" and args.pack_workers > 1:
        parser.error("--pack-workers greater than 1 requires memory/auto packing")
    return args


def add_timing(timings: dict[str, float], name: str, started: float) -> None:
    """Accumulate elapsed wall-clock time for one processing stage."""
    timings[name] += time.perf_counter() - started


def rounded_timings(timings: dict[str, float]) -> dict[str, float]:
    """Return stable, readable timing values for the final report."""
    return {
        name: round(seconds, 6)
        for name, seconds in timings.items()
    }


def should_print_progress(
    args: argparse.Namespace,
    index: int,
    total: int,
) -> bool:
    if args.quiet:
        return False
    return (
        index == 1
        or index == total
        or index % args.progress_every == 0
    )


def print_progress(
    args: argparse.Namespace,
    timings: dict[str, float],
    index: int,
    total: int,
    message: str,
) -> None:
    """Print sparse progress with percentage and measure console overhead."""
    if not should_print_progress(args, index, total):
        return

    if total > 0:
        percentage = max(0.0, min(100.0, index * 100.0 / total))
    else:
        percentage = 100.0

    stage_started = time.perf_counter()
    print(f"[{percentage:6.2f}%] [{index}/{total}] {message}")
    add_timing(timings, "console_progress", stage_started)


def ensure_empty_output(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"output-root is not a directory: {path}")
        if any(path.iterdir()):
            raise RuntimeError(f"output-root must be new or empty: {path}")
    else:
        path.mkdir(parents=True)


@dataclass(frozen=True)
class InputSource:
    """One plain Y-file or one Y-file member stored inside an archive."""

    container: Path
    member: str | None = None
    uncompressed_size: int = 0
    compressed_size: int = 0
    archive_format: str | None = None

    @property
    def is_zip_member(self) -> bool:
        return self.member is not None and self.archive_format == "zip"

    @property
    def is_rar_member(self) -> bool:
        return self.member is not None and self.archive_format == "rar"

    @property
    def is_archive_member(self) -> bool:
        return self.member is not None

    @property
    def display_name(self) -> str:
        if self.member is None:
            return str(self.container)
        return f"{self.container}!/{self.member}"


def matches_pattern(name: str, pattern: str) -> bool:
    """
    Match a source name against --pattern.

    A pattern containing a slash is matched against the relative path.
    A simple pattern such as "*" or "*.y" is matched against the basename.
    """
    normalized_name = name.replace("\\", "/").lstrip("/")
    normalized_pattern = pattern.replace("\\", "/")

    if "/" in normalized_pattern:
        return fnmatch.fnmatchcase(normalized_name, normalized_pattern)

    return fnmatch.fnmatchcase(PurePosixPath(normalized_name).name, normalized_pattern)


def iter_input_sources(root: Path, pattern: str, recursive: bool) -> list[InputSource]:
    """
    Find plain input files and members of ZIP archives.

    ZIP members are only enumerated here. Their contents are decompressed later,
    one member at a time, so the complete archive is never extracted to disk or
    loaded into RAM.
    """
    if root.is_file():
        paths = [root]
        relative_names = {root: root.name}
    else:
        iterator = root.rglob("*") if recursive else root.glob("*")
        paths = sorted(path for path in iterator if path.is_file())
        relative_names = {
            path: path.relative_to(root).as_posix()
            for path in paths
        }

    sources: list[InputSource] = []

    for path in paths:
        suffix = path.suffix.lower()
        if suffix not in {".zip", ".rar"}:
            relative_name = relative_names[path]
            if matches_pattern(relative_name, pattern):
                size = path.stat().st_size
                sources.append(
                    InputSource(
                        container=path,
                        uncompressed_size=size,
                        compressed_size=size,
                    )
                )
            continue

        if suffix == ".zip":
            try:
                with zipfile.ZipFile(path, mode="r") as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue

                        member = info.filename.replace("\\", "/").lstrip("/")
                        if not member:
                            continue

                        # Nested archives are intentionally not expanded.
                        if member.lower().endswith((".zip", ".rar")):
                            continue

                        if not recursive and "/" in member:
                            continue

                        if not matches_pattern(member, pattern):
                            continue

                        if info.flag_bits & 0x1:
                            raise RuntimeError(
                                f"encrypted ZIP member is not supported: {path}!/{member}"
                            )

                        sources.append(
                            InputSource(
                                container=path,
                                member=info.filename,
                                uncompressed_size=int(info.file_size),
                                compressed_size=int(info.compress_size),
                                archive_format="zip",
                            )
                        )
            except zipfile.BadZipFile as exc:
                raise RuntimeError(f"invalid ZIP archive: {path}: {exc}") from exc
            continue

        try:
            import rarfile
        except ImportError as exc:
            raise RuntimeError(
                f"RAR archive found but Python package 'rarfile' is not installed: {path}"
            ) from exc

        try:
            with rarfile.RarFile(path) as archive:
                for info in archive.infolist():
                    if info.isdir():
                        continue

                    member = info.filename.replace("\\", "/").lstrip("/")
                    if not member:
                        continue

                    if member.lower().endswith((".zip", ".rar")):
                        continue

                    if not recursive and "/" in member:
                        continue

                    if not matches_pattern(member, pattern):
                        continue

                    sources.append(
                        InputSource(
                            container=path,
                            member=info.filename,
                            uncompressed_size=int(info.file_size),
                            compressed_size=int(getattr(info, "compress_size", 0)),
                            archive_format="rar",
                        )
                    )
        except Exception as exc:
            raise RuntimeError(f"invalid or unsupported RAR archive: {path}: {exc}") from exc

    return sorted(
        sources,
        key=lambda item: (
            str(item.container).lower(),
            (item.member or "").lower(),
        ),
    )


class InputSourceReader:
    """
    Reuse one open archive handle and read members sequentially.

    In prefetch mode this object is used only by the background worker thread.
    In non-prefetch mode it performs the same synchronous behavior as P3.
    """

    def __init__(self) -> None:
        self._archive_path: Path | None = None
        self._archive = None
        self._archive_format: str | None = None

    def __enter__(self) -> "InputSourceReader":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
        self._archive = None
        self._archive_path = None
        self._archive_format = None

    def _get_archive(self, source: InputSource):
        # Sources are sorted by container. Keeping only the current archive
        # open avoids repeated central-directory parsing without accumulating
        # open handles when more than one ZIP exists.
        if (
            self._archive is None
            or self._archive_path != source.container
            or self._archive_format != source.archive_format
        ):
            self.close()
            if source.is_zip_member:
                self._archive = zipfile.ZipFile(source.container, mode="r")
            elif source.is_rar_member:
                try:
                    import rarfile
                except ImportError as exc:
                    raise RuntimeError(
                        "RAR support requires Python package 'rarfile'"
                    ) from exc
                self._archive = rarfile.RarFile(source.container)
            else:
                raise RuntimeError(f"not an archive member: {source.display_name}")
            self._archive_path = source.container
            self._archive_format = source.archive_format
        return self._archive

    def read_archive_payload(self, source: InputSource) -> tuple[bytes, float]:
        """
        Decompress one archive member and return payload plus worker elapsed time.

        This method does not call ObsPy and is safe to execute in the dedicated
        single background worker used by the prefetch pipeline.
        """
        if not source.is_archive_member:
            raise RuntimeError(
                f"read_archive_payload requires an archive member: {source.display_name}"
            )

        started = time.perf_counter()
        try:
            archive = self._get_archive(source)
            with archive.open(source.member, mode="r") as member_handle:
                payload = member_handle.read()
        except Exception as exc:
            raise RuntimeError(
                f"cannot decompress archive member {source.display_name}: {exc}"
            ) from exc

        return payload, time.perf_counter() - started

    def read_zip_payload(self, source: InputSource) -> tuple[bytes, float]:
        return self.read_archive_payload(source)

    def read_y_stream(self, source: InputSource) -> Stream:
        """Synchronous input reader retained for --zip-prefetch 0."""
        try:
            if not source.is_archive_member:
                return read(str(source.container), format="Y")

            payload, _ = self.read_archive_payload(source)
            return read(io.BytesIO(payload), format="Y")
        except Exception as exc:
            raise RuntimeError(
                f"cannot read Nanometrics Y source {source.display_name}: {exc}"
            ) from exc


def decode_zip_payload(source: InputSource, payload: bytes) -> Stream:
    """Decode one already-decompressed Y-file payload with ObsPy."""
    try:
        return read(io.BytesIO(payload), format="Y")
    except Exception as exc:
        raise RuntimeError(
            f"cannot decode Nanometrics Y source {source.display_name}: {exc}"
        ) from exc


def submit_archive_prefetch(
    executor: ThreadPoolExecutor,
    reader: InputSourceReader,
    source: InputSource | None,
) -> Future[tuple[bytes, float]] | None:
    if source is None or not source.is_archive_member:
        return None
    return executor.submit(reader.read_archive_payload, source)


def read_sources_with_zip_prefetch(
    sources: list[InputSource],
    args: argparse.Namespace,
    timings: dict[str, float],
):
    """
    Yield (source, Stream) in the original deterministic source order.

    Only one worker is used. While the main thread decodes source N with ObsPy,
    the worker decompresses source N+1. At most the current and next ZIP member
    payloads are simultaneously resident in RAM.
    """
    if not sources:
        return

    with InputSourceReader() as zip_reader:
        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="zip-prefetch",
        ) as executor:
            current_future = submit_archive_prefetch(
                executor,
                zip_reader,
                sources[0],
            )

            for index, source in enumerate(sources):
                source_started = time.perf_counter()

                payload: bytes | None = None
                if source.is_archive_member:
                    if current_future is None:
                        raise RuntimeError(
                            f"missing archive prefetch future for {source.display_name}"
                        )

                    wait_started = time.perf_counter()
                    payload, worker_elapsed = current_future.result()
                    add_timing(timings, "zip_prefetch_wait", wait_started)
                    timings["zip_decompress_worker"] += worker_elapsed

                # Submit N+1 before decoding N, creating the overlap.
                next_source = (
                    sources[index + 1]
                    if index + 1 < len(sources)
                    else None
                )
                current_future = submit_archive_prefetch(
                    executor,
                    zip_reader,
                    next_source,
                )

                decode_started = time.perf_counter()
                if source.is_archive_member:
                    current = decode_zip_payload(source, payload)
                    add_timing(timings, "zip_y_decode", decode_started)
                else:
                    current = read(str(source.container), format="Y")
                    add_timing(timings, "plain_y_read", decode_started)

                add_timing(timings, "y_read", source_started)
                yield source, current


@dataclass(frozen=True)
class WorkerMetadataArgs:
    network: str | None
    station: str | None
    location: str | None
    channel: str | None


_Y_WORKER_METADATA_ARGS: WorkerMetadataArgs | None = None
_Y_WORKER_CORRECTIONS: dict[str, tuple[str, str, str, str]] | None = None


def initialize_y_decode_worker(
    metadata_args: WorkerMetadataArgs,
    corrections: dict[str, tuple[str, str, str, str]] | None,
) -> None:
    """Initialize immutable metadata shared by all tasks in one worker."""
    global _Y_WORKER_METADATA_ARGS, _Y_WORKER_CORRECTIONS
    _Y_WORKER_METADATA_ARGS = metadata_args
    _Y_WORKER_CORRECTIONS = corrections


def chunk_input_sources(
    sources: list[InputSource],
    chunk_size: int,
) -> list[tuple[int, list[InputSource]]]:
    return [
        (chunk_index, sources[start : start + chunk_size])
        for chunk_index, start in enumerate(
            range(0, len(sources), chunk_size)
        )
    ]


def decode_source_chunk_worker(
    task: tuple[int, list[InputSource]],
) -> tuple[int, Stream, int, dict[str, float]]:
    """
    Decode one contiguous source chunk inside a worker process.

    The returned Stream preserves the source order inside the chunk. The main
    process consumes chunk results in task order, so global ordering is also
    deterministic.
    """
    chunk_index, sources = task
    if _Y_WORKER_METADATA_ARGS is None:
        raise RuntimeError("Y decode worker was not initialized")

    local_timings: dict[str, float] = defaultdict(float)
    chunk_started = time.perf_counter()
    chunk_stream = Stream()

    with InputSourceReader() as source_reader:
        for source in sources:
            if source.is_archive_member:
                stage_started = time.perf_counter()
                payload, decompress_elapsed = source_reader.read_archive_payload(source)
                local_timings["worker_zip_decompress"] += decompress_elapsed

                decode_started = time.perf_counter()
                current = decode_zip_payload(source, payload)
                local_timings["worker_y_decode"] += (
                    time.perf_counter() - decode_started
                )
                local_timings["worker_y_source_total"] += (
                    time.perf_counter() - stage_started
                )
            else:
                stage_started = time.perf_counter()
                current = read(str(source.container), format="Y")
                local_timings["worker_plain_y_read"] += (
                    time.perf_counter() - stage_started
                )
                local_timings["worker_y_source_total"] += (
                    time.perf_counter() - stage_started
                )

            stage_started = time.perf_counter()
            apply_metadata(
                current,
                _Y_WORKER_METADATA_ARGS,
                source.display_name,
                _Y_WORKER_CORRECTIONS,
            )
            local_timings["worker_metadata_apply"] += (
                time.perf_counter() - stage_started
            )

            stage_started = time.perf_counter()
            chunk_stream += current
            local_timings["worker_stream_append"] += (
                time.perf_counter() - stage_started
            )

    local_timings["worker_chunk_wall"] = time.perf_counter() - chunk_started
    return chunk_index, chunk_stream, len(sources), dict(local_timings)


def read_sources_in_parallel(
    sources: list[InputSource],
    args: argparse.Namespace,
    corrections: dict[str, tuple[str, str, str, str]] | None,
    timings: dict[str, float],
):
    """
    Decode source chunks in worker processes and yield ordered chunk Streams.

    executor.map preserves task order even though chunks execute concurrently.
    This keeps the same deterministic input ordering as the sequential reader.
    """
    tasks = chunk_input_sources(sources, args.y_chunk_size)
    metadata_args = WorkerMetadataArgs(
        network=args.network,
        station=args.station,
        location=args.location,
        channel=args.channel,
    )

    executor_started = time.perf_counter()
    with ProcessPoolExecutor(
        max_workers=args.y_workers,
        initializer=initialize_y_decode_worker,
        initargs=(metadata_args, corrections),
    ) as executor:
        timings["parallel_executor_startup"] += (
            time.perf_counter() - executor_started
        )

        map_started = time.perf_counter()
        results = executor.map(
            decode_source_chunk_worker,
            tasks,
            chunksize=1,
        )

        for chunk_index, chunk_stream, source_count, worker_timings in results:
            for name, seconds in worker_timings.items():
                timings[name] += seconds
            yield chunk_index, chunk_stream, source_count

        timings["parallel_executor_map_wall"] += (
            time.perf_counter() - map_started
        )




def clean_code(value: str) -> str:
    return "".join(str(value or "").strip().split())


def build_sid_key(network: str, station: str, location: str, channel: str) -> str:
    return "_".join(
        (
            clean_code(network),
            clean_code(station),
            clean_code(location),
            clean_code(channel),
        )
    )


def split_sid_key(value: str) -> tuple[str, str, str, str]:
    parts = value.split("_")
    if len(parts) != 4:
        raise RuntimeError(f"invalid CorrectSID value: {value!r}")
    return tuple(clean_code(part) for part in parts)


def load_correct_sid(path: Path) -> dict[str, tuple[str, str, str, str]]:
    if not path.is_file():
        raise RuntimeError(f"CorrectSID.txt not found next to Python tool: {path}")

    corrections: dict[str, tuple[str, str, str, str]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or "=>" not in line:
            continue
        raw_key, corrected_key = (part.strip() for part in line.split("=>", 1))
        if not raw_key or not corrected_key:
            continue
        corrections[clean_code(raw_key)] = split_sid_key(corrected_key)

    if not corrections:
        raise RuntimeError(f"CorrectSID.txt has no usable entries: {path}")
    return corrections


def find_corrected_sid(
    corrections: dict[str, tuple[str, str, str, str]],
    raw_network: str,
    raw_station: str,
    raw_location: str,
    raw_channel: str,
) -> tuple[str, str, str, str] | None:
    raw_key = build_sid_key(raw_network, raw_station, raw_location, raw_channel)
    corrected = corrections.get(raw_key)
    if corrected is not None:
        return corrected

    if clean_code(raw_network):
        return None

    suffix = "_" + build_sid_key("", raw_station, raw_location, raw_channel).lstrip("_")
    matches = [
        value
        for key, value in corrections.items()
        if key.endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]

    return None


def apply_metadata(
    stream: Stream,
    args: argparse.Namespace,
    source: str,
    corrections: dict[str, tuple[str, str, str, str]] | None,
) -> None:
    for trace in stream:
        if corrections is not None:
            corrected = find_corrected_sid(
                corrections,
                trace.stats.network,
                trace.stats.station,
                trace.stats.location,
                trace.stats.channel,
            )
            if corrected is None:
                raw_key = build_sid_key(
                    trace.stats.network,
                    trace.stats.station,
                    trace.stats.location,
                    trace.stats.channel,
                )
                raise RuntimeError(f"{source}: missing CorrectSID.txt entry for raw SID {raw_key!r}")
            (
                trace.stats.network,
                trace.stats.station,
                trace.stats.location,
                trace.stats.channel,
            ) = corrected
        elif args.network is not None:
            trace.stats.network = args.network

        if args.station is not None:
            trace.stats.station = args.station
        if args.location is not None:
            trace.stats.location = args.location
        if args.channel is not None:
            trace.stats.channel = args.channel

        missing = [
            name
            for name in ("network", "station", "channel")
            if not getattr(trace.stats, name, "")
        ]
        if missing:
            raise RuntimeError(f"{source}: missing required metadata after overrides: {', '.join(missing)}")

        if trace.data.dtype != np.int32:
            trace.data = trace.data.astype(np.int32, copy=False)


@dataclass
class PackBackendState:
    requested: str
    active: str
    used: set[str]
    fallback_reason: str | None = None




def day_start(time_: UTCDateTime) -> UTCDateTime:
    return UTCDateTime(time_.year, time_.month, time_.day, precision=9)


def validate_packed_size(
    file_size: int,
    trace: Trace,
    record_length: int,
) -> None:
    if file_size == 0:
        raise RuntimeError(f"ObsPy wrote empty MiniSEED data for {trace.id}")
    if file_size % record_length != 0:
        raise RuntimeError(
            f"packed MiniSEED size is not divisible by record length for {trace.id}: "
            f"size={file_size}, reclen={record_length}"
        )


@dataclass
class PackedTraceResult:
    trace_index: int
    payload: bytes
    record_starts: list[UTCDateTime]
    mseed_write_seconds: float
    buffer_copy_seconds: float
    record_slice_seconds: float
    header_parse_seconds: float


def pack_trace_memory_worker(
    task: tuple[int, Trace, str, int],
) -> PackedTraceResult:
    """
    Pack and parse one merged Trace in a worker thread.

    No SDS file is opened here. The main thread later consumes results in
    original Trace order and performs all routing/writing deterministically.
    """
    trace_index, trace, encoding, record_length = task
    buffer = io.BytesIO()

    started = time.perf_counter()
    Stream([trace]).write(
        buffer,
        format="MSEED",
        encoding=encoding,
        reclen=record_length,
        flush=True,
    )
    mseed_write_seconds = time.perf_counter() - started

    started = time.perf_counter()
    payload = buffer.getvalue()
    buffer_copy_seconds = time.perf_counter() - started

    validate_packed_size(len(payload), trace, record_length)

    view = memoryview(payload)
    record_count = len(payload) // record_length
    record_starts: list[UTCDateTime] = []
    record_slice_seconds = 0.0
    header_parse_seconds = 0.0

    for record_index in range(record_count):
        offset = record_index * record_length

        started = time.perf_counter()
        record = view[offset : offset + record_length]
        record_slice_seconds += time.perf_counter() - started

        started = time.perf_counter()
        info = get_record_information(io.BytesIO(record))
        header_parse_seconds += time.perf_counter() - started

        actual_length = int(info.get("record_length", record_length))
        if actual_length != record_length:
            raise RuntimeError(
                f"unexpected MiniSEED record length for {trace.id}: "
                f"record={record_index + 1}, expected={record_length}, "
                f"actual={actual_length}"
            )

        record_start = info.get("starttime")
        if record_start is None:
            raise RuntimeError(
                f"MiniSEED record start time is missing for {trace.id}: "
                f"record={record_index + 1}"
            )

        record_starts.append(record_start)

    return PackedTraceResult(
        trace_index=trace_index,
        payload=payload,
        record_starts=record_starts,
        mseed_write_seconds=mseed_write_seconds,
        buffer_copy_seconds=buffer_copy_seconds,
        record_slice_seconds=record_slice_seconds,
        header_parse_seconds=header_parse_seconds,
    )


def pack_stream_in_parallel(
    stream: Stream,
    args: argparse.Namespace,
    timings: dict[str, float],
) -> list[PackedTraceResult]:
    """
    Pack all merged Traces with a bounded thread pool.

    executor.map preserves input order. Results are fully produced before SDS
    writing begins, so an auto-mode failure can safely fall back to disk
    without having written duplicate records.
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
        results = list(executor.map(pack_trace_memory_worker, tasks))
    timings["parallel_pack_wall"] += time.perf_counter() - started

    for result in results:
        timings["pack_thread_mseed_write_sum"] += result.mseed_write_seconds
        timings["pack_thread_buffer_copy_sum"] += result.buffer_copy_seconds
        timings["pack_thread_record_slice_sum"] += result.record_slice_seconds
        timings["pack_thread_header_parse_sum"] += result.header_parse_seconds

    return results


def iter_packed_records(
    payload: bytes,
    trace: Trace,
    args: argparse.Namespace,
    timings: dict[str, float],
):
    """
    Yield fixed-length MiniSEED records from an immutable in-memory payload.

    A memoryview avoids copying each record again when it is routed to SDS.
    """
    view = memoryview(payload)
    record_count = len(payload) // args.record_length

    for record_index in range(record_count):
        offset = record_index * args.record_length

        stage_started = time.perf_counter()
        record = view[offset : offset + args.record_length]
        add_timing(timings, "pack_memory_record_slice", stage_started)

        stage_started = time.perf_counter()
        info = get_record_information(io.BytesIO(record))
        add_timing(timings, "pack_record_header_parse", stage_started)

        actual_length = int(info.get("record_length", args.record_length))
        if actual_length != args.record_length:
            raise RuntimeError(
                f"unexpected MiniSEED record length for {trace.id}: "
                f"record={record_index + 1}, expected={args.record_length}, "
                f"actual={actual_length}"
            )

        record_start = info.get("starttime")
        if record_start is None:
            raise RuntimeError(
                f"MiniSEED record start time is missing for {trace.id}: "
                f"record={record_index + 1}"
            )

        yield record_start, record


def pack_trace_to_memory(
    trace: Trace,
    args: argparse.Namespace,
    timings: dict[str, float],
) -> bytes:
    """
    Pack one complete Trace directly into RAM.

    The returned immutable bytes payload remains valid while record memoryviews
    are yielded and written to SDS.
    """
    buffer = io.BytesIO()

    stage_started = time.perf_counter()
    Stream([trace]).write(
        buffer,
        format="MSEED",
        encoding=args.encoding,
        reclen=args.record_length,
        flush=True,
    )
    add_timing(timings, "pack_memory_mseed_write", stage_started)

    stage_started = time.perf_counter()
    payload = buffer.getvalue()
    add_timing(timings, "pack_memory_buffer_copy", stage_started)

    validate_packed_size(len(payload), trace, args.record_length)
    return payload


def pack_trace_records_disk(
    trace: Trace,
    args: argparse.Namespace,
    temp_dir: Path,
    trace_index: int,
    timings: dict[str, float],
):
    """Original temporary-file MiniSEED packing backend."""
    temp_path = temp_dir / f"trace_{trace_index:08d}.mseed"

    stage_started = time.perf_counter()
    Stream([trace]).write(
        str(temp_path),
        format="MSEED",
        encoding=args.encoding,
        reclen=args.record_length,
        flush=True,
    )
    add_timing(timings, "pack_disk_mseed_write", stage_started)

    stage_started = time.perf_counter()
    file_size = temp_path.stat().st_size
    add_timing(timings, "pack_disk_file_stat", stage_started)
    validate_packed_size(file_size, trace, args.record_length)

    try:
        stage_started = time.perf_counter()
        handle = temp_path.open("rb")
        add_timing(timings, "pack_disk_file_open", stage_started)

        try:
            record_index = 0
            while True:
                stage_started = time.perf_counter()
                record = handle.read(args.record_length)
                add_timing(timings, "pack_disk_record_read", stage_started)

                if not record:
                    break

                record_index += 1
                if len(record) != args.record_length:
                    raise RuntimeError(
                        f"short MiniSEED record for {trace.id}: "
                        f"record={record_index}, bytes={len(record)}"
                    )

                stage_started = time.perf_counter()
                info = get_record_information(io.BytesIO(record))
                add_timing(timings, "pack_record_header_parse", stage_started)

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
            add_timing(timings, "pack_disk_file_close", stage_started)
    finally:
        stage_started = time.perf_counter()
        temp_path.unlink(missing_ok=True)
        add_timing(timings, "pack_disk_file_delete", stage_started)


def pack_trace_records(
    trace: Trace,
    args: argparse.Namespace,
    temp_dir: Path,
    trace_index: int,
    timings: dict[str, float],
    backend_state: PackBackendState,
):
    """
    Pack a complete Trace and yield its raw fixed-length MiniSEED records.

    In auto mode, RAM is attempted first. Fallback to disk can only happen
    before any record from the current Trace has been yielded, preventing
    duplicate SDS records.
    """
    if backend_state.active == "memory":
        try:
            payload = pack_trace_to_memory(trace, args, timings)
        except Exception as exc:
            if backend_state.requested == "memory":
                raise

            backend_state.active = "disk"
            backend_state.fallback_reason = f"{type(exc).__name__}: {exc}"
            print(
                "WARNING: RAM MiniSEED packing failed; switching to disk backend: "
                f"{backend_state.fallback_reason}",
                file=sys.stderr,
            )
        else:
            backend_state.used.add("memory")
            yield from iter_packed_records(payload, trace, args, timings)
            return

    backend_state.used.add("disk")
    yield from pack_trace_records_disk(
        trace,
        args,
        temp_dir,
        trace_index,
        timings,
    )


def sds_path(root: Path, trace: Trace, day: UTCDateTime) -> Path:
    relative = SDS_FMTSTR.format(
        year=day.year,
        network=trace.stats.network,
        station=trace.stats.station,
        location=trace.stats.location,
        channel=trace.stats.channel,
        sds_type="D",
        doy=day.julday,
    )
    return root / relative


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    timings: dict[str, float] = defaultdict(float)

    stage_started = time.perf_counter()
    input_root = args.input_root.resolve()
    output_root = args.output_root.resolve()

    if not input_root.exists():
        raise RuntimeError(f"input-root does not exist: {input_root}")
    if not input_root.is_dir() and not input_root.is_file():
        raise RuntimeError(f"input-root is neither a file nor a directory: {input_root}")
    if input_root == output_root:
        raise RuntimeError("input-root and output-root must be different")
    if args.report and output_root in args.report.resolve().parents:
        raise RuntimeError("--report must be outside output-root")
    if args.no_correct_sid and not args.network:
        raise RuntimeError("--network is required when --no-correct-sid is used")
    add_timing(timings, "argument_and_path_validation", stage_started)

    stage_started = time.perf_counter()
    ensure_empty_output(output_root)
    add_timing(timings, "output_prepare", stage_started)

    stage_started = time.perf_counter()
    sources = iter_input_sources(input_root, args.pattern, args.recursive)
    add_timing(timings, "input_discovery_and_zip_scan", stage_started)
    if not sources:
        raise RuntimeError(f"no input files matched {args.pattern!r} under {input_root}")

    stage_started = time.perf_counter()
    plain_file_count = sum(not source.is_archive_member for source in sources)
    zip_member_count = sum(source.is_zip_member for source in sources)
    rar_member_count = sum(source.is_rar_member for source in sources)
    zip_archives = sorted(
        {source.container for source in sources if source.is_zip_member},
        key=lambda path: str(path).lower(),
    )
    rar_archives = sorted(
        {source.container for source in sources if source.is_rar_member},
        key=lambda path: str(path).lower(),
    )
    zip_uncompressed_bytes = sum(
        source.uncompressed_size for source in sources if source.is_archive_member
    )
    zip_compressed_bytes = sum(
        source.compressed_size for source in sources if source.is_archive_member
    )
    largest_zip_member_bytes = max(
        (
            source.uncompressed_size
            for source in sources
            if source.is_archive_member
        ),
        default=0,
    )
    add_timing(timings, "input_statistics", stage_started)

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")

    stage_started = time.perf_counter()
    corrections = None if args.no_correct_sid else load_correct_sid(args.correct_sid.resolve())
    add_timing(timings, "correct_sid_load", stage_started)
    if corrections is not None:
        print(f"Using CorrectSID: {args.correct_sid.resolve()} ({len(corrections)} entries)")

    print(
        f"Input sources: {len(sources)} "
        f"(plain={plain_file_count}, zip_members={zip_member_count}, "
        f"rar_members={rar_member_count}, "
        f"zip_archives={len(zip_archives)}, rar_archives={len(rar_archives)})"
    )
    print(
        "Performance profile: "
        f"y-workers={args.y_workers}, "
        f"y-chunk-size={args.y_chunk_size}, "
        f"pack-workers={args.pack_workers}"
    )
    if zip_member_count or rar_member_count:
        print(
            "Archive mode: members are decompressed one at a time in RAM; "
            "nothing is extracted to disk."
        )

    stream = Stream()
    input_pipeline_started = time.perf_counter()
    print(f"Checking ObsPy Y reader with: {sources[0].display_name}")

    if args.y_workers > 1:
        if args.zip_prefetch and not args.quiet:
            print(
                "Note: --zip-prefetch is ignored when --y-workers is greater "
                "than 1 because each worker reads its own ZIP chunk."
            )

        processed_sources = 0
        for chunk_index, chunk_stream, source_count in read_sources_in_parallel(
            sources,
            args,
            corrections,
            timings,
        ):
            stage_started = time.perf_counter()
            stream += chunk_stream
            add_timing(timings, "stream_append", stage_started)

            processed_sources += source_count
            progress_source = sources[processed_sources - 1]
            print_progress(
                args,
                timings,
                processed_sources,
                len(sources),
                (
                    f"parallel decoded through "
                    f"{progress_source.display_name}"
                ),
            )
    elif args.zip_prefetch:
        source_streams = read_sources_with_zip_prefetch(
            sources,
            args,
            timings,
        )
        for index, (source, current) in enumerate(source_streams, start=1):
            stage_started = time.perf_counter()
            apply_metadata(current, args, source.display_name, corrections)
            add_timing(timings, "metadata_apply", stage_started)

            stage_started = time.perf_counter()
            stream += current
            add_timing(timings, "stream_append", stage_started)

            print_progress(
                args,
                timings,
                index,
                len(sources),
                f"read {source.display_name}",
            )
    else:
        with InputSourceReader() as source_reader:
            for index, source in enumerate(sources, start=1):
                stage_started = time.perf_counter()
                current = source_reader.read_y_stream(source)
                add_timing(timings, "y_read", stage_started)

                stage_started = time.perf_counter()
                apply_metadata(current, args, source.display_name, corrections)
                add_timing(timings, "metadata_apply", stage_started)

                stage_started = time.perf_counter()
                stream += current
                add_timing(timings, "stream_append", stage_started)

                print_progress(
                    args,
                    timings,
                    index,
                    len(sources),
                    f"read {source.display_name}",
                )

    timings["input_pipeline_wall"] = time.perf_counter() - input_pipeline_started

    if args.y_workers > 1:
        timings["parallel_decode_wall"] = max(
            0.0,
            timings["input_pipeline_wall"]
            - timings["stream_append"]
            - timings["console_progress"],
        )

    stage_started = time.perf_counter()
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    add_timing(timings, "sort_before_merge", stage_started)

    stage_started = time.perf_counter()
    stream.merge(method=-1)
    add_timing(timings, "merge_method_minus_1", stage_started)

    stage_started = time.perf_counter()
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    add_timing(timings, "sort_after_merge", stage_started)

    record_counts: dict[Path, int] = defaultdict(int)
    output_handles = {}
    total_records = 0

    stage_started = time.perf_counter()
    total_samples = sum(int(trace.stats.npts) for trace in stream)
    add_timing(timings, "sample_count", stage_started)

    backend_state = PackBackendState(
        requested=args.pack_backend,
        active="disk" if args.pack_backend == "disk" else "memory",
        used=set(),
    )

    pack_pipeline_started = time.perf_counter()
    parallel_results: list[PackedTraceResult] | None = None

    if args.pack_workers > 1 and backend_state.active == "memory":
        try:
            parallel_results = pack_stream_in_parallel(
                stream,
                args,
                timings,
            )
        except Exception as exc:
            if backend_state.requested == "memory":
                raise

            backend_state.active = "disk"
            backend_state.fallback_reason = f"{type(exc).__name__}: {exc}"
            print(
                "WARNING: parallel RAM packing failed; switching to the "
                f"sequential disk backend: {backend_state.fallback_reason}",
                file=sys.stderr,
            )
        else:
            backend_state.used.add("memory")

    try:
        if parallel_results is not None:
            if len(parallel_results) != len(stream):
                raise RuntimeError(
                    "parallel pack result count mismatch: "
                    f"expected={len(stream)}, actual={len(parallel_results)}"
                )

            for expected_trace_index, (trace, result) in enumerate(
                zip(stream, parallel_results),
                start=1,
            ):
                trace_record_count = 0
                payload_view = memoryview(result.payload)

                if result.trace_index != expected_trace_index:
                    raise RuntimeError(
                        "parallel pack result order mismatch: "
                        f"expected={expected_trace_index}, "
                        f"actual={result.trace_index}"
                    )

                for record_index, record_start in enumerate(result.record_starts):
                    offset = record_index * args.record_length

                    stage_started = time.perf_counter()
                    record = payload_view[offset : offset + args.record_length]
                    add_timing(
                        timings,
                        "pack_parallel_route_record_slice",
                        stage_started,
                    )

                    stage_started = time.perf_counter()
                    day = day_start(record_start)
                    path = sds_path(output_root, trace, day)
                    add_timing(timings, "sds_route_path", stage_started)

                    if path not in output_handles:
                        stage_started = time.perf_counter()
                        path.parent.mkdir(parents=True, exist_ok=True)
                        output_handles[path] = path.open("ab")
                        add_timing(timings, "sds_open_output", stage_started)

                    stage_started = time.perf_counter()
                    output_handles[path].write(record)
                    add_timing(timings, "sds_record_write", stage_started)

                    record_counts[path] += 1
                    total_records += 1
                    trace_record_count += 1

                print_progress(
                    args,
                    timings,
                    result.trace_index,
                    len(stream),
                    (
                        f"parallel packed {trace.id} "
                        f"records={trace_record_count} samples={trace.stats.npts}"
                    ),
                )
        else:
            with tempfile.TemporaryDirectory(
                prefix="yfile_reference_pack_"
            ) as temp:
                temp_dir = Path(temp)
                for trace_index, trace in enumerate(stream, start=1):
                    trace_record_count = 0
                    for record_start, record in pack_trace_records(
                        trace,
                        args,
                        temp_dir,
                        trace_index,
                        timings,
                        backend_state,
                    ):
                        stage_started = time.perf_counter()
                        day = day_start(record_start)
                        path = sds_path(output_root, trace, day)
                        add_timing(timings, "sds_route_path", stage_started)

                        if path not in output_handles:
                            stage_started = time.perf_counter()
                            path.parent.mkdir(parents=True, exist_ok=True)
                            output_handles[path] = path.open("ab")
                            add_timing(timings, "sds_open_output", stage_started)

                        stage_started = time.perf_counter()
                        output_handles[path].write(record)
                        add_timing(timings, "sds_record_write", stage_started)

                        record_counts[path] += 1
                        total_records += 1
                        trace_record_count += 1

                    print_progress(
                        args,
                        timings,
                        trace_index,
                        len(stream),
                        (
                            f"packed {trace.id} "
                            f"records={trace_record_count} "
                            f"samples={trace.stats.npts}"
                        ),
                    )
    finally:
        stage_started = time.perf_counter()
        for handle in output_handles.values():
            handle.close()
        add_timing(timings, "sds_close_outputs", stage_started)
        timings["pack_and_sds_pipeline_wall"] = (
            time.perf_counter() - pack_pipeline_started
        )

    stage_started = time.perf_counter()
    for index, path in enumerate(sorted(record_counts), start=1):
        expected_size = record_counts[path] * args.record_length
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                f"SDS file size mismatch: {path}: "
                f"expected={expected_size}, actual={actual_size}"
            )
        print_progress(
            args,
            timings,
            index,
            len(record_counts),
            (
                f"wrote {path} "
                f"records={record_counts[path]} bytes={actual_size}"
            ),
        )
    add_timing(timings, "output_validation", stage_started)

    elapsed_seconds = time.perf_counter() - started

    # Helpful aggregate values. Aggregate wall timers are excluded from the
    # non-overlapping measured total to avoid double-counting.
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
    if parallel_results is not None:
        timings["pack_total"] = (
            timings["parallel_pack_wall"]
            + timings["pack_parallel_route_record_slice"]
        )
    else:
        timings["pack_total"] = sum(
            timings[name] for name in sequential_pack_component_names
        )

    timings["sds_output_total"] = (
        timings["sds_route_path"]
        + timings["sds_open_output"]
        + timings["sds_record_write"]
        + timings["sds_close_outputs"]
        + timings["output_validation"]
    )

    if args.y_workers > 1:
        timings["input_pipeline_unmeasured"] = max(
            0.0,
            timings["input_pipeline_wall"]
            - timings["parallel_decode_wall"]
            - timings["stream_append"]
            - timings["console_progress"],
        )
    else:
        timings["input_pipeline_unmeasured"] = max(
            0.0,
            timings["input_pipeline_wall"]
            - timings["y_read"]
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
        "input_pipeline_wall",
        "pack_and_sds_pipeline_wall",
        "input_pipeline_unmeasured",
        "pack_pipeline_unmeasured",
        # These are diagnostic subcomponents already included in y_read.
        "zip_prefetch_wait",
        "zip_decompress_worker",
        "zip_y_decode",
        "plain_y_read",
        # Parallel worker timings are sums across processes and may exceed wall time.
        "parallel_executor_startup",
        "parallel_executor_map_wall",
        "worker_zip_decompress",
        "worker_y_decode",
        "worker_plain_y_read",
        "worker_y_source_total",
        "worker_metadata_apply",
        "worker_stream_append",
        "worker_chunk_wall",
        # Parallel pack worker values are summed diagnostics, not wall time.
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
    timings["unmeasured_overhead"] = max(
        0.0,
        elapsed_seconds - timings["measured_stage_total"],
    )

    report = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "obspy": obspy.__version__,
        "numpy": np.__version__,
        "files_read": len(sources),
        "plain_files_read": plain_file_count,
        "zip_archives_read": len(zip_archives),
        "zip_members_read": zip_member_count,
        "zip_compressed_bytes": zip_compressed_bytes,
        "zip_uncompressed_bytes": zip_uncompressed_bytes,
        "largest_zip_member_bytes": largest_zip_member_bytes,
        "sds_files_written": len(record_counts),
        "records_written": total_records,
        "samples_written": total_samples,
        "encoding": args.encoding,
        "record_length": args.record_length,
        "correct_sid": str(args.correct_sid.resolve()) if corrections is not None else None,
        "progress_every": args.progress_every,
        "quiet": args.quiet,
        "benchmark": args.benchmark,
        "zip_prefetch": args.zip_prefetch,
        "y_workers": args.y_workers,
        "y_chunk_size": args.y_chunk_size,
        "pack_workers": args.pack_workers,
        "merged_trace_count": len(stream),
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
        "input_decode_mode": (
            "process_pool"
            if args.y_workers > 1
            else ("zip_prefetch" if args.zip_prefetch else "sequential")
        ),
        "optimized_profile": {
            "recommended_y_workers": 4,
            "recommended_y_chunk_size": 100,
            "recommended_pack_workers": 4,
            "active": (
                args.y_workers == 4
                and args.y_chunk_size == 100
                and args.pack_workers == 4
            ),
        },
        "pack_backend_requested": backend_state.requested,
        "pack_backends_used": sorted(backend_state.used),
        "pack_backend_final": backend_state.active,
        "pack_backend_fallback_reason": backend_state.fallback_reason,
        "timings_seconds": rounded_timings(timings),
        "elapsed_seconds": round(elapsed_seconds, 6),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    if args.benchmark:
        print()
        print("Benchmark timings:")
        benchmark_names = [
            "input_pipeline_wall",
            "parallel_decode_wall",
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
