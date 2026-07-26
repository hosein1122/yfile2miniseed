#include "file_discovery.hpp"

namespace yfile2miniseed {

    FileDiscovery::FileDiscovery() {}

    void FileDiscovery::addDirectory(const std::string& dirPath) {
        directoryQueue.push(dirPath);
    }

    std::vector<std::string> FileDiscovery::getAllFiles() {
        std::vector<std::string> allFiles;

        while (!directoryQueue.empty()) {
            std::string currentDir = directoryQueue.front();
            directoryQueue.pop();

            try {
                for (const auto& entry : fs::directory_iterator(currentDir)) {

                    std::string dds = "";

                    try {
                        dds = entry.path().string();
                    }
                    catch (const std::exception&) {
                        std::cerr << "Error! UnSopported UTF8 char in file name or address." << std::endl;
                        continue;
                    }

                    if (fs::is_regular_file(entry.status())) {
                        allFiles.push_back(dds);
                    }
                    else if (fs::is_directory(entry.status())) {
                        addDirectory(dds);
                    }
                }
            }
            catch (const std::exception&) {
                std::cerr << "Error! UnSopported Directory." << std::endl;
                continue;
            }
        }

        return allFiles;
    }

}
