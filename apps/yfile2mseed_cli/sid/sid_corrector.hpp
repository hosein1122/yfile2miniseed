#pragma once

#include <string>
#include <unordered_map>
#include <fstream>
#include <mutex>

namespace yfile2miniseed::cli::sid
{

    /// <summary>
    /// ممکن است برخی اطلاعات y-file ها اشتباه ثبت شده باشد
    /// هدف این کلاس تصحیح و به یاد سپردن تصحیح ها برای کل پروژه هست
    /// مثلا FAR_SHI_SP_N اشتباه است و باید یه
    /// IR_SHI__SPN تبدیل شود
    /// </summary>
    class SIDCorrector
    {
    public:

        struct CorrectedEntry
        {
            std::string network;
            std::string station;
            std::string location; // ممکن است خالی باشد.
            std::string channel;
        };

        explicit SIDCorrector(const std::string& filePath);
        ~SIDCorrector();

        SIDCorrector(const SIDCorrector&) = delete;
        SIDCorrector& operator=(const SIDCorrector&) = delete;

        bool GetCorrectedInteractive(
            const std::string& rawNetwork,
            const std::string& rawStation,
            const std::string& rawLocation,
            const std::string& rawChannel,
            CorrectedEntry& corrected);

        bool GetCorrected(
            const std::string& rawNetwork,
            const std::string& rawStation,
            const std::string& rawLocation,
            const std::string& rawChannel,
            CorrectedEntry& corrected) const;

        bool HasCorrections() const;

        void SaveAndCompactFile();

    private:

        std::string m_filePath;

        std::unordered_map<std::string, CorrectedEntry> m_corrections;

        std::ofstream m_appendStream;

        std::mutex m_mutex;

        static std::string BuildKey(
            const std::string& network,
            const std::string& station,
            const std::string& location,
            const std::string& channel);

        static std::string Trim(const std::string& s);

        static std::string RemoveInnerSpaces(const std::string& s);

        void LoadFromFile();

        void SaveNewEntry(
            const std::string& rawKey,
            const CorrectedEntry& entry);

        static std::string ReadRequiredField(const std::string& fieldName);

        static std::string ReadOptionalField(const std::string& fieldName);

        void SortAndRewriteFile();
    };

}
