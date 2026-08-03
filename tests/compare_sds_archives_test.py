#!/usr/bin/env python3

from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

try:
    import numpy as np
    from obspy import Stream, Trace, UTCDateTime
except Exception as exc:
    print(f"SKIP: ObsPy test dependencies are not available: {exc}")
    sys.exit(77)


def strict_path(root: Path) -> Path:
    return root / "2020" / "IR" / "TST" / "BHZ.D" / "IR.TST..BHZ.D.2020.001"


def make_trace(data, start, sampling_rate=50.0):
    trace = Trace(data=np.asarray(data, dtype=np.int32))
    trace.stats.network = "IR"
    trace.stats.station = "TST"
    trace.stats.location = ""
    trace.stats.channel = "BHZ"
    trace.stats.starttime = start
    trace.stats.sampling_rate = sampling_rate
    return trace


def write_sds(root: Path, traces) -> None:
    path = strict_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    Stream(list(traces)).write(str(path), format="MSEED", encoding="STEIM1", reclen=4096)


def run_compare(script: Path, reference: Path, candidate: Path, report: Path, expect_ok: bool) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--reference-sds",
            str(reference),
            "--cpp-sds",
            str(candidate),
            "--report",
            str(report),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if expect_ok and result.returncode != 0:
        print(result.stdout)
        raise AssertionError("compare_sds_archives.py reported an unexpected difference")
    if not expect_ok and result.returncode == 0:
        print(result.stdout)
        raise AssertionError("compare_sds_archives.py did not reject the mismatch")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: compare_sds_archives_test.py <compare_sds_archives.py>")
        return 2

    script = Path(sys.argv[1]).resolve()
    start = UTCDateTime(2020, 1, 1)
    data = np.arange(1000, dtype=np.int32)

    with tempfile.TemporaryDirectory(prefix="compare_sds_archives_") as tmp:
        work = Path(tmp)
        reference = work / "reference"
        candidate = work / "candidate"
        report = work / "report"

        write_sds(reference, [make_trace(data, start)])
        first = make_trace(data[:500], start)
        second = make_trace(data[500:], start + 500 / 50.0)
        write_sds(candidate, [first, second])
        run_compare(script, reference, candidate, report, expect_ok=True)

        bad_candidate = work / "bad_candidate"
        shutil.copytree(candidate, bad_candidate)
        bad_file = strict_path(bad_candidate)
        bad_file.rename(bad_file.with_name(bad_file.name + ".mseed"))
        run_compare(script, reference, bad_candidate, work / "bad_report", expect_ok=False)

    print("compare_sds_archives self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
