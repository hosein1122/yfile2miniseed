cmake_minimum_required(VERSION 3.24)

project(yfile2miniseed
    VERSION 0.1.0
    LANGUAGES C CXX
)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Prefer bundled Find*.cmake modules over vcpkg/system packages.
list(PREPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake/modules")

# Build all bundled dependencies as static libraries.
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Build dependencies as static libraries" FORCE)

# Static CRT on MSVC → fewer runtime DLL dependencies (/MT, /MTd).
if(MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>" CACHE STRING "" FORCE)
endif()

# When Visual Studio uses the vcpkg toolchain, do not copy DLLs next to our exe.
if(DEFINED VCPKG_APPLOCAL_DEPS)
    set(VCPKG_APPLOCAL_DEPS OFF CACHE BOOL "Disable vcpkg applocal DLL deployment" FORCE)
endif()

include(FetchContent)

# ---------------- libmseed ----------------

FetchContent_Declare(
    libmseed
    GIT_REPOSITORY https://github.com/EarthScope/libmseed
    GIT_TAG v3.4.0
)
FetchContent_MakeAvailable(libmseed)

# ---------------- spdlog ----------------

set(SPDLOG_BUILD_SHARED OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
    spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG v1.17.0
)
FetchContent_MakeAvailable(spdlog)

# ---------------- zlib (static) ----------------
# libzip requires ZLIB; override vcpkg's dynamic zd.dll / z.dll.

set(ZLIB_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

# Drop stale results from a previous configure (e.g. vcpkg dynamic libs).
unset(ZLIB_LIBRARY CACHE)
unset(ZLIB_LIBRARY_DEBUG CACHE)
unset(ZLIB_LIBRARY_RELEASE CACHE)
unset(ZLIB_INCLUDE_DIR CACHE)

FetchContent_Declare(
    zlib
    GIT_REPOSITORY https://github.com/madler/zlib
    GIT_TAG v1.3.1
    OVERRIDE_FIND_PACKAGE
)
FetchContent_MakeAvailable(zlib)

if(TARGET zlibstatic AND NOT TARGET ZLIB::ZLIB)
    add_library(ZLIB::ZLIB ALIAS zlibstatic)
    target_include_directories(zlibstatic INTERFACE
        "${zlib_SOURCE_DIR}"
        "${zlib_BINARY_DIR}"
    )
endif()

# ---------------- libzip ----------------
# Option names match libzip's CMakeLists.txt (not LIBZIP_BUILD_* aliases).

set(BUILD_DOC OFF CACHE BOOL "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_REGRESS OFF CACHE BOOL "" FORCE)
set(BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(BUILD_OSSFUZZ OFF CACHE BOOL "" FORCE)
set(LIBZIP_DO_INSTALL OFF CACHE BOOL "" FORCE)

set(ENABLE_COMMONCRYPTO OFF CACHE BOOL "" FORCE)
set(ENABLE_GNUTLS OFF CACHE BOOL "" FORCE)
set(ENABLE_MBEDTLS OFF CACHE BOOL "" FORCE)
set(ENABLE_OPENSSL OFF CACHE BOOL "" FORCE)
set(ENABLE_WINDOWS_CRYPTO OFF CACHE BOOL "" FORCE)
set(ENABLE_LZMA OFF CACHE BOOL "" FORCE)
set(ENABLE_ZSTD OFF CACHE BOOL "" FORCE)
# BZip2 is optional for ZIP reading; disable to avoid vcpkg bz2.dll dependency.
# Nanometrics Y-File archives typically use store/deflate (zlib only).
set(ENABLE_BZIP2 OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
    libzip
    GIT_REPOSITORY https://github.com/nih-at/libzip.git
    GIT_TAG v1.11.4
)
FetchContent_MakeAvailable(libzip)

# ---------------- library ----------------

add_library(yfile2miniseed_lib STATIC
    src/yfile_reader.cpp
    src/mseed_processor.cpp
)

target_include_directories(yfile2miniseed_lib PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_link_libraries(yfile2miniseed_lib
    PUBLIC
        spdlog::spdlog
)

if(TARGET mseed_static)
    message(STATUS "Linking yfile2miniseed_lib against static libmseed")
    target_link_libraries(yfile2miniseed_lib PUBLIC mseed_static)
elseif(TARGET mseed)
    message(STATUS "Linking yfile2miniseed_lib against libmseed")
    target_link_libraries(yfile2miniseed_lib PUBLIC mseed)
else()
    message(FATAL_ERROR "libmseed target not found")
endif()

# ---------------- apps ----------------

add_subdirectory(apps/yfile2mseed_cli)

# ---------------- tests ----------------

include(CTest)
if(BUILD_TESTING)
    add_subdirectory(tests)
endif()
