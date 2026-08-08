
#include <yfile2miniseed/mseed_processor.hpp>

#include <spdlog/spdlog.h>
#include <libmseed.h>
#include <chrono>
#include <iostream>
#include <fstream>
#include <filesystem>
#include <map>
#include <vector>
#include <cstring>
#include <string>
#include <cmath>
#include <random>
#include <cstdlib>
#include <cstdint>
#include <algorithm>
#include <cstdio>

namespace yfile2miniseed {

	namespace {
		struct PendingPackContext
		{
			MSeedProcessor* self = nullptr;
			bool ok = true;
		};
	}

	// constructor
	MSeedProcessor::MSeedProcessor() {
		msr = msr3_init(nullptr);
		if (!msr) {
			spdlog::critical("Failed to initialize MS3Record.");
			throw std::runtime_error("MS3Record initialization failed");
		}

		mstl = mstl3_init(nullptr);
		if (!mstl) {
			spdlog::critical("Failed to initialize MS3TraceList.");
			msr3_free(&msr);   // ✅ جلوگیری از leak
			throw std::runtime_error("MS3TraceList initialization failed");
		}

		spdlog::info("MSeedProcessor initialized successfully.");
	}

	// destructor
	MSeedProcessor::~MSeedProcessor() {
		if (msr) msr3_free(&msr);
		if (mstl) mstl3_free(&mstl, 1);

		// حتی اگر spdlog خطا بدهد، catch می‌کنیم تا استثنا بالا نرود
		try {
			spdlog::info("MSeedProcessor resources released.");
		}
		catch (...) {
			// هیچ کاری نمی‌کنیم، فقط نمی‌ذاریم exception بیرون بره
		}
	}

	// ====================
	// Public functions
	// ====================


	void MSeedProcessor::PrintTraceList(bool printData) const {
		if (!mstl) {
			spdlog::error("TraceList is null!");
			return;
		}

		MS3TraceID* tid = mstl->traces.next[0];
		char starttimestr[32], endtimestr[32];

		while (tid) {
			ms_nstime2timestr_n(tid->earliest, starttimestr, sizeof(starttimestr), ISOMONTHDAY_Z, MICRO);
			ms_nstime2timestr_n(tid->latest, endtimestr, sizeof(endtimestr), ISOMONTHDAY_Z, MICRO);

			spdlog::info("TraceID: {} | Date: [{} ~ {}] | Segments: {}",
				tid->sid, starttimestr, endtimestr, tid->numsegments);

			for (MS3TraceSeg* seg = tid->first; seg != nullptr; seg = seg->next) {
				ms_nstime2timestr_n(seg->starttime, starttimestr, sizeof(starttimestr), ISOMONTHDAY_Z, NANO);
				ms_nstime2timestr_n(seg->endtime, endtimestr, sizeof(endtimestr), ISOMONTHDAY_Z, NANO);

				spdlog::info("  Segment: {} - {}, Samples: {}, Rate: {:.2f}Hz", // دقت اعشار برای نرخ نمونه‌برداری
					starttimestr, endtimestr, seg->samplecnt, seg->samprate);

				// بخش نمایش Recordlist
				if (seg->recordlist) {
					for (MS3RecordPtr* rec = seg->recordlist->first; rec; rec = rec->next) {
						spdlog::debug("    RECORD: File: {}, Offset: {}",
							rec->filename ? rec->filename : "NULL", rec->fileoffset);
					}
				}

				// بخش Unpack و چاپ داده‌ها (در صورت نیاز)
				if (printData) {
					// استفاده از متد داخلی libmseed برای آنپک
					int64_t unpacked = mstl3_unpack_recordlist(tid, seg, nullptr, 0, 0);
					if (unpacked == seg->samplecnt) {
						PrintSamples(tid, seg);
					}
					else {
						spdlog::error("Failed to unpack data for segment.");
					}
				}
			}
			tid = tid->next[0];
		}
	}

	std::string MSeedProcessor::MakeSID(const char* network, const char* station, const char* location, const char* channel) {
		char sid[LM_SIDLEN]; // حجم مشخص‌شده توسط libmseed
		ms_nslc2sid(sid, sizeof(sid), 0, network, station, location, channel);
		return std::string(sid);
	}

	void MSeedProcessor::AddNewData(
		const char* sid,
		const int32_t* data, int64_t numsamples, const char* startTimeStr,
		double samprate)
	{
		int64_t startTime = ms_timestr2nstime(startTimeStr);

		AddNewDataTo(
			mstl,
			sid,
			data, numsamples, startTime,
			samprate
		);
	}

	void MSeedProcessor::AddNewData(
		const char* sid,
		const int32_t* data, int64_t numsamples, int64_t startTime,
		double samprate)
	{
		AddNewDataTo(
			mstl,
			sid,
			data, numsamples, startTime,
			samprate
		);
	}

	void MSeedProcessor::ShowDataAvailability(bool showGapDetails)
	{
		if (!mstl)
		{
			spdlog::warn("ShowDataAvailability(): TraceList is null — nothing to display.");
			return;
		}

		spdlog::info("=== TraceList Summary ===");

		// چاپ ساختار کلی trace list
		mstl3_printtracelist(
			mstl,
			ISOMONTHDAY,   // قالب نمایش زمان
			1,             // شامل مفصل‌ترین جزئیات (یک خط در هر trace)
			1,             // چاپ داده‌ها در یک خط کلی
			verbose        // سطح جزئیات بر اساس تنظیم کلاس
		);

		if (!showGapDetails)
			return;

		spdlog::info("=== Gap/Overlap Details ===");

		mstl3_printgaplist(
			mstl,
			ISOMONTHDAY,   // فرمت زمان مشابه بالا
			NULL, NULL     // همهٔ traceها
		);

		spdlog::info("===========================\n");

	}

	bool MSeedProcessor::ReadMSeed(const std::string& inputFile, size_t& TotalSampleNum)
	{
		if (!ReadMSeedTo(inputFile, mstl))
			return false;

		//ShowDataAvailability();

		TotalSampleNum = 0;

		//loop in mstl for all of its sample numbers
		for (MS3TraceID* tid = mstl->traces.next[0]; tid; tid = tid->next[0])
		{
			for (MS3TraceSeg* seg = tid->first; seg; seg = seg->next)
			{
				TotalSampleNum += seg->numsamples;
			}
		}

		return true;
	}

	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	int64_t MSeedProcessor::WriteMSeed(const std::string& outputFile, bool miniSeedVersion3)
	{
		if (!mstl) {
			spdlog::error("TraceList is null, nothing to write");
			return -1;
		}

		if (outputFile.empty()) {
			spdlog::error("Output filename is empty");
			return -1;
		}

		uint32_t flags = MSF_FLUSHDATA;
		if (!miniSeedVersion3)		//if version 2 is selected so the flag shuld be updated here!!
			flags |= MSF_PACKVER2;	//pack as miniseed version 2

		int64_t packedrecords = mstl3_writemseed(
			mstl,
			outputFile.c_str(),
			1,              // overwrite existing file
			reclen,
			encoding,
			flags,
			verbose
		);

		if (packedrecords < 0)
		{
			spdlog::error(
				"mstl3_writemseed() failed for '{}' (code={})",
				outputFile,
				packedrecords
			);
			return packedrecords;
		}

		if (packedrecords == 0)
		{
			spdlog::warn(
				"No MiniSEED records written to '{}'",
				outputFile
			);
			return 0;
		}

		spdlog::info(
			"Successfully wrote {} MiniSEED records to '{}'",
			packedrecords,
			outputFile
		);

		if (!RepackMSeedFileOnce(outputFile, miniSeedVersion3))
			return -1;

		return packedrecords;
	}



	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	bool MSeedProcessor::TraceList_Export_MSeed(const std::string& BasePath, bool& anyNewFileWritten, bool miniSeedVersion3)
	{
		if (!mstl) return false;

		anyNewFileWritten = false;
		const nstime_t DAY_NS = 86400000000000LL; // نانوثانیه در یک روز


		// پیمایش Traceها
		for (MS3TraceID* tid = mstl->traces.next[0]; tid; tid = tid->next[0])
		{
			spdlog::info("Processing SID: '{}'", tid->sid);

			nstime_t maxHalfSampleNS = 0;
			for (MS3TraceSeg* seg = tid->first; seg != nullptr; seg = seg->next)
			{
				if (seg->samprate > 0.0)
				{
					nstime_t dT = (nstime_t)(NSTMODULUS / seg->samprate + 0.5);
					if ((dT / 2) > maxHalfSampleNS)
						maxHalfSampleNS = dT / 2;
				}
			}

			nstime_t startDayNS = ((tid->earliest - maxHalfSampleNS) / DAY_NS) * DAY_NS;
			nstime_t endDayNS = ((tid->latest + maxHalfSampleNS) / DAY_NS) * DAY_NS;

			// بهینه‌سازی: نگه داشتن آخرین سگمنت بررسی شده برای شروع سریع‌تر در روز بعد
			MS3TraceSeg* startSegForDay = tid->first;

			for (nstime_t currentDay = startDayNS; currentDay <= endDayNS; currentDay += DAY_NS)
			{
				nstime_t nextDay = currentDay + DAY_NS;

				bool recordAddedForThisDay = false;
				int64_t packedrecordsForThisDay = 0;

				std::string mseedPath, mseedFile;
				GetDirectoryAndFileName(mseedPath, mseedFile, currentDay, tid->sid, BasePath);

				// پیمایش سگمنت‌ها با شروع از نقطه‌ی بهینه
				MS3TraceSeg* seg = startSegForDay;

				for (; seg; seg = seg->next)
				{
					// ۱. اگر سگمنت قبل از امروز تمام شده باشد، در روزهای بعد هم به درد نمی‌خورد
					if (seg->endtime < currentDay)
					{
						startSegForDay = seg->next;
						continue;
					}

					// ۲. اگر سگمنت بعد از امروز شروع شود، برای امروز و سگمنت‌های بعدی امروز دیتایی ندارد
					if (seg->starttime >= nextDay)
					{
						// این سگمنت می‌تواند نقطه شروع خوبی برای روز بعد باشد
						startSegForDay = seg;
						break;
					}

					// ۳. لود کردن فایل قدیمی (فقط یکبار در صورت نیاز)
					if (!std::filesystem::exists(mseedPath))
					{
						if (!CreateDirectoryIfNotExists(mseedPath))
						{
							spdlog::error("Failed to create output directory '{}'", mseedPath);
							return false;
						}
					}

					// ۴. استخراج دیتای مربوط به این روز
					DataRange segDay{};
					BuildSegDay(seg, currentDay, nextDay, segDay);
					if (segDay.numsamples > 0)
					{
						// Preserve overlapping input segments as distinct MiniSEED records.
						// Packing through MS3TraceList treats data as one time series and
						// can absorb a shared boundary sample into a neighboring segment.
						int64_t packedrecords = WriteDataRangeToMSeed(
							mseedFile,
							tid->sid,
							segDay,
							seg->samprate,
							!recordAddedForThisDay,
							miniSeedVersion3
						);
						if (packedrecords < 0)
						{
							spdlog::error(
								"msr3_writemseed() failed for '{}' (code={})",
								mseedFile,
								packedrecords
							);
							return false;
						}

						packedrecordsForThisDay += packedrecords;
						recordAddedForThisDay = true;
					}

					//اگر سگمنت فعلی بزرگتر از محدوده روز جاری است. نیازی نیست به سگمنت بعدی برویم
					// تمام داده امروز را برداشته ایم. فردا بقیه داده را از همین سگمنت میخوانیم
					//در روز بعد هم باید از همین سگمنت شروع کنیم
					if (seg->endtime >= nextDay)
					{
						startSegForDay = seg;
						break;
					}
				}

				// ذخیره‌سازی در صورت تغییر
				if (recordAddedForThisDay)
				{
					if (packedrecordsForThisDay == 0)
					{
						spdlog::warn("No MiniSEED records written to '{}'", mseedFile);
					}
					else
					{
						spdlog::info("Wrote {} records to '{}'", packedrecordsForThisDay, mseedFile);
						anyNewFileWritten = true;
					}
				}


			}
		}

		return true;
	}

