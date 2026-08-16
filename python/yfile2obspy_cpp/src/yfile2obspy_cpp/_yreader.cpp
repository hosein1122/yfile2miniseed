#define PY_SSIZE_T_CLEAN

#include <Python.h>
#include <numpy/arrayobject.h>

#include <yfile2miniseed/yfile_reader.hpp>

#include <cstdint>
#include <cstring>
#include <string>

// Keep this extension self-contained for setuptools builds. The reader has no
// dependency on the MiniSEED writer or CLI code paths.
#include "../../../../src/yfile_reader.cpp"

namespace {

std::string clean_string(const uint8_t* value)
{
    if (!value)
        return {};

    const char* text = reinterpret_cast<const char*>(value);
    size_t length = 0;
    while (text[length] != '\0')
        ++length;

    while (length > 0 && (text[length - 1] == ' ' || text[length - 1] == '\t'))
        --length;

    return std::string(text, length);
}

int64_t seconds_to_ns(double seconds)
{
    return static_cast<int64_t>(seconds * 1'000'000'000.0);
}

PyObject* build_result(const yfile2miniseed::Y5FileReader& reader)
{
    const npy_intp dims[1] = { static_cast<npy_intp>(reader.t5.NumSamples) };
    PyObject* samples = PyArray_SimpleNew(1, const_cast<npy_intp*>(dims), NPY_INT32);
    if (!samples)
        return nullptr;

    const size_t sample_bytes =
        static_cast<size_t>(reader.t5.NumSamples) * sizeof(int32_t);
    std::memcpy(PyArray_DATA(reinterpret_cast<PyArrayObject*>(samples)),
                reader.t7.samples,
                sample_bytes);

    PyObject* result = Py_BuildValue(
        "{s:s,s:s,s:s,s:s,s:L,s:L,s:d,s:I,s:O}",
        "network", clean_string(reader.t1.NetworkID).c_str(),
        "station", clean_string(reader.t1.StationID.Station).c_str(),
        "location", clean_string(reader.t1.StationID.Location).c_str(),
        "channel", clean_string(reader.t1.StationID.Channel).c_str(),
        "start_ns", static_cast<long long>(seconds_to_ns(reader.t5.StartTime)),
        "end_ns", static_cast<long long>(seconds_to_ns(reader.t5.EndTime)),
        "sample_rate", static_cast<double>(reader.t3.SampleRate),
        "npts", static_cast<unsigned int>(reader.t5.NumSamples),
        "samples", samples);

    Py_DECREF(samples);
    return result;
}

PyObject* read_yfile_path(PyObject*, PyObject* args)
{
    PyObject* path_object = nullptr;
    if (!PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &path_object))
        return nullptr;

    const char* path = PyBytes_AsString(path_object);
    if (!path)
    {
        Py_DECREF(path_object);
        return nullptr;
    }

    yfile2miniseed::Y5FileReader reader;
    const bool ok = reader.ReadFromFile(path);
    Py_DECREF(path_object);

    if (!ok)
    {
        PyErr_SetString(PyExc_ValueError, "cannot read Nanometrics Y-file");
        return nullptr;
    }

    return build_result(reader);
}

PyObject* read_yfile_bytes(PyObject*, PyObject* args)
{
    Py_buffer buffer{};
    if (!PyArg_ParseTuple(args, "y*", &buffer))
        return nullptr;

    yfile2miniseed::Y5FileReader reader;
    const bool ok = reader.ReadFromRAM(
        reinterpret_cast<const uint8_t*>(buffer.buf),
        static_cast<size_t>(buffer.len));
    PyBuffer_Release(&buffer);

    if (!ok)
    {
        PyErr_SetString(PyExc_ValueError, "cannot read Nanometrics Y-file bytes");
        return nullptr;
    }

    return build_result(reader);
}

PyMethodDef methods[] = {
    {
        "read_yfile_path",
        read_yfile_path,
        METH_VARARGS,
        "Read a Nanometrics Y-file from a filesystem path."
    },
    {
        "read_yfile_bytes",
        read_yfile_bytes,
        METH_VARARGS,
        "Read a Nanometrics Y-file from an in-memory bytes object."
    },
    { nullptr, nullptr, 0, nullptr }
};

PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_yreader",
    "Fast Nanometrics Y-file reader bindings.",
    -1,
    methods,
};

} // namespace

PyMODINIT_FUNC PyInit__yreader()
{
    import_array();
    return PyModule_Create(&module);
}
