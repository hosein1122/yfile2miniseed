#!/usr/bin/env python3
"""
Build a focused Y-file evidence case for one station.

The tool:
  1. Extracts one station/channel prefix from input ZIP files while preserving
     ZIP-internal folders.
  2. Runs Nanometrics Y5DUMP -H for each extracted Y-file.
  3. Parses header timing/sample metadata into CSV reports.
  4. Compares center and candidate MiniSEED output trees with
     compare_mseed_outputs.py.

It intentionally does not run either converter. Put converter outputs in the
case folder first, then run this tool to create repeatable evidence reports.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create focused Y-file/Nanometrics/ObsPy reports for one station."
    )
    parser.add_argument("--case-root", required=True, type=Path, help="Focused case folder.")
    parser.add_argument(
        "--raw",
        type=Path,
        help="Folder containing input ZIP files. Default: <case-root>/raw",
    )
    parser.add_argument("--station", required=True, help="Station code, e.g. KAZ.")
    parser.add_argument(
        "--channel-prefix",
        default="SP",
        help="Y-file channel prefix after station. Default: SP, matching SPE/SPN/SPZ.",
    )
    parser.add_argument(
        "--components",
        default="ENZ",
        help="Component letters to keep. Default: ENZ.",
    )
    parser.add_argument(
        "--y5dump",
        type=Path,
        default=Path(r"D:\MSeed_Test\Tools\Nanometrics\y5dump.exe"),
        help="Path to Nanometrics y5dump.exe.",
    )
    parser.add_argument(
        "--center-output",
        type=Path,
        help="Center MiniSEED/SDS output. Default: <case-root>/center_output",
    )
    parser.add_argument(
        "--our-output",
        type=Path,
        help="Candidate MiniSEED/SDS output. Default: <case-root>/our_output",
    )
    parser.add_argument(
        "--compare-tool",
        type=Path,
        default=Path(__file__).resolve().with_name("compare_mseed_outputs.py"),
        help="Path to compare_mseed_outputs.py.",
    )
    parser.add_argument(
        "--compare-level",
        choices=["simple", "medium", "deep"],
        default="deep",
        help="MiniSEED comparison level. Default: deep.",
    )
    parser.add_argument(
        "--id-mode",
        choices=["strict", "component"],
        default="component",
        help="MiniSEED comparison id mode. Default: component.",
    )
    parser.add_argument("--default-network", default="IR")
    parser.add_argument("--max-deep-samples", type=int, default=10_000_000)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clear generated raw_<station>, nanometrics_dump, and focused reports first.",
    )
    return parser.parse_args()


def ensure_empty(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def station_pattern(station: str, channel_prefix: str, components: str) -> re.Pattern[str]:
    comp = re.escape(components.upper())
    return re.compile(rf"^Y{re.escape(station.upper())}{re.escape(channel_prefix.upper())}[{comp}]\.")


def extract_station_files(raw_dir: Path, output_dir: Path, pattern: re.Pattern[str]) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    for zip_path in sorted(raw_dir.rglob("*.zip")):
        zip_tag = zip_path.stem
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                name = Path(member.filename).name
                if member.is_dir() or not pattern.match(name):
                    continue
                out_path = output_dir / zip_tag / Path(member.filename)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, out_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                extracted.append(out_path)
        print(f"{zip_path.name}: extracted {sum(1 for p in extracted if p.parts[-3] == zip_tag)} files so far")
    return sorted(extracted)


def write_manifest(files: list[Path], raw_station_dir: Path, report_dir: Path) -> None:
    rows = []
    for path in files:
        rel = path.relative_to(raw_station_dir)
        parts = rel.parts
        name = path.name
        rows.append(
            {
                "zip_day": parts[0] if len(parts) > 0 else "",
                "hour_folder": parts[1] if len(parts) > 1 else "",
                "name": name,
                "channel": name[4:7],
                "start_from_name": name.split(".", 1)[1] if "." in name else "",
                "length": path.stat().st_size,
                "relative_path": str(rel),
                "full_name": str(path),
            }
        )

    with (report_dir / "raw_station_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["name"])
        writer.writeheader()
        writer.writerows(rows)

    by_name: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_name[row["name"]].append(row)
    duplicates = [item for values in by_name.values() if len(values) > 1 for item in values]
    fields = list(rows[0].keys()) if rows else ["name"]
    with (report_dir / "raw_station_duplicate_names.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(duplicates)


HEADER_PATTERNS = {
    "file": re.compile(r"^YFILE5\s*:\s*(.+)$"),
    "stnlocchn": re.compile(r"^\s*StnLocChn:\s*(\S+)\s+(\S+)"),
    "network": re.compile(r"^\s*NetWork ID:\s*(\S+)"),
    "sample_rate": re.compile(r"^\s*Sample Rate:\s*([0-9.]+)"),
    "start": re.compile(r"^\s*Start Time:\s*(\S+)"),
    "end": re.compile(r"^\s*End Time:\s*(\S+)"),
    "npts": re.compile(r"^Number of Samples:\s*(\d+)"),
    "max": re.compile(r"^\s*Max Amplitude:\s*(-?\d+)"),
    "min": re.compile(r"^\s*Min Amplitude:\s*(-?\d+)"),
}


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d_%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def run_y5dump_headers(files: list[Path], raw_station_dir: Path, dump_dir: Path, y5dump: Path) -> list[dict]:
    dump_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    errors = []
    for path in files:
        rel = path.relative_to(raw_station_dir)
        out_path = dump_dir / (str(rel) + ".header.txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [str(y5dump), "-H", str(path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            errors="replace",
        )
        out_path.write_text(result.stdout, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            errors.append({"file": str(path), "returncode": result.returncode, "output": result.stdout})
            continue
        row = parse_header_text(result.stdout)
        row["relative_yfile"] = str(rel)
        row["yfile"] = str(path)
        row["header_file"] = str(out_path)
        parts = rel.parts
        row["zip_day"] = parts[0] if len(parts) > 0 else ""
        row["hour_folder"] = parts[1] if len(parts) > 1 else ""
        row["name"] = path.name
        rows.append(row)

    return rows, errors


def parse_header_text(text: str) -> dict:
    row: dict = {}
    for line in text.splitlines():
        for key, pattern in HEADER_PATTERNS.items():
            match = pattern.match(line)
            if not match:
                continue
            if key == "stnlocchn":
                row["station"] = match.group(1).strip()
                row["channel"] = match.group(2).strip()
            elif key == "sample_rate":
                row[key] = float(match.group(1))
            elif key == "npts":
                row[key] = int(match.group(1))
            elif key in {"start", "end"}:
                row[key] = parse_time(match.group(1))
                row[key + "_text"] = match.group(1)
            else:
                row[key] = match.group(1).strip()
    return row


def write_y5dump_reports(rows: list[dict], errors: list[dict], report_dir: Path) -> None:
    fields = [
        "zip_day",
        "hour_folder",
        "name",
        "station",
        "channel",
        "network",
        "sample_rate",
        "start_text",
        "end_text",
        "npts",
        "min",
        "max",
        "relative_yfile",
        "header_file",
    ]
    with (report_dir / "nanometrics_yfile_headers.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    with (report_dir / "nanometrics_header_dump_errors.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["file", "returncode", "output"])
        writer.writeheader()
        writer.writerows(errors)

    summary_rows = coverage_summary(rows)
    fields = [
        "channel",
        "file_count",
        "start",
        "end",
        "sample_sum",
        "gap_count",
        "gap_samples",
        "overlap_count",
        "overlap_samples",
    ]
    with (report_dir / "nanometrics_yfile_coverage_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)


def coverage_summary(rows: list[dict]) -> list[dict]:
    result = []
    channels = sorted({row.get("channel") for row in rows if row.get("channel")})
    for channel in channels:
        items = sorted(
            [row for row in rows if row.get("channel") == channel],
            key=lambda row: (row["start"], row["end"]),
        )
        gaps = []
        overlaps = []
        for previous, current in zip(items, items[1:]):
            sample_rate = float(current.get("sample_rate") or previous.get("sample_rate") or 50.0)
            step = timedelta(seconds=1.0 / sample_rate)
            expected = previous["end"] + step
            delta = current["start"] - expected
            samples = round(delta.total_seconds() * sample_rate)
            if samples > 0:
                gaps.append(samples)
            elif samples < 0:
                overlaps.append(-samples)
        result.append(
            {
                "channel": channel,
                "file_count": len(items),
                "start": items[0]["start"].isoformat().replace("+00:00", "Z") if items else "",
                "end": items[-1]["end"].isoformat().replace("+00:00", "Z") if items else "",
                "sample_sum": sum(int(row.get("npts", 0)) for row in items),
                "gap_count": len(gaps),
                "gap_samples": sum(gaps),
                "overlap_count": len(overlaps),
                "overlap_samples": sum(overlaps),
            }
        )
    return result


def run_compare(args: argparse.Namespace, report_dir: Path) -> None:
    center_output = args.center_output or args.case_root / "center_output"
    our_output = args.our_output or args.case_root / "our_output"
    if not center_output.exists() or not any(center_output.rglob("*")):
        print(f"Skipping MiniSEED compare; center output is empty or missing: {center_output}")
        return
    if not our_output.exists() or not any(our_output.rglob("*")):
        print(f"Skipping MiniSEED compare; candidate output is empty or missing: {our_output}")
        return
    command = [
        sys.executable,
        str(args.compare_tool),
        "--reference",
        str(center_output),
        "--candidate",
        str(our_output),
        "--report",
        str(report_dir / "compare_center_vs_ours"),
        "--level",
        args.compare_level,
        "--station",
        args.station.upper(),
        "--id-mode",
        args.id_mode,
        "--default-network",
        args.default_network,
        "--max-deep-samples",
        str(args.max_deep_samples),
    ]
    subprocess.run(command, check=True)


def write_case_summary(args: argparse.Namespace, extracted: list[Path], report_dir: Path) -> None:
    summary = {
        "case_root": str(args.case_root),
        "station": args.station.upper(),
        "channel_prefix": args.channel_prefix.upper(),
        "components": args.components.upper(),
        "raw_files_extracted": len(extracted),
        "reports": {
            "raw_manifest": str(report_dir / "raw_station_manifest.csv"),
            "duplicate_names": str(report_dir / "raw_station_duplicate_names.csv"),
            "nanometrics_headers": str(report_dir / "nanometrics_yfile_headers.csv"),
            "nanometrics_coverage": str(report_dir / "nanometrics_yfile_coverage_summary.csv"),
            "mseed_compare": str(report_dir / "compare_center_vs_ours"),
        },
    }
    (report_dir / "focused_case_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    lines = [
        f"# Focused Y-file Case: {args.station.upper()}",
        "",
        f"- Case root: `{args.case_root}`",
        f"- Extracted raw files: `{len(extracted)}`",
        f"- Channel prefix: `{args.channel_prefix.upper()}`",
        f"- Components: `{args.components.upper()}`",
        "",
        "## Reports",
        "",
        f"- Raw manifest: `{report_dir / 'raw_station_manifest.csv'}`",
        f"- Duplicate names: `{report_dir / 'raw_station_duplicate_names.csv'}`",
        f"- Nanometrics headers: `{report_dir / 'nanometrics_yfile_headers.csv'}`",
        f"- Nanometrics coverage: `{report_dir / 'nanometrics_yfile_coverage_summary.csv'}`",
        f"- MiniSEED comparison: `{report_dir / 'compare_center_vs_ours'}`",
        "",
    ]
    (report_dir / "focused_case_summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.case_root = args.case_root.resolve()
    raw_dir = (args.raw or args.case_root / "raw").resolve()
    raw_station_dir = args.case_root / f"raw_{args.station.lower()}"
    dump_dir = args.case_root / "nanometrics_dump" / "headers"
    report_dir = args.case_root / "reports"

    if not raw_dir.exists():
        print(f"Raw input folder does not exist: {raw_dir}", file=sys.stderr)
        return 2
    if not args.y5dump.exists():
        print(f"Y5DUMP not found: {args.y5dump}", file=sys.stderr)
        return 2

    if args.clean:
        ensure_empty(raw_station_dir)
        ensure_empty(dump_dir)
        ensure_empty(report_dir)
    else:
        raw_station_dir.mkdir(parents=True, exist_ok=True)
        dump_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

    pattern = station_pattern(args.station, args.channel_prefix, args.components)
    extracted = extract_station_files(raw_dir, raw_station_dir, pattern)
    if not extracted:
        print("No matching Y-files were extracted.", file=sys.stderr)
        return 1

    write_manifest(extracted, raw_station_dir, report_dir)
    header_rows, header_errors = run_y5dump_headers(extracted, raw_station_dir, dump_dir, args.y5dump)
    write_y5dump_reports(header_rows, header_errors, report_dir)
    run_compare(args, report_dir)
    write_case_summary(args, extracted, report_dir)

    print(f"Extracted Y-files: {len(extracted)}")
    print(f"Nanometrics headers parsed: {len(header_rows)}")
    print(f"Header dump errors: {len(header_errors)}")
    print(f"Reports written to: {report_dir}")
    return 0 if not header_errors else 1


if __name__ == "__main__":
    sys.exit(main())