	void MSeedProcessor::ClearTraceList()
	{
		if (mstl)
		{
			mstl3_free(&mstl, 1);
			mstl = mstl3_init(NULL);
			//mstl = mstl3_init(nullptr);

		}
	}

	bool MSeedProcessor::BeginPendingSession(const std::string& sdsRoot)
	{
		namespace fs = std::filesystem;

		try
		{
			pendingSdsRoot = fs::absolute(fs::path(sdsRoot));
			const auto ticks = std::chrono::duration_cast<std::chrono::milliseconds>(
				std::chrono::system_clock::now().time_since_epoch()).count();
			pendingSessionName = "session-" + std::to_string(ticks);
			pendingSessionPath = pendingSdsRoot / ".yfile2mseed-session" / pendingSessionName;
			fs::create_directories(pendingSessionPath);
			pendingFilesByFinal.clear();
			pendingWriters.clear();
			pendingWriterClock = 0;
			return true;
		}
		catch (const std::exception& ex)
		{
			spdlog::error("BeginPendingSession failed: {}", ex.what());
			return false;
		}
	}

	bool MSeedProcessor::AppendYFileToPendingSession(
		const char* sid,
		const int32_t* data,
		int64_t numsamples,
		int64_t startTime,
		double samprate,
		bool miniSeedVersion3)
	{
		if (pendingSessionPath.empty())
		{
			spdlog::error("Pending session was not initialized");
			return false;
		}
		if (!sid || !data || numsamples <= 0 || samprate <= 0.0)
		{
			spdlog::error("AppendYFileToPendingSession(): invalid parameters");
			return false;
		}

		msr->datasamples = nullptr;
		msr->numsamples = 0;
		msr->samplecnt = 0;

		strncpy_s(msr->sid, sizeof(msr->sid), sid, _TRUNCATE);
		msr->reclen = reclen;
		msr->pubversion = 1;
		msr->samprate = samprate;
		msr->encoding = encoding;
		msr->sampletype = 'i';
		msr->datasamples = const_cast<int32_t*>(data);
		msr->numsamples = numsamples;
		msr->samplecnt = numsamples;
		msr->starttime = startTime;

		PendingPackContext context{ this, true };

		uint32_t flags = MSF_FLUSHDATA;
		if (!miniSeedVersion3)
			flags |= MSF_PACKVER2;

		int64_t packedSamples = 0;
		const int64_t packedRecords = msr3_pack(
			msr,
			&MSeedProcessor::PendingRecordHandler,
			&context,
			&packedSamples,
			flags,
			verbose);

		msr->datasamples = nullptr;
		msr->numsamples = 0;
		msr->samplecnt = 0;

		if (packedRecords < 0)
		{
			spdlog::error("msr3_pack() failed for SID '{}'", sid);
			ClosePendingWriters();
			return false;
		}
		if (!context.ok)
		{
			spdlog::error("Writing pending records failed for SID '{}'", sid);
			ClosePendingWriters();
			return false;
		}
		if (packedSamples != numsamples)
		{
			spdlog::error(
				"Packed sample count mismatch for SID '{}': {} != {}",
				sid,
				packedSamples,
				numsamples);
			ClosePendingWriters();
			return false;
		}

		return true;
	}

	bool MSeedProcessor::AppendMSeedFileToPendingSession(const std::string& inputFile)
	{
		if (pendingSessionPath.empty())
		{
			spdlog::error("Pending session was not initialized");
			return false;
		}

		MS3FileParam* msfp = nullptr;
		MS3Record* record = nullptr;
		constexpr uint32_t flags = MSF_VALIDATECRC;
		int retcode = MS_NOERROR;
		bool ok = true;

		while ((retcode = ms3_readmsr_r(&msfp, &record, inputFile.c_str(), flags, verbose)) == MS_NOERROR)
		{
			if (!record || !record->record || record->reclen <= 0)
			{
				spdlog::error("Invalid MiniSEED record while reading '{}'", inputFile);
				ok = false;
				break;
			}
			if (!WritePendingRecord(record->record, record->reclen))
			{
				ok = false;
				break;
			}
		}

		ms3_readmsr_r(&msfp, &record, nullptr, 0, 0);

		if (retcode != MS_ENDOFFILE && retcode != MS_NOERROR)
		{
			spdlog::error("Cannot read MiniSEED '{}': {}", inputFile, ms_errorstr(retcode));
			ok = false;
		}

		if (!ok)
			ClosePendingWriters();

		return ok;
	}

	bool MSeedProcessor::ClosePendingWriters()
	{
		bool ok = true;
		for (auto& item : pendingWriters)
		{
			item.second.stream.flush();
			if (!item.second.stream)
			{
				spdlog::error("Failed to flush pending file '{}'", item.second.path.string());
				ok = false;
			}
			item.second.stream.close();
			if (!item.second.stream)
			{
				spdlog::error("Failed to close pending file '{}'", item.second.path.string());
				ok = false;
			}
		}
		pendingWriters.clear();
		return ok;
	}

	bool MSeedProcessor::FinalizePendingSession(bool miniSeedVersion3)
	{
		if (pendingSessionPath.empty())
			return true;

		if (!ClosePendingWriters())
			return false;

		for (const auto& item : pendingFilesByFinal)
		{
			const std::filesystem::path finalPath(item.first);
			const std::filesystem::path& pendingPath = item.second;

			if (!std::filesystem::exists(pendingPath))
			{
				spdlog::warn("Pending file not found, skipping '{}'", pendingPath.string());
				continue;
			}

			if (!CommitOnePendingFile(finalPath, pendingPath, miniSeedVersion3))
				return false;
		}

		CleanupPendingSessionIfEmpty();
		return true;
	}

	bool MSeedProcessor::FinalizePendingSessionAppendOnly(bool sortAndDeduplicate, bool miniSeedVersion3)
	{
		if (pendingSessionPath.empty())
			return true;

		if (!ClosePendingWriters())
			return false;

		std::vector<std::filesystem::path> committedFiles;
		for (const auto& item : pendingFilesByFinal)
		{
			const std::filesystem::path finalPath(item.first);
			const std::filesystem::path& pendingPath = item.second;

			if (!std::filesystem::exists(pendingPath))
			{
				spdlog::warn("Pending file not found, skipping '{}'", pendingPath.string());
				continue;
			}

			if (!CommitOnePendingFileAppendOnly(finalPath, pendingPath))
				return false;

			committedFiles.push_back(finalPath);
		}

		if (sortAndDeduplicate)
		{
			constexpr uint32_t sortReadFlags = MSF_VALIDATECRC | MSF_UNPACKDATA;
			constexpr uint32_t dedupReadFlags = MSF_VALIDATECRC | MSF_UNPACKDATA | MSF_SKIPADJACENTDUPLICATES;
			for (const auto& finalPath : committedFiles)
			{
				if (!RewriteFinalFileFromTraceList(finalPath, sortReadFlags, "Sort appended SDS", miniSeedVersion3))
					return false;
				if (!RewriteFinalFileFromTraceList(finalPath, dedupReadFlags, "Deduplicate appended SDS", miniSeedVersion3))
					return false;
			}
		}

		CleanupPendingSessionIfEmpty();
		return true;
	}


	// ====================
	// Private functions
	// ====================
	bool MSeedProcessor::BuildStrictSDSPath(
		std::filesystem::path& outPath,
		nstime_t startDate,
		const char sid[LM_SIDLEN],
		const std::filesystem::path& basePath)
	{
		uint16_t year = 0;
		uint16_t yday = 0;
		uint8_t hour = 0;
		uint8_t min = 0;
		uint8_t sec = 0;
		uint32_t nsec = 0;

		char network[11]{};
		char station[11]{};
		char location[11]{};
		char channel[31]{};

		ms_nstime2time(startDate, &year, &yday, &hour, &min, &sec, &nsec);
		if (year == 0 || yday == 0)
			return false;

		if (ms_sid2nslc_n(
			sid,
			network, sizeof(network),
			station, sizeof(station),
			location, sizeof(location),
			channel, sizeof(channel)) != 0)
		{
			return false;
		}

		if (network[0] == '\0' || station[0] == '\0' || channel[0] == '\0')
			return false;

		std::filesystem::path path(basePath);
		path /= std::to_string(year);
		path /= network;
		path /= station;
		path /= std::string(channel) + ".D";

		char fileName[256]{};
		const int n = _snprintf_s(
			fileName,
			sizeof(fileName),
			_TRUNCATE,
			"%s.%s.%s.%s.D.%u.%03u",
			network,
			station,
			location,
			channel,
			static_cast<unsigned>(year),
			static_cast<unsigned>(yday));

		if (n < 0 || static_cast<size_t>(n) >= sizeof(fileName))
			return false;

		outPath = path / fileName;
		return true;
	}

	void MSeedProcessor::PendingRecordHandler(char* record, int recordLength, void* handlerdata)
	{
		auto* context = static_cast<PendingPackContext*>(handlerdata);
		if (!context || !context->self || !context->ok)
			return;

		if (!context->self->WritePendingRecord(record, recordLength))
			context->ok = false;
	}

	bool MSeedProcessor::WritePendingRecord(const char* record, int recordLength)
	{
		if (!record || recordLength <= 0)
		{
			spdlog::error("WritePendingRecord(): invalid record");
			return false;
		}

		MS3Record* parsed = nullptr;
		const int parseCode = msr3_parse(record, static_cast<uint64_t>(recordLength), &parsed, 0, verbose);
		if (parseCode != MS_NOERROR || !parsed)
		{
			spdlog::error("msr3_parse() failed for generated record: {}", ms_errorstr(parseCode));
			msr3_free(&parsed);
			return false;
		}

		std::filesystem::path finalPath;
		if (!BuildStrictSDSPath(finalPath, parsed->starttime, parsed->sid, pendingSdsRoot))
		{
			spdlog::error("Cannot build SDS path for SID '{}'", parsed->sid);
			msr3_free(&parsed);
			return false;
		}
		msr3_free(&parsed);

		PendingWriter* writer = nullptr;
		if (!OpenPendingWriter(finalPath, writer))
			return false;

		writer->stream.write(record, recordLength);
		if (!writer->stream)
		{
			spdlog::error("Failed to write pending record to '{}'", writer->path.string());
			return false;
		}

		return true;
	}

