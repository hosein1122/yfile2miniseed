#pragma once

#include <libmseed.h>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <map>
#include <vector>


namespace yfile2miniseed {

	// --------------------
	// Structs
	// --------------------

	struct DataRange {
		int32_t* dataPtr = nullptr;
		nstime_t start = 0;
		nstime_t end = 0;
		int64_t numsamples = 0;

		void Reset() {
			dataPtr = nullptr;
			start = 0;
			end = 0;
			numsamples = 0;
		}
	};


	struct Range {
		nstime_t start = 0;
		nstime_t end = 0;
	};

	// --------------------
	// Main Processor Class
	// --------------------

	class MSeedProcessor {
	public:
		MSeedProcessor();
		~MSeedProcessor();

		/**
		 * @brief Copy operations are disabled.
		 *
		 * MSeedProcessor manages libmseed resources (MS3Record and MS3TraceList)
		 * through raw pointers. Copying the object would result in multiple
		 * instances owning the same underlying resources, which could lead to
		 * double-free errors and undefined behavior.
		 *
		 * Therefore copy constructor and copy assignment are explicitly deleted.
		 */
		MSeedProcessor(const MSeedProcessor&) = delete;
		MSeedProcessor& operator=(const MSeedProcessor&) = delete;


		/// <summary>
		/// جزئیات تریس لیست را ارائه میدهد
		/// </summary>
		void PrintTraceList(bool printData = false) const;

		static std::string MakeSID(const char* network, const char* station, const char* location, const char* channel);

		/// <summary>
		/// افزودن داده جدید به لیست تریس در رم
		/// </summary>
		void AddNewData(
			const char* sid,
			const int32_t* data, int64_t numsamples, int64_t startTime,
			double samprate
		);

		/// <summary>
		/// افزودن داده جدید به لیست تریس در رم
		/// </summary>
		void AddNewData(
			const char* sid,
			const int32_t* data, int64_t numsamples, const char* startTimeStr,
			double samprate);

		/// <summary>
		/// نمایش وضعیت داده‌ها و گپ‌ها در TraceList موجود.
		/// </summary>
		void ShowDataAvailability(bool showGapDetails = false);


		/// <summary>
		/// mseed نسخه 2 و 3 را میخواند و به TraceList اضافه میکند.
		/// </summary>
		/// <param name="inputFile">فایل miniseed ورودی</param>
		/// <param name="TotalSampleNum">اگر عملیات خواندن موفق باشد، تعداد کل نمونه های درون trace list در این پارامتر قرار میگیرد</param>
		/// <returns> اگر موفق نبود false برمیگرداند</returns>
		bool ReadMSeed(const std::string& inputFile, size_t& TotalSampleNum);

		/// <summary>
		/// ذخیره TraceList به فایل MiniSEED
		/// </summary>
		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		int64_t WriteMSeed(const std::string& outputFile, bool miniSeedVersion3 = true);


		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		bool TraceList_Export_MSeed(const std::string& BasePath, bool& anyNewFileWritten, bool miniSeedVersion3 = true);

		void ClearTraceList();

		bool BeginPendingSession(const std::string& sdsRoot);
		bool AppendYFileToPendingSession(
			const char* sid,
			const int32_t* data,
			int64_t numsamples,
			int64_t startTime,
			double samprate,
			bool miniSeedVersion3 = true);
		bool AppendMSeedFileToPendingSession(const std::string& inputFile);
		bool ClosePendingWriters();
		bool FinalizePendingSession(bool miniSeedVersion3 = true);
		bool FinalizePendingSessionAppendOnly(bool sortAndDeduplicate, bool miniSeedVersion3 = true);
		const std::filesystem::path& PendingSessionPath() const { return pendingSessionPath; }

		//test
		bool PropertyTestComputeOkSeg();
		bool SimulationTestComputeOkSeg();
		bool TestBuildSegDayMidnightSplit();
		void TestComputeOkSeg();
		void Test2();

	private:
		MS3Record* msr = nullptr;
		MS3TraceList* mstl = nullptr;

		int8_t verbose = 0;

		int reclen = 4096; /* Desired maximum record length */
		uint8_t encoding = DE_STEIM2; /* Desired data encoding */

