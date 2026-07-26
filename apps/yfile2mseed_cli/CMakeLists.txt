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