	bool MSeedProcessor::OpenPendingWriter(const std::filesystem::path& finalPath, PendingWriter*& writer)
	{
		const std::string key = finalPath.string();
		auto found = pendingWriters.find(key);
		if (found != pendingWriters.end())
		{
			found->second.lastUsed = ++pendingWriterClock;
			writer = &found->second;
			return true;
		}

		if (pendingWriters.size() >= maxOpenPendingWriters)
		{
			auto victim = std::min_element(
				pendingWriters.begin(),
				pendingWriters.end(),
				[](const auto& lhs, const auto& rhs) {
					return lhs.second.lastUsed < rhs.second.lastUsed;
				});

			if (victim != pendingWriters.end())
			{
				victim->second.stream.flush();
				if (!victim->second.stream)
				{
					spdlog::error("Failed to flush LRU pending writer '{}'", victim->second.path.string());
					return false;
				}
				victim->second.stream.close();
				if (!victim->second.stream)
				{
					spdlog::error("Failed to close LRU pending writer '{}'", victim->second.path.string());
					return false;
				}
				pendingWriters.erase(victim);
			}
		}

		const std::filesystem::path pendingPath = PendingPathForFinal(finalPath);
		try
		{
			std::filesystem::create_directories(pendingPath.parent_path());
		}
		catch (const std::exception& ex)
		{
			spdlog::error("Cannot create pending directory '{}': {}", pendingPath.parent_path().string(), ex.what());
			return false;
		}

		PendingWriter newWriter;
		newWriter.path = pendingPath;
		newWriter.lastUsed = ++pendingWriterClock;
		newWriter.stream.open(pendingPath, std::ios::binary | std::ios::app);
		if (!newWriter.stream)
		{
			spdlog::error("Cannot open pending file '{}'", pendingPath.string());
			return false;
		}

		pendingFilesByFinal[key] = pendingPath;
		auto inserted = pendingWriters.emplace(key, std::move(newWriter));
		writer = &inserted.first->second;
		return true;
	}

	std::filesystem::path MSeedProcessor::PendingPathForFinal(const std::filesystem::path& finalPath) const
	{
		std::error_code ec;
		std::filesystem::path relative = std::filesystem::relative(finalPath, pendingSdsRoot, ec);
		if (ec)
			relative = finalPath.filename();

		std::filesystem::path pendingPath = pendingSessionPath / relative;
		pendingPath.replace_filename(pendingPath.filename().string() + ".pending");
		return pendingPath;
	}

	bool MSeedProcessor::ReadMSeedTo(
		const std::string& inputFile,
		MS3TraceList*& outMstl,
		uint32_t flags,
		const char* label)
	{
		if (inputFile.empty())
		{
			spdlog::error("{}: input filename is empty", label ? label : "ReadMSeedTo");
			return false;
		}

		const int retcode = ms3_readtracelist(
			&outMstl,
			inputFile.c_str(),
			nullptr,
			0,
			flags,
			verbose);

		if (retcode != MS_NOERROR)
		{
			spdlog::error("{}: cannot read '{}': {}", label ? label : "ReadMSeedTo", inputFile, ms_errorstr(retcode));
			return false;
		}

		return true;
	}

	bool MSeedProcessor::WriteTraceListFile(
		MS3TraceList* traceList,
		const std::filesystem::path& path,
		bool miniSeedVersion3)
	{
		if (!traceList)
			return false;

		try
		{
			std::filesystem::create_directories(path.parent_path());
		}
		catch (const std::exception& ex)
		{
			spdlog::error("Cannot create output directory '{}': {}", path.parent_path().string(), ex.what());
			return false;
		}

		uint32_t flags = MSF_FLUSHDATA;
		if (!miniSeedVersion3)
			flags |= MSF_PACKVER2;

		const int64_t records = mstl3_writemseed(
			traceList,
			path.string().c_str(),
			1,
			reclen,
			encoding,
			flags,
			verbose);

		if (records <= 0)
		{
			spdlog::error("mstl3_writemseed() failed for '{}' (records={})", path.string(), records);
			return false;
		}

		return true;
	}

	bool MSeedProcessor::ValidateMSeedFile(const std::filesystem::path& path) const
	{
		MS3TraceList* validateList = nullptr;
		constexpr uint32_t flags = MSF_VALIDATECRC | MSF_UNPACKDATA;
		const int retcode = ms3_readtracelist(
			&validateList,
			path.string().c_str(),
			nullptr,
			0,
			flags,
			verbose);
		mstl3_free(&validateList, 1);

		if (retcode != MS_NOERROR)
		{
			spdlog::error("Validation failed for '{}': {}", path.string(), ms_errorstr(retcode));
			return false;
		}

		return true;
	}

	bool MSeedProcessor::ResolveStaleCommitState(const std::filesystem::path& finalPath) const
	{
		namespace fs = std::filesystem;
		const fs::path parent = finalPath.parent_path();
		const std::string baseName = finalPath.filename().string();
		std::vector<fs::path> backups;
		std::vector<fs::path> buildings;

		if (fs::exists(parent))
		{
			for (const auto& entry : fs::directory_iterator(parent))
			{
				if (!entry.is_regular_file())
					continue;
				const std::string name = entry.path().filename().string();
				if (name.rfind(baseName + ".", 0) != 0)
					continue;
				if (name.size() >= 7 && name.substr(name.size() - 7) == ".backup")
					backups.push_back(entry.path());
				else if (name.size() >= 9 && name.substr(name.size() - 9) == ".building")
					buildings.push_back(entry.path());
			}
		}

		if (!backups.empty())
		{
			if (!fs::exists(finalPath) && backups.size() == 1 && buildings.empty())
			{
				std::error_code ec;
				fs::rename(backups.front(), finalPath, ec);
				if (ec)
				{
					spdlog::error("Cannot restore stale backup '{}' to '{}': {}", backups.front().string(), finalPath.string(), ec.message());
					return false;
				}
				spdlog::warn("Restored stale backup '{}'", finalPath.string());
				return true;
			}

			spdlog::error("Stale backup exists for '{}'. Resolve it before continuing.", finalPath.string());
			return false;
		}

		if (!buildings.empty())
		{
			spdlog::error("Stale building file exists for '{}'. Resolve it before continuing.", finalPath.string());
			return false;
		}

		return true;
	}

	bool MSeedProcessor::CommitOnePendingFile(
		const std::filesystem::path& finalPath,
		const std::filesystem::path& pendingPath,
		bool miniSeedVersion3)
	{
		namespace fs = std::filesystem;

		if (!ResolveStaleCommitState(finalPath))
			return false;

		const std::string workBase = finalPath.filename().string() + "." + pendingSessionName;
		const fs::path sortedPath = finalPath.parent_path() / (workBase + ".sorted.tmp");
		const fs::path buildingPath = finalPath.parent_path() / (workBase + ".building");
		const fs::path backupPath = finalPath.parent_path() / (workBase + ".backup");

		if (fs::exists(sortedPath) || fs::exists(buildingPath) || fs::exists(backupPath))
		{
			spdlog::error("Session work file already exists for '{}'", finalPath.string());
			return false;
		}

		MS3TraceList* combined = nullptr;
		constexpr uint32_t readFlags = MSF_VALIDATECRC | MSF_UNPACKDATA;
		if (fs::exists(finalPath))
		{
			if (!ReadMSeedTo(finalPath.string(), combined, readFlags, "Read existing SDS"))
			{
				mstl3_free(&combined, 1);
				return false;
			}
		}

		if (!ReadMSeedTo(pendingPath.string(), combined, readFlags, "Read pending SDS"))
		{
			mstl3_free(&combined, 1);
			return false;
		}

		if (!WriteTraceListFile(combined, sortedPath, miniSeedVersion3))
		{
			mstl3_free(&combined, 1);
			return false;
		}
		mstl3_free(&combined, 1);

		MS3TraceList* dedupList = nullptr;
		constexpr uint32_t dedupReadFlags = MSF_VALIDATECRC | MSF_UNPACKDATA | MSF_SKIPADJACENTDUPLICATES;
		if (!ReadMSeedTo(sortedPath.string(), dedupList, dedupReadFlags, "Read sorted SDS with adjacent duplicate skip"))
		{
			mstl3_free(&dedupList, 1);
			return false;
		}

		if (!WriteTraceListFile(dedupList, buildingPath, miniSeedVersion3))
		{
			mstl3_free(&dedupList, 1);
			return false;
		}
		mstl3_free(&dedupList, 1);

		if (!ValidateMSeedFile(buildingPath))
			return false;

		try
		{
			fs::create_directories(finalPath.parent_path());
		}
		catch (const std::exception& ex)
		{
			spdlog::error("Cannot create final SDS directory '{}': {}", finalPath.parent_path().string(), ex.what());
			return false;
		}

		std::error_code ec;
		const bool hadOriginal = fs::exists(finalPath);
		if (hadOriginal)
		{
			fs::rename(finalPath, backupPath, ec);
			if (ec)
			{
				spdlog::error("Cannot move original '{}' to backup '{}': {}", finalPath.string(), backupPath.string(), ec.message());
				return false;
			}
		}

		fs::rename(buildingPath, finalPath, ec);
		if (ec)
		{
			spdlog::error("Cannot move building '{}' to final '{}': {}", buildingPath.string(), finalPath.string(), ec.message());
			if (hadOriginal)
			{
				std::error_code restoreEc;
				fs::rename(backupPath, finalPath, restoreEc);
				if (restoreEc)
					spdlog::error("Cannot restore backup '{}': {}", backupPath.string(), restoreEc.message());
			}
			return false;
		}

		if (!ValidateMSeedFile(finalPath))
		{
			std::error_code removeEc;
			fs::remove(finalPath, removeEc);
			if (hadOriginal)
			{
				std::error_code restoreEc;
				fs::rename(backupPath, finalPath, restoreEc);
				if (restoreEc)
					spdlog::error("Cannot restore backup '{}': {}", backupPath.string(), restoreEc.message());
			}
			return false;
		}

		if (hadOriginal)
			fs::remove(backupPath, ec);
		fs::remove(pendingPath, ec);
		fs::remove(sortedPath, ec);

		return true;
	}

