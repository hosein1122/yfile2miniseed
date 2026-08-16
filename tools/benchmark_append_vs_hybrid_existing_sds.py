#!/usr/bin/env python3
"""Benchmark C++ append SDS vs hybrid yfile2obspy_cpp + ObsPy SDS updates."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated SDS updates and compare availability."
    )
    parser.add_argument("--input-root", required=True, type=Path)
    parser.add_argument("--result-root", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--append-workers", type=int, default=4)
    parser.add_argument("--pack-workers", type=int, default=4)
    parser.add_argument("--encoding", default="STEIM2", choices=("STEIM1", "STEIM2", "INT32"))
    parser.add_argument("--record-length", type=int, default=4096)
    parser.add_argument("--append-exe", type=Path, default=SCRIPT_DIR / "yfile2mseed_append.exe")
    parser.add_argument("--hybrid-script", type=Path, default=SCRIPT_DIR / "yfiles_to_mseed_sds_hybrid_cppread.py")
    parser.add_argument("--correct-sid", type=Path, default=SCRIPT_DIR / "CorrectSID.txt")
    parser.add_argument("--fresh-start", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    args = parser.parse_args()

    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    return args


def run_command(command: list[str], cwd: Path, log_path: Path) -> tuple[float, int]:
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        check=False,
    )
    elapsed = time.perf_counter() - started
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(result.stdout, encoding="utf-8")
    return elapsed, result.returncode


def require_path(path: Path, label: str) -> None:
    if not path.exists():
        raise RuntimeError(f"{label} not found: {path}")


def average(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def fmt_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.6f}"


def write_summary(
    summary_path: Path,
    rows: list[dict],
    append_root: Path,
    hybrid_root: Path,
    availability_compare: Path,
) -> None:
    append_all = [float(row["append_seconds"]) for row in rows]
    hybrid_all = [float(row["hybrid_seconds"]) for row in rows]
    append_existing = [float(row["append_seconds"]) for row in rows if int(row["iteration"]) > 1]
    hybrid_existing = [float(row["hybrid_seconds"]) for row in rows if int(row["iteration"]) > 1]

    lines = [
        "Append vs Hybrid Existing-SDS Benchmark",
        "",
        f"Iterations: {len(rows)}",
        f"Append SDS: {append_root}",
        f"Hybrid SDS: {hybrid_root}",
        "",
        "Wall-clock seconds:",
        f"  append first run          : {fmt_seconds(append_all[0] if append_all else None)}",
        f"  hybrid first run          : {fmt_seconds(hybrid_all[0] if hybrid_all else None)}",
        f"  append average all runs   : {fmt_seconds(average(append_all))}",
        f"  hybrid average all runs   : {fmt_seconds(average(hybrid_all))}",
        f"  append average existing   : {fmt_seconds(average(append_existing))}",
        f"  hybrid average existing   : {fmt_seconds(average(hybrid_existing))}",
        "",
        f"Availability comparison: {availability_compare}",
    ]
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_root = args.input_root.resolve()
    result_root = args.result_root.resolve()
    append_root = result_root / "append_cpp"
    hybrid_root = result_root / "hybrid_cppread_obspy"
    logs_root = result_root / "logs"
    availability_root = result_root / "Availability"
    raw_root = result_root / "RawSegments"

    require_path(input_root, "input-root")
    require_path(args.append_exe, "append executable")
    require_path(args.hybrid_script, "hybrid script")
    require_path(args.correct_sid, "CorrectSID")

    if args.fresh_start and result_root.exists():
        shutil.rmtree(result_root)

    result_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for iteration in range(1, args.iterations + 1):
        append_command = [
            str(args.append_exe),
            str(input_root),
            "-o",
            str(append_root),
            "-V2",
            "--workers",
            str(args.append_workers),
        ]
        hybrid_report = logs_root / f"hybrid_iter_{iteration:02d}.json"
        hybrid_command = [
            sys.executable,
            str(args.hybrid_script),
            "--input-root",
            str(input_root),
            "--output-root",
            str(hybrid_root),
            "--correct-sid",
            str(args.correct_sid),
            "--encoding",
            args.encoding,
            "--record-length",
            str(args.record_length),
            "--pack-workers",
            str(args.pack_workers),
            "--quiet",
            "--report",
            str(hybrid_report),
        ]

        print(f"[{iteration}/{args.iterations}] Running append C++...")
        append_seconds, append_rc = run_command(
            append_command,
            SCRIPT_DIR,
            logs_root / f"append_iter_{iteration:02d}.log",
        )
        print(f"    append: {append_seconds:.3f}s rc={append_rc}")
        if append_rc != 0:
            raise RuntimeError(f"append failed at iteration {iteration}")

        print(f"[{iteration}/{args.iterations}] Running hybrid...")
        hybrid_seconds, hybrid_rc = run_command(
            hybrid_command,
            SCRIPT_DIR,
            logs_root / f"hybrid_iter_{iteration:02d}.log",
        )
        print(f"    hybrid: {hybrid_seconds:.3f}s rc={hybrid_rc}")
        if hybrid_rc != 0:
            raise RuntimeError(f"hybrid failed at iteration {iteration}")

        rows.append(
            {
                "iteration": iteration,
                "append_seconds": f"{append_seconds:.9f}",
                "hybrid_seconds": f"{hybrid_seconds:.9f}",
                "append_returncode": append_rc,
                "hybrid_returncode": hybrid_rc,
                "existing_sds": iteration > 1,
            }
        )

    timings_csv = result_root / "timings.csv"
    with timings_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    if not args.skip_cleanup:
        for label, root in (("append", append_root), ("hybrid", hybrid_root)):
            print(f"Cleaning {label} SDS with ObsPy merge(method=-1)...")
            seconds, rc = run_command(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "sds_obspy_cleanup_inplace.py"),
                    "--sds-root",
                    str(root),
                ],
                SCRIPT_DIR,
                logs_root / f"cleanup_{label}.log",
            )
            print(f"    cleanup {label}: {seconds:.3f}s rc={rc}")
            if rc != 0:
                raise RuntimeError(f"cleanup failed for {label}")

    print("Creating availability reports...")
    for label, root in (("append", append_root), ("hybrid", hybrid_root)):
        seconds, rc = run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "sds_availability_report.py"),
                "--input",
                str(root),
                "--output",
                str(availability_root / label),
            ],
            SCRIPT_DIR,
            logs_root / f"availability_{label}.log",
        )
        print(f"    availability {label}: {seconds:.3f}s rc={rc}")
        if rc != 0:
            raise RuntimeError(f"availability failed for {label}")

    print("Creating raw segment reports...")
    raw_root.mkdir(parents=True, exist_ok=True)
    for label, root in (("append", append_root), ("hybrid", hybrid_root)):
        seconds, rc = run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "sds_raw_segment_report.py"),
                "--input",
                str(root),
                "--output",
                str(raw_root / f"{label}_raw_segments.txt"),
            ],
            SCRIPT_DIR,
            logs_root / f"raw_segments_{label}.log",
        )
        print(f"    raw {label}: {seconds:.3f}s rc={rc}")
        if rc != 0:
            raise RuntimeError(f"raw segment report failed for {label}")

    availability_compare = availability_root / "hybrid-vs-append.txt"
    seconds, rc = run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "compare_availability_lines.py"),
            "--center",
            str(availability_root / "hybrid" / "availability.txt"),
            "--ours",
            str(availability_root / "append" / "availability.txt"),
            "--center-label",
            "hybrid",
            "--ours-label",
            "append",
            "--output",
            str(availability_compare),
        ],
        SCRIPT_DIR,
        logs_root / "compare_availability_hybrid_vs_append.log",
    )
    print(f"Availability comparison: {seconds:.3f}s rc={rc}")

    write_summary(
        result_root / "benchmark_summary.txt",
        rows,
        append_root,
        hybrid_root,
        availability_compare,
    )

    metadata = {
        "input_root": str(input_root),
        "result_root": str(result_root),
        "iterations": args.iterations,
        "append_workers": args.append_workers,
        "pack_workers": args.pack_workers,
        "timings_csv": str(timings_csv),
        "summary": str(result_root / "benchmark_summary.txt"),
        "availability_compare": str(availability_compare),
    }
    (result_root / "benchmark_metadata.json").write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8",
    )

    print()
    print((result_root / "benchmark_summary.txt").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

