#!/usr/bin/env python3
"""Run fresh and existing-SDS raw comparisons for selected Y-file folders."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = [
    "Kaz_Test2",
    "PAR_Test1",
    "Kaz_Test3",
    "TwoYOverlapCase",
    "Kaz_Test1",
]


@dataclass
class RunResult:
    seconds: float
    returncode: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare append, ObsPy, and hybrid raw SDS outputs for multiple input folders."
    )
    parser.add_argument("--data-root", type=Path, default=Path(r"D:\MSeed_Test\Data"))
    parser.add_argument(
        "--result-root",
        type=Path,
        default=Path(r"D:\MSeed_Test\Result\raw_sds_matrix_5cases"),
    )
    parser.add_argument("--case", action="append", help="Input folder name. Repeatable.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--append-workers", type=int, default=4)
    parser.add_argument("--pack-workers", type=int, default=4)
    parser.add_argument("--append-exe", type=Path, default=SCRIPT_DIR / "yfile2mseed_append.exe")
    parser.add_argument("--obspy-script", type=Path, default=SCRIPT_DIR / "yfiles_to_mseed_sds_obspy.py")
    parser.add_argument("--hybrid-script", type=Path, default=SCRIPT_DIR / "yfiles_to_mseed_sds_hybrid_cppread.py")
    parser.add_argument("--correct-sid", type=Path, default=SCRIPT_DIR / "CorrectSID.txt")
    parser.add_argument("--keep-result-root", action="store_true")
    return parser.parse_args()


def run_command(command: list[str], cwd: Path, log_path: Path) -> RunResult:
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
    return RunResult(elapsed, result.returncode)


def require_path(path: Path, label: str) -> None:
    if not path.exists():
        raise RuntimeError(f"{label} not found: {path}")


def selected_cases(args: argparse.Namespace) -> list[str]:
    if args.case:
        return args.case[: args.limit]

    cases = [case for case in DEFAULT_CASES if (args.data_root / case).is_dir()]
    if len(cases) >= args.limit:
        return cases[: args.limit]

    for path in sorted(args.data_root.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_dir() or path.name == "shi201001.month" or path.name in cases:
            continue
        cases.append(path.name)
        if len(cases) >= args.limit:
            break
    return cases


def sds_bytes(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


RAW_LINE_RE = re.compile(
    r"^\s+FDSN:\S+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+"
    r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+[-\d]+\s+(\d+)\s*$"
)


def raw_stats(report_path: Path) -> tuple[int, int]:
    if not report_path.exists():
        return 0, 0
    segments = 0
    samples = 0
    for line in report_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = RAW_LINE_RE.match(line)
        if not match:
            continue
        segments += 1
        samples += int(match.group(1))
    return segments, samples


def read_difference_count(report_path: Path) -> int | None:
    if not report_path.exists():
        return None
    for line in report_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip() == "No differences found.":
            return 0
        if line.startswith("Different entries:"):
            return int(line.split(":", 1)[1].strip())
    return None


def build_append(args: argparse.Namespace, input_root: Path, output_root: Path, log_path: Path) -> RunResult:
    return run_command(
        [
            str(args.append_exe),
            str(input_root),
            "-o",
            str(output_root),
            "-V2",
            "--workers",
            str(args.append_workers),
        ],
        SCRIPT_DIR,
        log_path,
    )


def build_obspy(args: argparse.Namespace, input_root: Path, output_root: Path, log_path: Path) -> RunResult:
    return run_command(
        [
            sys.executable,
            str(args.obspy_script),
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--recursive",
        ],
        SCRIPT_DIR,
        log_path,
    )


def build_hybrid(args: argparse.Namespace, input_root: Path, output_root: Path, log_path: Path, report_path: Path) -> RunResult:
    return run_command(
        [
            sys.executable,
            str(args.hybrid_script),
            "--input-root",
            str(input_root),
            "--output-root",
            str(output_root),
            "--correct-sid",
            str(args.correct_sid),
            "--pack-workers",
            str(args.pack_workers),
            "--quiet",
            "--report",
            str(report_path),
        ],
        SCRIPT_DIR,
        log_path,
    )


def make_raw_report(root: Path, output: Path, log_path: Path) -> RunResult:
    return run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "sds_raw_segment_report.py"),
            "--input",
            str(root),
            "--output",
            str(output),
            "--no-split-on-sequence-reset",
        ],
        SCRIPT_DIR,
        log_path,
    )


def make_availability(root: Path, output: Path, log_path: Path) -> RunResult:
    return run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "sds_availability_report.py"),
            "--input",
            str(root),
            "--output",
            str(output),
        ],
        SCRIPT_DIR,
        log_path,
    )


def compare_availability(center: Path, ours: Path, output: Path, log_path: Path) -> RunResult:
    return run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "compare_availability_lines.py"),
            "--center",
            str(center),
            "--ours",
            str(ours),
            "--output",
            str(output),
        ],
        SCRIPT_DIR,
        log_path,
    )


def compare_gaps(a: Path, b: Path, output: Path, a_label: str, b_label: str, log_path: Path) -> RunResult:
    return run_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "compare_sds_gaps.py"),
            "--sds-a",
            str(a),
            "--sds-b",
            str(b),
            "--report",
            str(output),
            "--a-label",
            a_label,
            "--b-label",
            b_label,
            "--allow-differences",
        ],
        SCRIPT_DIR,
        log_path,
    )


def stage_paths(case_root: Path, stage: str) -> dict[str, Path]:
    root = case_root / stage
    return {
        "root": root,
        "append": root / "append_cpp",
        "obspy": root / "obspy",
        "hybrid": root / "hybrid_cppread_obspy",
        "raw": root / "RawSegments",
        "availability": root / "Availability",
        "compare": root / "Compare",
        "logs": root / "logs",
    }


def summarize_stage(case: str, stage: str, paths: dict[str, Path], timings: dict[str, RunResult]) -> dict:
    raw_reports = {
        "append": paths["raw"] / "append_raw_segments.txt",
        "obspy": paths["raw"] / "obspy_raw_segments.txt",
        "hybrid": paths["raw"] / "hybrid_raw_segments.txt",
    }
    stats = {}
    for label, report in raw_reports.items():
        segments, samples = raw_stats(report)
        stats[label] = {
            "bytes": sds_bytes(paths[label]),
            "raw_segments": segments,
            "raw_samples": samples,
        }

    compare_root = paths["compare"]
    return {
        "case": case,
        "stage": stage,
        "append_seconds": f"{timings['append'].seconds:.6f}",
        "obspy_seconds": "" if "obspy" not in timings else f"{timings['obspy'].seconds:.6f}",
        "hybrid_seconds": f"{timings['hybrid'].seconds:.6f}",
        "append_bytes": stats["append"]["bytes"],
        "obspy_bytes": stats["obspy"]["bytes"],
        "hybrid_bytes": stats["hybrid"]["bytes"],
        "append_raw_segments": stats["append"]["raw_segments"],
        "obspy_raw_segments": stats["obspy"]["raw_segments"],
        "hybrid_raw_segments": stats["hybrid"]["raw_segments"],
        "append_raw_samples": stats["append"]["raw_samples"],
        "obspy_raw_samples": stats["obspy"]["raw_samples"],
        "hybrid_raw_samples": stats["hybrid"]["raw_samples"],
        "diff_obspy_append": read_difference_count(compare_root / "obspy-vs-append.txt"),
        "diff_obspy_hybrid": read_difference_count(compare_root / "obspy-vs-hybrid.txt"),
        "diff_append_hybrid": read_difference_count(compare_root / "append-vs-hybrid.txt"),
    }


def run_stage(
    args: argparse.Namespace,
    case: str,
    input_root: Path,
    paths: dict[str, Path],
    stage: str,
    run_obspy: bool,
) -> dict:
    for key in ("raw", "availability", "compare", "logs"):
        paths[key].mkdir(parents=True, exist_ok=True)

    timings: dict[str, RunResult] = {}
    timings["append"] = build_append(
        args,
        input_root,
        paths["append"],
        paths["logs"] / "append.log",
    )
    if timings["append"].returncode != 0:
        raise RuntimeError(f"{case} {stage}: append failed")

    if run_obspy:
        timings["obspy"] = build_obspy(
            args,
            input_root,
            paths["obspy"],
            paths["logs"] / "obspy.log",
        )
        if timings["obspy"].returncode != 0:
            raise RuntimeError(f"{case} {stage}: obspy failed")

    timings["hybrid"] = build_hybrid(
        args,
        input_root,
        paths["hybrid"],
        paths["logs"] / "hybrid.log",
        paths["logs"] / "hybrid_report.json",
    )
    if timings["hybrid"].returncode != 0:
        raise RuntimeError(f"{case} {stage}: hybrid failed")

    roots_for_reports = {
        "append": paths["append"],
        "hybrid": paths["hybrid"],
    }
    if run_obspy:
        roots_for_reports["obspy"] = paths["obspy"]
    else:
        roots_for_reports["obspy"] = stage_paths(paths["root"].parent, "fresh")["obspy"]

    for label, root in roots_for_reports.items():
        make_raw_report(
            root,
            paths["raw"] / f"{label}_raw_segments.txt",
            paths["logs"] / f"raw_{label}.log",
        )
        make_availability(
            root,
            paths["availability"] / label,
            paths["logs"] / f"availability_{label}.log",
        )

    availability_files = {
        "append": paths["availability"] / "append" / "availability.txt",
        "obspy": paths["availability"] / "obspy" / "availability.txt",
        "hybrid": paths["availability"] / "hybrid" / "availability.txt",
    }
    compare_availability(
        availability_files["obspy"],
        availability_files["append"],
        paths["compare"] / "obspy-vs-append.txt",
        paths["logs"] / "compare_obspy_append.log",
    )
    compare_availability(
        availability_files["obspy"],
        availability_files["hybrid"],
        paths["compare"] / "obspy-vs-hybrid.txt",
        paths["logs"] / "compare_obspy_hybrid.log",
    )
    compare_availability(
        availability_files["append"],
        availability_files["hybrid"],
        paths["compare"] / "append-vs-hybrid.txt",
        paths["logs"] / "compare_append_hybrid.log",
    )

    compare_gaps(
        roots_for_reports["obspy"],
        paths["append"],
        paths["compare"] / "gaps_obspy_append",
        "obspy",
        "append",
        paths["logs"] / "gaps_obspy_append.log",
    )
    compare_gaps(
        roots_for_reports["obspy"],
        paths["hybrid"],
        paths["compare"] / "gaps_obspy_hybrid",
        "obspy",
        "hybrid",
        paths["logs"] / "gaps_obspy_hybrid.log",
    )

    return summarize_stage(case, stage, paths, timings)


def write_summary(rows: list[dict], result_root: Path) -> None:
    csv_path = result_root / "summary.csv"
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    lines = ["Raw SDS Matrix Summary", ""]
    for row in rows:
        lines.append(
            "{case} [{stage}] append={append_raw_segments} seg/{append_bytes} B, "
            "obspy={obspy_raw_segments} seg/{obspy_bytes} B, "
            "hybrid={hybrid_raw_segments} seg/{hybrid_bytes} B, "
            "diff(obspy,append)={diff_obspy_append}, "
            "diff(obspy,hybrid)={diff_obspy_hybrid}".format(**row)
        )
    (result_root / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (result_root / "summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


def main() -> int:
    args = parse_args()
    require_path(args.data_root, "data-root")
    require_path(args.append_exe, "append executable")
    require_path(args.obspy_script, "ObsPy script")
    require_path(args.hybrid_script, "hybrid script")
    require_path(args.correct_sid, "CorrectSID")

    cases = selected_cases(args)
    if not cases:
        raise RuntimeError("no input cases selected")

    if args.result_root.exists() and not args.keep_result_root:
        shutil.rmtree(args.result_root)
    args.result_root.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for index, case in enumerate(cases, start=1):
        input_root = args.data_root / case
        print(f"[{index}/{len(cases)}] Case: {case}")
        case_root = args.result_root / case

        fresh_paths = stage_paths(case_root, "fresh")
        existing_paths = stage_paths(case_root, "existing")
        if existing_paths["root"].exists():
            shutil.rmtree(existing_paths["root"])
        shutil.copytree(fresh_paths["root"], existing_paths["root"], dirs_exist_ok=True) if fresh_paths["root"].exists() else None

        print("  fresh run...")
        rows.append(run_stage(args, case, input_root, fresh_paths, "fresh", run_obspy=True))

        if existing_paths["root"].exists():
            shutil.rmtree(existing_paths["root"])
        shutil.copytree(fresh_paths["root"], existing_paths["root"])

        print("  existing-SDS rerun...")
        rows.append(run_stage(args, case, input_root, existing_paths, "existing", run_obspy=False))

    write_summary(rows, args.result_root)
    print()
    print((args.result_root / "summary.txt").read_text(encoding="utf-8"))
    print(f"Summary CSV: {args.result_root / 'summary.csv'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
