from __future__ import annotations

from pathlib import Path

import numpy
from setuptools import Extension, setup


ROOT = Path(__file__).resolve().parents[2]


extension = Extension(
    "yfile2obspy_cpp._yreader",
    sources=[
        str(Path("src") / "yfile2obspy_cpp" / "_yreader.cpp"),
    ],
    include_dirs=[
        str(ROOT / "include"),
        numpy.get_include(),
    ],
    language="c++",
    extra_compile_args=(
        ["/std:c++17", "/utf-8", "/GL-"]
        if __import__("os").name == "nt"
        else ["-std=c++17"]
    ),
)


setup(
    ext_modules=[extension],
)
