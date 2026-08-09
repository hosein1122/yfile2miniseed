#!/usr/bin/env python3
"""Fast sample-level comparison for one SDS stream and one short time window."""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import obspy
from obspy.clients.filesystem.sds import Client
from obspy.core.utcdatetime import ObsPyDeprecationWarning, UTCDateTime


warnings.filterwarnings(
    "ignore",
    message=r"Comparing UTCDateTime objects of different precision.*",
    category=ObsPyDeprecationWarning,
)


DEFAULT_TIME_TOLERANCE_NS = 1_000_000


@dataclass
class Difference:
    kind: str
    sample_index: int
    time: str
    value_a: int | float | None
    value_b: int | float | None
    detail: str


@dataclass
class SideSummary:
    label: str
    root: str
    traces_read: int
    samples_read: int
    unique_samples: int
    duplicate_same_value: int
    duplicate_conflicts: int
    first_sample_time: str | None
    last_sample_time: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare exact sample values for one station component in two SDS "
            "archives over a short time window."
        )
    )
    parser.add_argument("--sds-a", required=True, type=Path, help="First SDS root.")
    parser.add_argument("--sds-b", required=True, type=Path, help="Second SDS root.")
    parser.add_argument("--label-a", default="A", help="Label for the first SDS.")
    parser.add_argument("--label-b", default="B", help="Label for the second SDS.")
    parser.add_argument(
        "--stream-id",
        help=(
            "Stream id as NET.STA.LOC.CHA. Empty location is written as a double "
            "dot, for example IR.TST..BHZ."
        ),
    )
    parser.add_argument("--network", help="Network code. Not needed with --stream-id.")
    parser.add_argument("--station", help="Station code. Not needed with --stream-id.")
    parser.add_argument(
        "--location",
        default="",
        help="Location code. Use empty string for blank location.",
    )
    parser.add_argument("--channel", help="Channel/component code. Not needed with --stream-id.")
    parser.add_argument("--start", required=True, help="UTC start time.")
    end_group = parser.add_mutually_exclusive_group(required=True)
    end_group.add_argument("--end", help="UTC end time.")
    end_group.add_argument(
        "--duration-seconds",
        type=float,
        help="Window length in seconds, used to calculate --end.",
    )
    parser.add_argument(
        "--sample-tolerance",
        type=float,
        default=0.0,
        help="Allowed absolute sample-value difference. Default is exact match.",
    )
    parser.add_argument(
        "--time-tolerance-ns",
        type=int,
        default=DEFAULT_TIME_TOLERANCE_NS,
        help="Allowed sample-grid alignment tolerance in nanoseconds.",
    )
    parser.add_argument(
        "--max-diffs",
        type=int,
        default=20,
        help="Maximum individual differences to print and store. Default: 20.",
    )
    parser.add_argument("--report", type=Path, help="Optional JSON report path.")
    parser.add_argument(
        "--allow-differences",
        action="store_true",
        help="Exit with code 0 even when differences are found.",
    )
    args = parser.parse_args()

    if args.max_diffs < 1:
        parser.error("--max-diffs must be at least 1")
    if args.sample_tolerance < 0:
        parser.error("--sample-tolerance cannot be negative")
    if args.time_tolerance_ns < 0:
        parser.error("--time-tolerance-ns cannot be negative")

    return args


def parse_stream_id(args: argparse.Namespace) -> tuple[str, str, str, str]:
    if args.stream_id:
        parts = args.stream_id.split(".")
        if len(parts) != 4:
            raise ValueError(
                "--stream-id must have four dot-separated fields: NET.STA.LOC.CHA"
            )
        network, station, location, channel = parts
    else:
        missing = [
            name
            for name in ("network", "station", "channel")
            if not getattr(args, name)
        ]
        if missing:
            raise ValueError(
                "Provide --stream-id or provide --network, --station, and --channel"
            )
        network = args.network
        station = args.station
        location = args.location
        channel = args.channel

    return network, station, location, channel


def parse_window(args: argparse.Namespace) -> tuple[UTCDateTime, UTCDateTime]:
    start = UTCDateTime(args.start, precision=9)
    if args.end:
        end = UTCDateTime(args.end, precision=9)
    else:
        end = start + float(args.duration_seconds)

    if end <= start:
        raise ValueError("--end must be after --start")
    return start, end


def normalize_time(value: UTCDateTime) -> UTCDateTime:
    return UTCDateTime(ns=value.ns, precision=9)


