#!/usr/bin/env python3
"""List and compare gaps/overlaps in two SDS MiniSEED trees with ObsPy."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import obspy
from obspy import Stream, read
from obspy.core.utcdatetime import UTCDateTime


SKIP_SUFFIXES = {
    ".bak",
    ".backup",
    ".bat",
    ".building",
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
    ".pending",
    ".pdf",
    ".png",
    ".py",
    ".rar",
    ".tmp",
    ".txt",
    ".whl",
    ".xml",
    ".zip",
}


@dataclass(frozen=True)
class GapEntry:
    stream_id: str
    kind: str
    previous_end_utc: str
    next_start_utc: str
    previous_end_ns: int
    next_start_ns: int
    delta_seconds: float
    samples: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save ordered ObsPy gap/overlap text reports for two SDS trees and compare them."
    )
    parser.add_argument("--sds-a", required=True, type=Path, help="First SDS root.")
    parser.add_argument("--sds-b", required=True, type=Path, help="Second SDS root.")
    parser.add_argument("--report", required=True, type=Path, help="Output report folder.")
    parser.add_argument("--a-label", default="sds_a", help="Label for the first SDS tree.")
    parser.add_argument("--b-label", default="sds_b", help="Label for the second SDS tree.")
    parser.add_argument("--network", action="append", help="Optional network filter. Repeatable.")
    parser.add_argument("--station", action="append", help="Optional station filter. Repeatable.")
    parser.add_argument("--channel", action="append", help="Optional channel filter. Repeatable.")
    parser.add_argument(
        "--time-tolerance-ns",
        type=int,
        default=5_000_000,
        help="Timestamp tolerance when comparing gap boundaries. Default: 5000000 ns (0.005 s).",
    )
    parser.add_argument(
        "--allow-differences",
        action="store_true",
        help="Return success even when the two gap lists differ.",
    )
    return parser.parse_args()


def normalized_filter(values: list[str] | None) -> set[str]:
    return {item.strip().upper() for item in values or [] if item.strip()}


def safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return label.strip("_") or "sds"


def trace_matches(trace, networks: set[str], stations: set[str], channels: set[str]) -> bool:
    if networks and trace.stats.network.strip().upper() not in networks:
        return False
    if stations and trace.stats.station.strip().upper() not in stations:
        return False
    if channels and trace.stats.channel.strip().upper() not in channels:
        return False
    return True


def iter_candidate_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix and suffix in SKIP_SUFFIXES:
            continue
        yield path


CENTISECOND_NS = 10_000_000


def round_ns_to_centisecond(ns: int) -> int:
    """Round integer nanoseconds to the nearest 0.01 second."""
    if ns >= 0:
        return ((ns + CENTISECOND_NS // 2) // CENTISECOND_NS) * CENTISECOND_NS
    return -(((-ns + CENTISECOND_NS // 2) // CENTISECOND_NS) * CENTISECOND_NS)


def utc_text(value: UTCDateTime) -> str:
    """Format UTC time rounded to centiseconds (two decimal places)."""
    rounded = UTCDateTime(ns=round_ns_to_centisecond(int(value.ns)), precision=9)
    return rounded.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-4]


def gap_kind(samples: int) -> str:
    return "GAP" if samples > 0 else "OVERLAP"


def scan_gap_entries(
    root: Path,
    networks: set[str],
    stations: set[str],
    channels: set[str],
) -> tuple[list[GapEntry], list[dict]]:
    grouped: dict[str, Stream] = defaultdict(Stream)
    errors: list[dict] = []

    if not root.is_dir():
        return [], [{"file": str(root), "error": "SDS root does not exist or is not a directory"}]

    for path in iter_candidate_files(root):
        try:
            stream = read(str(path), format="MSEED", headonly=True)
        except Exception as exc:
            errors.append({"file": str(path), "error": repr(exc)})
            continue

        for trace in stream:
            if not trace_matches(trace, networks, stations, channels):
                continue
            trace.stats.starttime = UTCDateTime(ns=trace.stats.starttime.ns, precision=9)
            grouped[trace.id].append(trace)

    entries: list[GapEntry] = []
    for stream_id in sorted(grouped):
        stream = grouped[stream_id]
        stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
        for item in stream.get_gaps():
            previous_end = UTCDateTime(ns=item[4].ns, precision=9)
            next_start = UTCDateTime(ns=item[5].ns, precision=9)
            samples = int(item[7])
            entries.append(
                GapEntry(
                    stream_id=f"{item[0]}.{item[1]}.{item[2]}.{item[3]}",
                    kind=gap_kind(samples),
                    previous_end_utc=utc_text(previous_end),
                    next_start_utc=utc_text(next_start),
                    previous_end_ns=int(previous_end.ns),
                    next_start_ns=int(next_start.ns),
                    delta_seconds=float(item[6]),
                    samples=samples,
                )
            )

    return (
        sorted(
            entries,
            key=lambda item: (
                item.stream_id,
                item.previous_end_ns,
                item.next_start_ns,
                item.kind,
            ),
        ),
        errors,
    )


def same_gap(left: GapEntry, right: GapEntry, tolerance_ns: int) -> bool:
    return (
        left.stream_id == right.stream_id
        and left.kind == right.kind
        and left.samples == right.samples
        and abs(left.previous_end_ns - right.previous_end_ns) <= tolerance_ns
        and abs(left.next_start_ns - right.next_start_ns) <= tolerance_ns
    )


def find_matching_gap(
    target: GapEntry,
    candidates: list[GapEntry],
    used_indexes: set[int],
    tolerance_ns: int,
) -> int | None:
    """Return the closest unused matching gap inside the time tolerance."""
    best_index: int | None = None
    best_error: tuple[int, int] | None = None

    for index, candidate in enumerate(candidates):
        if index in used_indexes or not same_gap(target, candidate, tolerance_ns):
            continue

        previous_error = abs(target.previous_end_ns - candidate.previous_end_ns)
        next_error = abs(target.next_start_ns - candidate.next_start_ns)
        error = (max(previous_error, next_error), previous_error + next_error)

        if best_error is None or error < best_error:
            best_error = error
            best_index = index

    return best_index


def comparison_row(status: str, owner: str, entry: GapEntry) -> dict:
    return {
        "status": status,
        "gap_owner": owner,
        "stream_id": entry.stream_id,
        "kind": entry.kind,
        "previous_end_utc": entry.previous_end_utc,
        "next_start_utc": entry.next_start_utc,
        "previous_end_ns": entry.previous_end_ns,
        "next_start_ns": entry.next_start_ns,
        "delta_seconds": entry.delta_seconds,
        "samples": entry.samples,
    }


def compare_gap_entries(
    left: list[GapEntry],
    right: list[GapEntry],
    tolerance_ns: int,
    left_label: str,
    right_label: str,
) -> list[dict]:
    left_by_stream: dict[str, list[GapEntry]] = defaultdict(list)
    right_by_stream: dict[str, list[GapEntry]] = defaultdict(list)

    for entry in left:
        left_by_stream[entry.stream_id].append(entry)
    for entry in right:
        right_by_stream[entry.stream_id].append(entry)

    rows: list[dict] = []
    for stream_id in sorted(set(left_by_stream) | set(right_by_stream)):
        left_items = left_by_stream.get(stream_id, [])
        right_items = right_by_stream.get(stream_id, [])

        used_right: set[int] = set()
        for left_entry in left_items:
            match_index = find_matching_gap(left_entry, right_items, used_right, tolerance_ns)
            if match_index is None:
                rows.append(comparison_row(f"ONLY_IN_{left_label}", left_label, left_entry))
            else:
                used_right.add(match_index)

        used_left: set[int] = set()
        for right_entry in right_items:
            match_index = find_matching_gap(right_entry, left_items, used_left, tolerance_ns)
            if match_index is None:
                rows.append(comparison_row(f"ONLY_IN_{right_label}", right_label, right_entry))
            else:
                used_left.add(match_index)

    return sorted(
        rows,
        key=lambda item: (
            item["stream_id"],
            item["previous_end_ns"],
            item["next_start_ns"],
            item["gap_owner"],
        ),
    )


def remove_previous_outputs(report: Path, a_label: str, b_label: str) -> None:
    """Remove current and legacy report files so stale results are never kept."""
    names = {
        "report.txt",
        "report.json",
        "gap_comparison.txt",
        "gap_comparison.csv",
        f"gaps_{a_label}.txt",
        f"gaps_{b_label}.txt",
        f"gaps_{a_label}.csv",
        f"gaps_{b_label}.csv",
        f"errors_{a_label}.txt",
        f"errors_{b_label}.txt",
        f"errors_{a_label}.csv",
        f"errors_{b_label}.csv",
    }
    for name in names:
        (report / name).unlink(missing_ok=True)


def format_gap_entry(index: int, entry: GapEntry) -> list[str]:
    return [
        f"[{index}] {entry.kind}  {entry.stream_id}",
        f"    Previous end UTC : {entry.previous_end_utc}",
        f"    Next start UTC   : {entry.next_start_utc}",
        f"    Samples          : {entry.samples}",
        "",
    ]


def write_gap_report(
    path: Path,
    label: str,
    sds_root: Path,
    rows: list[GapEntry],
) -> bool:
    """Write a gap report only when at least one gap/overlap exists."""
    if not rows:
        path.unlink(missing_ok=True)
        return False

    lines = [
        f"SDS Gap/Overlap Report: {label}",
        f"SDS root: {sds_root}",
        f"Entries: {len(rows)}",
        "",
    ]
    for index, entry in enumerate(rows, start=1):
        lines.extend(format_gap_entry(index, entry))

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def write_comparison_report(
    path: Path,
    rows: list[dict],
    a_label: str,
    b_label: str,
    tolerance_ns: int,
) -> bool:
    """Write only unmatched gaps/overlaps; create no file when both sides match."""
    if not rows:
        path.unlink(missing_ok=True)
        return False

    lines = [
        "SDS Gap/Overlap Differences",
        f"First SDS label : {a_label}",
        f"Second SDS label: {b_label}",
        f"Time tolerance  : {tolerance_ns / 1_000_000_000:.3f} s ({tolerance_ns} ns)",
        f"Differences     : {len(rows)}",
        "",
    ]

    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"[{index}] {row['status']}",
                f"    Owner            : {row['gap_owner']}",
                f"    Stream ID        : {row['stream_id']}",
                f"    Kind             : {row['kind']}",
                f"    Previous end UTC : {row['previous_end_utc']}",
                f"    Next start UTC   : {row['next_start_utc']}",
                f"    Samples          : {row['samples']}",
                "",
            ]
        )

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def write_error_report(path: Path, label: str, rows: list[dict]) -> bool:
    """Write an error report only when real errors exist."""
    if not rows:
        path.unlink(missing_ok=True)
        return False

    lines = [
        f"SDS Processing Errors: {label}",
        f"Errors: {len(rows)}",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"[{index}] File: {row.get('file', '')}",
                f"    Error: {row.get('error', '')}",
                "",
            ]
        )

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return True


def write_summary_report(
    path: Path,
    *,
    sds_a: Path,
    sds_b: Path,
    a_label: str,
    b_label: str,
    a_report_label: str,
    b_report_label: str,
    tolerance_ns: int,
    gap_count_a: int,
    gap_count_b: int,
    difference_count: int,
    errors_a: int,
    errors_b: int,
    generated_files: list[str],
    ok: bool,
) -> None:
    lines = [
        "SDS Gap/Overlap Comparison Summary",
        "",
        f"SDS A              : {sds_a}",
        f"SDS B              : {sds_b}",
        f"Label A            : {a_label}",
        f"Label B            : {b_label}",
        f"Report label A     : {a_report_label}",
        f"Report label B     : {b_report_label}",
        f"Python             : {sys.version.split()[0]}",
        f"ObsPy              : {obspy.__version__}",
        f"Time tolerance     : {tolerance_ns / 1_000_000_000:.3f} s ({tolerance_ns} ns)",
        "",
        f"Gap/overlap count A: {gap_count_a}",
        f"Gap/overlap count B: {gap_count_b}",
        f"Differences        : {difference_count}",
        f"Errors A           : {errors_a}",
        f"Errors B           : {errors_b}",
        f"Result             : {'OK' if ok else 'DIFFERENCES OR ERRORS FOUND'}",
        "",
        "Generated detail reports:",
    ]

    if generated_files:
        lines.extend(f"  - {name}" for name in generated_files)
    else:
        lines.append("  None; no gaps, overlaps, differences, or errors were found.")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    report = args.report.resolve()
    report.mkdir(parents=True, exist_ok=True)

    sds_a = args.sds_a.resolve()
    sds_b = args.sds_b.resolve()
    networks = normalized_filter(args.network)
    stations = normalized_filter(args.station)
    channels = normalized_filter(args.channel)
    a_label = safe_label(args.a_label)
    b_label = safe_label(args.b_label)

    remove_previous_outputs(report, a_label, b_label)

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__}")
    print(f"Scanning {args.a_label}: {sds_a}")
    gaps_a, errors_a = scan_gap_entries(sds_a, networks, stations, channels)
    print(f"Scanning {args.b_label}: {sds_b}")
    gaps_b, errors_b = scan_gap_entries(sds_b, networks, stations, channels)

    differences = compare_gap_entries(
        gaps_a,
        gaps_b,
        args.time_tolerance_ns,
        a_label,
        b_label,
    )

    generated_files: list[str] = []

    gaps_a_path = report / f"gaps_{a_label}.txt"
    if write_gap_report(gaps_a_path, args.a_label, sds_a, gaps_a):
        generated_files.append(gaps_a_path.name)

    gaps_b_path = report / f"gaps_{b_label}.txt"
    if write_gap_report(gaps_b_path, args.b_label, sds_b, gaps_b):
        generated_files.append(gaps_b_path.name)

    comparison_path = report / "gap_comparison.txt"
    if write_comparison_report(
        comparison_path,
        differences,
        args.a_label,
        args.b_label,
        args.time_tolerance_ns,
    ):
        generated_files.append(comparison_path.name)

    errors_a_path = report / f"errors_{a_label}.txt"
    if write_error_report(errors_a_path, args.a_label, errors_a):
        generated_files.append(errors_a_path.name)

    errors_b_path = report / f"errors_{b_label}.txt"
    if write_error_report(errors_b_path, args.b_label, errors_b):
        generated_files.append(errors_b_path.name)

    ok = not differences and not errors_a and not errors_b
    summary_path = report / "report.txt"
    write_summary_report(
        summary_path,
        sds_a=sds_a,
        sds_b=sds_b,
        a_label=args.a_label,
        b_label=args.b_label,
        a_report_label=a_label,
        b_report_label=b_label,
        tolerance_ns=args.time_tolerance_ns,
        gap_count_a=len(gaps_a),
        gap_count_b=len(gaps_b),
        difference_count=len(differences),
        errors_a=len(errors_a),
        errors_b=len(errors_b),
        generated_files=generated_files,
        ok=ok,
    )

    print(f"Gap/overlap count {args.a_label}: {len(gaps_a)}")
    print(f"Gap/overlap count {args.b_label}: {len(gaps_b)}")
    print(f"Differences: {len(differences)}")
    print(f"Errors {args.a_label}: {len(errors_a)}")
    print(f"Errors {args.b_label}: {len(errors_b)}")
    print(f"Result: {'OK' if ok else 'DIFFERENCES OR ERRORS FOUND'}")
    print(f"Summary report: {summary_path}")

    if ok or args.allow_differences:
        return 0
    if errors_a or errors_b:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
