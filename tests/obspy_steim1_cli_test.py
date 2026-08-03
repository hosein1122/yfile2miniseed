from pathlib import Path
import subprocess
import sys
import tempfile

try:
    import numpy as np
    from obspy import Stream, Trace, UTCDateTime, read
except Exception as exc:
    print(f"SKIP: ObsPy test dependencies are not available: {exc}")
    sys.exit(77)


def write_steim1(path, data, starttime, sampling_rate):
    trace = Trace(data=np.asarray(data, dtype=np.int32))
    trace.stats.network = "IR"
    trace.stats.station = "TST"
    trace.stats.location = ""
    trace.stats.channel = "BHZ"
    trace.stats.starttime = starttime
    trace.stats.sampling_rate = sampling_rate
    Stream([trace]).write(str(path), format="MSEED", encoding="STEIM1", reclen=4096)


def run_cli(cli, input_path, output_dir):
    (output_dir.parent / "CorrectSID.txt").write_text(
        "IR_TST__BHZ => IR_TST__BHZ\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(cli), str(input_path), "-o", str(output_dir), "-V2"],
        cwd=str(output_dir.parent),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        print(result.stdout)
        raise AssertionError(f"CLI failed with exit code {result.returncode}")


def output_files(output_dir):
    files = []
    for path in Path(output_dir).rglob("*"):
        if not path.is_file():
            continue
        if ".yfile2mseed-session" in path.parts:
            continue
        if path.name.endswith((".tmp", ".pending", ".building", ".backup", ".txt", ".json", ".csv")):
            continue
        files.append(path)
    files = sorted(files)
    if not files:
        raise AssertionError("CLI did not write any MiniSEED files")
    if any(file.suffix.lower() == ".mseed" for file in files):
        raise AssertionError("SDS output filenames must not use .mseed suffix")
    return files


def read_output_data(output_dir):
    stream = Stream()
    for file in output_files(output_dir):
        stream += read(str(file))
    stream.sort(keys=["starttime"])
    stream.merge(method=1)
    if len(stream) != 1:
        raise AssertionError(f"Expected one merged trace, got {len(stream)}")
    return stream[0]


def read_output_stream(output_dir):
    stream = Stream()
    for file in output_files(output_dir):
        stream += read(str(file))
    stream.sort(keys=["starttime"])
    stream.merge(method=1)
    return stream


def assert_same_trace(output_dir, expected_data, expected_start, expected_rate):
    trace = read_output_data(output_dir)
    if trace.stats.starttime != expected_start:
        raise AssertionError(f"Start mismatch: {trace.stats.starttime} != {expected_start}")
    if abs(trace.stats.sampling_rate - expected_rate) > 1e-9:
        raise AssertionError(f"Rate mismatch: {trace.stats.sampling_rate} != {expected_rate}")
    if not np.array_equal(trace.data, np.asarray(expected_data, dtype=np.int32)):
        raise AssertionError("Sample data changed during CLI round-trip")


def assert_same_stream(actual, expected):
    actual = actual.copy()
    expected = expected.copy()
    actual.traces.sort(key=lambda trace: (trace.id, trace.stats.starttime.timestamp))
    expected.traces.sort(key=lambda trace: (trace.id, trace.stats.starttime.timestamp))
    actual.merge(method=1)
    expected.merge(method=1)

    if len(actual) != len(expected):
        raise AssertionError(f"Trace count mismatch: {len(actual)} != {len(expected)}")

    for idx, (got, exp) in enumerate(zip(actual, expected)):
        if got.id != exp.id:
            raise AssertionError(f"Trace {idx} id mismatch: {got.id} != {exp.id}")
        if got.stats.starttime != exp.stats.starttime:
            raise AssertionError(f"Trace {idx} start mismatch: {got.stats.starttime} != {exp.stats.starttime}")
        if got.stats.endtime != exp.stats.endtime:
            raise AssertionError(f"Trace {idx} end mismatch: {got.stats.endtime} != {exp.stats.endtime}")
        if abs(got.stats.sampling_rate - exp.stats.sampling_rate) > 1e-9:
            raise AssertionError(
                f"Trace {idx} rate mismatch: {got.stats.sampling_rate} != {exp.stats.sampling_rate}"
            )
        if not np.array_equal(got.data, exp.data):
            raise AssertionError(f"Trace {idx} sample data mismatch")


def build_master(start, seconds, sampling_rate):
    npts = int(seconds * sampling_rate) + 1
    data = np.arange(npts, dtype=np.int32)
    trace = Trace(data=data)
    trace.stats.network = "IR"
    trace.stats.station = "TST"
    trace.stats.location = ""
    trace.stats.channel = "BHZ"
    trace.stats.starttime = start
    trace.stats.sampling_rate = sampling_rate
    return Stream([trace])


def write_stream_part(path, stream):
    stream.write(str(path), format="MSEED", encoding="STEIM1", reclen=4096)


def run_scenario(cli, work, name, parts):
    input_dir = work / name / "input"
    output_dir = work / name / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    expected = Stream()
    for idx, part in enumerate(parts):
        path = input_dir / f"part_{idx:03d}.mseed"
        write_stream_part(path, part)
        expected += part

    first_source = read(str(input_dir / "part_000.mseed"))[0]
    if first_source.stats.mseed.encoding != "STEIM1":
        raise AssertionError(f"{name}: ObsPy did not create a STEIM1 source")

    run_cli(cli, input_dir, output_dir)
    actual = read_output_stream(output_dir)
    assert_same_stream(actual, expected)


def test_single_file_roundtrip(cli, work):
    data = np.arange(1000, dtype=np.int32) * 3 - 400
    source = work / "single_steim1.mseed"
    output = work / "single_out"
    output.mkdir()

    start = UTCDateTime(2020, 1, 2, 3, 4, 5)
    rate = 50.0
    write_steim1(source, data, start, rate)
    source_trace = read(str(source))[0]
    if source_trace.stats.mseed.encoding != "STEIM1":
        raise AssertionError("ObsPy did not create a STEIM1 input file")

    run_cli(cli, source, output)
    assert_same_trace(output, data, start, rate)


def test_existing_output_is_not_duplicated(cli, work):
    data = np.arange(500, dtype=np.int32) % 37
    source = work / "dedup_steim1.mseed"
    output = work / "dedup_out"
    output.mkdir()

    start = UTCDateTime(2021, 5, 6, 7, 8, 9)
    rate = 25.0
    write_steim1(source, data, start, rate)

    run_cli(cli, source, output)
    assert_same_trace(output, data, start, rate)

    run_cli(cli, source, output)
    assert_same_trace(output, data, start, rate)


def test_complex_steim1_scenarios(cli, work):
    base = UTCDateTime(2020, 1, 1, 0, 0, 0)
    rate = 50.0
    master = build_master(base, seconds=3 * 86400, sampling_rate=rate)

    def sl(start_offset, end_offset):
        return master.slice(base + start_offset, base + end_offset)

    scenarios = {
        "gap": [
            sl(0, 3600),
            sl(7200, 10800),
        ],
        "overlap": [
            sl(0, 7200),
            sl(3600, 10800),
        ],
        "duplicate": [
            sl(0, 3600),
            sl(0, 3600),
        ],
        "same_start_different_length": [
            sl(0, 300),
            sl(0, 900),
            sl(0, 120),
        ],
        "reverse_order": [
            sl(1800, 3600),
            sl(0, 1800),
            sl(3600 + 1 / rate, 5400),
        ],
        "midnight_split": [
            sl(86400 - 120, 86400 + 120),
        ],
        "cross_day_overlap": [
            sl(86400 - 300, 86400 + 300),
            sl(86400 - 60, 86400 + 600),
        ],
        "many_small_segments": [
            sl(i * 3, i * 3 + 1) for i in range(40)
        ],
    }

    for name, parts in scenarios.items():
        run_scenario(cli, work, f"scenario_{name}", parts)


def main():
    if len(sys.argv) != 2:
        print("usage: obspy_steim1_cli_test.py <yfile2mseed executable>")
        return 2

    cli = Path(sys.argv[1]).resolve()
    if not cli.exists():
        raise AssertionError(f"CLI executable does not exist: {cli}")

    with tempfile.TemporaryDirectory(prefix="yfile2miniseed_obspy_") as tmp:
        work = Path(tmp)
        test_single_file_roundtrip(cli, work)
        test_existing_output_is_not_duplicated(cli, work)
        test_complex_steim1_scenarios(cli, work)

    print("ObsPy STEIM1 CLI tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
