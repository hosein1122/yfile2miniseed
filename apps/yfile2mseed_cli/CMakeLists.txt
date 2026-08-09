add_executable(yfile2mseed_cli
    main.cpp
    filesystem/file_discovery.cpp
    logging/app_logger.cpp
    sid/sid_corrector.cpp
    stats/data_availability_stats.cpp
    utils/time_utils.cpp
    utils/zip_utils.cpp
)

set_target_properties(yfile2mseed_cli PROPERTIES
    OUTPUT_NAME yfile2mseed
)

target_link_libraries(yfile2mseed_cli
    PRIVATE
        yfile2miniseed_lib
        libzip::zip
)

target_include_directories(yfile2mseed_cli
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)

add_executable(yfile2mseed_append_cli
    append_main.cpp
    filesystem/file_discovery.cpp
    logging/app_logger.cpp
    sid/sid_corrector.cpp
    stats/data_availability_stats.cpp
    utils/time_utils.cpp
    utils/zip_utils.cpp
)

set_target_properties(yfile2mseed_append_cli PROPERTIES
    OUTPUT_NAME yfile2mseed_append
)

target_link_libraries(yfile2mseed_append_cli
    PRIVATE
        yfile2miniseed_lib
        libzip::zip
)

target_include_directories(yfile2mseed_append_cli
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)

add_executable(yfile2mseed_sid_scan
    sid_inventory_main.cpp
    filesystem/file_discovery.cpp
    utils/zip_utils.cpp
)

target_link_libraries(yfile2mseed_sid_scan
    PRIVATE
        yfile2miniseed_lib
        libzip::zip
)

target_include_directories(yfile2mseed_sid_scan
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)

add_executable(yfile2obspy_bridge
    bridge_main.cpp
    utils/zip_utils.cpp
)

target_link_libraries(yfile2obspy_bridge
    PRIVATE
        yfile2miniseed_lib
        libzip::zip
)

target_include_directories(yfile2obspy_bridge
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}
)
