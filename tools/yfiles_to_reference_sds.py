#!/usr/bin/env python3
"""Build a strict SDS archive from Nanometrics Y-files with ObsPy."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import obspy
from obspy import Stream, Trace, UTCDateTime, read
from obspy.clients.filesystem.sds import SDS_FMTSTR


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CORRECT_SID = SCRIPT_DIR / "CorrectSID.txt"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read Nanometrics Y-files with ObsPy and write daily strict SDS MiniSEED."
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--network", help="Fallback network if --no-correct-sid is used.")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--encoding", default="STEIM1", choices=("STEIM1", "STEIM2", "INT32"))
    parser.add_argument("--record-length", type=int, default=4096)
    parser.add_argument("--pattern", default="*")
    parser.add_argument("--station", help="Override station on all traces.")
    parser.add_argument("--location", help="Override location on all traces. Use --location= for empty.")
    parser.add_argument("--channel", help="Override channel on all traces.")
    parser.add_argument("--correct-sid", type=Path, default=DEFAULT_CORRECT_SID)
    parser.add_argument("--no-correct-sid", action="store_true")
    parser.add_argument("--report", type=Path, help="Optional JSON report path outside output-root.")
    return parser.parse_args()


def ensure_empty_output(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"output-root is not a directory: {path}")
        if any(path.iterdir()):
            raise RuntimeError(f"output-root must be new or empty: {path}")
    else:
        path.mkdir(parents=True)


def iter_input_files(root: Path, pattern: str, recursive: bool) -> list[Path]:
    iterator = root.rglob(pattern) if recursive else root.glob(pattern)
    return sorted(path for path in iterator if path.is_file())


def clean_code(value: str) -> str:
    return "".join(str(value or "").strip().split())


def build_sid_key(network: str, station: str, location: str, channel: str) -> str:
    return "_".join(
        (
            clean_code(network),
            clean_code(station),
            clean_code(location),
            clean_code(channel),
        )
    )


def split_sid_key(value: str) -> tuple[str, str, str, str]:
    parts = value.split("_")
    if len(parts) != 4:
        raise RuntimeError(f"invalid CorrectSID value: {value!r}")
    return tuple(clean_code(part) for part in parts)


def load_correct_sid(path: Path) -> dict[str, tuple[str, str, str, str]]:
    if not path.is_file():
        raise RuntimeError(f"CorrectSID.txt not found next to Python tool: {path}")

    corrections: dict[str, tuple[str, str, str, str]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or "=>" not in line:
            continue
        raw_key, corrected_key = (part.strip() for part in line.split("=>", 1))
        if not raw_key or not corrected_key:
            continue
        corrections[clean_code(raw_key)] = split_sid_key(corrected_key)

    if not corrections:
        raise RuntimeError(f"CorrectSID.txt has no usable entries: {path}")
    return corrections


def find_corrected_sid(
    corrections: dict[str, tuple[str, str, str, str]],
    raw_network: str,
    raw_station: str,
    raw_location: str,
    raw_channel: str,
) -> tuple[str, str, str, str] | None:
    raw_key = build_sid_key(raw_network, raw_station, raw_location, raw_channel)
    corrected = corrections.get(raw_key)
    if corrected is not None:
        return corrected

    if clean_code(raw_network):
        return None

    suffix = "_" + build_sid_key("", raw_station, raw_location, raw_channel).lstrip("_")
    matches = [
        value
        for key, value in corrections.items()
        if key.endswith(suffix)
    ]
    if len(matches) == 1:
        return matches[0]

    return None


def apply_metadata(
    stream: Stream,
    args: argparse.Namespace,
    source: Path,
    corrections: dict[str, tuple[str, str, str, str]] | None,
) -> None:
    for trace in stream:
        if corrections is not None:
            corrected = find_corrected_sid(
                corrections,
                trace.stats.network,
                trace.stats.station,
                trace.stats.location,
                trace.stats.channel,
            )
            if corrected is None:
                raw_key = build_sid_key(
                    trace.stats.network,
                    trace.stats.station,
                    trace.stats.location,
                    trace.stats.channel,
                )
                raise RuntimeError(f"{source}: missing CorrectSID.txt entry for raw SID {raw_key!r}")
            (
                trace.stats.network,
                trace.stats.station,
                trace.stats.location,
                trace.stats.channel,
            ) = corrected
        elif args.network is not None:
            trace.stats.network = args.network

        if args.station is not None:
            trace.stats.station = args.station
        if args.location is not None:
            trace.stats.location = args.location
        if args.channel is not None:
            trace.stats.channel = args.channel

        missing = [
            name
            for name in ("network", "station", "channel")
            if not getattr(trace.stats, name, "")
        ]
        if missing:
            raise RuntimeError(f"{source}: missing required metadata after overrides: {', '.join(missing)}")

        if trace.data.dtype != np.int32:
            trace.data = trace.data.astype(np.int32, copy=False)


def day_start(time_: UTCDateTime) -> UTCDateTime:
    return UTCDateTime(time_.year, time_.month, time_.day, precision=9)


def daily_parts(trace: Trace):
    current_day = day_start(trace.stats.starttime)
    end_time = trace.stats.endtime

    while current_day <= end_time:
        next_day = current_day + 86400
        part = trace.slice(
            max(trace.stats.starttime, current_day),
            min(end_time, next_day - 0.000000001),
            nearest_sample=False,
        ).copy()
        if part.stats.npts:
            yield current_day, part
        current_day = next_day


def sds_path(root: Path, trace: Trace, day: UTCDateTime) -> Path:
    relative = SDS_FMTSTR.format(
        year=day.year,
        network=trace.stats.network,
        station=trace.stats.station,
        location=trace.stats.location,
        channel=trace.stats.channel,
        sds_type="D",
        doy=day.julday,
    )
    return root / relative


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    input_root = args.input_root.resolve()
    output_root = args.output_root.resolve()

    if not input_root.is_dir():
        raise RuntimeError(f"input-root does not exist: {input_root}")
    if input_root == output_root:
        raise RuntimeError("input-root and output-root must be different")
    if args.report and output_root in args.report.resolve().parents:
        raise RuntimeError("--report must be outside output-root")
    if args.no_correct_sid and not args.network:
        raise RuntimeError("--network is required when --no-correct-sid is used")

    ensure_empty_output(output_root)
    files = iter_input_files(input_root, args.pattern, args.recursive)
    if not files:
        raise RuntimeError(f"no input files matched {args.pattern!r} under {input_root}")

    print(f"Python {sys.version.split()[0]} | ObsPy {obspy.__version__} | NumPy {np.__version__}")
    corrections = None if args.no_correct_sid else load_correct_sid(args.correct_sid.resolve())
    if corrections is not None:
        print(f"Using CorrectSID: {args.correct_sid.resolve()} ({len(corrections)} entries)")
    print(f"Checking ObsPy Y reader with: {files[0]}")
    read(str(files[0]), format="Y")

    stream = Stream()
    for index, path in enumerate(files, start=1):
        current = read(str(path), format="Y")
        apply_metadata(current, args, path, corrections)
        stream += current
        print(f"[{index}/{len(files)}] read {path}")

    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
    stream.merge(method=-1)
    stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])

    destinations: dict[Path, list[Trace]] = defaultdict(list)
    for trace in stream:
        for day, part in daily_parts(trace):
            destinations[sds_path(output_root, part, day)].append(part)

    total_samples = 0
    for index, path in enumerate(sorted(destinations), start=1):
        daily_stream = Stream(destinations[path])
        daily_stream.sort(keys=["network", "station", "location", "channel", "starttime", "endtime"])
        path.parent.mkdir(parents=True, exist_ok=True)
        daily_stream.write(
            str(path),
            format="MSEED",
            encoding=args.encoding,
            reclen=args.record_length,
            flush=True,
        )
        samples = sum(int(trace.stats.npts) for trace in daily_stream)
        total_samples += samples
        print(f"[{index}/{len(destinations)}] wrote {path} traces={len(daily_stream)} samples={samples}")

    report = {
        "input_root": str(input_root),
        "output_root": str(output_root),
        "obspy": obspy.__version__,
        "numpy": np.__version__,
        "files_read": len(files),
        "sds_files_written": len(destinations),
        "samples_written": total_samples,
        "encoding": args.encoding,
        "record_length": args.record_length,
        "correct_sid": str(args.correct_sid.resolve()) if corrections is not None else None,
        "elapsed_seconds": time.perf_counter() - started,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("Completed successfully")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
