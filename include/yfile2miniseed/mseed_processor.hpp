#pragma once

#include <libmseed.h>
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
		int64_t WriteMSeed(const std::string& outputFile, bool miniSeedVersion3 = true);


		bool TraceList_Export_MSeed(const std::string& BasePath, bool& anyNewFileWritten, bool miniSeedVersion3 = true);

		void ClearTraceList();

		//test
		bool PropertyTestComputeOkSeg();
		bool SimulationTestComputeOkSeg();
		void TestComputeOkSeg();
		void Test2();

	private:
		MS3Record* msr = nullptr;
		MS3TraceList* mstl = nullptr;

		int8_t verbose = 0;

		int reclen = 4096; /* Desired maximum record length */
		uint8_t encoding = DE_STEIM2; /* Desired data encoding */




		void PrintSamples(MS3TraceID* tid, MS3TraceSeg* seg) const;

		void AddNewDataTo(
			MS3TraceList* out_mstl,
			const char* sid,
			const int32_t* data, int64_t numsamples, int64_t startTime,
			double samprate
		);

		bool ReadMSeedTo(const std::string& inputFile, MS3TraceList*& outMstl);

		bool RepackMSeedFileOnce(const std::string& mseedFile, bool miniSeedVersion3);

		static bool CreateDirectoryIfNotExists(const std::string& path);

		static void GetDirectoryAndFileName(
			std::string& outPath,
			std::string& outMSeedFile,
			nstime_t startDate,
			const char sid[LM_SIDLEN],
			const std::string& basePath);

		//const nstime_t dT = static_cast<nstime_t>(1e9 / samprate);  //Delta Sampling time in Nano Sec.
		static void ComputeOkSeg(
			const Range& oldData,
			DataRange& newSeg,
			nstime_t dT,
			DataRange& okSeg);

		static void FillDataAvailable(
			MS3TraceList* out_mstl,
			std::map<double, std::vector<Range>>& oldDataDic,
			const char* sid);

		void BuildSegDay(const MS3TraceSeg* seg,
			nstime_t dayStart,
			nstime_t dayEnd,
			DataRange& segDay);

		bool LoadOldMSeedIfExists(
			const std::string& mseedPath,
			const std::string& mseedFile,
			MS3TraceList*& outList,
			std::map<double, std::vector<Range>>& oldDataDic,
			const char* sid);

		bool AppendSegDayAvoidDuplicate(
			MS3TraceList* out_mstl,
			const char* sid,
			DataRange& segDay,
			double samprate,
			std::map<double, std::vector<Range>>& oldDataDic);

		void AddTest2(size_t _st, size_t _en, nstime_t* _stTime, double* _samRate, const char* _sid, int32_t* _data0);
	};

} // namespace MSeed
