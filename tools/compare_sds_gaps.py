#!/usr/bin/env python3
"""List and compare gaps/overlaps in two SDS MiniSEED trees with ObsPy."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
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
        description="Save ordered ObsPy gap/overlap lists for two SDS trees and compare them."
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
        default=1_000_000,
        help="Timestamp tolerance when comparing gap boundaries. Default: 1000000 ns.",
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


def utc_text(value: UTCDateTime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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

    return sorted(entries, key=lambda item: (item.stream_id, item.previous_end_ns, item.next_start_ns, item.kind)), errors


def same_gap(left: GapEntry, right: GapEntry, tolerance_ns: int) -> bool:
    return (
        left.stream_id == right.stream_id
        and left.kind == right.kind
        and abs(left.previous_end_ns - right.previous_end_ns) <= tolerance_ns
        and abs(left.next_start_ns - right.next_start_ns) <= tolerance_ns
    )


def find_matching_gap(
    target: GapEntry,
    candidates: list[GapEntry],
    used_indexes: set[int],
    tolerance_ns: int,
) -> int | None:
    for index, candidate in enumerate(candidates):
        if index in used_indexes:
            continue
        if same_gap(target, candidate, tolerance_ns):
            return index
    return None


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


def write_gap_csv(path: Path, rows: list[GapEntry]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "stream_id",
                "kind",
                "previous_end_utc",
                "next_start_utc",
                "previous_end_ns",
                "next_start_ns",
                "delta_seconds",
                "samples",
            ],
        )
        writer.writeheader()
        writer.writerows(asdict(item) for item in rows)


def write_dict_csv(path: Path, rows: list[dict]) -> None:
    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = [
            "status",
            "gap_owner",
            "stream_id",
            "kind",
            "previous_end_utc",
            "next_start_utc",
            "previous_end_ns",
            "next_start_ns",
            "delta_seconds",
            "samples",
        ]

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_error_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "error"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    report = args.report
    report.mkdir(parents=True, exist_ok=True)

    networks = normalized_filter(args.network)
    stations = normalized_filter(args.station)
    channels = normalized_filter(args.channel)
    a_label = safe_label(args.a_label)
    b_label = safe_label(args.b_label)

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__}")
    print(f"Scanning {args.a_label}: {args.sds_a}")
    gaps_a, errors_a = scan_gap_entries(args.sds_a, networks, stations, channels)
    print(f"Scanning {args.b_label}: {args.sds_b}")
    gaps_b, errors_b = scan_gap_entries(args.sds_b, networks, stations, channels)

    comparison = compare_gap_entries(
        gaps_a,
        gaps_b,
        args.time_tolerance_ns,
        a_label,
        b_label,
    )
    different_rows = [row for row in comparison if row["status"] != "MATCH"]

    write_gap_csv(report / f"gaps_{a_label}.csv", gaps_a)
    write_gap_csv(report / f"gaps_{b_label}.csv", gaps_b)
    write_dict_csv(report / "gap_comparison.csv", comparison)
    write_error_csv(report / f"errors_{a_label}.csv", errors_a)
    write_error_csv(report / f"errors_{b_label}.csv", errors_b)

    summary = {
        "sds_a": str(args.sds_a),
        "sds_b": str(args.sds_b),
        "a_label": args.a_label,
        "b_label": args.b_label,
        "a_report_label": a_label,
        "b_report_label": b_label,
        "obspy": obspy.__version__,
        "time_tolerance_ns": args.time_tolerance_ns,
        "gap_count_a": len(gaps_a),
        "gap_count_b": len(gaps_b),
        "comparison_rows": len(comparison),
        "different_rows": len(different_rows),
        "errors_a": len(errors_a),
        "errors_b": len(errors_b),
        "ok": not different_rows and not errors_a and not errors_b,
    }
    (report / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    if summary["ok"] or args.allow_differences:
        return 0
    if errors_a or errors_b:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
