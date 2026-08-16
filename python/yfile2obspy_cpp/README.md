# yfile2obspy-cpp

Python bindings for the C++ Nanometrics Y-file reader used by the hybrid SDS
converter.

## Install

From the repository root:

```bat
python -m pip install --upgrade pip setuptools wheel build numpy
python -m pip install --force-reinstall .\python\yfile2obspy_cpp
```

To build a wheel:

```bat
cd python\yfile2obspy_cpp
python -m build --wheel
python -m pip install --force-reinstall .\dist\yfile2obspy_cpp-0.1.0-cp314-cp314-win_amd64.whl
```

Use the exact wheel filename produced in `dist`; Windows `cmd.exe` does not
expand `*.whl` in `pip install` commands.

## API

```python
import yfile2obspy_cpp

record = yfile2obspy_cpp.read_yfile_path(r"D:\YFiles\YPARSPE.20100107.095933")
record = yfile2obspy_cpp.read_yfile_bytes(payload)
```

Returned fields:

```text
network
station
location
channel
start_ns
end_ns
sample_rate
npts
samples
```

`samples` is a NumPy `int32` array. The package does not know about SDS,
CorrectSID, ZIP/RAR discovery, ObsPy merge, or MiniSEED writing. Those behaviors
live in `tools/yfiles_to_mseed_sds_hybrid_cppread.py`.

## Archive Use

The hybrid converter can read Y-files from ZIP and RAR archives. Archive members
are decompressed one at a time in RAM, then passed to `read_yfile_bytes()`.

RAR support requires:

```bat
python -m pip install rarfile
winget install 7zip.7zip
setx PATH "%PATH%;C:\Program Files\7-Zip"
```