	bool MSeedProcessor::CommitOnePendingFileAppendOnly(
		const std::filesystem::path& finalPath,
		const std::filesystem::path& pendingPath)
	{
		namespace fs = std::filesystem;

		if (!ResolveStaleCommitState(finalPath))
			return false;

		const std::string workBase = finalPath.filename().string() + "." + pendingSessionName;
		const fs::path buildingPath = finalPath.parent_path() / (workBase + ".building");
		const fs::path backupPath = finalPath.parent_path() / (workBase + ".backup");

		if (fs::exists(buildingPath) || fs::exists(backupPath))
		{
			spdlog::error("Session work file already exists for '{}'", finalPath.string());
			return false;
		}

		try
		{
			fs::create_directories(finalPath.parent_path());
		}
		catch (const std::exception& ex)
		{
			spdlog::error("Cannot create final SDS directory '{}': {}", finalPath.parent_path().string(), ex.what());
			return false;
		}

		if (!ValidateMSeedFile(pendingPath))
			return false;

		if (fs::exists(finalPath))
		{
			try
			{
				fs::copy_file(finalPath, buildingPath, fs::copy_options::none);
			}
			catch (const std::exception& ex)
			{
				spdlog::error("Cannot copy original '{}' to building '{}': {}", finalPath.string(), buildingPath.string(), ex.what());
				return false;
			}

			std::ifstream in(pendingPath, std::ios::binary);
			std::ofstream out(buildingPath, std::ios::binary | std::ios::app);
			out << in.rdbuf();
			if (!in || !out)
			{
				spdlog::error("Cannot append pending '{}' to building '{}'", pendingPath.string(), buildingPath.string());
				return false;
			}
		}
		else
		{
			std::error_code ec;
			fs::rename(pendingPath, buildingPath, ec);
			if (ec)
			{
				try
				{
					fs::copy_file(pendingPath, buildingPath, fs::copy_options::none);
				}
				catch (const std::exception& ex)
				{
					spdlog::error("Cannot move/copy pending '{}' to building '{}': {}", pendingPath.string(), buildingPath.string(), ex.what());
					return false;
				}
			}
		}

		if (!ValidateMSeedFile(buildingPath))
			return false;

		std::error_code ec;
		const bool hadOriginal = fs::exists(finalPath);
		if (hadOriginal)
		{
			fs::rename(finalPath, backupPath, ec);
			if (ec)
			{
				spdlog::error("Cannot move original '{}' to backup '{}': {}", finalPath.string(), backupPath.string(), ec.message());
				return false;
			}
		}

		fs::rename(buildingPath, finalPath, ec);
		if (ec)
		{
			spdlog::error("Cannot move building '{}' to final '{}': {}", buildingPath.string(), finalPath.string(), ec.message());
			if (hadOriginal)
			{
				std::error_code restoreEc;
				fs::rename(backupPath, finalPath, restoreEc);
				if (restoreEc)
					spdlog::error("Cannot restore backup '{}': {}", backupPath.string(), restoreEc.message());
			}
			return false;
		}

		if (!ValidateMSeedFile(finalPath))
		{
			std::error_code removeEc;
			fs::remove(finalPath, removeEc);
			if (hadOriginal)
			{
				std::error_code restoreEc;
				fs::rename(backupPath, finalPath, restoreEc);
				if (restoreEc)
					spdlog::error("Cannot restore backup '{}': {}", backupPath.string(), restoreEc.message());
			}
			return false;
		}

		if (hadOriginal)
			fs::remove(backupPath, ec);
		fs::remove(pendingPath, ec);

		return true;
	}

	bool MSeedProcessor::RewriteFinalFileFromTraceList(
		const std::filesystem::path& finalPath,
		uint32_t readFlags,
		const char* label,
		bool miniSeedVersion3)
	{
		namespace fs = std::filesystem;

		if (!ResolveStaleCommitState(finalPath))
			return false;

		const std::string workBase = finalPath.filename().string() + "." + pendingSessionName;
		const fs::path buildingPath = finalPath.parent_path() / (workBase + ".building");
		const fs::path backupPath = finalPath.parent_path() / (workBase + ".backup");

		if (fs::exists(buildingPath) || fs::exists(backupPath))
		{
			spdlog::error("Session work file already exists for '{}'", finalPath.string());
			return false;
		}

		MS3TraceList* traceList = nullptr;
		if (!ReadMSeedTo(finalPath.string(), traceList, readFlags, label))
		{
			mstl3_free(&traceList, 1);
			return false;
		}

		if (!WriteTraceListFile(traceList, buildingPath, miniSeedVersion3))
		{
			mstl3_free(&traceList, 1);
			return false;
		}
		mstl3_free(&traceList, 1);

		if (!ValidateMSeedFile(buildingPath))
			return false;

		std::error_code ec;
		fs::rename(finalPath, backupPath, ec);
		if (ec)
		{
			spdlog::error("Cannot move original '{}' to backup '{}': {}", finalPath.string(), backupPath.string(), ec.message());
			return false;
		}

		fs::rename(buildingPath, finalPath, ec);
		if (ec)
		{
			spdlog::error("Cannot move building '{}' to final '{}': {}", buildingPath.string(), finalPath.string(), ec.message());
			std::error_code restoreEc;
			fs::rename(backupPath, finalPath, restoreEc);
			if (restoreEc)
				spdlog::error("Cannot restore backup '{}': {}", backupPath.string(), restoreEc.message());
			return false;
		}

		if (!ValidateMSeedFile(finalPath))
		{
			std::error_code removeEc;
			fs::remove(finalPath, removeEc);
			std::error_code restoreEc;
			fs::rename(backupPath, finalPath, restoreEc);
			if (restoreEc)
				spdlog::error("Cannot restore backup '{}': {}", backupPath.string(), restoreEc.message());
			return false;
		}

		fs::remove(backupPath, ec);
		return true;
	}

	void MSeedProcessor::CleanupPendingSessionIfEmpty() const
	{
		if (pendingSessionPath.empty())
			return;

		std::error_code ec;
		std::filesystem::remove_all(pendingSessionPath, ec);
		if (ec)
		{
			spdlog::warn("Cannot remove pending session '{}': {}", pendingSessionPath.string(), ec.message());
			return;
		}

		const auto sessionsRoot = pendingSessionPath.parent_path();
		if (sessionsRoot.empty())
			return;

		ec.clear();
		if (std::filesystem::exists(sessionsRoot, ec) && std::filesystem::is_empty(sessionsRoot, ec))
		{
			ec.clear();
			std::filesystem::remove(sessionsRoot, ec);
			if (ec)
				spdlog::warn("Cannot remove pending sessions root '{}': {}", sessionsRoot.string(), ec.message());
		}
	}

	void MSeedProcessor::AddNewDataTo(
		MS3TraceList* out_mstl,
		const char* sid,
		const int32_t* data, int64_t numsamples, int64_t startTime,
		double samprate
	) {
		// ---- validation ----
		if (!out_mstl || !sid || !data || numsamples <= 0 || samprate <= 0.0) {
			spdlog::error("AddNewDataTo(): invalid parameters");
			return;
		}

		//flags |= MSF_VALIDATECRC;	//[Parsing] Validate CRC (if version 3)
		//flags |= MSF_RECORDLIST;	//[TraceList] Build a ::MS3RecordList for each ::MS3TraceSeg
		//flags |= MSF_UNPACKDATA;	//[Parsing] Unpack data samples
		constexpr uint32_t flags = MSF_FLUSHDATA;
		//constexpr uint32_t flags = 0;

		// ---- reset minimal state ----
		msr->datasamples = nullptr;
		msr->numsamples = 0;
		msr->samplecnt = 0;

		// ---- SID ----
		strncpy_s(msr->sid, sizeof(msr->sid), sid, _TRUNCATE);

		// ---- record header fields ----
		msr->reclen = reclen;
		msr->pubversion = 1;

		msr->samprate = samprate;
		msr->encoding = encoding;	//	DE_STEIM2;
		msr->sampletype = 'i';		/* declare data type to be 32-bit integers */

		msr->datasamples = (void*)const_cast<int32_t*>(data);
		msr->numsamples = numsamples;
		msr->samplecnt = numsamples;
		msr->starttime = startTime;

		// Architectural decision: keep adjacent segments separate; do not auto-heal/merge them.
		if (!mstl3_addmsr(out_mstl, msr, 0, 0, flags, NULL))
		{
			spdlog::error("mstl3_addmsr() failed for SID: {}", sid);
		}
		// ---- reset minimal state ----
		msr->datasamples = nullptr;
		msr->numsamples = 0;
		msr->samplecnt = 0;
	}

	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	int64_t MSeedProcessor::WriteDataRangeToMSeed(
		const std::string& mseedFile,
		const char* sid,
		const DataRange& segDay,
		double samprate,
		bool overwrite,
		bool miniSeedVersion3)
	{
		if (!sid || !segDay.dataPtr || segDay.numsamples <= 0 || samprate <= 0.0) {
			spdlog::error("WriteDataRangeToMSeed(): invalid parameters");
			return -1;
		}

		uint32_t flags = MSF_FLUSHDATA;
		if (!miniSeedVersion3)
			flags |= MSF_PACKVER2;

		msr->datasamples = nullptr;
		msr->numsamples = 0;
		msr->samplecnt = 0;

		strncpy_s(msr->sid, sizeof(msr->sid), sid, _TRUNCATE);
		msr->reclen = reclen;
		msr->pubversion = 1;
		msr->samprate = samprate;
		msr->encoding = encoding;
		msr->sampletype = 'i';
		msr->datasamples = segDay.dataPtr;
		msr->numsamples = segDay.numsamples;
		msr->samplecnt = segDay.numsamples;
		msr->starttime = segDay.start;

		int64_t packedrecords = msr3_writemseed(
			msr,
			mseedFile.c_str(),
			overwrite ? 1 : 0,
			flags,
			this->verbose);

		msr->datasamples = nullptr;
		msr->numsamples = 0;
		msr->samplecnt = 0;

		return packedrecords;
	}


	void MSeedProcessor::PrintSamples(MS3TraceID* tid, MS3TraceSeg* seg) const
	{
		if (!seg || !seg->recordlist || !seg->recordlist->first)
			return;

		int64_t unpacked = mstl3_unpack_recordlist(tid, seg, nullptr, 0, 0);

		if (unpacked < 0)
		{
			spdlog::error("Cannot unpack samples for {}", tid->sid);
			return;
		}

		uint8_t  samplesize = 0;
		char sampletype = seg->sampletype;

		ms_encoding_sizetype(
			(uint8_t)seg->recordlist->first->msr->encoding,
			&samplesize,
			&sampletype);

		spdlog::info("DATA ({} samples) type '{}'", seg->numsamples, sampletype);

		const int64_t maxPrint = 20;

		int64_t count = seg->numsamples;
		int64_t printCount = count < maxPrint ? count : maxPrint;

		for (int64_t i = 0; i < printCount; i++)
		{
			char* sptr = (char*)seg->datasamples + (i * samplesize);

			if (sampletype == 'i')
			{
				int32_t v = *(int32_t*)sptr;
				spdlog::info("{}", v);
			}
			else if (sampletype == 'f')
			{
				float v = *(float*)sptr;
				spdlog::info("{}", v);
			}
			else if (sampletype == 'd')
			{
				double v = *(double*)sptr;
				spdlog::info("{}", v);
			}
		}

		if (count > maxPrint)
			spdlog::info("... ({} more samples)", count - maxPrint);
	}

	bool MSeedProcessor::ReadMSeedTo(const std::string& inputFile, MS3TraceList*& outMstl)
	{
		constexpr uint32_t flags =
			MSF_VALIDATECRC |
			MSF_UNPACKDATA |
			MSF_SKIPADJACENTDUPLICATES;

		return ReadMSeedTo(inputFile, outMstl, flags, "ReadMSeedTo");
	}

	bool MSeedProcessor::RepackMSeedFileOnce(const std::string& mseedFile, bool miniSeedVersion3)
	{
		// With the current libmseed behavior, MSF_SKIPADJACENTDUPLICATES only
		// drops adjacent duplicate records while reading. A write/read/write pass
		// can turn non-adjacent duplicates into adjacent ones, then skip them.
		MS3TraceList* repackList = nullptr;
		if (!ReadMSeedTo(mseedFile, repackList))
		{
			spdlog::error("RepackMSeedFileOnce: cannot read '{}'", mseedFile);
			return false;
		}

		uint32_t flags = MSF_FLUSHDATA;
		if (!miniSeedVersion3)
			flags |= MSF_PACKVER2;

		const int64_t packedrecords = mstl3_writemseed(
			repackList,
			mseedFile.c_str(),
			1,
			this->reclen,
			this->encoding,
			flags,
			this->verbose
		);

		mstl3_free(&repackList, 1);

		if (packedrecords < 0)
		{
			spdlog::error(
				"RepackMSeedFileOnce: mstl3_writemseed() failed for '{}' (code={})",
				mseedFile,
				packedrecords
			);
			return false;
		}

		spdlog::info("Repacked '{}' with {} MiniSEED records", mseedFile, packedrecords);
		return true;
	}

