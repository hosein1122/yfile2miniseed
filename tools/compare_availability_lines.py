#!/usr/bin/env python3
"""Compare two availability text reports line by line."""

from __future__ import annotations

import argparse
import sys
from itertools import zip_longest
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write line-by-line differences between two text reports.")
    parser.add_argument("--center", required=True, type=Path, help="Center/reference availability text file.")
    parser.add_argument("--ours", required=True, type=Path, help="Our/candidate availability text file.")
    parser.add_argument("--output", required=True, type=Path, help="Output difference text file.")
    return parser.parse_args()


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def main() -> int:
    args = parse_args()
    if not args.center.exists():
        print(f"Center file not found: {args.center}", file=sys.stderr)
        return 2
    if not args.ours.exists():
        print(f"Our file not found: {args.ours}", file=sys.stderr)
        return 2

    center_lines = read_lines(args.center)
    our_lines = read_lines(args.ours)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    diff_count = 0
    out_lines = [
        "Availability Line Differences",
        f"Center file: {args.center}",
        f"Our file   : {args.ours}",
        "",
    ]

    for line_number, (center_line, our_line) in enumerate(
        zip_longest(center_lines, our_lines, fillvalue=""),
        start=1,
    ):
        if center_line.rstrip() == our_line.rstrip():
            continue
        diff_count += 1
        out_lines.extend(
            [
                f"Line {line_number}",
                f"CENTER: {center_line}",
                f"OUR   : {our_line}",
                "",
            ]
        )

    if diff_count == 0:
        out_lines.append("No differences found.")
    else:
        out_lines.insert(4, f"Different lines: {diff_count}")
        out_lines.insert(5, "")

    args.output.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Different lines: {diff_count}")
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
