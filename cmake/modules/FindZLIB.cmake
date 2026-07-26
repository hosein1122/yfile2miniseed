# Bundled static zlib (FetchContent) — satisfies find_package(ZLIB) for libzip.
include_guard(GLOBAL)

if(NOT TARGET zlibstatic)
    message(FATAL_ERROR "FindZLIB.cmake: bundled zlibstatic target is missing")
endif()

if(NOT TARGET ZLIB::ZLIB)
    add_library(ZLIB::ZLIB ALIAS zlibstatic)
endif()

set(ZLIB_FOUND TRUE)
set(ZLIB_INCLUDE_DIRS "${zlib_SOURCE_DIR}" "${zlib_BINARY_DIR}")
set(ZLIB_INCLUDE_DIR "${ZLIB_INCLUDE_DIRS}")
set(ZLIB_LIBRARIES ZLIB::ZLIB)
set(ZLIB_LIBRARY ZLIB::ZLIB)
