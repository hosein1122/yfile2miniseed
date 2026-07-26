#include "yfile2miniseed/mseed_processor.hpp"
#include <yfile2miniseed/yfile_reader.hpp>
#include "filesystem/file_discovery.hpp"
#include "logging/app_logger.hpp"
#include "utils/time_utils.hpp"
#include "sid/sid_corrector.hpp"
#include "stats/data_availability_stats.hpp"
#include "utils/zip_utils.hpp"
#include <iostream>
#include <libmseed.h>
#include <vector>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <filesystem>
#include <algorithm>
#include <cctype>

//to do
//1-  can read mseed files (ver 2 and 3) as input file	done. but need to check!!
//2-  can write mseed as version 2 (اختیاری)	done. but need to check !!

using namespace yfile2miniseed;
namespace fs = std::filesystem;

bool miniSeedVersion3 = true;

//using namespace std;
yfile2miniseed::cli::sid::SIDCorrector sidCorrector("CorrectSID.txt");

static bool DoWriteMemoryToDisk(
	yfile2miniseed::MSeedProcessor& processor,
	const std::string& outputDir,
	size_t& TotalRAMSampleNum
) {

	bool anyNewFileWritten = false;

	if (!processor.TraceList_Export_MSeed(outputDir, anyNewFileWritten, miniSeedVersion3))
	{
		spdlog::error("MiniSEED write failed. Keeping in-memory data intact.");
		return false;
	}

	processor.ClearTraceList();

	TotalRAMSampleNum = 0;
	return true;
}


