#include "time_utils.hpp"

namespace yfile2miniseed::time
{

    std::chrono::system_clock::time_point SecondsToUtcDateTime(double seconds)
    {
        return std::chrono::system_clock::from_time_t(static_cast<std::time_t>(seconds));
    }


    std::string formatTime(double seconds, bool includeMilliSeconds)
    {
        auto timePoint = std::chrono::system_clock::from_time_t(static_cast<std::time_t>(seconds));
        std::time_t tt = std::chrono::system_clock::to_time_t(timePoint);

        std::tm utc_tm;

        if (gmtime_s(&utc_tm, &tt) != 0)
            std::cerr << "Error converting time!" << std::endl;

        std::stringstream ss;
        ss << std::put_time(&utc_tm, "%Y-%m-%d %H:%M:%S");

        if (includeMilliSeconds)
        {
            double microseconds =
                round((seconds - static_cast<double>(static_cast<std::time_t>(seconds))) * 10'000.0);

            ss << "." << std::setw(4) << std::setfill('0') << microseconds;
        }

        return ss.str();
    }


    std::string PrintTime(std::time_t time)
    {
        std::tm utc_tm;

        if (gmtime_s(&utc_tm, &time) != 0)
            std::cerr << "Error converting time!" << std::endl;

        std::stringstream ss;
        ss << std::put_time(&utc_tm, "%Y-%m-%d %H:%M:%S");

        return ss.str();
    }


    void utcTime::Init(double seconds)
    {
        auto timePoint =
            std::chrono::system_clock::from_time_t(static_cast<std::time_t>(seconds));

        std::time_t tt = std::chrono::system_clock::to_time_t(timePoint);

        std::tm utc_tm;

        if (gmtime_s(&utc_tm, &tt) != 0)
            std::cerr << "Error converting time!" << std::endl;

        Year = utc_tm.tm_year + 1900;
        Month = utc_tm.tm_mon + 1;
        Day = utc_tm.tm_mday;

        Hour = utc_tm.tm_hour;
        Minute = utc_tm.tm_min;
        second = utc_tm.tm_sec;

        double mili = (seconds - static_cast<double>(static_cast<std::time_t>(seconds))) * 1000.0;

        millisecond = static_cast<int>(round(mili));
    }


    std::string utcTime::toString(bool includeMilliSeconds) const
    {
        std::stringstream ss;

        ss << std::setfill('0') << std::setw(4) << Year << "-"
            << std::setw(2) << Month << "-"
            << std::setw(2) << Day << " "
            << std::setw(2) << Hour << ":"
            << std::setw(2) << Minute << ":"
            << std::setw(2) << second;

        if (includeMilliSeconds)
            ss << "." << millisecond;

        return ss.str();
    }


    std::string utcTime::toString2() const
    {
        std::stringstream ss;

        ss << std::setfill('0') << std::setw(4) << Year
            << std::setw(2) << Month
            << std::setw(2) << Day << "."
            << std::setw(2) << Hour
            << std::setw(2) << Minute
            << std::setw(2) << second;

        return ss.str();
    }

}
