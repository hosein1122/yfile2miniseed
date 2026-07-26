#pragma once

#include <memory>
#include <vector>

#include <spdlog/spdlog.h>
#include <spdlog/logger.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/sinks/daily_file_sink.h>

namespace yfile2miniseed::logging
{
    void init();
}


/*
Example usage:

#include <spdlog/spdlog.h>

void ProcessFile(const std::string& file)
{
    spdlog::info("Processing {}", file);

    if (file.empty())
    {
        spdlog::warn("Empty filename");
    }
}
*/
