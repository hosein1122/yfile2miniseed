#!/usr/bin/env python3
"""Report raw MiniSEED record runs in an SDS tree without merging traces."""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from pathlib import Path

from obspy.core.utcdatetime import UTCDateTime
from obspy.io.mseed.util import get_record_information


DEFAULT_TIME_TOLERANCE_NS = 1_000_000


SKIP_SUFFIXES = {
    ".bat",
    ".cmd",
    ".csv",
    ".dll",
    ".doc",
    ".docx",
    ".exe",
    ".json",
    ".lib",
    ".log",
    ".md",
    ".pdf",
    ".png",
    ".py",
    ".rar",
    ".txt",
    ".whl",
    ".xml",
    ".zip",
}


@dataclass
class RawRecord:
    source_id: str
    start: UTCDateTime
    end: UTCDateTime
    sample_rate: float
    npts: int
    sequence: int | None
    file: str
    offset: int


@dataclass
class Segment:
    source_id: str
    start: UTCDateTime
    end: UTCDateTime
    sample_rate: float
    npts: int
    first_file: str
    last_file: str
    record_count: int
    first_sequence: int | None
    last_sequence: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List raw MiniSEED segments stored in an SDS tree without ObsPy "
            "trace merging or cleanup."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="Input SDS root.")
    parser.add_argument("--output", required=True, type=Path, help="Output TXT file.")
    parser.add_argument("--network", action="append", help="Optional network filter. Repeatable.")
    parser.add_argument("--station", action="append", help="Optional station filter. Repeatable.")
    parser.add_argument("--channel", action="append", help="Optional channel filter. Repeatable.")
    parser.add_argument(
        "--empty-location-code",
        default="_",
        help="Location token to print when MiniSEED location is blank. Default: _",
    )
    parser.add_argument(
        "--time-tolerance-ns",
        type=int,
        default=DEFAULT_TIME_TOLERANCE_NS,
        help="Tolerance for deciding whether adjacent records are contiguous.",
    )
    parser.add_argument(
        "--no-split-on-sequence-reset",
        action="store_true",
        help=(
            "Do not start a new segment when MiniSEED2 record sequence numbers "
            "restart or go backward."
        ),
    )
    parser.add_argument(
        "--include-file-comments",
        action="store_true",
        help="Add source file and record-count comments under each segment.",
    )
    args = parser.parse_args()
    if args.time_tolerance_ns < 0:
        parser.error("--time-tolerance-ns cannot be negative")
    return args


def normalized_filter(values: list[str] | None) -> set[str]:
    return {value.upper() for value in values or []}


def source_id(network: str, station: str, location: str, channel: str, empty_location_code: str) -> str:
    channel = channel.strip()
    prefix = channel[:-1] if len(channel) > 1 else channel
    component = channel[-1] if channel else ""
    location_token = location.strip() or empty_location_code
    return f"FDSN:{network.strip()}_{station.strip()}_{prefix}_{component}_{location_token}"


def parse_sequence(record: bytes) -> int | None:
    if len(record) < 8:
        return None
    if record[6:7] not in {b"D", b"R", b"Q", b"M"}:
        return None
    try:
        text = record[:6].decode("ascii")
    except UnicodeDecodeError:
        return None
    return int(text) if text.isdigit() else None


def record_matches(info: dict, networks: set[str], stations: set[str], channels: set[str]) -> bool:
    if networks and str(info.get("network", "")).upper() not in networks:
        return False
    if stations and str(info.get("station", "")).upper() not in stations:
        return False
    if channels and str(info.get("channel", "")).upper() not in channels:
        return False
    return True


def iter_records(path: Path, args: argparse.Namespace, networks: set[str], stations: set[str], channels: set[str]):
    payload = path.read_bytes()
    offset = 0
    while offset < len(payload):
        try:
            info = get_record_information(io.BytesIO(payload[offset:]))
        except Exception as exc:
            raise RuntimeError(f"{path}: cannot parse record at byte {offset}: {exc}") from exc

        record_length = int(info.get("record_length") or 0)
        if record_length <= 0:
            raise RuntimeError(f"{path}: invalid record length at byte {offset}")
        if offset + record_length > len(payload):
            raise RuntimeError(
                f"{path}: short MiniSEED record at byte {offset}, length={record_length}"
            )

        record = payload[offset : offset + record_length]
        if record_matches(info, networks, stations, channels):
            yield RawRecord(
                source_id=source_id(
                    str(info.get("network", "")),
                    str(info.get("station", "")),
                    str(info.get("location", "")),
                    str(info.get("channel", "")),
                    args.empty_location_code,
                ),
                start=UTCDateTime(info["starttime"], precision=9),
                end=UTCDateTime(info["endtime"], precision=9),
                sample_rate=float(info["samp_rate"]),
                npts=int(info["npts"]),
                sequence=parse_sequence(record),
                file=str(path),
                offset=offset,
            )

        offset += record_length


