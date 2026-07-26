#include "sid_corrector.hpp"

#include <iostream>
#include <sstream>
#include <algorithm>
#include <cctype>
#include <vector>

namespace yfile2miniseed::cli::sid
{

    // ---------------------------
    // Helpers
    // ---------------------------

    std::string SIDCorrector::Trim(const std::string& s)
    {
        if (s.empty()) return s;

        size_t start = 0;
        size_t end = s.size();

        while (start < s.size() && std::isspace(static_cast<unsigned char>(s[start])))
            start++;

        while (end > start && std::isspace(static_cast<unsigned char>(s[end - 1])))
            end--;

        return s.substr(start, end - start);
    }

    std::string SIDCorrector::RemoveInnerSpaces(const std::string& s)
    {
        std::string out;
        out.reserve(s.size());

        for (char c : s)
            if (!std::isspace(static_cast<unsigned char>(c)))
                out.push_back(c);

        return out;
    }

    std::string SIDCorrector::BuildKey(
        const std::string& network,
        const std::string& station,
        const std::string& location,
        const std::string& channel)
    {
        std::string n = RemoveInnerSpaces(Trim(network));
        std::string s = RemoveInnerSpaces(Trim(station));
        std::string l = RemoveInnerSpaces(Trim(location));
        std::string c = RemoveInnerSpaces(Trim(channel));

        std::string key;
        key.reserve(n.size() + s.size() + l.size() + c.size() + 3);

        key += n + "_";
        key += s + "_";
        key += l + "_";
        key += c;

        return key;
    }

    std::string SIDCorrector::ReadRequiredField(const std::string& fieldName)
    {
        while (true)
        {
            std::cout << fieldName << ": ";

            std::string line;

            if (!std::getline(std::cin, line))
            {
                std::cin.clear();
                continue;
            }

            std::string trimmed = RemoveInnerSpaces(Trim(line));

            if (trimmed.empty())
            {
                std::cout << "Field \"" << fieldName
                    << "\" is required. Please enter a non-empty value.\n";
                continue;
            }

            return trimmed;
        }
    }

    std::string SIDCorrector::ReadOptionalField(const std::string& fieldName)
    {
        std::cout << fieldName << " (press Enter to leave empty): ";

        std::string line;

        if (!std::getline(std::cin, line))
        {
            std::cin.clear();
            return std::string();
        }

        return RemoveInnerSpaces(Trim(line));
    }

    // ---------------------------
    // Constructor / Destructor
    // ---------------------------
    SIDCorrector::SIDCorrector(const std::string& filePath)
        : m_filePath(filePath)
    {
        LoadFromFile();

        m_appendStream.open(m_filePath, std::ios::out | std::ios::app);

        if (!m_appendStream.is_open())
            std::cerr << "Warning: Cannot open " << m_filePath << " in append mode.\n";
    }

    SIDCorrector::~SIDCorrector()
    {
        if (m_appendStream.is_open())
        {
            m_appendStream.close();
            SaveAndCompactFile();
        }
    }

    // ---------------------------
    // Load existing corrections
    // ---------------------------
    void SIDCorrector::LoadFromFile()
    {
        std::ifstream in(m_filePath);

        if (!in.is_open())
            return;

        std::string line;

        while (std::getline(in, line))
        {
            line = Trim(line);

            if (line.empty())
                continue;

            auto p = line.find("=>");

            if (p == std::string::npos)
                continue;

            std::string rawKey = Trim(line.substr(0, p));
            std::string newKey = Trim(line.substr(p + 2));

            if (rawKey.empty() || newKey.empty())
                continue;

            // Split newKey
            std::istringstream iss(newKey);
            std::string net, sta, loc, cha;
            std::getline(iss, net, '_');
            std::getline(iss, sta, '_');
            std::getline(iss, loc, '_');
            std::getline(iss, cha, '_');

            CorrectedEntry entry{ net, sta, loc, cha };
            m_corrections[rawKey] = entry;
        }
    }

    // ---------------------------
    // Save new correction
    // ---------------------------
    void SIDCorrector::SaveNewEntry(
        const std::string& rawKey,
        const CorrectedEntry& entry)
    {
        std::lock_guard<std::mutex> lock(m_mutex);

        if (m_corrections.find(rawKey) != m_corrections.end())
        {
            m_corrections[rawKey] = entry;
            return;
        }

        m_corrections[rawKey] = entry;

        if (!m_appendStream.is_open())
            return;

        std::string newKey = BuildKey(
            entry.network,
            entry.station,
            entry.location,
            entry.channel);

        m_appendStream << rawKey << " => " << newKey << "\n";
        m_appendStream.flush();
    }

    // ---------------------------
    // Main correction logic
    // ---------------------------
    bool SIDCorrector::GetCorrectedInteractive(
        const std::string& rawNetwork,
        const std::string& rawStation,
        const std::string& rawLocation,
        const std::string& rawChannel,
        CorrectedEntry& corrected)
    {
        const std::string rawKey =
            BuildKey(rawNetwork, rawStation, rawLocation, rawChannel);

        auto it = m_corrections.find(rawKey);

        if (it != m_corrections.end())
        {
            corrected = it->second;
            return true;
        }

        std::cout << "\nDetected new SID combination:\n";
        std::cout << "  Network : " << rawNetwork << "\n";
        std::cout << "  Station : " << rawStation << "\n";
        std::cout << "  Location: " << rawLocation << "\n";
        std::cout << "  Channel : " << rawChannel << "\n";
        std::cout << "Enter corrected values below:\n";

        corrected.network = ReadRequiredField("Network");
        corrected.station = ReadRequiredField("Station");
        corrected.location = ReadOptionalField("Location");
        corrected.channel = ReadRequiredField("Channel");

        SaveNewEntry(rawKey, corrected);

        return true;
    }

    void SIDCorrector::SortAndRewriteFile()
    {
        std::ofstream out(m_filePath, std::ios::out | std::ios::trunc);

        if (!out.is_open())
            return;

        std::vector<std::pair<std::string, CorrectedEntry>> sortedEntries(
            m_corrections.begin(),
            m_corrections.end());

        std::sort(
            sortedEntries.begin(),
            sortedEntries.end(),
            [](const auto& a, const auto& b)
            {
                return a.first < b.first;
            });

        for (const auto& pair : sortedEntries)
        {
            std::string newKey = BuildKey(
                pair.second.network,
                pair.second.station,
                pair.second.location,
                pair.second.channel);

            out << pair.first << " => " << newKey << "\n";
        }
    }

    void SIDCorrector::SaveAndCompactFile()
    {
        std::lock_guard<std::mutex> lock(m_mutex);
        SortAndRewriteFile();
    }

}
