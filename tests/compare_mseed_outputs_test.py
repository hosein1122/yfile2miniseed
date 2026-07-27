#!/usr/bin/env python3

from pathlib import Path
import subprocess
import sys
import tempfile

try:
    import numpy as np
    from obspy import Stream, Trace, UTCDateTime
except Exception as exc:
    print(f"SKIP: ObsPy test dependencies are not available: {exc}")
    sys.exit(77)


def write_trace(path: Path, data, channel="BHZ"):
    trace = Trace(data=np.asarray(data, dtype=np.int32))
    trace.stats.network = "IR"
    trace.stats.station = "TST"
    trace.stats.location = ""
    trace.stats.channel = channel
    trace.stats.starttime = UTCDateTime(2020, 1, 1)
    trace.stats.sampling_rate = 50.0
    Stream([trace]).write(str(path), format="MSEED", encoding="STEIM2", reclen=4096)


def run_compare(script: Path, reference: Path, candidate: Path, report: Path, level: str):
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--reference",
            str(reference),
            "--candidate",
            str(candidate),
            "--report",
            str(report),
            "--level",
            level,
            "--fail-on-difference",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout)
        raise AssertionError(f"compare tool failed at level {level}")


def main():
    if len(sys.argv) != 2:
        print("usage: compare_mseed_outputs_test.py <compare_mseed_outputs.py>")
        return 2

    script = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="compare_mseed_outputs_") as tmp:
        root = Path(tmp)
        reference = root / "reference"
        candidate = root / "candidate"
        reference.mkdir()
        candidate.mkdir()

        data = np.arange(1000, dtype=np.int32) - 500
        write_trace(reference / "IR.TST..BHZ.D.2020.001", data)
        write_trace(candidate / "IR.TST..BHZ.D.2020.001", data)

        run_compare(script, reference, candidate, root / "report_simple", "simple")
        run_compare(script, reference, candidate, root / "report_medium", "medium")
        run_compare(script, reference, candidate, root / "report_deep", "deep")

    print("compare_mseed_outputs self-test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
