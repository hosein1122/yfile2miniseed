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
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

bool miniSeedVersion3 = true;

using SteadyClock = std::chrono::steady_clock;

double elapsed_seconds(SteadyClock::time_point start)
{
	return std::chrono::duration<double>(SteadyClock::now() - start).count();
}

struct CliBenchmarkStats {
	double collectFilesSeconds = 0.0;
	double zipValidateSeconds = 0.0;
	double zipExtractSeconds = 0.0;
	double yReadRamSeconds = 0.0;
	double yReadFileSeconds = 0.0;
	double sidCorrectionSeconds = 0.0;
	double appendYFileSeconds = 0.0;
	double statsUpdateSeconds = 0.0;
	double flushSeconds = 0.0;
	double finalizeSeconds = 0.0;
	double writeStatsSeconds = 0.0;
	uint64_t zipFiles = 0;
	uint64_t zipItems = 0;
	uint64_t zipExtractedBytes = 0;
	uint64_t ramYFilesRead = 0;
	uint64_t fileYFilesRead = 0;
};

bool flush_pending_writers(
	yfile2miniseed::MSeedProcessor& processor,
	CliBenchmarkStats* benchmarkStats = nullptr)
{
	const auto started = SteadyClock::now();
	if (!processor.ClosePendingWriters())
	{
		if (benchmarkStats)
			benchmarkStats->flushSeconds += elapsed_seconds(started);
		spdlog::error("Pending MiniSEED writer flush failed.");
		return false;
	}
	if (benchmarkStats)
		benchmarkStats->flushSeconds += elapsed_seconds(started);

	return true;
}