	bool MSeedProcessor::CreateDirectoryIfNotExists(const std::string& path)
	{
		namespace fs = std::filesystem;

		try
		{
			if (fs::create_directories(path))
			{
				spdlog::info("Directory created: {}", path);
			}
			else
			{
				spdlog::debug("Directory already exists: {}", path);
			}

			return true;
		}
		catch (const fs::filesystem_error& e)
		{
			spdlog::error("Error creating directory '{}': {}", path, e.what());
			return false;
		}
	}

	void MSeedProcessor::GetDirectoryAndFileName(
		std::string& outPath,
		std::string& outMSeedFile,
		nstime_t startDate,
		const char sid[LM_SIDLEN],
		const std::string& basePath)
	{
		uint16_t year = 0;
		uint16_t yday = 0;
		uint8_t hour = 0;
		uint8_t min = 0;
		uint8_t sec = 0;
		uint32_t nsec = 0;

		char network[11]{};
		char station[11]{};
		char location[11]{};
		char channel[31]{};

		// تبدیل nstime به سال/روز سال
		ms_nstime2time(startDate, &year, &yday, &hour, &min, &sec, &nsec);

		// تبدیل SID به N/S/L/C
		ms_sid2nslc_n(
			sid,
			network, sizeof(network),
			station, sizeof(station),
			location, sizeof(location),
			channel, sizeof(channel));

		//// هندل location خالی → "--" طبق convention رایج
		//// location معمولاً 2 کاراکتر است؛ اگر خالی بود، جایگزینش می‌کنیم
		//if (location[0] == '\0')
		//{
		//	location[0] = '-';
		//	location[1] = '-';
		//	location[2] = '\0';
		//}

		namespace fs = std::filesystem;

		//<root>/YEAR/NET/STA/CHA.D/NET.STA.LOC.CHA.D.YEAR.DAY

		 // ساخت مسیر SDS:
		// basePath / YEAR / NET / STA / CHA.D
		fs::path _path(basePath);
		_path /= std::to_string(year);   // YEAR
		_path /= network;                // NET
		_path /= station;                // STA

		// CHA.D (کانال + "." + کیفیت روزانه 'D')
		{
			std::string chaDir;
			chaDir.reserve(std::char_traits<char>::length(channel) + 2);
			chaDir.append(channel);
			chaDir.push_back('.');
			chaDir.push_back('D');
			_path /= chaDir;
		}

		outPath = _path.string();  // یا utf8_string() اگر لازم شد

		// نام فایل SDS: NET.STA.LOC.CHA.D.YEAR.DAY.mseed
		// اگر location خالی باشد → filename به شکل NET.STA..CHA.D.YEAR.DAY خواهد شد (طبق SDS)
		// (DAY سه رقمی با صفر پیشرو)
		char _fileName[256];
		int n = _snprintf_s(
			_fileName,
			sizeof(_fileName),
			_TRUNCATE,
			"%s.%s.%s.%s.D.%u.%03u.mseed",
			network,
			station,
			location,
			channel,
			static_cast<unsigned>(year),
			static_cast<unsigned>(yday));


		if (n < 0 || static_cast<size_t>(n) >= sizeof(_fileName))
		{
			// در صورت overflow یا خطا، می‌شود لاگ کرد/exception پرتاب کرد
			// فعلاً یک fallback ساده:
			_fileName[sizeof(_fileName) - 1] = '\0';
		}

		fs::path fullFilePath = _path / _fileName;
		outMSeedFile = fullFilePath.string();

	}

	//const nstime_t dT = static_cast<nstime_t>(1e9 / samprate);  //Delta Sampling time in Nano Sec.
	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	void MSeedProcessor::ComputeOkSeg(
		const Range& oldData,
		DataRange& newSeg,
		nstime_t dT,
		DataRange& okSeg)
	{

		const nstime_t oldStart = oldData.start;
		const nstime_t oldEnd = oldData.end;

		nstime_t newStart = newSeg.start;
		nstime_t newEnd = newSeg.end;

		// Case 1 : new completely before old
		//	!*****!					NewSeg
		//	!*********!				NewSeg
		//			  !'....'		Old Available Data
		// ------------'----'---
		//	!*****!    '....'		OkSeg
		//	!*********!'....'		OkSeg
		//داده تکراری نیست
		//if (newSeg.end < oldData.start)
		if (newEnd < oldStart)
		{
			//همه رنج جدید اضافه شود
			okSeg = newSeg;
			newSeg.Reset();
			return;
		}

		// Case 6 : new completely after old
		//            '!*****!
		//            '  !***!
		//	    '.....'				Old Available Data
		// -----'-----'---------
		//								No OkSeg!
		//            '!*****!			NewSeg2
		//            '  !***!			NewSeg2
		//کل NewSeg به چک بعدی موکول میشه
		//if (newSeg.start > oldData.end)
		if (newStart > oldEnd)
		{
			okSeg.Reset();
			//NewSeg will be passed back for next check
			return;
		}

		// Case 4 : fully duplicate
		//	    !***! ' 			NewSeg
		//	    !*****! 			NewSeg
		//	    '  !**! 			NewSeg
		//	    '.....'				Old Available Data
		// -----'-----'---------
		// هیچ داده جدیدی وجود ندارد!
		//if (newSeg.end <= oldData.end)
		if (newEnd <= oldEnd && newStart >= oldStart)
		{
			okSeg.Reset();
			newSeg.Reset();
			return;
		}

		// Case 2 : overlap at end
		//	!**********!			NewSeg
		//	!**********'**!			NewSeg
		//	!**********'******!		NewSeg
		//	          !!			NewSeg
		//	          !***!			NewSeg
		//	          !*******!		NewSeg
		//		       '......'		Old Available Data
		// ------------'------'-
		//	!*********!'......'		OkSeg
		//	          !'......'		OkSeg
		//انتهای داده جدید باید حذف شود
		//if (newSeg.start < oldData.start && newSeg.end <= oldData.end)
		if (newStart < oldStart && newEnd <= oldEnd)
		{
			okSeg.dataPtr = newSeg.dataPtr;
			okSeg.start = newStart;
			okSeg.end = oldStart - dT;
			if (okSeg.end < okSeg.start) okSeg.end = okSeg.start;

			okSeg.numsamples =
				(okSeg.end - okSeg.start) / dT + 1;

			newSeg.Reset();
			return;
		}

		// Case 5 : overlap at begin
		//	    !*****'! 			    NewSeg
		//	    !*****'*******!			NewSeg
		//	    '  !**'! 			    NewSeg
		//	    '  !**'*******! 		NewSeg
		//	    '     !! 			    NewSeg
		//	    '     !*******! 		NewSeg
		//	    '.....'				    Old Available Data
		// -----'-----'---------
		//								No OkSeg!
		//		       !				NewSeg2
		//		       !******!			NewSeg2
		//if (newSeg.start <= oldData.end && newSeg.end > oldData.end)
		if (newStart >= oldStart && newStart <= oldEnd && newEnd > oldEnd)
		{
			okSeg.Reset();

			const int64_t omitted =
				(oldEnd - newStart) / dT + 1;

			newSeg.dataPtr += omitted;
			newSeg.start = oldEnd + dT;
			if (newSeg.start > newSeg.end) newSeg.start = newSeg.end;
			newSeg.numsamples -= omitted;

			return;
		}


		// Case 3 : split before and after
		//	!***'*****'!			NewSeg
		//	!***'*****'******!		NewSeg
		//	   !'*****'!			NewSeg
		//	   !'*****'***!			NewSeg
		//	    '.....'				Old Available Data
		// -----'-----'---------
		//  !**!'.....'				OkSeg
		//     !'.....'				OkSeg
		//      '.....'!			NewSeg2 (to check again)
		//      '.....'!*****!		NewSeg2 (to check again)
		//دیتای بعد از دیتای قدیمی باید جدا شود و مجددا تست شود (در گام بعدی)
		//if (newSeg.start < oldData.start && newSeg.end > oldData.end)
		if (newStart < oldStart && newEnd > oldEnd)
		{
			okSeg.dataPtr = newSeg.dataPtr;
			okSeg.start = newStart;
			okSeg.end = oldStart - dT;
			if (okSeg.end < okSeg.start) okSeg.end = okSeg.start;

			okSeg.numsamples =
				(okSeg.end - okSeg.start) / dT + 1;

			const int64_t oldSamples =
				(oldEnd - oldStart) / dT + 1;

			const int64_t skip =
				okSeg.numsamples + oldSamples;

			newSeg.dataPtr += skip;
			newSeg.start = oldEnd + dT;
			if (newSeg.start > newSeg.end) newSeg.start = newSeg.end;

			newSeg.numsamples -= skip;

			return;
		}

	}


	void MSeedProcessor::FillDataAvailable(
		MS3TraceList* out_mstl,
		std::map<double, std::vector<Range>>& oldDataDic,
		const char* sid)
	{
		oldDataDic.clear(); // پاک کردن لیست برای روز جدید

		if (!out_mstl)
			return;

		// عبور از تمام Traceها
		for (MS3TraceID* old_tid = out_mstl->traces.next[0]; old_tid; old_tid = old_tid->next[0])
		{
			if (strcmp(old_tid->sid, sid) != 0)
				continue; // فقط برای همین ID

			for (MS3TraceSeg* old_seg = old_tid->first; old_seg; old_seg = old_seg->next)
			{
				oldDataDic[old_seg->samprate].push_back({ old_seg->starttime, old_seg->endtime });
			}
		}
	}

	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	void MSeedProcessor::BuildSegDay(const MS3TraceSeg* seg,
		nstime_t dayStart,
		nstime_t dayEnd,
		DataRange& segDay)
	{
		// Validate required fields early
		if (!seg || seg->numsamples <= 0 || seg->datasamples == nullptr) {
			spdlog::error("BuildSegDay: invalid seg (null or empty)");
			return;
		}

		long double sr = seg->samprate;
		nstime_t dT = (nstime_t)(NSTMODULUS / sr + 0.5);
		if (dT <= 0) {
			spdlog::error("BuildSegDay: invalid sample interval {}", dT);
			return;
		}
		nstime_t halfSample = dT / 2;
		nstime_t effectiveDayStart = dayStart + halfSample;
		nstime_t effectiveDayEnd = dayEnd + halfSample;

		// Samples are assigned to the day whose boundary they are closest to.
		if (seg->endtime < effectiveDayStart || seg->starttime >= effectiveDayEnd) {
			return;
		}

		auto ceilDivPositive = [](nstime_t numerator, nstime_t denominator) -> int64_t {
			if (numerator <= 0) {
				return 0;
			}
			return static_cast<int64_t>(1 + ((numerator - 1) / denominator));
		};
		auto exactSampleIndex = [&](nstime_t sampleTime, int64_t& index) -> bool {
			nstime_t diff = sampleTime - seg->starttime;
			if (diff < 0 || diff % dT != 0) {
				return false;
			}

			index = diff / dT;
			return index >= 0 && index < seg->numsamples;
		};

		int64_t idxStart = 0;
		int64_t idxEnd = seg->numsamples - 1;

		// Clip start index by day start
		if (seg->starttime < effectiveDayStart)
		{
			int64_t exactBoundaryIndex = 0;
			if (exactSampleIndex(dayStart, exactBoundaryIndex))
			{
				idxStart = exactBoundaryIndex;
			}
			else
			{
				nstime_t diff = effectiveDayStart - seg->starttime;
				idxStart = ceilDivPositive(diff, dT);
			}
		}

		// Clip end index by day end
		int64_t exactBoundaryIndex = 0;
		if (exactSampleIndex(dayEnd, exactBoundaryIndex))
		{
			idxEnd = exactBoundaryIndex - 1;
		}
		else if (seg->endtime >= effectiveDayEnd)
		{
			nstime_t diff = effectiveDayEnd - seg->starttime;
			idxEnd = ceilDivPositive(diff, dT) - 1;
		}

		if (idxStart >= seg->numsamples || idxEnd < 0 || idxEnd < idxStart)
		{
			return;
		}

		if (idxStart < 0 || idxStart >= seg->numsamples ||
			idxEnd < 0 || idxEnd >= seg->numsamples || idxEnd < idxStart)
		{
			spdlog::error(
				"BuildSegDay: sample index out of range! "
				"idxStart={}, idxEnd={}, seg->numsamples={}, seg->starttime={}, seg->endtime={}",
				idxStart, idxEnd, seg->numsamples, seg->starttime, seg->endtime
			);

			return;
		}

		segDay.dataPtr = static_cast<int32_t*>(seg->datasamples) + idxStart;
		segDay.start = seg->starttime + (idxStart * dT);
		segDay.end = seg->starttime + (idxEnd * dT);
		segDay.numsamples = idxEnd - idxStart + 1;

		return;
	}

