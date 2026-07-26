#pragma once

#include <algorithm>
#include <codecvt>
#include <filesystem>
#include <iostream>
#include <locale>
#include <queue>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace yfile2miniseed {

    class FileDiscovery {
    public:
        FileDiscovery();

        void addDirectory(const std::string& dirPath);

        std::vector<std::string> getAllFiles();

    private:
        std::queue<std::string> directoryQueue;
    };

}
