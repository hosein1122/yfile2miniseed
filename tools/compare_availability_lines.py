#!/usr/bin/env python3
"""Compare two availability text reports by SourceID and start sample."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write availability differences matched by SourceID and start sample.")
    parser.add_argument("--center", required=True, type=Path, help="Center/reference availability text file.")
    parser.add_argument("--ours", required=True, type=Path, help="Our/candidate availability text file.")
    parser.add_argument("--output", required=True, type=Path, help="Output difference text file.")
    parser.add_argument("--center-label", default="CENTER", help="Label to print for the first report.")
    parser.add_argument("--ours-label", default="OUR", help="Label to print for the second report.")
    return parser.parse_args()


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


@dataclass(frozen=True)
class AvailabilityEntry:
    source_id: str
    start: str
    line: str
    order: int


def parse_availability_entries(lines: list[str]) -> list[AvailabilityEntry]:
    entries: list[AvailabilityEntry] = []
    for line in lines:
        parts = line.split()
        if len(parts) != 7 or not parts[0].startswith("FDSN:"):
            continue

        entries.append(
            AvailabilityEntry(
                source_id=parts[0],
                start=f"{parts[1]} {parts[2]}",
                line=line.rstrip(),
                order=len(entries),
            )
        )
    return entries


def group_entries(entries: list[AvailabilityEntry]) -> dict[tuple[str, str], list[AvailabilityEntry]]:
    grouped: dict[tuple[str, str], list[AvailabilityEntry]] = defaultdict(list)
    for entry in entries:
        grouped[(entry.source_id, entry.start)].append(entry)
    return grouped


def key_order(
    center_entries: list[AvailabilityEntry],
    our_entries: list[AvailabilityEntry],
) -> list[tuple[str, str]]:
    first_seen: dict[tuple[str, str], int] = {}
    sequence = 0
    for entry in center_entries + our_entries:
        key = (entry.source_id, entry.start)
        if key not in first_seen:
            first_seen[key] = sequence
            sequence += 1
    return sorted(first_seen, key=lambda item: (item[0], item[1], first_seen[item]))


def main() -> int:
    args = parse_args()
    if not args.center.exists():
        print(f"Center file not found: {args.center}", file=sys.stderr)
        return 2
    if not args.ours.exists():
        print(f"Our file not found: {args.ours}", file=sys.stderr)
        return 2

    center_entries = parse_availability_entries(read_lines(args.center))
    our_entries = parse_availability_entries(read_lines(args.ours))
    center_by_key = group_entries(center_entries)
    our_by_key = group_entries(our_entries)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    diff_count = 0
    out_lines = [
        "Availability Differences",
        f"{args.center_label} file: {args.center}",
        f"{args.ours_label} file: {args.ours}",
        "Match key: SourceID + Start sample",
        "",
    ]

    for key in key_order(center_entries, our_entries):
        center_items = center_by_key.get(key, [])
        our_items = our_by_key.get(key, [])
        count = max(len(center_items), len(our_items))
        for index in range(count):
            center_line = center_items[index].line if index < len(center_items) else ""
            our_line = our_items[index].line if index < len(our_items) else ""
            if center_line == our_line:
                continue
            diff_count += 1
            out_lines.extend(
                [
                    f"{args.center_label}: {center_line}",
                    f"{args.ours_label}: {our_line}",
                    "",
                ]
            )

    if diff_count == 0:
        out_lines.append("No differences found.")
    else:
        out_lines.insert(5, f"Different entries: {diff_count}")
        out_lines.insert(6, "")

    args.output.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Different entries: {diff_count}")
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