	bool MSeedProcessor::TestBuildSegDayMidnightSplit()
	{
		const nstime_t DAY_NS = 86400000000000LL;
		const nstime_t dT = 20000000LL;
		const int64_t midnightPreviousIndex = 153703;
		const int64_t numsamples = 154650;

		// Sample 153703 is 8.9 ms before midnight; sample 153704 is 11.1 ms after it.
		const nstime_t segStart = DAY_NS - (midnightPreviousIndex * dT) - 8900000LL;
		std::vector<int32_t> data(static_cast<size_t>(numsamples), 0);

		MS3TraceSeg seg{};
		seg.starttime = segStart;
		seg.endtime = segStart + ((numsamples - 1) * dT);
		seg.samprate = 50.0;
		seg.numsamples = numsamples;
		seg.datasamples = data.data();

		DataRange firstDay{};
		BuildSegDay(&seg, 0, DAY_NS, firstDay);
		if (firstDay.numsamples != midnightPreviousIndex + 1 ||
			firstDay.dataPtr != data.data() ||
			firstDay.start != segStart ||
			firstDay.end != DAY_NS - 8900000LL)
		{
			return false;
		}

		DataRange secondDay{};
		BuildSegDay(&seg, DAY_NS, 2 * DAY_NS, secondDay);
		if (secondDay.numsamples != numsamples - midnightPreviousIndex - 1 ||
			secondDay.dataPtr != data.data() + midnightPreviousIndex + 1 ||
			secondDay.start != DAY_NS + 11100000LL ||
			secondDay.end != seg.endtime)
		{
			return false;
		}

		const int64_t midnightNextIndex = 2307;
		const int64_t shortSamples = 2310;
		const nstime_t afterMidnightSample = DAY_NS + 3375000LL;
		const nstime_t shortSegStart = afterMidnightSample - (midnightNextIndex * dT);
		std::vector<int32_t> shortData(static_cast<size_t>(shortSamples), 0);

		MS3TraceSeg shortSeg{};
		shortSeg.starttime = shortSegStart;
		shortSeg.endtime = shortSegStart + ((shortSamples - 1) * dT);
		shortSeg.samprate = 50.0;
		shortSeg.numsamples = shortSamples;
		shortSeg.datasamples = shortData.data();

		DataRange previousDay{};
		BuildSegDay(&shortSeg, 0, DAY_NS, previousDay);
		if (previousDay.numsamples != midnightNextIndex + 1 ||
			previousDay.dataPtr != shortData.data() ||
			previousDay.start != shortSegStart ||
			previousDay.end != afterMidnightSample)
		{
			return false;
		}

		DataRange nextDay{};
		BuildSegDay(&shortSeg, DAY_NS, 2 * DAY_NS, nextDay);
		if (nextDay.numsamples != shortSamples - midnightNextIndex - 1 ||
			nextDay.dataPtr != shortData.data() + midnightNextIndex + 1 ||
			nextDay.start != afterMidnightSample + dT ||
			nextDay.end != shortSeg.endtime)
		{
			return false;
		}

		return true;
	}


	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	bool MSeedProcessor::LoadOldMSeedIfExists(
		const std::string& mseedPath,
		const std::string& mseedFile,
		MS3TraceList*& out_mstl,
		std::map<double, std::vector<Range>>& oldDataDic,
		const char* sid)
	{
		// ایجاد لیست خالی
		out_mstl = mstl3_init(nullptr);
		if (!out_mstl)
		{
			spdlog::error("LoadOldMSeedIfExists: failed to allocate MS3TraceList.");
			return false;
		}

		//اینجا باید چک کنیم دیتای قدیمی نوشته شده روی هارد داریم یا نه!
		if (std::filesystem::exists(mseedFile))
		{
			// فایل mseed روزانه است و برای هر تعداد سگمنت هم که باشد فقط یک بار باید خوانده شود!!
			if (!ReadMSeedTo(mseedFile.c_str(), out_mstl))
			{
				spdlog::error("LoadOldMSeedIfExists: cannot read existing mseed file '{}'", mseedFile);
				mstl3_free(&out_mstl, 1);
				return false;
			}

			//کل موجودی mseed قبلی رو برای بررسی های بعدی در دیکشنری نگه میداریم
			FillDataAvailable(out_mstl, oldDataDic, sid);

			return true;
		}

		// اگر فایل نبود ولی دایرکتوری هم نبود → درستش کن
		if (!std::filesystem::exists(mseedPath))
		{
			if (!CreateDirectoryIfNotExists(mseedPath))
			{
				spdlog::error("LoadOldMSeedIfExists: cannot create directory '{}'", mseedPath);
				mstl3_free(&out_mstl, 1);
				return false;
			}
		}

		// اینجا فایل قدیمی نداریم، ولی:
		// - out_mstl یک لیست خالی معتبر است
		// - oldDataDic خالی است
		// - دایرکتوری آماده است (اگر لازم بوده ساخته شده)
		return true;  // هیچ فایل قدیمی نبود، ولی وضعیت ok است
	}


	// Legacy SDS export path.
	// Retained temporarily for regression comparison.
	// Not used by the new CLI SDS workflow.
	bool MSeedProcessor::AppendSegDayAvoidDuplicate(
		MS3TraceList* out_mstl,
		const char* sid,
		DataRange& segDay,
		double samprate,
		std::map<double, std::vector<Range>>& oldDataDic)
	{
		bool recordAdded = false;
		DataRange segToAdd{};

		auto& ranges = oldDataDic[samprate];

		const nstime_t dT = (nstime_t)(NSTMODULUS / samprate + 0.5);

		// اگر اصلا داده قدیمی ای موجود نیست
		if (ranges.empty())
		{
			AddNewDataTo(
				out_mstl,
				sid,
				segDay.dataPtr,
				segDay.numsamples,
				segDay.start,
				samprate
			);

			// بروزرسانی موجودی داده
			//FillDataAvailable(out_mstl, oldDataDic, sid);
			ranges.push_back({ segDay.start, segDay.end });

			return true;
		}

		// بررسی تداخل با داده های قبلی
		for (size_t i = 0; i < ranges.size(); i++)
		{
			segToAdd = {};
			ComputeOkSeg(ranges[i], segDay, dT, segToAdd);

			if (segToAdd.numsamples > 0)
			{
				AddNewDataTo(
					out_mstl,
					sid,
					segToAdd.dataPtr,
					segToAdd.numsamples,
					segToAdd.start,
					samprate
				);

				FillDataAvailable(out_mstl, oldDataDic, sid);
				//ranges.push_back({ segToAdd.start, segToAdd.end });

				recordAdded = true;
			}

			// سگمنت جاری خالی شده
			if (segDay.numsamples == 0)
				break;
		}

		// اگر هنوز داده ای باقی مانده که در لیست نبود
		if (segDay.numsamples > 0)
		{
			AddNewDataTo(
				out_mstl,
				sid,
				segDay.dataPtr,
				segDay.numsamples,
				segDay.start,
				samprate
			);

			FillDataAvailable(out_mstl, oldDataDic, sid);
			//ranges.push_back({ segDay.start, segDay.end });

			recordAdded = true;
		}

		return recordAdded;
	}




	//--------------- TEST ------------------

	/// <summary>
	/// Create a synthetic DataRange segment for testing
	/// The function simulates a segment that contains regularly
	/// spaced samples in the buffer.
	/// </summary>
	/// <param name="newSeg">output DataRange that will be filled</param>
	/// <param name="dataPtr">pointer to beginning of a sample buffer</param>
	/// <param name="start">start time of segment (nstime_t)</param>
	/// <param name="end">end time of segment (nstime_t)</param>
	/// <param name="dT">sample interval in nanoseconds</param>
	static void FillTestSeg(
		DataRange& newSeg,
		int32_t* dataPtr,
		nstime_t start,
		nstime_t end,
		nstime_t dT)
	{
		newSeg.Reset();

		if (dT <= 0 || end < start)
			return;

		newSeg.dataPtr = dataPtr;
		newSeg.start = start;
		newSeg.end = end;

		// number of samples in the segment
		newSeg.numsamples = (end - start) / dT + 1;
	}



	/// <summary>
	/// Verify that a DataRange matches expected values
	/// </summary>
	/// <param name="seg">actual segment returned by algorithm</param>
	/// <param name="expectedPtr">expected pointer into data buffer</param>
	/// <param name="start">expected time range</param>
	/// <param name="end">expected time range</param>
	/// <param name="numsamples">expected number of samples</param>
	/// <returns></returns>
	static bool CheckSeg(
		const DataRange& seg,
		int32_t* expectedPtr,
		nstime_t start,
		nstime_t end,
		int64_t numsamples)
	{
		return seg.dataPtr == expectedPtr &&
			seg.start == start &&
			seg.end == end &&
			seg.numsamples == numsamples;
	}


