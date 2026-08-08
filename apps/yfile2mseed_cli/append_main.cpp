#include "yfile2miniseed/mseed_processor.hpp"
#include <yfile2miniseed/yfile_reader.hpp>
#include "filesystem/file_discovery.hpp"
#include "logging/app_logger.hpp"
#include "sid/sid_corrector.hpp"
#include "stats/data_availability_stats.hpp"
#include "utils/zip_utils.hpp"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

bool miniSeedVersion3 = true;

bool flush_pending_writers(
	yfile2miniseed::MSeedProcessor& processor,
	size_t& totalRamSampleNum)
{
	if (!processor.ClosePendingWriters())
	{
		spdlog::error("Pending MiniSEED writer flush failed.");
		return false;
	}

	totalRamSampleNum = 0;
	return true;
}

bool append_current_yfile(
	yfile2miniseed::Y5FileReader& reader,
	yfile2miniseed::MSeedProcessor& processor,
	yfile2miniseed::cli::sid::SIDCorrector& sidCorrector,
	size_t& totalRamSampleNum,
	size_t minRamInt)
{
	yfile2miniseed::cli::sid::SIDCorrector::CorrectedEntry corrected;
	const bool ok = sidCorrector.GetCorrected(
		reinterpret_cast<const char*>(reader.t1.NetworkID),
		reinterpret_cast<const char*>(reader.t1.StationID.Station),
		reinterpret_cast<const char*>(reader.t1.StationID.Location),
		reinterpret_cast<const char*>(reader.t1.StationID.Channel),
		corrected);

	if (!ok)
	{
		spdlog::error(
			"Missing CorrectSID.txt entry for raw SID '{}_{}_{}_{}'. "
			"Run yfile2mseed_sid_scan on the input path, review CorrectSID.txt, then run this converter again.",
			reinterpret_cast<const char*>(reader.t1.NetworkID),
			reinterpret_cast<const char*>(reader.t1.StationID.Station),
			reinterpret_cast<const char*>(reader.t1.StationID.Location),
			reinterpret_cast<const char*>(reader.t1.StationID.Channel));
		return false;
	}

	const std::string sid = yfile2miniseed::MSeedProcessor::MakeSID(
		corrected.network.c_str(),
		corrected.station.c_str(),
		corrected.location.c_str(),
		corrected.channel.c_str());

	if (sid == "FDSN:___")
		return false;

	if (!processor.AppendYFileToPendingSession(
		sid.c_str(),
		reader.t7.samples,
		static_cast<int64_t>(reader.t5.NumSamples),
		static_cast<int64_t>(reader.t5.StartTime * 1'000'000'000),
		static_cast<double>(reader.t3.SampleRate),
		miniSeedVersion3))
	{
		return false;
	}

	yfile2miniseed::cli::stats::checkNewData(
		sid,
		{ reader.t5.StartTime, reader.t5.EndTime },
		reader.t3.SampleRate);

	totalRamSampleNum += reader.t5.NumSamples;
	if (totalRamSampleNum > (minRamInt * 184'000'000ULL))
	{
		spdlog::info("RAM limit reached. Flushing pending MiniSEED writers...");
		if (!flush_pending_writers(processor, totalRamSampleNum))
			return false;
	}

	return true;
}

std::vector<std::string> collect_files(const std::string& inputPath)
{
	const fs::path path(inputPath);
	if (fs::is_regular_file(path))
		return { path.string() };

	yfile2miniseed::FileDiscovery discovery;
	discovery.addDirectory(inputPath);
	return discovery.getAllFiles();
}

std::string lower_extension(const fs::path& path)
{
	std::string ext = path.extension().string();
	std::transform(ext.begin(), ext.end(), ext.begin(), [](unsigned char c) {
		return static_cast<char>(std::tolower(c));
	});
	return ext;
}

void print_usage()
{
	std::cout
		<< "usage:\n"
		<< "\tyfile2mseed_append inputPath [-o outputDir] [-R MinRAM] [-V2] [--sort-dedup]\n"
		<< "\n"
		<< "\tinputPath      Input directory or file containing Y-Files or ZIPs\n"
		<< "\t-o outputDir   Output SDS root. Default: OutPutStore\n"
		<< "\t-R MinRAM      Flush pending writers after this rough RAM threshold. Default: 2\n"
		<< "\t-V2            Write MiniSEED version 2 instead of version 3\n"
		<< "\t--sort-dedup   After append, rewrite changed SDS files once for sorting and once for duplicate removal\n"
		<< "\t-h             Show help\n";
}

} // namespace