bool append_current_yfile(
	yfile2miniseed::Y5FileReader& reader,
	yfile2miniseed::MSeedProcessor& processor,
	yfile2miniseed::cli::sid::SIDCorrector& sidCorrector,
	CliBenchmarkStats* benchmarkStats = nullptr)
{
	yfile2miniseed::cli::sid::SIDCorrector::CorrectedEntry corrected;
	const auto sidStarted = SteadyClock::now();
	const bool ok = sidCorrector.GetCorrected(
		reinterpret_cast<const char*>(reader.t1.NetworkID),
		reinterpret_cast<const char*>(reader.t1.StationID.Station),
		reinterpret_cast<const char*>(reader.t1.StationID.Location),
		reinterpret_cast<const char*>(reader.t1.StationID.Channel),
		corrected);
	if (benchmarkStats)
		benchmarkStats->sidCorrectionSeconds += elapsed_seconds(sidStarted);

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

	const auto appendStarted = SteadyClock::now();
	if (!processor.AppendYFileToPendingSession(
		sid.c_str(),
		reader.t7.samples,
		static_cast<int64_t>(reader.t5.NumSamples),
		static_cast<int64_t>(reader.t5.StartTime * 1'000'000'000),
		static_cast<double>(reader.t3.SampleRate),
		miniSeedVersion3))
	{
		if (benchmarkStats)
			benchmarkStats->appendYFileSeconds += elapsed_seconds(appendStarted);
		return false;
	}
	if (benchmarkStats)
		benchmarkStats->appendYFileSeconds += elapsed_seconds(appendStarted);

	const auto statsStarted = SteadyClock::now();
	yfile2miniseed::cli::stats::checkNewData(
		sid,
		{ reader.t5.StartTime, reader.t5.EndTime },
		reader.t3.SampleRate);
	if (benchmarkStats)
		benchmarkStats->statsUpdateSeconds += elapsed_seconds(statsStarted);

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
		<< "\tyfile2mseed_append inputPath [-o outputDir] [-V2] [--sort-dedup] [--benchmark] [--workers N]\n"
		<< "\n"
		<< "\tinputPath      Input directory or file containing Y-Files or ZIPs\n"
		<< "\t-o outputDir   Output SDS root. Default: OutPutStore\n"
		<< "\t-V2            Write MiniSEED version 2 instead of version 3\n"
		<< "\t--sort-dedup   After append, rewrite changed SDS files once for sorting and once for duplicate removal\n"
		<< "\t--benchmark    Print stage-level timing statistics\n"
		<< "\t--workers N    Parallel SDS finalize workers. Default: 1\n"
		<< "\t-h             Show help\n";
}

void print_benchmark(
	const CliBenchmarkStats& cliStats,
	const yfile2miniseed::MSeedProcessor::AppendSessionStats& processorStats)
{
	const double pendingMb = static_cast<double>(processorStats.pendingBytes) / (1024.0 * 1024.0);
	const double zipMb = static_cast<double>(cliStats.zipExtractedBytes) / (1024.0 * 1024.0);

	spdlog::info("==================================================");
	spdlog::info("Append Benchmark");
	spdlog::info("==================================================");
	spdlog::info("CLI collect files        : {:.6f} sec", cliStats.collectFilesSeconds);
	spdlog::info("CLI zip validate         : {:.6f} sec", cliStats.zipValidateSeconds);
	spdlog::info("CLI zip extract          : {:.6f} sec ({} items, {:.2f} MiB)", cliStats.zipExtractSeconds, cliStats.zipItems, zipMb);
	spdlog::info("CLI Y read from RAM      : {:.6f} sec ({} files)", cliStats.yReadRamSeconds, cliStats.ramYFilesRead);
	spdlog::info("CLI Y read from file     : {:.6f} sec ({} files)", cliStats.yReadFileSeconds, cliStats.fileYFilesRead);
	spdlog::info("CLI SID correction       : {:.6f} sec", cliStats.sidCorrectionSeconds);
	spdlog::info("CLI append YFile         : {:.6f} sec", cliStats.appendYFileSeconds);
	spdlog::info("CLI availability stats   : {:.6f} sec", cliStats.statsUpdateSeconds);
	spdlog::info("CLI close/flush writers  : {:.6f} sec", cliStats.flushSeconds);
	spdlog::info("CLI finalize session     : {:.6f} sec", cliStats.finalizeSeconds);
	spdlog::info("CLI write stats report   : {:.6f} sec", cliStats.writeStatsSeconds);
	spdlog::info("Processor pack           : {:.6f} sec ({} records)", processorStats.packSeconds, processorStats.packedRecords);
	spdlog::info("Processor route records  : {:.6f} sec", processorStats.recordRouteSeconds);
	spdlog::info("Processor pending open   : {:.6f} sec ({} files)", processorStats.pendingOpenSeconds, processorStats.pendingFiles);
	spdlog::info("Processor pending write  : {:.6f} sec ({} records, {:.2f} MiB)", processorStats.pendingWriteSeconds, processorStats.pendingRecords, pendingMb);
	spdlog::info("Processor pending close  : {:.6f} sec", processorStats.pendingCloseSeconds);
	spdlog::info("Processor validate       : {:.6f} sec ({} validations)", processorStats.validateSeconds, processorStats.validations);
	spdlog::info("Processor commit copy    : {:.6f} sec ({} existing files)", processorStats.commitCopySeconds, processorStats.copiedExistingFiles);
	spdlog::info("Processor commit append  : {:.6f} sec", processorStats.commitAppendSeconds);
	spdlog::info("Processor commit rename  : {:.6f} sec", processorStats.commitRenameSeconds);
	spdlog::info("Processor committed files: {} created, {} total", processorStats.createdFiles, processorStats.committedFiles);
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
	bool showHelp = false;
	bool sortAndDeduplicate = false;
	bool benchmark = false;
	size_t workers = 1;

	for (int i = 1; i < argc; ++i)
	{
		const std::string arg = argv[i];
		if ((arg == "-o" || arg == "--output") && i + 1 < argc)
		{
			outputDir = argv[++i];
		}
		else if (arg == "-R")
		{
			spdlog::error("-R was removed from yfile2mseed_append; append batching is managed internally.");
			return 2;
		}
		else if (arg == "-V2")
		{
			miniSeedVersion3 = false;
		}
		else if (arg == "--sort-dedup")
		{
			sortAndDeduplicate = true;
		}
		else if (arg == "--benchmark")
		{
			benchmark = true;
		}
		else if (arg == "--workers" && i + 1 < argc)
		{
			workers = std::max<size_t>(1, static_cast<size_t>(std::stoull(argv[++i])));
		}
		else if (arg == "--workers")
		{
			spdlog::error("--workers requires a positive integer value.");
			return 2;
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

	CliBenchmarkStats benchmarkStats;
	const auto collectStarted = SteadyClock::now();
	const std::vector<std::string> files = collect_files(inputPath);
	benchmarkStats.collectFilesSeconds += elapsed_seconds(collectStarted);
	std::vector<std::string> errorFiles;
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
				benchmarkStats.zipFiles++;
				const auto zipValidateStarted = SteadyClock::now();
				const bool isZipFile = yfile2miniseed::cli::ziputils::IsZipFile(filePath);
				benchmarkStats.zipValidateSeconds += elapsed_seconds(zipValidateStarted);
				if (!isZipFile)
				{
					++failedCount;
					errorFiles.push_back(filePath);
					continue;
				}

				const auto zipExtractStarted = SteadyClock::now();
				const auto extractedFiles = yfile2miniseed::cli::ziputils::ExtractZipToMemory(filePath);
				benchmarkStats.zipExtractSeconds += elapsed_seconds(zipExtractStarted);
				benchmarkStats.zipItems += extractedFiles.size();
				for (const auto& yFile : extractedFiles)
					benchmarkStats.zipExtractedBytes += yFile.data.size();

				size_t zipIndex = 0;
				for (const auto& yFile : extractedFiles)
				{
					++zipIndex;
					spdlog::info("[{}/{}] ZIP item: {}", zipIndex, extractedFiles.size(), yFile.name);

					bool readOk = false;
					if (!yFile.data.empty())
					{
						const auto yReadStarted = SteadyClock::now();
						readOk = reader.ReadFromRAM(yFile.data.data(), yFile.data.size());
						benchmarkStats.yReadRamSeconds += elapsed_seconds(yReadStarted);
						if (readOk)
							benchmarkStats.ramYFilesRead++;
					}

					if (yFile.data.empty() || !readOk)
					{
						++failedCount;
						errorFiles.push_back(yFile.name);
						continue;
					}

					if (append_current_yfile(reader, processor, sidCorrector, &benchmarkStats))
						++successCount;
					else
					{
						++failedCount;
						errorFiles.push_back(yFile.name);
					}
				}

				if (!flush_pending_writers(processor, &benchmarkStats))
					return 1;
			}
			else
			{
				const auto yReadStarted = SteadyClock::now();
				const bool readOk = reader.ReadFromFile(filePath);
				benchmarkStats.yReadFileSeconds += elapsed_seconds(yReadStarted);
				if (readOk)
					benchmarkStats.fileYFilesRead++;

				if (readOk && append_current_yfile(reader, processor, sidCorrector, &benchmarkStats))
					++successCount;
				else if (readOk)
				{
					++failedCount;
					errorFiles.push_back(filePath);
				}
				else
				{
					++failedCount;
					errorFiles.push_back(filePath);
				}
			}
		}
		catch (const std::exception& ex)
		{
			++failedCount;
			spdlog::error("Error processing file: {} | {}", filePath, ex.what());
			errorFiles.push_back(filePath);
		}
	}

	spdlog::info(
		"Finalizing append-only SDS session{} with {} worker(s)...",
		sortAndDeduplicate ? " with sort/dedup" : "",
		workers);
	const auto finalizeStarted = SteadyClock::now();
	if (!processor.FinalizePendingSessionAppendOnly(sortAndDeduplicate, miniSeedVersion3, workers))
		return 1;
	benchmarkStats.finalizeSeconds += elapsed_seconds(finalizeStarted);

	const auto writeStatsStarted = SteadyClock::now();
	yfile2miniseed::cli::stats::WriteStats();
	benchmarkStats.writeStatsSeconds += elapsed_seconds(writeStatsStarted);

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

	if (benchmark)
		print_benchmark(benchmarkStats, processor.GetAppendSessionStats());

	if (!errorFiles.empty())
	{
		std::ofstream errFile("error_files.txt");
		for (const auto& item : errorFiles)
			errFile << item << '\n';
		spdlog::warn("Error list saved to: error_files.txt");
	}

	return failedCount == 0 ? 0 : 1;
}
