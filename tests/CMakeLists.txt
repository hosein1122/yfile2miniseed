find_package(Python3 COMPONENTS Interpreter)

add_executable(yfile2miniseed_compute_tests
    compute_okseg_tests.cpp
)

target_link_libraries(yfile2miniseed_compute_tests
    PRIVATE
        yfile2miniseed_lib
)

add_test(
    NAME compute_okseg
    COMMAND yfile2miniseed_compute_tests
)

if(Python3_Interpreter_FOUND)
    add_test(
        NAME obspy_steim1_cli
        COMMAND
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/obspy_steim1_cli_test.py
            $<TARGET_FILE:yfile2mseed_cli>
    )
    set_tests_properties(obspy_steim1_cli PROPERTIES
        SKIP_RETURN_CODE 77
    )

    add_test(
        NAME compare_mseed_outputs
        COMMAND
            ${Python3_EXECUTABLE}
            ${CMAKE_CURRENT_SOURCE_DIR}/compare_mseed_outputs_test.py
            ${CMAKE_CURRENT_SOURCE_DIR}/../tools/compare_mseed_outputs.py
    )
    set_tests_properties(compare_mseed_outputs PROPERTIES
        SKIP_RETURN_CODE 77
    )
endif()