def scan_records(args: argparse.Namespace) -> tuple[list[RawRecord], list[str]]:
    networks = normalized_filter(args.network)
    stations = normalized_filter(args.station)
    channels = normalized_filter(args.channel)
    records: list[RawRecord] = []
    errors: list[str] = []

    for path in sorted(args.input.rglob("*")):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            records.extend(iter_records(path, args, networks, stations, channels))
        except Exception as exc:
            errors.append(str(exc))

    return records, errors


def records_are_contiguous(previous: RawRecord, current: RawRecord, tolerance_ns: int) -> bool:
    if previous.source_id != current.source_id:
        return False
    if abs(previous.sample_rate - current.sample_rate) > 1e-12:
        return False
    expected_start = previous.end + (1.0 / previous.sample_rate)
    return abs(expected_start.ns - current.start.ns) <= tolerance_ns


def sequence_continues(previous: RawRecord, current: RawRecord) -> bool:
    if previous.sequence is None or current.sequence is None:
        return True
    return current.sequence == previous.sequence + 1


def build_segments(records: list[RawRecord], args: argparse.Namespace) -> list[Segment]:
    if not records:
        return []

    ordered = sorted(records, key=lambda item: (item.source_id, item.start.ns, item.file, item.offset))
    segments: list[Segment] = []
    current = Segment(
        source_id=ordered[0].source_id,
        start=ordered[0].start,
        end=ordered[0].end,
        sample_rate=ordered[0].sample_rate,
        npts=ordered[0].npts,
        first_file=ordered[0].file,
        last_file=ordered[0].file,
        record_count=1,
        first_sequence=ordered[0].sequence,
        last_sequence=ordered[0].sequence,
    )
    previous = ordered[0]

    for record in ordered[1:]:
        same_segment = records_are_contiguous(previous, record, args.time_tolerance_ns)
        if same_segment and not args.no_split_on_sequence_reset:
            same_segment = sequence_continues(previous, record)

        if same_segment:
            current.end = record.end
            current.npts += record.npts
            current.last_file = record.file
            current.record_count += 1
            current.last_sequence = record.sequence
        else:
            segments.append(current)
            current = Segment(
                source_id=record.source_id,
                start=record.start,
                end=record.end,
                sample_rate=record.sample_rate,
                npts=record.npts,
                first_file=record.file,
                last_file=record.file,
                record_count=1,
                first_sequence=record.sequence,
                last_sequence=record.sequence,
            )
        previous = record

    segments.append(current)
    return segments


def fmt_time(value: UTCDateTime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-2]


def gap_samples(previous: Segment | None, current: Segment) -> int:
    if previous is None:
        return 0
    if previous.source_id != current.source_id:
        return 0
    step = 1.0 / current.sample_rate
    delta = (current.start - previous.end) - step
    return round(delta * current.sample_rate)


def write_report(segments: list[Segment], errors: list[str], args: argparse.Namespace) -> None:
    args.output.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "            Raw SDS MiniSEED Segment Contents:",
        "",
    ]

    previous_by_source: dict[str, Segment] = {}
    current_source = None
    for segment in sorted(segments, key=lambda item: (item.source_id, item.start.ns, item.end.ns)):
        if segment.source_id != current_source:
            if current_source is not None:
                lines.append("")
            lines.append(
                "         SourceID                 Start sample                End sample                  GapSamples      DataSamples"
            )
            current_source = segment.source_id

        previous = previous_by_source.get(segment.source_id)
        lines.append(
            f"    {segment.source_id:<24} "
            f"{fmt_time(segment.start):<27} "
            f"{fmt_time(segment.end):<27} "
            f"{gap_samples(previous, segment):11d} "
            f"{segment.npts:16d}"
        )
        if args.include_file_comments:
            seq = (
                ""
                if segment.first_sequence is None or segment.last_sequence is None
                else f" seq={segment.first_sequence}-{segment.last_sequence}"
            )
            lines.append(
                f"        # records={segment.record_count}{seq} file={segment.first_file}"
            )
        previous_by_source[segment.source_id] = segment

    if errors:
        lines.append("")
        lines.append("MiniSEED parse warnings:")
        for error in errors:
            lines.append(f"    {error}")

    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not args.input.is_dir():
        print(f"Input SDS root not found: {args.input}", file=sys.stderr)
        return 2

    records, errors = scan_records(args)
    segments = build_segments(records, args)
    write_report(segments, errors, args)

    print(f"Records parsed: {len(records)}")
    print(f"Segments written: {len(segments)}")
    print(f"Parse warnings: {len(errors)}")
    print(f"Report: {args.output}")
    return 0 if not errors else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
