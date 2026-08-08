#!/usr/bin/env python3
"""
Create a standard availability report from Nanometrics Y-file v5 input.

The report format intentionally mirrors the center-style availability text:

    SourceID    Start sample    End sample    GapSamples    DataSamples

It uses Nanometrics Y5DUMP -H as the source of header metadata.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class Segment:
    source_id: str
    start: datetime
    end: datetime
    sample_rate: float
    npts: int
    file: str


HEADER_PATTERNS = {
    "file": re.compile(r"^YFILE5\s*:\s*(.+)$"),
    "stnlocchn": re.compile(r"^\s*StnLocChn:\s*(\S+)\s+(\S+)"),
    "network": re.compile(r"^\s*NetWork ID:\s*(\S+)"),
    "sample_rate": re.compile(r"^\s*Sample Rate:\s*([0-9.]+)"),
    "start": re.compile(r"^\s*Start Time:\s*(\S+)"),
    "end": re.compile(r"^\s*End Time:\s*(\S+)"),
    "npts": re.compile(r"^Number of Samples:\s*(\d+)"),
}


def filename_station_channel(path: Path) -> tuple[str, str] | None:
    name = path.name.upper()
    underscore = re.match(r"^Y([A-Z0-9]+)_([A-Z0-9]{3})(?:[._]|$)", name)
    if underscore:
        return underscore.group(1), underscore.group(2)
    compact = re.match(r"^Y([A-Z0-9]{3})([A-Z0-9]{3})(?:[._]|$)", name)
    if compact:
        return compact.group(1), compact.group(2)
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report Y-file availability using Y5DUMP headers.")
    parser.add_argument("--input", required=True, type=Path, help="Input folder containing Y-files or ZIP files.")
    parser.add_argument("--output", required=True, type=Path, help="Output report folder.")
    parser.add_argument(
        "--y5dump",
        required=True,
        type=Path,
        help="Path to Nanometrics y5dump.exe.",
    )
    parser.add_argument("--station", help="Optional station filter, e.g. KAZ.")
    parser.add_argument("--channel-prefix", help="Optional channel prefix filter, e.g. SP.")
    parser.add_argument("--components", default="ENZ", help="Component letters for filtering. Default: ENZ.")
    parser.add_argument(
        "--include-data",
        action="store_true",
        help="Also save raw Y5DUMP header text for each input file.",
    )
    parser.add_argument(
        "--tolerance-samples",
        type=float,
        default=1.1,
        help="Treat gaps/overlaps up to this many samples as contiguous. Default: 1.1.",
    )
    parser.add_argument(
        "--snap-times",
        action="store_true",
        help=(
            "Accepted for command compatibility, but Y5DUMP Start/End times are kept "
            "verbatim in the report."
        ),
    )
    return parser.parse_args()


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d_%H:%M:%S.%f").replace(tzinfo=timezone.utc)


def fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-2]


def computed_end_sample_time(start: datetime, sample_rate: float, npts: int) -> datetime:
    if npts <= 0:
        return start
    return start + timedelta(seconds=(npts - 1) / sample_rate)


def source_id(network: str, station: str, channel: str) -> str:
    prefix = channel[:-1] if len(channel) > 1 else channel
    component = channel[-1] if channel else ""
    return f"FDSN:{network}_{station}_{prefix}_{component}__"


def matches_filters(path: Path, args: argparse.Namespace) -> bool:
    parsed = filename_station_channel(path)
    if not parsed:
        return False
    station, channel = parsed
    if args.station and station != args.station.upper():
        return False
    if args.channel_prefix and not channel.startswith(args.channel_prefix.upper()):
        return False
    if args.channel_prefix and args.components and channel[-1] not in args.components.upper():
        return False
    return True


def iter_input_files(input_dir: Path, args: argparse.Namespace, temp_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            extract_root = temp_root / path.stem
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir():
                        continue
                    member_name = Path(member.filename).name
                    fake_path = Path(member_name)
                    if not matches_filters(fake_path, args):
                        continue
                    out_path = extract_root / member.filename
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, out_path.open("wb") as target:
                        target.write(source.read())
                    files.append(out_path)
        elif matches_filters(path, args):
            files.append(path)
    return sorted(files)


def parse_y5dump_header(text: str, fallback_file: Path) -> Segment | None:
    values: dict[str, object] = {"file": str(fallback_file)}
    for line in text.splitlines():
        for key, pattern in HEADER_PATTERNS.items():
            match = pattern.match(line)
            if not match:
                continue
            if key == "stnlocchn":
                values["station"] = match.group(1).strip()
                values["channel"] = match.group(2).strip()
            elif key == "sample_rate":
                values[key] = float(match.group(1))
            elif key == "npts":
                values[key] = int(match.group(1))
            elif key in {"start", "end"}:
                values[key] = parse_time(match.group(1))
            else:
                values[key] = match.group(1).strip()
    required = {"network", "station", "channel", "sample_rate", "start", "npts"}
    if not required.issubset(values):
        return None
    start = values["start"]  # type: ignore[assignment]
    sample_rate = float(values["sample_rate"])
    npts = int(values["npts"])
    return Segment(
        source_id=source_id(str(values["network"]), str(values["station"]), str(values["channel"])),
        start=start,  # type: ignore[arg-type]
        end=computed_end_sample_time(start, sample_rate, npts),  # type: ignore[arg-type]
        sample_rate=sample_rate,
        npts=npts,
        file=str(values["file"]),
    )


def run_y5dump(files: list[Path], args: argparse.Namespace, output_dir: Path) -> tuple[list[Segment], list[dict]]:
    segments: list[Segment] = []
    errors: list[dict] = []
    raw_dump_dir = output_dir / "y5dump_headers"
    if args.include_data:
        raw_dump_dir.mkdir(parents=True, exist_ok=True)

    for path in files:
        result = subprocess.run(
            [str(args.y5dump), "-H", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
        if args.include_data:
            safe_name = str(path).replace(":", "").replace("\\", "__").replace("/", "__")
            (raw_dump_dir / f"{safe_name}.header.txt").write_text(result.stdout, encoding="utf-8")
        if result.returncode != 0:
            errors.append({"file": str(path), "error": result.stdout.strip()})
            continue
        segment = parse_y5dump_header(result.stdout, path)
        if segment:
            segments.append(segment)
        else:
            errors.append({"file": str(path), "error": "Could not parse Y5DUMP header"})
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

    # حذف خروجی‌های قدیمی این ابزار تا فایل‌های منسوخ یا خطای قبلی باقی نمانند.
    (output_dir / "availability.txt").unlink(missing_ok=True)
    (output_dir / "availability.csv").unlink(missing_ok=True)
    (output_dir / "errors.csv").unlink(missing_ok=True)

    grouped: dict[str, list[Segment]] = {}
    for segment in segments:
        grouped.setdefault(segment.source_id, []).append(segment)

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
            previous = item
        text_lines.append("")

    (output_dir / "y-availability.txt").write_text(
        "\n".join(text_lines),
        encoding="utf-8",
    )

    # فایل خطا فقط زمانی ساخته می‌شود که دست‌کم یک خطا وجود داشته باشد.
    if errors:
        with (output_dir / "errors.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["file", "error"])
            writer.writeheader()
            writer.writerows(errors)


def main() -> int:
    args = parse_args()
    if not args.input.exists():
        print(f"Input folder not found: {args.input}", file=sys.stderr)
        return 2
    if not args.y5dump.exists():
        print(f"Y5DUMP not found: {args.y5dump}", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yfile_availability_") as temp:
        files = iter_input_files(args.input, args, Path(temp))
        segments, errors = run_y5dump(files, args, args.output)
    write_reports(
        segments,
        errors,
        args.output,
        "Input Y-Files Availability Contents:",
        args.tolerance_samples,
    )
    print(f"Input files scanned: {len(files)}")
    print(f"Segments parsed: {len(segments)}")
    print(f"Errors: {len(errors)}")
    print(f"Reports: {args.output}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
