#pragma once

#include <chrono>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

namespace yfile2miniseed::time
{

    std::chrono::system_clock::time_point SecondsToUtcDateTime(double seconds);

    std::string formatTime(double seconds, bool includeMilliSeconds = true);

    std::string PrintTime(std::time_t time);


    struct utcTime
    {
        int Year;
        int Month;
        int Day;

        int Hour;
        int Minute;
        int second;
        int millisecond;

        void Init(double seconds);

        std::string toString(bool includeMilliSeconds = false) const;

        std::string toString2() const;
    };

}
