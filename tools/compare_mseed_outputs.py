#!/usr/bin/env python3
"""
Compare two MiniSEED/SDS output trees with ObsPy.

Levels:
  simple  - headers only: ids, time span, rates, sample counts, file counts.
  medium  - headers only plus coverage/gap/overlap comparison.
  deep    - reads sample data and compares merged traces.

The reference tree is usually the national-center converter output. The
candidate tree is usually yfile2miniseed output.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

try:
    import numpy as np
    from obspy import Stream, read
    from obspy.core.utcdatetime import UTCDateTime
except Exception as exc:  # pragma: no cover - used for operator diagnostics
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
class TraceHeader:
    key: str
    original_id: str
    network: str
    station: str
    location: str
    channel: str
    start: str
    end: str
    sampling_rate: float
    npts: int
    file: str
    encoding: str


@dataclass
class GroupSummary:
    key: str
    trace_count: int
    file_count: int
    start: str | None
    end: str | None
    sampling_rates: list[float]
    sample_sum: int
    union_sample_count: int
    gap_count: int
    overlap_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two MiniSEED/SDS output trees with ObsPy."
    )
    parser.add_argument("--reference", required=True, type=Path, help="Reference output tree.")
    parser.add_argument("--candidate", required=True, type=Path, help="Candidate output tree.")
    parser.add_argument("--report", required=True, type=Path, help="Report output folder.")
    parser.add_argument(
        "--level",
        choices=["simple", "medium", "deep"],
        default="simple",
        help="Comparison depth. Use deep only for small datasets or filtered runs.",
    )
    parser.add_argument(
        "--id-mode",
        choices=["strict", "component"],
        default="strict",
        help=(
            "strict compares full NET.STA.LOC.CHA. component compares NET.STA.LOC plus "
            "the last channel letter, useful when one converter rewrites BHZ/SPZ to SHZ."
        ),
    )
    parser.add_argument(
        "--default-network",
        default="",
        help="Network code to use when a trace has an empty network, e.g. IR.",
    )
    parser.add_argument("--station", action="append", help="Only compare this station. Repeatable.")
    parser.add_argument("--channel", action="append", help="Only compare this channel. Repeatable.")
    parser.add_argument("--date-from", help="Only include traces ending at/after this UTC time.")
    parser.add_argument("--date-to", help="Only include traces starting at/before this UTC time.")
    parser.add_argument(
        "--max-deep-samples",
        type=int,
        default=2_000_000,
        help="Maximum union samples per trace key for deep comparison.",
    )
    parser.add_argument(
        "--fail-on-difference",
        action="store_true",
        help="Return exit code 1 if any difference is found.",
    )
    return parser.parse_args()


def normalize_code(value: str) -> str:
    return (value or "").strip()


def trace_key(stats, id_mode: str, default_network: str) -> str:
    network = normalize_code(stats.network) or default_network
    station = normalize_code(stats.station)
    location = normalize_code(stats.location)
    channel = normalize_code(stats.channel)

    if id_mode == "component":
        component = channel[-1:] if channel else ""
        return f"{network}.{station}.{location}.{component}"

    return f"{network}.{station}.{location}.{channel}"


def iter_candidate_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path


def in_filters(stats, args: argparse.Namespace) -> bool:
    if args.station and normalize_code(stats.station) not in set(args.station):
        return False
    if args.channel and normalize_code(stats.channel) not in set(args.channel):
        return False
    if args.date_from and stats.endtime < UTCDateTime(args.date_from):
        return False
    if args.date_to and stats.starttime > UTCDateTime(args.date_to):
        return False
    return True


def scan_headers(root: Path, args: argparse.Namespace) -> tuple[list[TraceHeader], list[dict]]:
    headers: list[TraceHeader] = []
    errors: list[dict] = []

    for file in iter_candidate_files(root):
        try:
            stream = read(str(file), headonly=True)
        except Exception as exc:
            errors.append({"file": str(file), "error": repr(exc)})
            continue

        for trace in stream:
            stats = trace.stats
            if not in_filters(stats, args):
                continue
            mseed = getattr(stats, "mseed", {})
            key = trace_key(stats, args.id_mode, args.default_network)
            network = normalize_code(stats.network) or args.default_network
            headers.append(
                TraceHeader(
                    key=key,
                    original_id=trace.id,
                    network=network,
                    station=normalize_code(stats.station),
                    location=normalize_code(stats.location),
                    channel=normalize_code(stats.channel),
                    start=str(stats.starttime),
                    end=str(stats.endtime),
                    sampling_rate=float(stats.sampling_rate),
                    npts=int(stats.npts),
                    file=str(file),
                    encoding=str(mseed.get("encoding", "")),
                )
            )

    return headers, errors


def group_headers(headers: list[TraceHeader]) -> dict[str, list[TraceHeader]]:
    grouped: dict[str, list[TraceHeader]] = defaultdict(list)
    for header in headers:
        grouped[header.key].append(header)
    return dict(grouped)


def sample_step(rate: float) -> float:
    if rate <= 0.0:
        return math.inf
    return 1.0 / rate


def summarize_group(key: str, headers: list[TraceHeader]) -> GroupSummary:
    if not headers:
        return GroupSummary(key, 0, 0, None, None, [], 0, 0, 0, 0)

    starts = [UTCDateTime(h.start) for h in headers]
    ends = [UTCDateTime(h.end) for h in headers]
    rates = sorted({round(h.sampling_rate, 12) for h in headers})
    sample_sum = sum(h.npts for h in headers)
    file_count = len({h.file for h in headers})

    union_samples, gap_count, overlap_count = coverage_stats(headers)

    return GroupSummary(
        key=key,
        trace_count=len(headers),
        file_count=file_count,
        start=str(min(starts)),
        end=str(max(ends)),
        sampling_rates=rates,
        sample_sum=sample_sum,
        union_sample_count=union_samples,
        gap_count=gap_count,
        overlap_count=overlap_count,
    )


def coverage_stats(headers: list[TraceHeader]) -> tuple[int, int, int]:
    by_rate: dict[float, list[tuple[UTCDateTime, UTCDateTime, int]]] = defaultdict(list)
    for h in headers:
        by_rate[round(h.sampling_rate, 12)].append((UTCDateTime(h.start), UTCDateTime(h.end), h.npts))

    union_samples = 0
    gap_count = 0
    overlap_count = 0

    for rate, intervals in by_rate.items():
        intervals.sort(key=lambda item: item[0])
        if not intervals:
            continue

        step = sample_step(rate)
        tolerance = step * 0.51
        current_start, current_end, _ = intervals[0]

        for start, end, _npts in intervals[1:]:
            if start <= current_end + tolerance:
                if start <= current_end - tolerance:
                    overlap_count += 1
                if end > current_end:
                    current_end = end
            else:
                gap_count += 1
                union_samples += samples_between(current_start, current_end, rate)
                current_start, current_end = start, end

        union_samples += samples_between(current_start, current_end, rate)

    return union_samples, gap_count, overlap_count


def samples_between(start: UTCDateTime, end: UTCDateTime, rate: float) -> int:
    if end < start or rate <= 0:
        return 0
    return int(round((end - start) * rate)) + 1


def compare_summaries(
    ref: dict[str, GroupSummary],
    cand: dict[str, GroupSummary],
) -> list[dict]:
    rows: list[dict] = []
    all_keys = sorted(set(ref) | set(cand))

    for key in all_keys:
        r = ref.get(key)
        c = cand.get(key)
        status = "OK"
        notes: list[str] = []

        if r is None:
            status = "ONLY_CANDIDATE"
            notes.append("missing from reference")
        elif c is None:
            status = "ONLY_REFERENCE"
            notes.append("missing from candidate")
        else:
            checks = [
                ("start", r.start, c.start),
                ("end", r.end, c.end),
                ("sampling_rates", r.sampling_rates, c.sampling_rates),
                ("union_sample_count", r.union_sample_count, c.union_sample_count),
            ]
            for name, left, right in checks:
                if left != right:
                    status = "DIFF"
                    notes.append(f"{name}: {left} != {right}")

            if r.gap_count != c.gap_count:
                status = "DIFF"
                notes.append(f"gap_count: {r.gap_count} != {c.gap_count}")
            if r.overlap_count != c.overlap_count:
                status = "DIFF"
                notes.append(f"overlap_count: {r.overlap_count} != {c.overlap_count}")

        rows.append(
            {
                "key": key,
                "status": status,
                "notes": "; ".join(notes),
                "reference": asdict(r) if r else None,
                "candidate": asdict(c) if c else None,
            }
        )

    return rows


def load_stream_for_key(root: Path, key: str, args: argparse.Namespace) -> Stream:
    stream = Stream()
    for file in iter_candidate_files(root):
        try:
            part = read(str(file))
        except Exception:
            continue
        for trace in part:
            if not in_filters(trace.stats, args):
                continue
            if trace_key(trace.stats, args.id_mode, args.default_network) == key:
                stream += trace
    stream.sort(keys=["starttime"])
    stream.merge(method=1, fill_value=None)
    return stream


def arrays_equal(left, right) -> tuple[bool, str]:
    if len(left) != len(right):
        return False, f"length {len(left)} != {len(right)}"

    left_mask = np.ma.getmaskarray(left)
    right_mask = np.ma.getmaskarray(right)
    if not np.array_equal(left_mask, right_mask):
        return False, "gap mask differs"

    left_data = np.ma.filled(left, 0)
    right_data = np.ma.filled(right, 0)
    diff = np.flatnonzero(left_data != right_data)
    if diff.size:
        idx = int(diff[0])
        return False, f"first sample diff at index {idx}: {left_data[idx]} != {right_data[idx]}"
    return True, ""


def deep_compare(
    ref_root: Path,
    cand_root: Path,
    compare_rows: list[dict],
    args: argparse.Namespace,
) -> list[dict]:
    deep_rows: list[dict] = []
    keys = [row["key"] for row in compare_rows if row["status"] != "ONLY_REFERENCE" and row["status"] != "ONLY_CANDIDATE"]

    for key in keys:
        ref_summary = next(row["reference"] for row in compare_rows if row["key"] == key)
        candidate_summary = next(row["candidate"] for row in compare_rows if row["key"] == key)
        max_samples = max(ref_summary["union_sample_count"], candidate_summary["union_sample_count"])
        if max_samples > args.max_deep_samples:
            deep_rows.append(
                {
                    "key": key,
                    "status": "SKIPPED_TOO_LARGE",
                    "notes": f"{max_samples} samples exceeds --max-deep-samples={args.max_deep_samples}",
                }
            )
            continue

        ref_stream = load_stream_for_key(ref_root, key, args)
        cand_stream = load_stream_for_key(cand_root, key, args)
        if len(ref_stream) != len(cand_stream):
            deep_rows.append(
                {
                    "key": key,
                    "status": "DIFF",
                    "notes": f"merged trace count {len(ref_stream)} != {len(cand_stream)}",
                }
            )
            continue

        status = "OK"
        notes: list[str] = []
        for idx, (ref_trace, cand_trace) in enumerate(zip(ref_stream, cand_stream)):
            if ref_trace.stats.starttime != cand_trace.stats.starttime:
                status = "DIFF"
                notes.append(f"trace {idx} start differs")
            if ref_trace.stats.endtime != cand_trace.stats.endtime:
                status = "DIFF"
                notes.append(f"trace {idx} end differs")
            if abs(ref_trace.stats.sampling_rate - cand_trace.stats.sampling_rate) > 1e-9:
                status = "DIFF"
                notes.append(f"trace {idx} sampling_rate differs")

            same, detail = arrays_equal(ref_trace.data, cand_trace.data)
            if not same:
                status = "DIFF"
                notes.append(f"trace {idx} data: {detail}")
                break

        deep_rows.append({"key": key, "status": status, "notes": "; ".join(notes)})

    return deep_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = sorted({key for row in rows for key in row.keys()})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            flat = {}
            for key, value in row.items():
                if isinstance(value, (dict, list)):
                    flat[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    flat[key] = value
            writer.writerow(flat)


def main() -> int:
    args = parse_args()
    args.reference = args.reference.resolve()
    args.candidate = args.candidate.resolve()
    args.report.mkdir(parents=True, exist_ok=True)

    ref_headers, ref_errors = scan_headers(args.reference, args)
    cand_headers, cand_errors = scan_headers(args.candidate, args)

    ref_groups = {key: summarize_group(key, items) for key, items in group_headers(ref_headers).items()}
    cand_groups = {key: summarize_group(key, items) for key, items in group_headers(cand_headers).items()}
    comparison = compare_summaries(ref_groups, cand_groups)

    report = {
        "reference": str(args.reference),
        "candidate": str(args.candidate),
        "level": args.level,
        "id_mode": args.id_mode,
        "reference_file_errors": ref_errors,
        "candidate_file_errors": cand_errors,
        "reference_trace_count": len(ref_headers),
        "candidate_trace_count": len(cand_headers),
        "comparison": comparison,
    }

    if args.level == "deep":
        report["deep_comparison"] = deep_compare(args.reference, args.candidate, comparison, args)

    (args.report / "comparison_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_csv(args.report / "comparison_summary.csv", comparison)

    mismatch_rows = [row for row in comparison if row["status"] != "OK"]
    if args.level == "deep":
        mismatch_rows += [row for row in report["deep_comparison"] if row["status"] != "OK"]

    mismatch_text = "\n".join(
        f"{row['status']}\t{row['key']}\t{row.get('notes', '')}" for row in mismatch_rows
    )
    (args.report / "mismatches.txt").write_text(mismatch_text + ("\n" if mismatch_text else ""), encoding="utf-8")

    print(f"Reference traces: {len(ref_headers)}")
    print(f"Candidate traces: {len(cand_headers)}")
    print(f"Compared keys: {len(comparison)}")
    print(f"Differences: {len(mismatch_rows)}")
    print(f"Reports written to: {args.report}")

    if args.fail_on_difference and mismatch_rows:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
