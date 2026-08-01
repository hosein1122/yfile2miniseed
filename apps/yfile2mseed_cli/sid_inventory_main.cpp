#include <yfile2miniseed/yfile_reader.hpp>

#include "filesystem/file_discovery.hpp"
#include "utils/zip_utils.hpp"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

std::string trim(const std::string& value)
{
    size_t start = 0;
    size_t end = value.size();
    while (start < value.size() && std::isspace(static_cast<unsigned char>(value[start])))
        ++start;
    while (end > start && std::isspace(static_cast<unsigned char>(value[end - 1])))
        --end;
    return value.substr(start, end - start);
}

std::string remove_inner_spaces(const std::string& value)
{
    std::string out;
    out.reserve(value.size());
    for (char ch : value)
    {
        if (!std::isspace(static_cast<unsigned char>(ch)))
            out.push_back(ch);
    }
    return out;
}

std::string clean_field(const char* value)
{
    return remove_inner_spaces(trim(value ? value : ""));
}

std::string build_key(
    const std::string& network,
    const std::string& station,
    const std::string& location,
    const std::string& channel)
{
    return clean_field(network.c_str()) + "_" +
        clean_field(station.c_str()) + "_" +
        clean_field(location.c_str()) + "_" +
        clean_field(channel.c_str());
}

std::string raw_key_from_reader(const yfile2miniseed::Y5FileReader& reader)
{
    return build_key(
        reinterpret_cast<const char*>(reader.t1.NetworkID),
        reinterpret_cast<const char*>(reader.t1.StationID.Station),
        reinterpret_cast<const char*>(reader.t1.StationID.Location),
        reinterpret_cast<const char*>(reader.t1.StationID.Channel));
}

std::map<std::string, std::string> load_existing(const fs::path& path)
{
    std::map<std::string, std::string> entries;
    std::ifstream in(path);
    if (!in.is_open())
        return entries;

    std::string line;
    while (std::getline(in, line))
    {
        const auto pos = line.find("=>");
        if (pos == std::string::npos)
            continue;

        std::string raw = trim(line.substr(0, pos));
        std::string corrected = trim(line.substr(pos + 2));
        if (raw.empty() || corrected.empty())
            continue;

        entries.emplace(raw, corrected);
    }
    return entries;
}

void write_entries(const fs::path& path, const std::map<std::string, std::string>& entries)
{
    if (path.has_parent_path())
        fs::create_directories(path.parent_path());

    std::ofstream out(path, std::ios::out | std::ios::trunc);
    if (!out.is_open())
        throw std::runtime_error("cannot write output file");

    for (const auto& [raw, corrected] : entries)
        out << raw << " => " << corrected << "\n";
}

void add_reader_sid(
    const yfile2miniseed::Y5FileReader& reader,
    std::set<std::string>& discovered)
{
    const std::string raw = raw_key_from_reader(reader);
    if (!raw.empty() && raw != "___")
        discovered.insert(raw);
}

bool scan_yfile_path(const std::string& path, std::set<std::string>& discovered)
{
    yfile2miniseed::Y5FileReader reader;
    if (!reader.ReadFromFile(path))
        return false;
    add_reader_sid(reader, discovered);
    return true;
}

void scan_zip_path(const std::string& path, std::set<std::string>& discovered, size_t& yfiles)
{
    using namespace yfile2miniseed::cli::ziputils;

    if (!IsZipFile(path))
        return;

    const auto files = ExtractZipToMemory(path);
    for (const auto& file : files)
    {
        if (file.data.empty())
            continue;

        yfile2miniseed::Y5FileReader reader;
        if (!reader.ReadFromRAM(file.data.data(), file.data.size()))
            continue;

        add_reader_sid(reader, discovered);
        ++yfiles;
    }
}

std::vector<std::string> collect_files(const std::string& input)
{
    fs::path input_path(input);
    if (fs::is_regular_file(input_path))
        return { input_path.string() };

    yfile2miniseed::FileDiscovery discovery;
    discovery.addDirectory(input);
    return discovery.getAllFiles();
}

void print_usage()
{
    std::cout
        << "usage:\n"
        << "  yfile2mseed_sid_scan inputPath [-o CorrectSID.txt]\n\n"
        << "examples:\n"
        << "  yfile2mseed_sid_scan D:\\YFiles -o CorrectSID.txt\n"
        << "  yfile2mseed_sid_scan D:\\one.zip\n";
}

} // namespace

int main(int argc, char* argv[])
{
    std::string input;
    fs::path output = "CorrectSID.txt";
    bool show_help = false;

    for (int i = 1; i < argc; ++i)
    {
        std::string arg = argv[i];
        if (arg == "-h" || arg == "--help")
        {
            show_help = true;
        }
        else if (arg == "-o" || arg == "--output")
        {
            if (++i < argc)
                output = argv[i];
        }
        else if (input.empty())
        {
            input = arg;
        }
    }

    if (show_help || input.empty())
    {
        print_usage();
        return input.empty() && !show_help ? 2 : 0;
    }

    if (!fs::exists(input))
    {
        std::cerr << "Input path not found: " << input << "\n";
        return 2;
    }

    std::map<std::string, std::string> entries = load_existing(output);
    std::set<std::string> discovered;
    size_t yfiles = 0;
    size_t errors = 0;

    const auto files = collect_files(input);
    for (const auto& path : files)
    {
        try
        {
            if (scan_yfile_path(path, discovered))
            {
                ++yfiles;
                continue;
            }

            scan_zip_path(path, discovered, yfiles);
        }
        catch (const std::exception& exc)
        {
            ++errors;
            std::cerr << "Warning: cannot scan " << path << ": " << exc.what() << "\n";
        }
    }

    size_t added = 0;
    for (const auto& raw : discovered)
    {
        if (entries.find(raw) != entries.end())
            continue;

        entries.emplace(raw, raw);
        ++added;
    }

    try
    {
        write_entries(output, entries);
    }
    catch (const std::exception& exc)
    {
        std::cerr << "Cannot write " << output << ": " << exc.what() << "\n";
        return 1;
    }

    std::cout << "Input files scanned: " << files.size() << "\n";
    std::cout << "Y-files parsed: " << yfiles << "\n";
    std::cout << "SID combinations found: " << discovered.size() << "\n";
    std::cout << "Existing entries preserved: " << (entries.size() - added) << "\n";
    std::cout << "New entries added: " << added << "\n";
    std::cout << "Output: " << output << "\n";
    if (errors)
        std::cout << "Warnings: " << errors << "\n";

    return errors ? 1 : 0;
}
