#!/usr/bin/env python3
"""
Clean an SDS MiniSEED tree in place with ObsPy.

The tool assumes a standard SDS layout and builds jobs from paths, without a
separate header scan:

    ROOT/YEAR/NET/STA/CHAN.TYPE/NET.STA.LOC.CHAN.TYPE.YEAR.DOY[.mseed]

Each job is one network/station/day. All waveform channel files for that
station-day are read, validated against their SDS path, merged with
``Stream.merge(method=-1)``, and written back to the same files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from obspy import Stream, read
except Exception as exc:  # pragma: no cover - operator diagnostic
    print(f"ObsPy import failed: {exc}", file=sys.stderr)
    sys.exit(2)


@dataclass(frozen=True)
class SdsFileInfo:
    path: Path
    network: str
    station: str
    location: str
    channel: str
    data_type: str
    year: int
    day_of_year: int

    @property
    def trace_id(self) -> str:
        return f"{self.network}.{self.station}.{self.location}.{self.channel}"

    @property
    def station_day_key(self) -> tuple[str, str, int, int]:
        return (self.network, self.station, self.year, self.day_of_year)


@dataclass
class JobResult:
    network: str
    station: str
    year: int
    day_of_year: int
    files: int
    traces_before: int = 0
    traces_after: int = 0
    traces_written: int = 0
    changed_files: int = 0
    status: str = "OK"
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ObsPy merge(method=-1) cleanup on an SDS tree in place."
    )
    parser.add_argument("--sds-root", required=True, type=Path, help="SDS root folder to update in place.")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--network", action="append", help="Optional network filter. Repeatable.")
    parser.add_argument("--station", action="append", help="Optional station filter. Repeatable.")
    parser.add_argument("--year", action="append", type=int, help="Optional year filter. Repeatable.")
    parser.add_argument("--doy", action="append", type=int, help="Optional day-of-year filter. Repeatable.")
    parser.add_argument("--dry-run", action="store_true", help="Read and merge, but do not rewrite files.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed job.")
    parser.add_argument(
        "--backup-suffix",
        default="",
        help="Optional backup suffix, e.g. .bak. Empty means no backup.",
    )
    parser.add_argument(
        "--encoding",
        default=None,
        help="Optional ObsPy MiniSEED encoding for rewritten files, e.g. STEIM2.",
    )
    parser.add_argument(
        "--reclen",
        type=int,
        default=None,
        help="Optional MiniSEED record length for rewritten files, e.g. 4096.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional report folder. Writes cleanup_summary.csv and cleanup_summary.json.",
    )
    return parser.parse_args()


def parse_sds_path(file_path: Path, sds_root: Path) -> SdsFileInfo:
    relative = file_path.relative_to(sds_root)
    if len(relative.parts) != 5:
        raise ValueError(f"Invalid SDS path depth: {file_path}")

    year_dir, network_dir, station_dir, channel_type_dir, filename = relative.parts
    fields = filename.split(".")
    if len(fields) == 8 and fields[-1].lower() == "mseed":
        fields = fields[:-1]
    if len(fields) != 7:
        raise ValueError(f"Invalid SDS filename: {filename}")

    network, station, location, channel, data_type, year_text, day_text = fields
    expected_channel_dir = f"{channel}.{data_type}"

    if year_dir != year_text:
        raise ValueError(f"Year mismatch in SDS path: {file_path}")
    if network_dir != network:
        raise ValueError(f"Network mismatch in SDS path: {file_path}")
    if station_dir != station:
        raise ValueError(f"Station mismatch in SDS path: {file_path}")
    if channel_type_dir != expected_channel_dir:
        raise ValueError(f"Channel/type mismatch in SDS path: {file_path}")

    year = int(year_text)
    day_of_year = int(day_text)
    if not 1 <= day_of_year <= 366:
        raise ValueError(f"Invalid day of year in: {file_path}")

    return SdsFileInfo(
        path=file_path,
        network=network,
        station=station,
        location=location,
        channel=channel,
        data_type=data_type,
        year=year,
        day_of_year=day_of_year,
    )


def allowed(value: str | int, selected: set[str] | set[int]) -> bool:
    return not selected or value in selected


def build_station_day_jobs(args: argparse.Namespace) -> list[tuple[tuple[str, str, int, int], tuple[SdsFileInfo, ...]]]:
    networks = {item.upper() for item in args.network or []}
    stations = {item.upper() for item in args.station or []}
    years = set(args.year or [])
    days = set(args.doy or [])

    jobs: dict[tuple[str, str, int, int], list[SdsFileInfo]] = defaultdict(list)
    for path in args.sds_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            info = parse_sds_path(path, args.sds_root)
        except ValueError:
            continue
        if info.data_type != "D":
            continue
        if not allowed(info.network.upper(), networks):
            continue
        if not allowed(info.station.upper(), stations):
            continue
        if not allowed(info.year, years):
            continue
        if not allowed(info.day_of_year, days):
            continue
        jobs[info.station_day_key].append(info)

    ordered_jobs = []
    for key, files in jobs.items():
        files.sort(key=lambda item: (item.network, item.station, item.location, item.channel, str(item.path)))
        ordered_jobs.append((key, tuple(files)))
    return sorted(ordered_jobs, key=lambda item: item[0])


def validate_loaded_stream(stream: Stream, info: SdsFileInfo) -> None:
    for trace in stream:
        if trace.id != info.trace_id:
            raise ValueError(
                "SDS content mismatch: "
                f"file={info.path}, expected={info.trace_id}, actual={trace.id}"
            )


def write_stream_in_place(path: Path, stream: Stream, args_dict: dict) -> bool:
    if args_dict["dry_run"]:
        return False

    write_kwargs = {"format": "MSEED"}
    if args_dict.get("encoding"):
        write_kwargs["encoding"] = args_dict["encoding"]
    if args_dict.get("reclen"):
        write_kwargs["reclen"] = args_dict["reclen"]

    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        stream.write(str(temp_path), **write_kwargs)
        if args_dict.get("backup_suffix"):
            backup_path = path.with_name(path.name + args_dict["backup_suffix"])
            shutil.copy2(path, backup_path)
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return True


def process_station_day(job: tuple[tuple[str, str, int, int], tuple[SdsFileInfo, ...]], args_dict: dict) -> JobResult:
    key, files = job
    network, station, year, day_of_year = key
    result = JobResult(network, station, year, day_of_year, files=len(files))

    try:
        stream = Stream()
        for info in files:
            part = read(str(info.path), format="MSEED", check_compression=False)
            validate_loaded_stream(part, info)
            stream += part

        result.traces_before = len(stream)
        stream.sort(keys=["network", "station", "location", "channel", "starttime"])
        stream.merge(method=-1)
        result.traces_after = len(stream)

        by_id: dict[str, Stream] = defaultdict(Stream)
        for trace in stream:
            by_id[trace.id] += trace

        by_path = {info.trace_id: info.path for info in files}
        for trace_id, output_stream in by_id.items():
            output_path = by_path.get(trace_id)
            if output_path is None:
                raise ValueError(f"Merged stream produced unexpected trace id: {trace_id}")
            output_stream.sort(keys=["starttime"])
            result.traces_written += len(output_stream)
            if write_stream_in_place(output_path, output_stream, args_dict):
                result.changed_files += 1

        return result
    except Exception as exc:
        result.status = "ERROR"
        result.error = repr(exc)
        return result


def run_jobs(jobs: list[tuple[tuple[str, str, int, int], tuple[SdsFileInfo, ...]]], args: argparse.Namespace) -> list[JobResult]:
    args_dict = {
        "dry_run": args.dry_run,
        "backup_suffix": args.backup_suffix,
        "encoding": args.encoding,
        "reclen": args.reclen,
    }
    if args.workers <= 1:
        results = []
        for job in jobs:
            result = process_station_day(job, args_dict)
            results.append(result)
            print_result(result)
            if args.fail_fast and result.status != "OK":
                break
        return results

    results: list[JobResult] = []
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        future_to_key = {executor.submit(process_station_day, job, args_dict): job[0] for job in jobs}
        for future in as_completed(future_to_key):
            result = future.result()
            results.append(result)
            print_result(result)
            if args.fail_fast and result.status != "OK":
                for pending in future_to_key:
                    pending.cancel()
                break
    return sorted(results, key=lambda item: (item.network, item.station, item.year, item.day_of_year))


def print_result(result: JobResult) -> None:
    print(
        f"{result.status} {result.network}.{result.station} "
        f"{result.year}.{result.day_of_year:03d} "
        f"files={result.files} traces={result.traces_before}->{result.traces_after} "
        f"written={result.changed_files}"
    )
    if result.error:
        print(f"  {result.error}", file=sys.stderr)


def write_report(results: list[JobResult], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    rows = [asdict(item) for item in results]
    with (report_dir / "cleanup_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(rows[0].keys()) if rows else list(JobResult("", "", 0, 0, 0).__dict__.keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    (report_dir / "cleanup_summary.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    args.sds_root = args.sds_root.resolve()
    if not args.sds_root.exists():
        print(f"SDS root not found: {args.sds_root}", file=sys.stderr)
        return 2
    if not args.sds_root.is_dir():
        print(f"SDS root is not a folder: {args.sds_root}", file=sys.stderr)
        return 2

    jobs = build_station_day_jobs(args)
    print(f"SDS root: {args.sds_root}")
    print(f"Jobs: {len(jobs)}")
    print(f"Workers: {args.workers}")
    if args.dry_run:
        print("Mode: dry-run")
    if not jobs:
        return 0

    results = run_jobs(jobs, args)
    if args.report:
        write_report(results, args.report)
        print(f"Report: {args.report}")

    errors = sum(1 for item in results if item.status != "OK")
    print(f"Completed jobs: {len(results)}")
    print(f"Errors: {errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
