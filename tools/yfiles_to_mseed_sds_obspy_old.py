#!/usr/bin/env python3
"""Build a record-routed strict SDS archive from Nanometrics Y-files with ObsPy."""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
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
    return parser.parse_args()


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
    """One plain Y-file or one Y-file member stored inside a ZIP archive."""

    container: Path
    member: str | None = None
    uncompressed_size: int = 0
    compressed_size: int = 0

    @property
    def is_zip_member(self) -> bool:
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
        if path.suffix.lower() != ".zip":
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

        try:
            with zipfile.ZipFile(path, mode="r") as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue

                    member = info.filename.replace("\\", "/").lstrip("/")
                    if not member:
                        continue

                    # Nested ZIP archives are intentionally not expanded.
                    if member.lower().endswith(".zip"):
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
                        )
                    )
        except zipfile.BadZipFile as exc:
            raise RuntimeError(f"invalid ZIP archive: {path}: {exc}") from exc

    return sorted(
        sources,
        key=lambda item: (
            str(item.container).lower(),
            (item.member or "").lower(),
        ),
    )


class InputSourceReader:
    """
    Reuse open ZIP handles and decompress each member into RAM only when needed.

    Only one member payload is retained temporarily during each ObsPy read.
    """

    def __init__(self) -> None:
        self._archive_path: Path | None = None
        self._archive: zipfile.ZipFile | None = None

    def __enter__(self) -> "InputSourceReader":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self._archive is not None:
            self._archive.close()
        self._archive = None
        self._archive_path = None

    def _get_archive(self, path: Path) -> zipfile.ZipFile:
        # Sources are sorted by container, so keeping only the current archive
        # open provides reuse without accumulating open file handles.
        if self._archive is None or self._archive_path != path:
            self.close()
            self._archive = zipfile.ZipFile(path, mode="r")
            self._archive_path = path
        return self._archive

    def read_y_stream(self, source: InputSource) -> Stream:
        try:
            if not source.is_zip_member:
                return read(str(source.container), format="Y")

            archive = self._get_archive(source.container)
            with archive.open(source.member, mode="r") as member_handle:
                payload = member_handle.read()

            # BytesIO is seekable, which is safer and faster for readers that
            # inspect the input more than once.
            return read(io.BytesIO(payload), format="Y")
        except Exception as exc:
            raise RuntimeError(
                f"cannot read Nanometrics Y source {source.display_name}: {exc}"
            ) from exc


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


def day_start(time_: UTCDateTime) -> UTCDateTime:
    return UTCDateTime(time_.year, time_.month, time_.day, precision=9)


def pack_trace_records(
    trace: Trace,
    args: argparse.Namespace,
    temp_dir: Path,
    trace_index: int,
):
    """
    Pack one complete Trace first, then yield its raw fixed-length MiniSEED
    records. This preserves natural MiniSEED record boundaries and avoids
    creating artificial Trace breaks exactly at midnight.
    """
    temp_path = temp_dir / f"trace_{trace_index:08d}.mseed"
    Stream([trace]).write(
        str(temp_path),
        format="MSEED",
        encoding=args.encoding,
        reclen=args.record_length,
        flush=True,
    )

    file_size = temp_path.stat().st_size
    if file_size == 0:
        raise RuntimeError(f"ObsPy wrote an empty MiniSEED file for {trace.id}")
    if file_size % args.record_length != 0:
        raise RuntimeError(
            f"packed MiniSEED size is not divisible by record length for {trace.id}: "
            f"size={file_size}, reclen={args.record_length}"
        )

    try:
        with temp_path.open("rb") as handle:
            record_index = 0
            while True:
                record = handle.read(args.record_length)
                if not record:
                    break
                record_index += 1
                if len(record) != args.record_length:
                    raise RuntimeError(
                        f"short MiniSEED record for {trace.id}: "
                        f"record={record_index}, bytes={len(record)}"
                    )

                info = get_record_information(io.BytesIO(record))
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
        temp_path.unlink(missing_ok=True)


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

    ensure_empty_output(output_root)
    sources = iter_input_sources(input_root, args.pattern, args.recursive)
    if not sources:
        raise RuntimeError(f"no input files matched {args.pattern!r} under {input_root}")

    plain_file_count = sum(not source.is_zip_member for source in sources)
    zip_member_count = sum(source.is_zip_member for source in sources)
    zip_archives = sorted(
        {source.container for source in sources if source.is_zip_member},
        key=lambda path: str(path).lower(),
    )
    zip_uncompressed_bytes = sum(
        source.uncompressed_size for source in sources if source.is_zip_member
    )
    zip_compressed_bytes = sum(
        source.compressed_size for source in sources if source.is_zip_member
    )
    largest_zip_member_bytes = max(
        (
            source.uncompressed_size
            for source in sources
            if source.is_zip_member
        ),
        default=0,
    )

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")
    corrections = None if args.no_correct_sid else load_correct_sid(args.correct_sid.resolve())
    if corrections is not None:
        print(f"Using CorrectSID: {args.correct_sid.resolve()} ({len(corrections)} entries)")

    print(
        f"Input sources: {len(sources)} "
        f"(plain={plain_file_count}, zip_members={zip_member_count}, "
        f"zip_archives={len(zip_archives)})"
    )
    if zip_member_count:
        print(
            "ZIP mode: members are decompressed one at a time in RAM; "
            "nothing is extracted to disk."
        )

    stream = Stream()
    with InputSourceReader() as source_reader:
        print(f"Checking ObsPy Y reader with: {sources[0].display_name}")
        first_stream = source_reader.read_y_stream(sources[0])

        for index, source in enumerate(sources, start=1):
            # Reuse the already-decoded first stream instead of reading it twice.
            current = first_stream if index == 1 else source_reader.read_y_stream(source)
            apply_metadata(current, args, source.display_name, corrections)
            stream += current
            print(f"[{index}/{len(sources)}] read {source.display_name}")

    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    stream.merge(method=-1)
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])

    record_counts: dict[Path, int] = defaultdict(int)
    output_handles = {}
    total_records = 0
    total_samples = sum(int(trace.stats.npts) for trace in stream)

    try:
        with tempfile.TemporaryDirectory(prefix="yfile_reference_pack_") as temp:
            temp_dir = Path(temp)
            for trace_index, trace in enumerate(stream, start=1):
                trace_record_count = 0
                for record_start, record in pack_trace_records(
                    trace,
                    args,
                    temp_dir,
                    trace_index,
                ):
                    day = day_start(record_start)
                    path = sds_path(output_root, trace, day)
                    if path not in output_handles:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        output_handles[path] = path.open("ab")

                    output_handles[path].write(record)
                    record_counts[path] += 1
                    total_records += 1
                    trace_record_count += 1

                print(
                    f"[{trace_index}/{len(stream)}] packed {trace.id} "
                    f"records={trace_record_count} samples={trace.stats.npts}"
                )
    finally:
        for handle in output_handles.values():
            handle.close()

    for index, path in enumerate(sorted(record_counts), start=1):
        expected_size = record_counts[path] * args.record_length
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            raise RuntimeError(
                f"SDS file size mismatch: {path}: "
                f"expected={expected_size}, actual={actual_size}"
            )
        print(
            f"[{index}/{len(record_counts)}] wrote {path} "
            f"records={record_counts[path]} bytes={actual_size}"
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
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Completed successfully")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