def read_window(
    root: Path,
    network: str,
    station: str,
    location: str,
    channel: str,
    start: UTCDateTime,
    end: UTCDateTime,
):
    client = Client(str(root))
    stream = client.get_waveforms(network, station, location, channel, start, end)
    for trace in stream:
        trace.stats.starttime = normalize_time(trace.stats.starttime)
    stream.trim(start, end, nearest_sample=False, pad=False)
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    return stream


def sample_value(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return value


def index_to_time(base: UTCDateTime, sample_index: int, sample_rate: float) -> UTCDateTime:
    return normalize_time(base + (sample_index / sample_rate))


def build_sample_map(
    stream,
    label: str,
    root: Path,
    base: UTCDateTime,
    end: UTCDateTime,
    time_tolerance_ns: int,
) -> tuple[dict[int, int | float], float | None, SideSummary, list[str]]:
    samples: dict[int, int | float] = {}
    sample_rate: float | None = None
    duplicate_same = 0
    duplicate_conflicts = 0
    notes: list[str] = []
    samples_read = 0
    first_ns: int | None = None
    last_ns: int | None = None

    for trace in stream:
        current_rate = float(trace.stats.sampling_rate)
        if current_rate <= 0.0:
            notes.append(f"{label}: invalid sample rate in trace {trace.id}")
            continue
        if sample_rate is None:
            sample_rate = current_rate
        elif not math.isclose(sample_rate, current_rate, rel_tol=0.0, abs_tol=1e-12):
            notes.append(
                f"{label}: mixed sample rates: {sample_rate} and {current_rate}"
            )
            continue

        start_offset = (trace.stats.starttime.ns - base.ns) * current_rate / 1_000_000_000
        first_index = int(round(start_offset))
        aligned_start_ns = (base + first_index / current_rate).ns
        if abs(aligned_start_ns - trace.stats.starttime.ns) > time_tolerance_ns:
            notes.append(
                f"{label}: trace {trace.id} start time is not aligned to the sample grid"
            )

        data = trace.data
        mask = np.ma.getmaskarray(data) if np.ma.isMaskedArray(data) else None
        values = np.ma.filled(data, 0) if np.ma.isMaskedArray(data) else np.asarray(data)

        for offset, raw_value in enumerate(values):
            samples_read += 1
            if mask is not None and bool(mask[offset]):
                continue

            sample_index = first_index + offset
            value = sample_value(raw_value)
            sample_ns = (base + sample_index / current_rate).ns
            if sample_ns < base.ns or sample_ns >= end.ns:
                continue
            first_ns = sample_ns if first_ns is None else min(first_ns, sample_ns)
            last_ns = sample_ns if last_ns is None else max(last_ns, sample_ns)

            existing = samples.get(sample_index)
            if existing is None:
                samples[sample_index] = value
            elif existing == value:
                duplicate_same += 1
            else:
                duplicate_conflicts += 1

    summary = SideSummary(
        label=label,
        root=str(root),
        traces_read=len(stream),
        samples_read=samples_read,
        unique_samples=len(samples),
        duplicate_same_value=duplicate_same,
        duplicate_conflicts=duplicate_conflicts,
        first_sample_time=(
            str(UTCDateTime(ns=first_ns, precision=9)) if first_ns is not None else None
        ),
        last_sample_time=(
            str(UTCDateTime(ns=last_ns, precision=9)) if last_ns is not None else None
        ),
    )
    return samples, sample_rate, summary, notes


def values_match(left, right, tolerance: float) -> bool:
    if tolerance == 0.0:
        return left == right
    return abs(float(left) - float(right)) <= tolerance


def compare_samples(
    samples_a: dict[int, int | float],
    samples_b: dict[int, int | float],
    sample_rate: float,
    base: UTCDateTime,
    tolerance: float,
    max_diffs: int,
) -> tuple[int, int, int, list[Difference]]:
    only_a = 0
    only_b = 0
    value_mismatches = 0
    differences: list[Difference] = []

    for sample_index in sorted(set(samples_a) | set(samples_b)):
        has_a = sample_index in samples_a
        has_b = sample_index in samples_b
        value_a = samples_a.get(sample_index)
        value_b = samples_b.get(sample_index)
        time = str(index_to_time(base, sample_index, sample_rate))

        if not has_a:
            only_b += 1
            if len(differences) < max_diffs:
                differences.append(
                    Difference("ONLY_B", sample_index, time, None, value_b, "sample exists only in B")
                )
            continue
        if not has_b:
            only_a += 1
            if len(differences) < max_diffs:
                differences.append(
                    Difference("ONLY_A", sample_index, time, value_a, None, "sample exists only in A")
                )
            continue
        if not values_match(value_a, value_b, tolerance):
            value_mismatches += 1
            if len(differences) < max_diffs:
                differences.append(
                    Difference(
                        "VALUE",
                        sample_index,
                        time,
                        value_a,
                        value_b,
                        "sample values differ",
                    )
                )

    return only_a, only_b, value_mismatches, differences


def main() -> int:
    args = parse_args()
    network, station, location, channel = parse_stream_id(args)
    start, end = parse_window(args)
    sds_a = args.sds_a.resolve()
    sds_b = args.sds_b.resolve()
    stream_id = f"{network}.{station}.{location}.{channel}"

    stream_a = read_window(sds_a, network, station, location, channel, start, end)
    stream_b = read_window(sds_b, network, station, location, channel, start, end)

    samples_a, rate_a, summary_a, notes_a = build_sample_map(
        stream_a,
        args.label_a,
        sds_a,
        start,
        end,
        args.time_tolerance_ns,
    )
    samples_b, rate_b, summary_b, notes_b = build_sample_map(
        stream_b,
        args.label_b,
        sds_b,
        start,
        end,
        args.time_tolerance_ns,
    )

    notes = [*notes_a, *notes_b]
    differences: list[Difference] = []
    only_a = 0
    only_b = 0
    value_mismatches = 0

    if rate_a is None and rate_b is None:
        notes.append("No samples were found in either SDS for this stream and window")
    elif rate_a is None:
        only_b = len(samples_b)
        notes.append(f"No samples were found in {args.label_a}")
    elif rate_b is None:
        only_a = len(samples_a)
        notes.append(f"No samples were found in {args.label_b}")
    elif not math.isclose(rate_a, rate_b, rel_tol=0.0, abs_tol=1e-12):
        notes.append(f"Sample rates differ: {rate_a} != {rate_b}")
    else:
        only_a, only_b, value_mismatches, differences = compare_samples(
            samples_a,
            samples_b,
            rate_a,
            start,
            args.sample_tolerance,
            args.max_diffs,
        )

    duplicate_conflicts = summary_a.duplicate_conflicts + summary_b.duplicate_conflicts
    difference_count = (
        only_a
        + only_b
        + value_mismatches
        + duplicate_conflicts
        + len(notes)
    )

    status = "OK" if difference_count == 0 else "DIFF"
    report = {
        "status": status,
        "stream_id": stream_id,
        "start": str(start),
        "end": str(end),
        "duration_seconds": float(end - start),
        "obspy": obspy.__version__,
        "numpy": np.__version__,
        "sample_tolerance": args.sample_tolerance,
        "time_tolerance_ns": args.time_tolerance_ns,
        "sample_rate_a": rate_a,
        "sample_rate_b": rate_b,
        "summary_a": asdict(summary_a),
        "summary_b": asdict(summary_b),
        "common_samples": len(set(samples_a) & set(samples_b)),
        "only_a_samples": only_a,
        "only_b_samples": only_b,
        "value_mismatches": value_mismatches,
        "notes": notes,
        "first_differences": [asdict(item) for item in differences],
    }

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")
    print(f"Stream: {stream_id}")
    print(f"Window: {start} <= t < {end} ({float(end - start):.6f} sec)")
    print(f"Status: {status}")
    print(
        f"{args.label_a}: traces={summary_a.traces_read}, "
        f"samples={summary_a.unique_samples}, duplicates={summary_a.duplicate_same_value}, "
        f"duplicate_conflicts={summary_a.duplicate_conflicts}"
    )
    print(
        f"{args.label_b}: traces={summary_b.traces_read}, "
        f"samples={summary_b.unique_samples}, duplicates={summary_b.duplicate_same_value}, "
        f"duplicate_conflicts={summary_b.duplicate_conflicts}"
    )
    print(
        f"Common={report['common_samples']} "
        f"Only-{args.label_a}={only_a} "
        f"Only-{args.label_b}={only_b} "
        f"Value-mismatches={value_mismatches}"
    )
    if notes:
        print("Notes:")
        for note in notes[: args.max_diffs]:
            print(f"  - {note}")
    if differences:
        print("First differences:")
        for item in differences:
            print(
                f"  - {item.kind} index={item.sample_index} time={item.time} "
                f"{args.label_a}={item.value_a} {args.label_b}={item.value_b}"
            )
    if args.report:
        print(f"Report: {args.report}")

    return 0 if args.allow_differences or status == "OK" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
