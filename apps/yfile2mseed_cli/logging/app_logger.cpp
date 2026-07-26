#include "app_logger.hpp"

namespace yfile2miniseed::logging
{

    void init()
    {
        static bool initialized = false;
        if (initialized) return;

        auto console_sink =
            std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
        console_sink->set_level(spdlog::level::trace);

        auto file_sink =
            std::make_shared<spdlog::sinks::daily_file_sink_mt>(
                "logs/HF_Y2MSeed.log",
                0,
                0,
                false
            );
        file_sink->set_level(spdlog::level::warn);

        std::vector<spdlog::sink_ptr> sinks{ console_sink, file_sink };

        auto log = std::make_shared<spdlog::logger>(
            "app", sinks.begin(), sinks.end());

        log->set_level(spdlog::level::trace);

        log->set_pattern("[%Y-%m-%d %H:%M:%S] [%^%l%$] %v");

        spdlog::set_default_logger(log);

        spdlog::flush_on(spdlog::level::err);

        initialized = true;
    }

}