int main(int argc, char* argv[])
{
	yfile2miniseed::logging::init();

	std::cout
		<< "\n"
		<< "                       ********************************************************                       \n"
		<< "                       *       Append Y-Files to SDS as raw MiniSEED records      *                       \n"
		<< "                       *                    yfile2mseed_append                    *                       \n"
		<< "                       ********************************************************                       \n"
		<< "\n";

	std::string inputPath;
	std::string outputDir = "OutPutStore";
	size_t minRamInt = 2;
	bool showHelp = false;
	bool sortAndDeduplicate = false;

	for (int i = 1; i < argc; ++i)
	{
		const std::string arg = argv[i];
		if ((arg == "-o" || arg == "--output") && i + 1 < argc)
		{
			outputDir = argv[++i];
		}
		else if (arg == "-R" && i + 1 < argc)
		{
			minRamInt = static_cast<size_t>(std::stoull(argv[++i]));
		}
		else if (arg == "-V2")
		{
			miniSeedVersion3 = false;
		}
		else if (arg == "--sort-dedup")
		{
			sortAndDeduplicate = true;
		}
		else if (arg == "-h" || arg == "--help")
		{
			showHelp = true;
		}
		else if (inputPath.empty())
		{
			inputPath = arg;
		}
	}

	if (argc == 1 || showHelp)
	{
		print_usage();
		return argc == 1 && !showHelp ? 2 : 0;
	}

	if (inputPath.empty() || !fs::exists(inputPath))
	{
		spdlog::error("Input path not found: {}", inputPath);
		return 2;
	}

	if (!fs::exists("CorrectSID.txt"))
	{
		spdlog::error(
			"CorrectSID.txt was not found. "
			"Run yfile2mseed_sid_scan on the input path first, review CorrectSID.txt, then run this converter.");
		return 2;
	}

	yfile2miniseed::cli::sid::SIDCorrector sidCorrector("CorrectSID.txt");
	if (!sidCorrector.HasCorrections())
	{
		spdlog::error("CorrectSID.txt is missing, empty, or has no valid entries.");
		return 2;
	}

	const std::vector<std::string> files = collect_files(inputPath);
	std::vector<std::string> errorFiles;
	size_t totalRamSampleNum = 0;
	size_t processed = 0;
	size_t successCount = 0;
	size_t failedCount = 0;

	yfile2miniseed::Y5FileReader reader;
	yfile2miniseed::MSeedProcessor processor;
	if (!processor.BeginPendingSession(outputDir))
		return 1;

	const auto startTime = std::chrono::steady_clock::now();

	for (const auto& filePath : files)
	{
		++processed;
		spdlog::info("[{}/{}] Processing: {}", processed, files.size(), filePath);

		try
		{
			const fs::path path(filePath);
			if (lower_extension(path) == ".zip")
			{
				if (!yfile2miniseed::cli::ziputils::IsZipFile(filePath))
				{
					++failedCount;
					errorFiles.push_back(filePath);
					continue;
				}

				const auto extractedFiles = yfile2miniseed::cli::ziputils::ExtractZipToMemory(filePath);
				size_t zipIndex = 0;
				for (const auto& yFile : extractedFiles)
				{
					++zipIndex;
					spdlog::info("[{}/{}] ZIP item: {}", zipIndex, extractedFiles.size(), yFile.name);

					if (yFile.data.empty() || !reader.ReadFromRAM(yFile.data.data(), yFile.data.size()))
					{
						++failedCount;
						errorFiles.push_back(yFile.name);
						continue;
					}

					if (append_current_yfile(reader, processor, sidCorrector, totalRamSampleNum, minRamInt))
						++successCount;
					else
					{
						++failedCount;
						errorFiles.push_back(yFile.name);
					}
				}

				if (!flush_pending_writers(processor, totalRamSampleNum))
					return 1;
			}
			else if (reader.ReadFromFile(filePath))
			{
				if (append_current_yfile(reader, processor, sidCorrector, totalRamSampleNum, minRamInt))
					++successCount;
				else
				{
					++failedCount;
					errorFiles.push_back(filePath);
				}
			}
			else
			{
				++failedCount;
				errorFiles.push_back(filePath);
			}
		}
		catch (const std::exception& ex)
		{
			++failedCount;
			spdlog::error("Error processing file: {} | {}", filePath, ex.what());
			errorFiles.push_back(filePath);
		}
	}

	if (totalRamSampleNum > 0)
	{
		if (!flush_pending_writers(processor, totalRamSampleNum))
			return 1;
	}

	spdlog::info(
		"Finalizing append-only SDS session{}...",
		sortAndDeduplicate ? " with sort/dedup" : "");
	if (!processor.FinalizePendingSessionAppendOnly(sortAndDeduplicate, miniSeedVersion3))
		return 1;

	yfile2miniseed::cli::stats::WriteStats();

	const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
		std::chrono::steady_clock::now() - startTime);
	const double elapsedSeconds = elapsed.count() / 1000.0;
	const double filesPerSecond = elapsedSeconds > 0.0
		? static_cast<double>(processed) / elapsedSeconds
		: 0.0;

	spdlog::info("==================================================");
	spdlog::info("Append Processing Summary");
	spdlog::info("==================================================");
	spdlog::info("Total Files      : {}", files.size());
	spdlog::info("Processed Files  : {}", processed);
	spdlog::info("Successful YFiles: {}", successCount);
	spdlog::info("Failed Items     : {}", failedCount);
	spdlog::info("Elapsed Time     : {:.2f} sec", elapsedSeconds);
	spdlog::info("Processing Speed : {:.2f} file/sec", filesPerSecond);

	if (!errorFiles.empty())
	{
		std::ofstream errFile("error_files.txt");
		for (const auto& item : errorFiles)
			errFile << item << '\n';
		spdlog::warn("Error list saved to: error_files.txt");
	}

	return failedCount == 0 ? 0 : 1;
}