	// ------------------------------------------------------------
	// Test for ComputeOkSeg
	//
	// This test checks how a new incoming segment should be split
	// into:
	//
	//   okSeg  -> part that does NOT overlap existing data
	//   newSeg -> remaining part after overlap removal
	//
	// oldData represents already existing data in storage.
	// ------------------------------------------------------------
	void MSeedProcessor::TestComputeOkSeg()
	{
		constexpr nstime_t NS = NSTMODULUS;   // 1 second in nanoseconds

		long double sampleRate = 1;	//1Hz
		const nstime_t dT = (nstime_t)(NSTMODULUS / sampleRate + 0.5);

		// existing data range
		Range oldData{ 10 * NS, 19 * NS };

		DataRange newSeg{};
		DataRange okSeg{};

		// sample buffer used for pointer offset tests
		static int32_t data[1000];
		int32_t* dataPtr = data;

		int testNum = 0;
		int failed = 0;

		// --------------------------------------------------------
		// Helper to run a single test case
		//
		//    newSt,newEn			: start/end of incoming new segment
		//
		//    exp_okSt,exp_okEn	: expected OK segment
		//
		//    exp_newSt,exp_newEn	: expected remaining new segment
		// --------------------------------------------------------
		auto RunTest =
			[&](nstime_t newSt, nstime_t newEn,
				nstime_t exp_okSt, nstime_t exp_okEn,
				nstime_t exp_newSt, nstime_t exp_newEn)
			{
				++testNum;

				// Fill newSeg by given data
				FillTestSeg(newSeg, dataPtr, newSt, newEn, dT);

				okSeg.Reset();

				// run algorithm
				ComputeOkSeg(oldData, newSeg, dT, okSeg);
				//now newSeg & okSeg has new values as result of compute

				// expected sample counts
				int64_t exp_okSamples =
					(exp_okEn != 0 && exp_okSt != 0 && exp_okEn >= exp_okSt) ?
					((exp_okEn - exp_okSt) / dT + 1) :
					0;

				int64_t exp_newSamples =
					(exp_newEn != 0 && exp_newSt != 0 && exp_newEn >= exp_newSt) ?
					((exp_newEn - exp_newSt) / dT + 1) :
					0;

				// expected pointer offsets in sample buffer
				int32_t* exp_okPtr =
					(exp_okSamples > 0) ? dataPtr + (exp_okSt - newSt) / dT : nullptr;

				int32_t* exp_newPtr =
					(exp_newSamples > 0) ? dataPtr + (exp_newSt - newSt) / dT : nullptr;

				bool ok =
					CheckSeg(okSeg, exp_okPtr, exp_okSt, exp_okEn, exp_okSamples) &&
					CheckSeg(newSeg, exp_newPtr, exp_newSt, exp_newEn, exp_newSamples);

				if (ok)
					std::cout << "Test " << testNum << " Passed\n";
				else {
					std::cout << "Test " << testNum << " FAILED\n";
					failed++;
				}
			};


		std::cout << "\n========== ComputeOkSeg TESTS ==========\n";


		// --------------------------------------------------------
		// CASE GROUP 1
		// New segment completely BEFORE old data
		// Expected: entire segment is OK
		// --------------------------------------------------------
		{
			RunTest(
				2 * NS, 8 * NS,
				2 * NS, 8 * NS,
				0, 0
			);

			// touches just before old start
			RunTest(
				2 * NS, 9 * NS,
				2 * NS, 9 * NS,
				0, 0
			);
		}


		// --------------------------------------------------------
		// CASE GROUP 2
		// New segment overlaps beginning of old data
		// --------------------------------------------------------
		{
			// overlaps exactly at old start
			RunTest(
				2 * NS, 10 * NS,
				2 * NS, 9 * NS,
				0, 0
			);

			// partial overlap
			RunTest(
				2 * NS, 15 * NS,
				2 * NS, 9 * NS,
				0, 0
			);

			// overlap reaches end of old
			RunTest(
				2 * NS, 19 * NS,
				2 * NS, 9 * NS,
				0, 0
			);
		}


		// --------------------------------------------------------
		// CASE GROUP 3
		// New segment overlaps old data but continues after it
		// --------------------------------------------------------
		{
			RunTest(
				2 * NS, 20 * NS,
				2 * NS, 9 * NS,
				20 * NS, 20 * NS
			);

			RunTest(
				2 * NS, 25 * NS,
				2 * NS, 9 * NS,
				20 * NS, 25 * NS
			);
		}

		// --------------------------------------------------------
		// CASE GROUP 4
		// New segment starts exactly in the gap before old data
		// --------------------------------------------------------
		{
			RunTest(
				9 * NS, 10 * NS,
				9 * NS, 9 * NS,
				0, 0
			);

			RunTest(
				9 * NS, 15 * NS,
				9 * NS, 9 * NS,
				0, 0
			);

			RunTest(
				9 * NS, 19 * NS,
				9 * NS, 9 * NS,
				0, 0
			);
		}


		// --------------------------------------------------------
		// CASE GROUP 5
		// Gap start + segment continues after old data
		// --------------------------------------------------------
		{
			RunTest(
				9 * NS, 20 * NS,
				9 * NS, 9 * NS,
				20 * NS, 20 * NS
			);

			RunTest(
				9 * NS, 25 * NS,
				9 * NS, 9 * NS,
				20 * NS, 25 * NS
			);
		}


		// --------------------------------------------------------
		// CASE GROUP 6
		// New segment fully inside old data
		// Expected: nothing to write
		// --------------------------------------------------------
		{
			RunTest(
				10 * NS, 15 * NS,
				0, 0,
				0, 0
			);

			RunTest(
				10 * NS, 19 * NS,
				0, 0,
				0, 0
			);
		}



		// --------------------------------------------------------
		// CASE GROUP 7
		// Segment starts inside old but extends after
		// --------------------------------------------------------
		{
			RunTest(
				10 * NS, 20 * NS,
				0, 0,
				20 * NS, 20 * NS
			);

			RunTest(
				10 * NS, 25 * NS,
				0, 0,
				20 * NS, 25 * NS
			);
		}



		// --------------------------------------------------------
		// CASE GROUP 8
		// Segment starts in middle of old data
		// --------------------------------------------------------
		{
			RunTest(
				15 * NS, 19 * NS,
				0, 0,
				0, 0
			);

			RunTest(
				15 * NS, 20 * NS,
				0, 0,
				20 * NS, 20 * NS
			);

			RunTest(
				15 * NS, 25 * NS,
				0, 0,
				20 * NS, 25 * NS
			);
		}



		// --------------------------------------------------------
		// CASE GROUP 9
		// Segment touches end of old data
		// --------------------------------------------------------
		{
			RunTest(
				19 * NS, 20 * NS,
				0, 0,
				20 * NS, 20 * NS
			);

			RunTest(
				19 * NS, 25 * NS,
				0, 0,
				20 * NS, 25 * NS
			);
		}



		// --------------------------------------------------------
		// CASE GROUP 10
		// Segment fully AFTER old data
		// --------------------------------------------------------
		{
			RunTest(
				20 * NS, 25 * NS,
				0, 0,
				20 * NS, 25 * NS
			);

			RunTest(
				22 * NS, 25 * NS,
				0, 0,
				22 * NS, 25 * NS
			);
		}



		std::cout << "========================================\n";
		std::cout << "Tests run: " << testNum << "\n";
		std::cout << "Failures : " << failed << "\n";

		if (failed == 0)
			std::cout << "ALL TESTS PASSED\n";
	}

	bool MSeedProcessor::PropertyTestComputeOkSeg()
	{
		constexpr int ITER = 100000;
		constexpr nstime_t NS = NSTMODULUS;

		std::mt19937_64 rng(0xC0FFEE);

		std::uniform_int_distribution<int> rateDist(1, 200);
		std::uniform_int_distribution<int> lenDist(1, 2000);
		std::uniform_int_distribution<int64_t> baseDist(0, 60LL * NS);
		std::uniform_int_distribution<int64_t> shiftDist(-5LL * NS, 5LL * NS);

		int errors = 0;

		for (int i = 0; i < ITER; ++i)
		{
			Range oldData{};
			DataRange newSeg{}, okSeg{};

			// اعمال منطق شما برای محاسبه دقیق dT
			long double sampleRate = rateDist(rng);
			const nstime_t dT = (nstime_t)(NSTMODULUS / sampleRate + 0.5);

			int oldLen = lenDist(rng);
			int newLen = lenDist(rng);

			nstime_t oldStart = baseDist(rng);
			nstime_t oldEnd = oldStart + (oldLen - 1) * dT;

			nstime_t newStart = oldStart + shiftDist(rng);
			nstime_t newEnd = newStart + (newLen - 1) * dT;

			// آماده‌سازی بافر فرضی
			std::vector<int32_t> data(oldLen + newLen + 16, 0);

			oldData.start = oldStart;
			oldData.end = oldEnd;

			newSeg.start = newStart;
			newSeg.end = newEnd;
			newSeg.numsamples = newLen;
			newSeg.dataPtr = data.data() + oldLen;

			// مقداردهی اولیه خروجی (پاک‌سازی)
			okSeg.start = 0;
			okSeg.end = 0;
			okSeg.numsamples = 0;
			okSeg.dataPtr = nullptr;

			// Snapshot ورودی‌ها قبل از فراخوانی
			const nstime_t inOldStart = oldData.start;
			const nstime_t inOldEnd = oldData.end;
			const nstime_t inNewStart = newSeg.start;
			const nstime_t inNewEnd = newSeg.end;
			const int64_t inNewSamples = newSeg.numsamples;
			int32_t* const inNewPtr = newSeg.dataPtr;

			// --- اجرای تابع مورد تست ---
			ComputeOkSeg(oldData, newSeg, dT, okSeg);

			bool fail = false;
			bool okEmpty = (okSeg.numsamples == 0);

			// Invariant 1: بررسی سلامت ساختاری okSeg
			if (!okEmpty)
			{
				if (okSeg.start > okSeg.end) fail = true; // بازه معکوس
				if (okSeg.numsamples < 0)    fail = true; // تعداد منفی
				if (okSeg.dataPtr == nullptr) fail = true; // داده معتبر بدون اشاره‌گر
			}
			else
			{
				// اگر سگمنت خالی است، باید فیلدها منطقی باشند (مثلاً صفر یا نال)
				// طبق مشاهده قبلی، کد شما 0 برمی‌گرداند.
				if (okSeg.dataPtr != nullptr && okSeg.numsamples == 0)
				{
					// این مورد لزوماً خطا نیست ولی معمولاً نال بهتر است. فعلاً سخت‌گیری نمی‌کنیم
				}
			}

			// Invariant 2: اگر داده‌ای داریم، باید حتماً در محدوده نیوسگمنت ورودی باشد
			if (!fail && !okEmpty)
			{
				if (okSeg.start < inNewStart || okSeg.end > inNewEnd)
					fail = true;
			}

			// Invariant 3: صحت تعداد نمونه‌ها نسبت به بازه زمانی
			if (!fail && !okEmpty)
			{
				int64_t expectedSamples = (okSeg.end - okSeg.start) / dT + 1;
				if (okSeg.numsamples != expectedSamples)
					fail = true;
			}

			// Invariant 4: صحت آدرس اشاره‌گر (باید افست درستی از دیتای ورودی باشد)
			if (!fail && !okEmpty)
			{
				int64_t offsetSamples = (okSeg.start - inNewStart) / dT;
				int32_t* expectedPtr = inNewPtr + offsetSamples;

				if (okSeg.dataPtr != expectedPtr)
					fail = true;
			}

			if (fail)
			{
				++errors;
				std::cout << "\n--- PropertyTestComputeOkSeg FAILED ---\n";
				std::cout << "Iteration   : " << i << "\n";
				std::cout << "sampleRate   : " << sampleRate << "\n";
				std::cout << "dT          : " << dT << "\n";

				std::cout << "oldData (In):\n";
				std::cout << "  start     : " << inOldStart << " | end: " << inOldEnd << "\n";

				std::cout << "newSeg (In):\n";
				std::cout << "  start     : " << inNewStart << " | end: " << inNewEnd << "\n";
				std::cout << "  samples   : " << inNewSamples << " | ptr: " << (void*)inNewPtr << "\n";

				std::cout << "okSeg (Out):\n";
				std::cout << "  start     : " << okSeg.start << " | end: " << okSeg.end << "\n";
				std::cout << "  samples   : " << okSeg.numsamples << " | ptr: " << (void*)okSeg.dataPtr << "\n";

				// نمایش وضعیت نهایی newSeg که توسط تابع تغییر کرده است
				std::cout << "newSeg (Final/Out):\n";
				std::cout << "  start     : " << newSeg.start << " | end: " << newSeg.end << "\n";
				std::cout << "  samples   : " << newSeg.numsamples << " | ptr: " << (void*)newSeg.dataPtr << "\n";

				if (!okEmpty)
				{
					int64_t offsetSamples = (okSeg.start - inNewStart) / dT;
					std::cout << "Verification:\n";
					std::cout << "  Expected Ptr Offset: " << offsetSamples << " samples\n";
				}
				break;
			}

		}

		if (errors == 0)
		{
			std::cout << "PropertyTestComputeOkSeg passed (" << ITER << " cases)\n";
			return true;
		}

		return false;
	}