static bool ProcessCurrentY(
	yfile2miniseed::Y5FileReader& reader,
	yfile2miniseed::MSeedProcessor& processor,
	const std::string& outputDir,
	size_t& TotalRAMSampleNum,
	size_t minRamInt
)
{
	yfile2miniseed::cli::sid::SIDCorrector::CorrectedEntry corrected;

	bool ok = sidCorrector.GetCorrectedInteractive(
		(const char*)reader.t1.NetworkID,
		(const char*)reader.t1.StationID.Station,
		(const char*)reader.t1.StationID.Location,
		(const char*)reader.t1.StationID.Channel,
		corrected
	);

	if (!ok)
	{
		// اگر در آینده حالت لغو کاربر را اضافه کردیم، می‌توانی اینجا تصمیم بگیری چه کار کنی
		// فعلاً همیشه true برمی‌گردد
	}

	// حالا SID نهایی را با مقادیر اصلاح‌شده می‌سازیم
	std::string sid = yfile2miniseed::MSeedProcessor::MakeSID(
		corrected.network.c_str(),
		corrected.station.c_str(),
		corrected.location.c_str(),  // اگر خالی باشد، MakeSID یا بعداً SDS آن را به "--" تبدیل می‌کند
		corrected.channel.c_str()
	);


	if (sid == "FDSN:___")
		return false;

	processor.AddNewData(
		sid.c_str(),
		reader.t7.samples,
		(int64_t)reader.t5.NumSamples,
		(int64_t)(reader.t5.StartTime * 1'000'000'000),
		(double)reader.t3.SampleRate
	);


	//yfile2miniseed::cli::stats::debug = true;
	yfile2miniseed::cli::stats::checkNewData(sid, { reader.t5.StartTime,reader.t5.EndTime }, reader.t3.SampleRate);
	//yfile2miniseed::cli::stats::printD();

	TotalRAMSampleNum += reader.t5.NumSamples;

	if (TotalRAMSampleNum > ((size_t)minRamInt * 184'000'000))	//1GB ~ 184'000'000 count sample
	{
		spdlog::info("RAM limit reached. Writing MiniSEED...");
		if (!DoWriteMemoryToDisk(processor, outputDir, TotalRAMSampleNum))
			return false;
	}

	return true;
}




int main(int argc, char* argv[])
{
	yfile2miniseed::logging::init();


	//spdlog::info("Converter started");
	//spdlog::warn("This warning goes to console AND file");
	//spdlog::error("This error also goes to file");

	{//Logo
		std::cout
			<< std::endl
			<< "                       ********************************************************                       " << std::endl
			<< "                       *  Converts Nanometrics Y-File V5 Data to MiniSeed V3  *                       " << std::endl
			<< "                       *                    Version 1.2.0                     *                       " << std::endl
			<< "                       *                  By H.Fahimi @2026                   *                       " << std::endl
			<< "                       ********************************************************                       " << std::endl
			<< std::endl;
	}

	std::string inputDir = "";
	std::string outputDir = "OutPutStore";
	std::string minRam = "2";
	int minRamInt = 2;
	bool showHelp = false;

	// دریافت آرگومان ها
	for (int i = 1; i < argc; ++i) {
		std::string arg = argv[i];

		// دایرکتوری ورودی
		if (i == 1)
			inputDir = arg;

		// دایرکتوری خروجی
		if (arg == "-o") {
			i++;
			if (i < argc)
				outputDir = argv[i];
		}
		// مقدار رم مجاز
		else if (arg == "-R") {
			i++;
			if (i < argc)
			{
				minRam = argv[i];
				minRamInt = stoi(minRam);
			}
		}
		else if (arg == "-V2") {
			miniSeedVersion3 = false;// will be saved as version 2
		}
		else if (arg == "-h") {
			showHelp = true;
		}
	}

	if (argc == 1 || showHelp)
	{
		std::cout
			<< "usage:\n"
			<< "\tHF_Y2MSeed inputDir [-o outputDir] [-R MinRAM]" << std::endl
			<< "\twhere : " << std::endl

			<< "\t  " << std::setw(15) << std::left << "inputDir" << "- input directory of Y - Files(version5.0)" << std::endl

			<< "\t  " << std::setw(15) << std::left << "-o outputDir" << "- optional output path for saving MSeed files there. (default: 'OutPutStore')" << std::endl

			<< "\t  " << std::setw(15) << std::left << "-R MinRAM" << "- optional minimum available RAM size (in GigaByte) for application to use. (default: 2) GigaBytes" << std::endl
			<< "\t  " << std::setw(15) << std::left << "-V2" << "- Using this option, miniSeed will be written as Version 2 othervise it will write as miniSeed Version 3 " << std::endl
			<< "\t  " << std::setw(15) << std::left << "-h" << "- show help" << std::endl
			<< std::endl
			<< "example:\n"
			<< "\tHF_Y2MSeed D:\\inputYFileDir  -o D:\\OutputStore -R 4 \n"
			<< "\tHF_Y2MSeed inputYFileDir  -o OutputStore \n"
			<< "\tHF_Y2MSeed D:\\inputYFileDir\n"
			<< std::endl << std::endl;

		return 0;
	}

	if (inputDir == "")
		return 0;

	std::vector<std::string> files;
	fs::path inputPath(inputDir);
	if (fs::is_regular_file(inputPath))
	{
		files.push_back(inputPath.string());
	}
	else
	{
		FileDiscovery queue;

		// Add the root directory to the queue
		queue.addDirectory(inputDir);
		files = queue.getAllFiles();
	}
	std::vector<std::string> errorFiles = {};

	using clock_type = std::chrono::steady_clock;

	auto startTime = clock_type::now();

	size_t totalFiles = files.size();
	size_t processed = 0;
	size_t successCount = 0;
	size_t failedCount = 0;

	yfile2miniseed::Y5FileReader reader;
	yfile2miniseed::MSeedProcessor processor;

	////-----------------------------------
	////اجرای تست های گوناگون بر روی تابع ComputeOkSeg:
	//processor.SimulationTestComputeOkSeg();
	//processor.PropertyTestComputeOkSeg();
	//processor.TestComputeOkSeg();
	////-----------------------------------

	size_t TotalRAMSampleNum = 0;



	for (const auto& filePath : files)
	{
		processed++;

		spdlog::info(
			"[{}/{}] Processing: {}",
			processed,
			totalFiles,
			filePath
		);

		try
		{

			fs::path p(filePath);
			std::string ext = p.extension().string();

			std::transform(ext.begin(), ext.end(), ext.begin(),
				[](unsigned char c) {
					return std::tolower(c);
				});


			if (ext == ".zip")
			{
				spdlog::info("ZIP detected: {}", filePath);

				if (!yfile2miniseed::cli::ziputils::IsZipFile(filePath))
				{
					spdlog::error("Invalid zip file: {}", filePath);
					failedCount++;
					errorFiles.push_back(filePath);
					continue;
				}

				auto extractedFiles =
					yfile2miniseed::cli::ziputils::ExtractZipToMemory(filePath);

				size_t zipTotal = extractedFiles.size();
				if (zipTotal == 0)
				{
					spdlog::warn("Zip file contains no files: {}", filePath);
					failedCount++;
					continue;
				}

				size_t zipIndex = 0;

				for (const auto& yFile : extractedFiles)
				{
					zipIndex++;

					double filePercent =
						(static_cast<double>(processed) / totalFiles) * 100.0;

					double zipPercent =
						(static_cast<double>(zipIndex) / zipTotal) * 100.0;

					spdlog::info(
						"[{:5.1f}%]/[{:5.1f}%] {}",
						filePercent,
						zipPercent,
						yFile.name
					);


					if (yFile.data.empty())
					{
						spdlog::warn("Empty file inside zip: {}", yFile.name);
						failedCount++;
						continue;
					}

					if (!reader.ReadFromRAM(yFile.data.data(), yFile.data.size()))
					{
						failedCount++;
						errorFiles.push_back(yFile.name);
						continue;
					}


					if (ProcessCurrentY(
						reader,
						processor,
						outputDir,
						TotalRAMSampleNum,
						minRamInt))
					{
						successCount++;
					}
					else
					{
						failedCount++;
						errorFiles.push_back(yFile.name);
					}

				}

				//Do Write After each Zip file!
				spdlog::info("Zip file passed. Writing MiniSEED...");
				if (!DoWriteMemoryToDisk(processor, outputDir, TotalRAMSampleNum))
				{
					errorFiles.push_back(filePath);
					return 1;
				}

			}
			else
			{
				// فایل معمولی
				double filePercent =
					(static_cast<double>(processed) / totalFiles) * 100.0;

				spdlog::info(
					"[{:5.1f}%]/[-----] {}",
					filePercent,
					filePath
				);

				//test for read Y-File
				if (reader.ReadFromFile(filePath))
				{
					if (ProcessCurrentY(
						reader,
						processor,
						outputDir,
						TotalRAMSampleNum,
						minRamInt))
					{
						successCount++;
					}
					else
					{
						failedCount++;
						errorFiles.push_back(filePath);
					}
				}

				//if it is not Y-File test if it is miniseed ver 2 or 3
				else if (processor.ReadMSeed(filePath, TotalRAMSampleNum))
				{
					successCount++;

					//We are NOT reading Y-File, So it will NOT add to Y_Files STATs
					//yfile2miniseed::cli::stats::checkNewData(sid, { reader.t5.StartTime,reader.t5.EndTime }, reader.t3.SampleRate);

					//TotalRAMSampleNum is updated inside the read function
					if (TotalRAMSampleNum > ((size_t)minRamInt * 184'000'000))	//1GB ~ 184'000'000 count sample
					{
						spdlog::info("RAM limit reached. Writing MiniSEED...");
						if (!DoWriteMemoryToDisk(processor, outputDir, TotalRAMSampleNum))
						{
							errorFiles.push_back(filePath);
							return 1;
						}
					}
				}
				else //it is not Y-File or miniseed
				{
					failedCount++;
					errorFiles.push_back(filePath);
					continue;
				}



			}



		}
		catch (const std::exception& ex)
		{
			failedCount++;

			spdlog::error(
				"Error processing file: {} | {}",
				filePath,
				ex.what()
			);

			errorFiles.push_back(filePath);
		}
		catch (...)
		{
			failedCount++;

			spdlog::error(
				"Unknown error processing file: {}",
				filePath
			);

			errorFiles.push_back(filePath);
		}
	}

	if (TotalRAMSampleNum > 0)	//any data existed
	{
		//processor.ShowDataAvailability();

		spdlog::info("Last Writing MiniSEED...");

		if (!DoWriteMemoryToDisk(processor, outputDir, TotalRAMSampleNum))
			return 1;
	}

	//Save Y-File Data Availability beside executed file
	yfile2miniseed::cli::stats::WriteStats();
	//yfile2miniseed::cli::stats::printD(true);

	auto endTime = clock_type::now();

	auto elapsed =
		std::chrono::duration_cast<std::chrono::milliseconds>(
			endTime - startTime
		);

	double elapsedSeconds = elapsed.count() / 1000.0;

	double filesPerSecond = 0.0;

	if (elapsedSeconds > 0.0)
	{
		filesPerSecond =
			static_cast<double>(processed) / elapsedSeconds;
	}

	//چاپ گزارش
	spdlog::info("==================================================");
	spdlog::info("Processing Summary");
	spdlog::info("==================================================");

	spdlog::info("Total Files      : {}", totalFiles);
	spdlog::info("Processed Files  : {}", processed);
	spdlog::info("Successful Files : {}", successCount);
	spdlog::info("Failed Files     : {}", failedCount);

	spdlog::info("Elapsed Time     : {:.2f} sec", elapsedSeconds);
	spdlog::info("Processing Speed : {:.2f} file/sec", filesPerSecond);



	//ذخیره فایلهای خطا دار
	if (!errorFiles.empty())
	{
		std::ofstream errFile("error_files.txt");

		for (const auto& f : errorFiles)
		{
			errFile << f << std::endl;
		}

		errFile.close();

		spdlog::warn("Error list saved to: error_files.txt");

	}



	return failedCount == 0 ? 0 : 1;
};
