#!/usr/bin/env python3
"""Semantically compare two strict SDS archives with ObsPy and NumPy."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import obspy
from obspy import Stream
from obspy.clients.filesystem.sds import Client
from obspy.core.utcdatetime import ObsPyDeprecationWarning, UTCDateTime


warnings.filterwarnings(
    "ignore",
    message=r"Comparing UTCDateTime objects of different precision.*",
    category=ObsPyDeprecationWarning,
)


DEFAULT_TIME_TOLERANCE_NS = 1_000_000


SDS_FILE_RE = re.compile(
    r"^(?P<network>[^.]+)\.(?P<station>[^.]+)\.(?P<location>[^.]*)\."
    r"(?P<channel>[^.]+)\.D\.(?P<year>\d{4})\.(?P<doy>\d{3})$"
)


@dataclass
class SdsFile:
    path: str
    stream_id: str
    network: str
    station: str
    location: str
    channel: str
    year: int
    doy: int
    start_ns: int
    end_ns: int
    trace_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Semantic ObsPy comparison for two strict SDS archives."
    )
    parser.add_argument("--reference-sds", required=True, type=Path)
    parser.add_argument("--cpp-sds", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--time-tolerance-ns",
        type=int,
        default=DEFAULT_TIME_TOLERANCE_NS,
        help="Nanosecond tolerance for ObsPy/MiniSEED timestamp round-trip comparison.",
    )
    parser.add_argument("--allow-differences", action="store_true")
    return parser.parse_args()


def strict_sds_metadata(root: Path, path: Path) -> tuple[dict, str | None]:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return {}, f"{path}: is not under {root}"

    parts = relative.parts
    if len(parts) != 5:
        return {}, f"{path}: expected YEAR/NET/STA/CHA.D/file"

    year_dir, network_dir, station_dir, channel_dir, filename = parts
    match = SDS_FILE_RE.match(filename)
    if not match:
        return {}, f"{path}: filename is not strict SDS or has an extra suffix"

    metadata = match.groupdict()
    expected_dir_values = {
        "year": year_dir,
        "network": network_dir,
        "station": station_dir,
        "channel_dir": f"{metadata['channel']}.D",
    }
    actual_dir_values = {
        "year": metadata["year"],
        "network": metadata["network"],
        "station": metadata["station"],
        "channel_dir": channel_dir,
    }
    for name, expected in expected_dir_values.items():
        actual = actual_dir_values[name]
        if expected != actual:
            return {}, f"{path}: SDS {name} mismatch: {expected!r} != {actual!r}"

    metadata["year"] = int(metadata["year"])
    metadata["doy"] = int(metadata["doy"])
    return metadata, None


def scan_archive(root: Path) -> tuple[dict[str, list[SdsFile]], list[str]]:
    errors: list[str] = []
    by_stream: dict[str, list[SdsFile]] = defaultdict(list)

    if not root.is_dir():
        return {}, [f"{root}: archive root does not exist or is not a directory"]

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        metadata, error = strict_sds_metadata(root, path)
        if error:
            errors.append(error)
            continue

        try:
            stream = obspy.read(str(path), format="MSEED", headonly=True)
        except Exception as exc:
            errors.append(f"{path}: cannot read MiniSEED header: {exc}")
            continue

        expected_id = (
            f"{metadata['network']}.{metadata['station']}."
            f"{metadata['location']}.{metadata['channel']}"
        )
        starts = []
        ends = []
        for trace in stream:
            if trace.id != expected_id:
                errors.append(f"{path}: trace id {trace.id!r} does not match SDS path {expected_id!r}")
                continue
            starts.append(trace.stats.starttime.ns)
            ends.append(trace.stats.endtime.ns)

        if starts and ends:
            by_stream[expected_id].append(
                SdsFile(
                    path=str(path),
                    stream_id=expected_id,
                    network=metadata["network"],
                    station=metadata["station"],
                    location=metadata["location"],
                    channel=metadata["channel"],
                    year=metadata["year"],
                    doy=metadata["doy"],
                    start_ns=min(starts),
                    end_ns=max(ends),
                    trace_count=len(stream),
                )
            )

    return dict(by_stream), errors


def load_stream(root: Path, files: list[SdsFile]) -> Stream:
    if not files:
        return Stream()
    first = min(UTCDateTime(ns=item.start_ns, precision=9) for item in files)
    last = max(UTCDateTime(ns=item.end_ns, precision=9) for item in files)
    meta = files[0]
    stream = Client(str(root)).get_waveforms(
        meta.network,
        meta.station,
        meta.location,
        meta.channel,
        first,
        last,
    )
    for trace in stream:
        trace.stats.starttime = UTCDateTime(ns=trace.stats.starttime.ns, precision=9)
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    stream.merge(method=-1)
    for trace in stream:
        trace.stats.starttime = UTCDateTime(ns=trace.stats.starttime.ns, precision=9)
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    return stream


def gap_overlap_signature(stream: Stream) -> list[dict]:
    signature = []
    for item in stream.get_gaps():
        signature.append(
            {
                "network": item[0],
                "station": item[1],
                "location": item[2],
                "channel": item[3],
                "start": str(item[4]),
                "end": str(item[5]),
                "seconds": item[6],
                "samples": item[7],
            }
        )
    return signature


def time_ns_close(left: int, right: int, tolerance_ns: int) -> bool:
    return abs(int(left) - int(right)) <= tolerance_ns


def gap_overlap_signatures_match(left: Stream, right: Stream, tolerance_ns: int) -> bool:
    left_gaps = left.get_gaps()
    right_gaps = right.get_gaps()
    if len(left_gaps) != len(right_gaps):
        return False

    for expected, actual in zip(left_gaps, right_gaps, strict=True):
        for field in range(4):
            if expected[field] != actual[field]:
                return False
        if not time_ns_close(expected[4].ns, actual[4].ns, tolerance_ns):
            return False
        if not time_ns_close(expected[5].ns, actual[5].ns, tolerance_ns):
            return False
        if expected[7] != actual[7]:
            return False
    return True


def arrays_equal(left, right) -> tuple[bool, str]:
    if np.ma.isMaskedArray(left) or np.ma.isMaskedArray(right):
        if not np.array_equal(np.ma.getmaskarray(left), np.ma.getmaskarray(right)):
            return False, "gap mask differs"
        left_data = np.ma.filled(left, 0)
        right_data = np.ma.filled(right, 0)
    else:
        left_data = np.asarray(left)
        right_data = np.asarray(right)

    if left_data.dtype != right_data.dtype:
        return False, f"dtype {left_data.dtype} != {right_data.dtype}"
    if not np.array_equal(left_data, right_data):
        diff = np.flatnonzero(left_data != right_data)
        detail = f"first sample differs at index {int(diff[0])}" if diff.size else "sample values differ"
        return False, detail
    return True, ""


def trace_matching_interval(container, target, tolerance_ns: int):
    if container.id != target.id:
        return None, "id differs"
    if float(container.stats.sampling_rate) != float(target.stats.sampling_rate):
        return None, "sampling_rate differs"
    if str(container.data.dtype) != str(target.data.dtype):
        return None, "dtype differs"

    rate = float(target.stats.sampling_rate)
    if rate <= 0.0:
        return None, "invalid sampling_rate"

    sample_period_ns = 1_000_000_000 / rate
    offset_float = (container.stats.starttime.ns - target.stats.starttime.ns) * rate / 1_000_000_000
    offset_candidates = {
        math.floor(offset_float) + delta
        for delta in range(-2, 3)
    } | {
        round(offset_float) + delta
        for delta in range(-2, 3)
    } | {
        math.ceil(offset_float) + delta
        for delta in range(-2, 3)
    }
    start_delta = abs(
        container.stats.starttime.ns
        - (target.stats.starttime.ns + round(offset_float * sample_period_ns))
    )
    if start_delta > max(tolerance_ns, int(round(sample_period_ns))):
        return None, "time alignment exceeds tolerance"

    alignment_tolerance_ns = max(tolerance_ns, int(round(0.5 * sample_period_ns)))
    last_reason = "time range is not covered"
    for container_offset_in_target in sorted(offset_candidates):
        target_start_index = max(0, container_offset_in_target)
        container_start_index = max(0, -container_offset_in_target)
        length = min(
            int(target.stats.npts) - target_start_index,
            int(container.stats.npts) - container_start_index,
        )
        if length <= 0:
            continue

        start_delta = abs(
            (container.stats.starttime.ns + round(container_start_index * sample_period_ns))
            - (target.stats.starttime.ns + round(target_start_index * sample_period_ns))
        )
        end_delta = abs(
            (container.stats.starttime.ns + round((container_start_index + length - 1) * sample_period_ns))
            - (target.stats.starttime.ns + round((target_start_index + length - 1) * sample_period_ns))
        )
        if start_delta > alignment_tolerance_ns or end_delta > alignment_tolerance_ns:
            last_reason = "time alignment exceeds tolerance"
            continue

        candidate = container.data[container_start_index : container_start_index + length]
        expected = target.data[target_start_index : target_start_index + length]
        same, detail = arrays_equal(candidate, expected)
        if same:
            return (target_start_index, target_start_index + length), ""
        last_reason = detail

    return None, last_reason


def trace_is_covered(target, containers: Stream, tolerance_ns: int) -> tuple[bool, str]:
    intervals = []
    last_reason = "no candidate traces"
    for container in containers:
        interval, reason = trace_matching_interval(container, target, tolerance_ns)
        if interval is None:
            last_reason = reason
            continue
        intervals.append(interval)

    if not intervals:
        return False, last_reason

    intervals.sort()
    covered_until = 0
    for start, end in intervals:
        if start > covered_until:
            return False, f"uncovered sample range starts at index {covered_until}"
        covered_until = max(covered_until, end)
        if covered_until >= target.stats.npts:
            return True, ""
    return False, f"covered until sample index {covered_until} of {target.stats.npts}"


def compare_streams(
    reference: Stream,
    candidate: Stream,
    key: str,
    tolerance_ns: int,
) -> tuple[str, list[str], list[dict]]:
    notes: list[str] = []
    trace_rows: list[dict] = []

    reference_sample_sum = sum(int(trace.stats.npts) for trace in reference)
    candidate_sample_sum = sum(int(trace.stats.npts) for trace in candidate)
    if reference_sample_sum != candidate_sample_sum:
        notes.append(f"sample count sum {reference_sample_sum} != {candidate_sample_sum}")

    for index, trace in enumerate(reference, start=1):
        covered, reason = trace_is_covered(trace, candidate, tolerance_ns)
        row = {
            "side": "reference",
            "index": index,
            "status": "OK" if covered else "DIFF",
            "notes": "" if covered else reason,
        }
        trace_rows.append(row)
        if not covered:
            notes.append(
                f"reference trace {index} is not covered by C++ data: "
                f"{trace.id} {trace.stats.starttime} {trace.stats.endtime} npts={trace.stats.npts}: {reason}"
            )

    for index, trace in enumerate(candidate, start=1):
        covered, reason = trace_is_covered(trace, reference, tolerance_ns)
        row = {
            "side": "cpp",
            "index": index,
            "status": "OK" if covered else "DIFF",
            "notes": "" if covered else reason,
        }
        trace_rows.append(row)
        if not covered:
            notes.append(
                f"C++ trace {index} is not covered by reference data: "
                f"{trace.id} {trace.stats.starttime} {trace.stats.endtime} npts={trace.stats.npts}: {reason}"
            )

    if not gap_overlap_signatures_match(reference, candidate, tolerance_ns):
        notes.append("remaining gaps/overlaps differ")

    return ("OK" if not notes else "DIFF"), notes, trace_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def main() -> int:
    args = parse_args()
    reference_root = args.reference_sds.resolve()
    cpp_root = args.cpp_sds.resolve()
    report_root = args.report.resolve()
    report_root.mkdir(parents=True, exist_ok=True)

    reference_files, reference_errors = scan_archive(reference_root)
    cpp_files, cpp_errors = scan_archive(cpp_root)

    rows: list[dict] = []
    all_keys = sorted(set(reference_files) | set(cpp_files))
    for key in all_keys:
        if key not in reference_files:
            rows.append({"stream_id": key, "status": "ONLY_CPP", "notes": "missing from reference SDS"})
            continue
        if key not in cpp_files:
            rows.append({"stream_id": key, "status": "ONLY_REFERENCE", "notes": "missing from C++ SDS"})
            continue

        reference_stream = load_stream(reference_root, reference_files[key])
        cpp_stream = load_stream(cpp_root, cpp_files[key])
        status, notes, trace_rows = compare_streams(reference_stream, cpp_stream, key, args.time_tolerance_ns)
        rows.append(
            {
                "stream_id": key,
                "status": status,
                "notes": "; ".join(notes),
                "reference_files": [asdict(item) for item in reference_files[key]],
                "cpp_files": [asdict(item) for item in cpp_files[key]],
                "trace_comparison": trace_rows,
            }
        )

    report = {
        "reference_sds": str(reference_root),
        "cpp_sds": str(cpp_root),
        "obspy": obspy.__version__,
        "numpy": np.__version__,
        "time_tolerance_ns": args.time_tolerance_ns,
        "strict_sds_reference_errors": reference_errors,
        "strict_sds_cpp_errors": cpp_errors,
        "stream_count": len(all_keys),
        "comparison": rows,
    }
    (report_root / "comparison_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_csv(report_root / "comparison_summary.csv", rows)

    mismatches = [
        row
        for row in rows
        if row["status"] != "OK"
    ]
    mismatch_lines = [
        *(f"STRICT_SDS_REFERENCE\t{item}" for item in reference_errors),
        *(f"STRICT_SDS_CPP\t{item}" for item in cpp_errors),
        *(f"{row['status']}\t{row['stream_id']}\t{row.get('notes', '')}" for row in mismatches),
    ]
    (report_root / "mismatches.txt").write_text(
        "\n".join(mismatch_lines) + ("\n" if mismatch_lines else ""),
        encoding="utf-8",
    )

    difference_count = len(reference_errors) + len(cpp_errors) + len(mismatches)
    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")
    print(f"Compared streams: {len(all_keys)}")
    print(f"Differences: {difference_count}")
    print(f"Report: {report_root}")
    return 0 if args.allow_differences or difference_count == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