		struct PendingWriter {
			std::filesystem::path path;
			std::ofstream stream;
			uint64_t lastUsed = 0;
		};

		std::filesystem::path pendingSdsRoot;
		std::filesystem::path pendingSessionPath;
		std::string pendingSessionName;
		std::map<std::string, std::filesystem::path> pendingFilesByFinal;
		std::map<std::string, PendingWriter> pendingWriters;
		uint64_t pendingWriterClock = 0;
		size_t maxOpenPendingWriters = 64;




		void PrintSamples(MS3TraceID* tid, MS3TraceSeg* seg) const;

		void AddNewDataTo(
			MS3TraceList* out_mstl,
			const char* sid,
			const int32_t* data, int64_t numsamples, int64_t startTime,
			double samprate
		);

		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		int64_t WriteDataRangeToMSeed(
			const std::string& mseedFile,
			const char* sid,
			const DataRange& segDay,
			double samprate,
			bool overwrite,
			bool miniSeedVersion3);

		bool ReadMSeedTo(const std::string& inputFile, MS3TraceList*& outMstl);

		bool ReadMSeedTo(
			const std::string& inputFile,
			MS3TraceList*& outMstl,
			uint32_t flags,
			const char* label);

		bool RepackMSeedFileOnce(const std::string& mseedFile, bool miniSeedVersion3);

		static bool CreateDirectoryIfNotExists(const std::string& path);

		static void GetDirectoryAndFileName(
			std::string& outPath,
			std::string& outMSeedFile,
			nstime_t startDate,
			const char sid[LM_SIDLEN],
			const std::string& basePath);

		static bool BuildStrictSDSPath(
			std::filesystem::path& outPath,
			nstime_t startDate,
			const char sid[LM_SIDLEN],
			const std::filesystem::path& basePath);

		bool WritePendingRecord(const char* record, int reclen);
		static void PendingRecordHandler(char* record, int reclen, void* handlerdata);
		bool OpenPendingWriter(const std::filesystem::path& finalPath, PendingWriter*& writer);
		std::filesystem::path PendingPathForFinal(const std::filesystem::path& finalPath) const;
		bool ResolveStaleCommitState(const std::filesystem::path& finalPath) const;
		bool CommitOnePendingFile(
			const std::filesystem::path& finalPath,
			const std::filesystem::path& pendingPath,
			bool miniSeedVersion3);
		bool CommitOnePendingFileAppendOnly(
			const std::filesystem::path& finalPath,
			const std::filesystem::path& pendingPath);
		bool RewriteFinalFileFromTraceList(
			const std::filesystem::path& finalPath,
			uint32_t readFlags,
			const char* label,
			bool miniSeedVersion3);
		bool ValidateMSeedFile(const std::filesystem::path& path) const;
		bool WriteTraceListFile(
			MS3TraceList* traceList,
			const std::filesystem::path& path,
			bool miniSeedVersion3);
		void CleanupPendingSessionIfEmpty() const;

		//const nstime_t dT = static_cast<nstime_t>(1e9 / samprate);  //Delta Sampling time in Nano Sec.
		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		static void ComputeOkSeg(
			const Range& oldData,
			DataRange& newSeg,
			nstime_t dT,
			DataRange& okSeg);

		static void FillDataAvailable(
			MS3TraceList* out_mstl,
			std::map<double, std::vector<Range>>& oldDataDic,
			const char* sid);

		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		void BuildSegDay(const MS3TraceSeg* seg,
			nstime_t dayStart,
			nstime_t dayEnd,
			DataRange& segDay);

		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		bool LoadOldMSeedIfExists(
			const std::string& mseedPath,
			const std::string& mseedFile,
			MS3TraceList*& outList,
			std::map<double, std::vector<Range>>& oldDataDic,
			const char* sid);

		// Legacy SDS export path.
		// Retained temporarily for regression comparison.
		// Not used by the new CLI SDS workflow.
		bool AppendSegDayAvoidDuplicate(
			MS3TraceList* out_mstl,
			const char* sid,
			DataRange& segDay,
			double samprate,
			std::map<double, std::vector<Range>>& oldDataDic);

		void AddTest2(size_t _st, size_t _en, nstime_t* _stTime, double* _samRate, const char* _sid, int32_t* _data0);
	};

} // namespace MSeed
