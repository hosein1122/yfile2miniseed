#!/usr/bin/env python3
"""
Create a standard availability report from a MiniSEED/SDS folder.

Output is intentionally shaped like yfile_availability_report.py so reports
from raw Y-files, center MiniSEED, and yfile2miniseed MiniSEED can be compared
side by side.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import numpy as np
    from obspy import Stream, read
except Exception as exc:
    print(f"ObsPy/Numpy import failed: {exc}", file=sys.stderr)
    sys.exit(2)


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
class Segment:
    source_id: str
    start: datetime
    end: datetime
    sample_rate: float
    npts: int
    file: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report MiniSEED/SDS availability with ObsPy.")
    parser.add_argument("--input", required=True, type=Path, help="Input MiniSEED/SDS folder.")
    parser.add_argument("--output", required=True, type=Path, help="Output report folder.")
    parser.add_argument("--station", help="Optional station filter, e.g. KAZ.")
    parser.add_argument("--channel", action="append", help="Optional channel filter. Can be repeated.")
    parser.add_argument(
        "--source-network",
        help="Optional network name to print in SourceID, without changing read metadata.",
    )
    parser.add_argument(
        "--tolerance-samples",
        type=float,
        default=1.1,
        help="Treat gaps/overlaps up to this many samples as contiguous. Default: 1.1.",
    )
    parser.add_argument(
        "--write-normalized",
        action="store_true",
        help="Also write an ObsPy-normalized report under <output>/normalized.",
    )
    parser.add_argument(
        "--snap-times",
        action="store_true",
        help="Snap displayed/report times to each SourceID sample grid. This affects reports only.",
    )
    return parser.parse_args()


def utc_to_datetime(value) -> datetime:
    return datetime.fromtimestamp(value.timestamp, tz=timezone.utc)


def fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-2]


def snap_time(value: datetime, origin: datetime, sample_rate: float) -> datetime:
    offset = (value - origin).total_seconds()
    snapped_offset = round(offset * sample_rate) / sample_rate
    return origin + timedelta(seconds=snapped_offset)


def snap_segments_to_sample_grid(segments: list[Segment]) -> list[Segment]:
    grouped: dict[str, list[Segment]] = {}
    for segment in segments:
        grouped.setdefault(segment.source_id, []).append(segment)

    snapped: list[Segment] = []
    for sid, items in grouped.items():
        ordered = sorted(items, key=lambda item: (item.start, item.end, item.file))
        origin = ordered[0].start
        for item in ordered:
            snapped.append(
                Segment(
                    source_id=sid,
                    start=snap_time(item.start, origin, item.sample_rate),
                    end=snap_time(item.end, origin, item.sample_rate),
                    sample_rate=item.sample_rate,
                    npts=item.npts,
                    file=item.file,
                )
            )
    return snapped


def source_id(network: str, station: str, channel: str, location: str) -> str:
    prefix = channel[:-1] if len(channel) > 1 else channel
    component = channel[-1] if channel else ""
    return f"FDSN:{network}_{station}_{prefix}_{component}_{location}"


def trace_source_id(trace, args: argparse.Namespace) -> str:
    network = args.source_network or trace.stats.network or ""
    return source_id(
        network.strip(),
        trace.stats.station.strip(),
        trace.stats.channel.strip(),
        trace.stats.location.strip(),
    )


def trace_matches(trace, args: argparse.Namespace, channels: set[str]) -> bool:
    if args.station and trace.stats.station.upper() != args.station.upper():
        return False
    if channels and trace.stats.channel.upper() not in channels:
        return False
    return True


def scan_mseed(args: argparse.Namespace) -> tuple[list[Segment], list[dict]]:
    segments: list[Segment] = []
    errors: list[dict] = []
    channels = {item.upper() for item in args.channel or []}
    for path in sorted(args.input.rglob("*")):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            stream = read(str(path), headonly=True)
        except Exception as exc:
            errors.append({"file": str(path), "error": repr(exc)})
            continue
        for trace in stream:
            if not trace_matches(trace, args, channels):
                continue
            segments.append(
                Segment(
                    source_id=trace_source_id(trace, args),
                    start=utc_to_datetime(trace.stats.starttime),
                    end=utc_to_datetime(trace.stats.endtime),
                    sample_rate=float(trace.stats.sampling_rate),
                    npts=int(trace.stats.npts),
                    file=str(path),
                )
            )
    return segments, errors


def split_merged_trace(trace, sid: str) -> list[Segment]:
    sample_rate = float(trace.stats.sampling_rate)
    data = trace.data
    npts = int(trace.stats.npts)
    if npts <= 0:
        return []

    mask = np.ma.getmaskarray(data)
    if mask.shape == () or not mask.any():
        return [
            Segment(
                source_id=sid,
                start=utc_to_datetime(trace.stats.starttime),
                end=utc_to_datetime(trace.stats.endtime),
                sample_rate=sample_rate,
                npts=npts,
                file="<ObsPy normalized>",
            )
        ]

    segments: list[Segment] = []
    index = 0
    while index < npts:
        while index < npts and mask[index]:
            index += 1
        if index >= npts:
            break
        start_index = index
        while index < npts and not mask[index]:
            index += 1
        end_index = index - 1
        start_time = trace.stats.starttime + (start_index / sample_rate)
        end_time = trace.stats.starttime + (end_index / sample_rate)
        segments.append(
            Segment(
                source_id=sid,
                start=utc_to_datetime(start_time),
                end=utc_to_datetime(end_time),
                sample_rate=sample_rate,
                npts=end_index - start_index + 1,
                file="<ObsPy normalized>",
            )
        )
    return segments


def scan_mseed_normalized(args: argparse.Namespace) -> tuple[list[Segment], list[dict]]:
    grouped: dict[str, Stream] = defaultdict(Stream)
    errors: list[dict] = []
    channels = {item.upper() for item in args.channel or []}

    for path in sorted(args.input.rglob("*")):
        if not path.is_file() or path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            stream = read(str(path), headonly=False)
        except Exception as exc:
            errors.append({"file": str(path), "error": repr(exc)})
            continue
        for trace in stream:
            if trace_matches(trace, args, channels):
                grouped[trace_source_id(trace, args)] += trace

    segments: list[Segment] = []
    for sid, stream in grouped.items():
        try:
            normalized = stream.copy()
            normalized.sort()
            normalized.merge(method=1, fill_value=None)
        except Exception as exc:
            errors.append({"file": sid, "error": f"ObsPy merge failed: {exc!r}"})
            continue
        for trace in normalized:
            segments.extend(split_merged_trace(trace, sid))
    return segments, errors


def gap_samples_from_previous(previous: Segment | None, current: Segment, tolerance_samples: float) -> int:
    if previous is None:
        return 0
    step = 1.0 / current.sample_rate
    delta = (current.start - previous.end).total_seconds() - step
    gap_samples = round(delta * current.sample_rate)
    tolerance = tolerance_samples * step
    if abs(delta) <= tolerance:
        return 0
    return gap_samples


def write_reports(
    segments: list[Segment],
    errors: list[dict],
    output_dir: Path,
    title: str,
    tolerance_samples: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[Segment]] = {}
    for segment in segments:
        grouped.setdefault(segment.source_id, []).append(segment)

    rows = []
    text_lines = [f"            {title}", ""]
    for sid in sorted(grouped):
        items = sorted(grouped[sid], key=lambda item: (item.start, item.end, item.file))
        text_lines.append(
            "         SourceID                 Start sample                End sample                  GapSamples      DataSamples"
        )
        previous = None
        for item in items:
            gap_samples = gap_samples_from_previous(previous, item, tolerance_samples)
            text_lines.append(
                f"    {sid:<24} {fmt_time(item.start):<27} {fmt_time(item.end):<27} {gap_samples:11d} {item.npts:16d}"
            )
            rows.append(
                {
                    "SourceID": sid,
                    "Start sample": fmt_time(item.start),
                    "End sample": fmt_time(item.end),
                    "GapSamples": gap_samples,
                    "DataSamples": item.npts,
                }
            )
            previous = item
        text_lines.append("")

    (output_dir / "availability.txt").write_text("\n".join(text_lines), encoding="utf-8")
    with (output_dir / "availability.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "SourceID",
            "Start sample",
            "End sample",
            "GapSamples",
            "DataSamples",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    with (output_dir / "errors.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "error"])
        writer.writeheader()
        writer.writerows(errors)


def main() -> int:
    args = parse_args()
    if not args.input.exists():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        return 2
    segments, errors = scan_mseed(args)
    if args.snap_times:
        segments = snap_segments_to_sample_grid(segments)
    write_reports(segments, errors, args.output, "MiniSEED Availability Contents:", args.tolerance_samples)
    if args.write_normalized:
        normalized_segments, normalized_errors = scan_mseed_normalized(args)
        if args.snap_times:
            normalized_segments = snap_segments_to_sample_grid(normalized_segments)
        write_reports(
            normalized_segments,
            normalized_errors,
            args.output / "normalized",
            "MiniSEED Availability Contents (ObsPy normalized):",
            args.tolerance_samples,
        )
        print(f"Normalized segments parsed: {len(normalized_segments)}")
        print(f"Normalized errors: {len(normalized_errors)}")
    print(f"Segments parsed: {len(segments)}")
    print(f"Errors: {len(errors)}")
    print(f"Reports: {args.output}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