	bool MSeedProcessor::SimulationTestComputeOkSeg()
	{
		constexpr int ITER = 20000;
		constexpr nstime_t NS = NSTMODULUS;

		std::mt19937_64 rng(0xBADC0DE);

		std::uniform_int_distribution<int> rateDist(1, 200);
		std::uniform_int_distribution<int> lenDist(1, 3000);
		std::uniform_int_distribution<int64_t> baseDist(0, 120LL * NS);
		std::uniform_int_distribution<int64_t> shiftDist(-20LL * NS, 20LL * NS);

		int errors = 0;

		for (int it = 0; it < ITER; ++it)
		{
			long double sampleRate = rateDist(rng);
			const nstime_t dT = (nstime_t)(NSTMODULUS / sampleRate + 0.5);

			const int oldLen = lenDist(rng);
			const int newLen = lenDist(rng);

			const nstime_t oldStart = baseDist(rng);
			const nstime_t oldEnd = oldStart + (oldLen - 1) * dT;

			const nstime_t newStart0 = oldStart + shiftDist(rng);
			const nstime_t newEnd0 = newStart0 + (newLen - 1) * dT;

			Range oldData{};
			oldData.start = oldStart;
			oldData.end = oldEnd;

			std::vector<int32_t> buffer(oldLen + newLen + 64, 0);

			DataRange newSeg{};
			newSeg.start = newStart0;
			newSeg.end = newEnd0;
			newSeg.numsamples = newLen;
			newSeg.dataPtr = buffer.data() + oldLen;

			const nstime_t inputStart = newSeg.start;
			const nstime_t inputEnd = newSeg.end;
			const int64_t inputSamples = newSeg.numsamples;
			int32_t* const inputPtr = newSeg.dataPtr;

			struct ProducedSeg
			{
				nstime_t start{};
				nstime_t end{};
				int64_t numsamples{};
				int32_t* dataPtr{};
			};

			std::vector<ProducedSeg> produced;

			// حداکثر چند مرحله برای جلوگیری از loop غیرمنتظره
			bool fail = false;
			constexpr int MAX_STEPS = 8;

			for (int step = 0; step < MAX_STEPS; ++step)
			{
				DataRange okSeg{};
				okSeg.Reset();

				const nstime_t beforeStart = newSeg.start;
				const nstime_t beforeEnd = newSeg.end;
				const int64_t beforeSamples = newSeg.numsamples;
				int32_t* const beforePtr = newSeg.dataPtr;

				ComputeOkSeg(oldData, newSeg, dT, okSeg);

				const bool okEmpty = (okSeg.numsamples == 0);
				const bool newEmpty = (newSeg.numsamples == 0);

				// --- بررسی okSeg ---
				if (!okEmpty)
				{
					if (okSeg.start > okSeg.end) fail = true;
					if (okSeg.numsamples <= 0) fail = true;
					if (okSeg.dataPtr == nullptr) fail = true;

					const int64_t expectedSamples =
						(okSeg.end - okSeg.start) / dT + 1;

					if (okSeg.numsamples != expectedSamples) fail = true;

					const int64_t offsetSamples =
						(okSeg.start - beforeStart) / dT;

					int32_t* const expectedPtr = beforePtr + offsetSamples;

					if (okSeg.dataPtr != expectedPtr) fail = true;

					produced.push_back({ okSeg.start, okSeg.end, okSeg.numsamples, okSeg.dataPtr });
				}

				// --- بررسی newSeg بعد از call ---
				if (!newEmpty)
				{
					if (newSeg.start > newSeg.end) fail = true;
					if (newSeg.numsamples <= 0) fail = true;
					if (newSeg.dataPtr == nullptr) fail = true;

					// این یکی مهم است: newSeg باقیمانده باید داخل بازه قبلی newSeg بماند
					if (newSeg.start < beforeStart) fail = true;
					if (newSeg.end > beforeEnd) fail = true;
				}

				if (fail)
				{
					std::cout << "\n--- SimulationTestComputeOkSeg FAILED ---\n";
					std::cout << "Iteration : " << it << "\n";
					std::cout << "Step      : " << step << "\n";
					std::cout << "sampleRate: " << sampleRate << "\n";
					std::cout << "dT        : " << dT << "\n";

					std::cout << "oldData:\n";
					std::cout << "  start   : " << oldStart << "\n";
					std::cout << "  end     : " << oldEnd << "\n";

					std::cout << "newSeg before:\n";
					std::cout << "  start   : " << beforeStart << "\n";
					std::cout << "  end     : " << beforeEnd << "\n";
					std::cout << "  samples : " << beforeSamples << "\n";
					std::cout << "  ptr     : " << static_cast<void*>(beforePtr) << "\n";

					std::cout << "okSeg out:\n";
					std::cout << "  start   : " << okSeg.start << "\n";
					std::cout << "  end     : " << okSeg.end << "\n";
					std::cout << "  samples : " << okSeg.numsamples << "\n";
					std::cout << "  ptr     : " << static_cast<void*>(okSeg.dataPtr) << "\n";

					std::cout << "newSeg after:\n";
					std::cout << "  start   : " << newSeg.start << "\n";
					std::cout << "  end     : " << newSeg.end << "\n";
					std::cout << "  samples : " << newSeg.numsamples << "\n";
					std::cout << "  ptr     : " << static_cast<void*>(newSeg.dataPtr) << "\n";

					break;
				}

				// اگر چیزی برای ادامه نمانده، تمام
				if (newSeg.numsamples == 0)
					break;

				// اگر ComputeOkSeg هیچ تغییری نداد، یعنی newSeg برای مرحله بعدی نگه داشته شده
				const bool unchanged =
					(newSeg.start == beforeStart &&
						newSeg.end == beforeEnd &&
						newSeg.numsamples == beforeSamples &&
						newSeg.dataPtr == beforePtr);

				if (unchanged)
					break;
			}

			if (fail)
			{
				++errors;
				break;
			}

			// --- بررسی sequence خروجی‌های okSeg ---
			for (size_t i = 0; i < produced.size(); ++i)
			{
				const auto& s = produced[i];

				if (s.start > s.end)
				{
					fail = true;
					break;
				}

				const int64_t expectedSamples = (s.end - s.start) / dT + 1;
				if (s.numsamples != expectedSamples)
				{
					fail = true;
					break;
				}

				// okSeg باید کاملاً بیرون oldData باشد
				const bool fullyBefore = (s.end < oldStart);
				const bool fullyAfter = (s.start > oldEnd);

				if (!(fullyBefore || fullyAfter))
				{
					fail = true;
					break;
				}
			}

			// نباید بین produced segmentها overlap باشد
			for (size_t i = 1; i < produced.size() && !fail; ++i)
			{
				if (produced[i - 1].end >= produced[i].start)
				{
					fail = true;
					break;
				}
			}

			if (fail)
			{
				++errors;
				std::cout << "\n--- Produced sequence validation FAILED ---\n";
				std::cout << "Iteration : " << it << "\n";
				std::cout << "sampleRate: " << sampleRate << "\n";
				std::cout << "dT        : " << dT << "\n";
				std::cout << "oldData   : [" << oldStart << ", " << oldEnd << "]\n";

				for (size_t i = 0; i < produced.size(); ++i)
				{
					std::cout << "produced[" << i << "]"
						<< " start=" << produced[i].start
						<< " end=" << produced[i].end
						<< " samples=" << produced[i].numsamples
						<< " ptr=" << static_cast<void*>(produced[i].dataPtr)
						<< "\n";
				}
				break;
			}
		}

		if (errors == 0)
		{
			std::cout << "SimulationTestComputeOkSeg passed (" << ITER << " cases)\n";
			return true;
		}

		return false;
	}


	void MSeedProcessor::AddTest2(size_t _st, size_t _en, nstime_t* _stTime, double* _samRate, const char* _sid, int32_t* _data0) {
		char _time1[40] = {};
		nstime_t _ds = (int64_t)1'000'000'000 / *_samRate;	//Delta Sampling time in Nano Sec.
		std::cout << "Adding " << _st << "--" << _en << "["
			<< ms_nstime2timestr_n((*_stTime + (_st - 1) * _ds), _time1, 40, ISOMONTHDAY, MICRO) << " - "
			<< ms_nstime2timestr_n((*_stTime + (_en - 1) * _ds), _time1, 40, ISOMONTHDAY, MICRO) << "]"
			<< std::endl;
		AddNewData(_sid, (_data0 + _st), (_en - _st + 1), (*_stTime + (_st - 1) * _ds), *_samRate);
	}

	void  MSeedProcessor::Test2()
	{
		char _time1[40] = {};
		size_t _st = 0, _en = 0;

		std::string _sid = MakeSID("IR", "ST", "", "BHZ");

		int32_t _data[1000] = {};
		for (size_t i = 0; i < 1000; i++)
			_data[i] = i;

		double _samRate = 50;
		nstime_t _stTime = ms_timestr2nstime("2026-04-10T00:00:00.0");
		nstime_t _ds = (int64_t)1'000'000'000 / _samRate;	//Delta Sampling time in Nano Sec.


		////.......................................
		////                    70---90
		////    10---20
		////      15-20
		////                      75-80
		////.......................................
		////    10---20       70---90
		AddTest2(70, 90, &_stTime, &_samRate, _sid.c_str(), &_data[0]);
		AddTest2(10, 20, &_stTime, &_samRate, _sid.c_str(), &_data[0]);
		AddTest2(15, 20, &_stTime, &_samRate, _sid.c_str(), &_data[0]);
		AddTest2(75, 80, &_stTime, &_samRate, _sid.c_str(), &_data[0]);
		mstl3_printtracelist(mstl, ISOMONTHDAY, 1, 1, verbose);
		std::cout << "\n/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_\n";


		//    10---200       70---90
		//.......................................
		//       15------------75
		//.......................................
		//    10-------------------90
		AddTest2(15, 75, &_stTime, &_samRate, _sid.c_str(), &_data[0]);
		mstl3_printtracelist(mstl, ISOMONTHDAY, 1, 1, verbose);
		std::cout << "\n/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_/\\_\n";

		bool newFileWritten;
		TraceList_Export_MSeed("TestData", newFileWritten);

	}



} // namespace MSeed
