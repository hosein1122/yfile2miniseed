#include <yfile2miniseed/mseed_processor.hpp>

#include <libmseed.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace {

struct PackCollector {
	std::vector<std::vector<char>> records;
};

void collect_record(char* record, int reclen, void* data)
{
	auto* collector = static_cast<PackCollector*>(data);
	collector->records.emplace_back(record, record + reclen);
}

MS3Record make_record(
	const std::string& sid,
	std::vector<int32_t>& data,
	nstime_t start,
	double sampleRate)
{
	MS3Record record{};
	std::strncpy(record.sid, sid.c_str(), sizeof(record.sid) - 1);
	record.reclen = 512;
	record.pubversion = 1;
	record.samprate = sampleRate;
	record.encoding = DE_INT32;
	record.sampletype = 'i';
	record.datasamples = data.data();
	record.numsamples = static_cast<int64_t>(data.size());
	record.samplecnt = static_cast<int64_t>(data.size());
	record.starttime = start;
	return record;
}

bool add_record(MS3TraceList* traceList, MS3Record& record)
{
	return mstl3_addmsr(traceList, &record, 0, 1, 0, nullptr) != nullptr;
}

int count_segments(MS3TraceList* traceList)
{
	int count = 0;
	for (MS3TraceID* tid = traceList->traces.next[0]; tid; tid = tid->next[0])
	{
		for (MS3TraceSeg* seg = tid->first; seg; seg = seg->next)
			++count;
	}
	return count;
}

int count_record_pointers(MS3TraceList* traceList)
{
	int count = 0;
	for (MS3TraceID* tid = traceList->traces.next[0]; tid; tid = tid->next[0])
	{
		for (MS3TraceSeg* seg = tid->first; seg; seg = seg->next)
		{
			if (!seg->recordlist)
				continue;
			for (MS3RecordPtr* rec = seg->recordlist->first; rec; rec = rec->next)
				++count;
		}
	}
	return count;
}

std::vector<char> pack_single_raw_record(std::vector<int32_t> data)
{
	const std::string sid = yfile2miniseed::MSeedProcessor::MakeSID("IR", "TST", "", "BHZ");
	MS3Record record = make_record(sid, data, 0, 50.0);
	PackCollector collector;
	int64_t packedSamples = 0;
	const int64_t packedRecords = msr3_pack(
		&record,
		collect_record,
		&collector,
		&packedSamples,
		MSF_FLUSHDATA | MSF_PACKVER2,
		0);

	if (packedRecords != 1 || packedSamples != static_cast<int64_t>(data.size()))
		throw std::runtime_error("Expected exactly one packed record");

	return collector.records.front();
}

std::filesystem::path unique_temp_path(const std::string& name)
{
	const auto ticks = std::chrono::duration_cast<std::chrono::milliseconds>(
		std::chrono::system_clock::now().time_since_epoch()).count();
	return std::filesystem::temp_directory_path() / ("yfile2miniseed_" + name + "_" + std::to_string(ticks));
}

void write_raw_records(const std::filesystem::path& path, const std::vector<std::vector<char>>& records)
{
	std::filesystem::create_directories(path.parent_path());
	std::ofstream out(path, std::ios::binary | std::ios::trunc);
	for (const auto& record : records)
		out.write(record.data(), static_cast<std::streamsize>(record.size()));
	if (!out)
		throw std::runtime_error("Failed to write raw record file");
}

bool test_adjacent_segments_connect()
{
	const std::string sid = yfile2miniseed::MSeedProcessor::MakeSID("IR", "TST", "", "BHZ");
	std::vector<int32_t> first(50, 1);
	std::vector<int32_t> second(50, 2);
	const nstime_t dt = NSTMODULUS / 50;

	MS3TraceList* traceList = mstl3_init(nullptr);
	MS3Record rec1 = make_record(sid, first, 0, 50.0);
	MS3Record rec2 = make_record(sid, second, static_cast<nstime_t>(first.size()) * dt, 50.0);
	const bool ok = add_record(traceList, rec1) && add_record(traceList, rec2);
	const int segments = count_segments(traceList);
	mstl3_free(&traceList, 1);
	return ok && segments == 1;
}

bool test_real_gap_does_not_connect()
{
	const std::string sid = yfile2miniseed::MSeedProcessor::MakeSID("IR", "TST", "", "BHZ");
	std::vector<int32_t> first(50, 1);
	std::vector<int32_t> second(50, 2);
	const nstime_t dt = NSTMODULUS / 50;

	MS3TraceList* traceList = mstl3_init(nullptr);
	MS3Record rec1 = make_record(sid, first, 0, 50.0);
	MS3Record rec2 = make_record(sid, second, (static_cast<nstime_t>(first.size()) + 1) * dt, 50.0);
	const bool ok = add_record(traceList, rec1) && add_record(traceList, rec2);
	const int segments = count_segments(traceList);
	mstl3_free(&traceList, 1);
	return ok && segments == 2;
}

bool test_different_sample_rates_do_not_connect()
{
	const std::string sid = yfile2miniseed::MSeedProcessor::MakeSID("IR", "TST", "", "BHZ");
	std::vector<int32_t> first(50, 1);
	std::vector<int32_t> second(40, 2);
	const nstime_t dt = NSTMODULUS / 50;

	MS3TraceList* traceList = mstl3_init(nullptr);
	MS3Record rec1 = make_record(sid, first, 0, 50.0);
	MS3Record rec2 = make_record(sid, second, static_cast<nstime_t>(first.size()) * dt, 40.0);
	const bool ok = add_record(traceList, rec1) && add_record(traceList, rec2);
	const int segments = count_segments(traceList);
	mstl3_free(&traceList, 1);
	return ok && segments == 2;
}

bool test_skip_adjacent_duplicate_raw_records()
{
	const std::filesystem::path path = unique_temp_path("duplicate") / "duplicate.mseed";
	const std::vector<char> record = pack_single_raw_record({ 1, 2, 3, 4, 5, 6, 7, 8 });
	write_raw_records(path, { record, record });

	MS3TraceList* traceList = nullptr;
	const int retcode = ms3_readtracelist(
		&traceList,
		path.string().c_str(),
		nullptr,
		0,
		MSF_VALIDATECRC | MSF_UNPACKDATA | MSF_RECORDLIST | MSF_SKIPADJACENTDUPLICATES,
		0);
	const int recordPointers = count_record_pointers(traceList);
	mstl3_free(&traceList, 1);
	std::filesystem::remove_all(path.parent_path());
	return retcode == MS_NOERROR && recordPointers == 1;
}

bool test_nonidentical_overlap_is_not_skipped()
{
	const std::filesystem::path path = unique_temp_path("overlap") / "overlap.mseed";
	const std::vector<char> first = pack_single_raw_record({ 1, 2, 3, 4, 5, 6, 7, 8 });
	const std::vector<char> second = pack_single_raw_record({ 1, 2, 3, 4, 5, 6, 7, 9 });
	write_raw_records(path, { first, second });

	MS3TraceList* traceList = nullptr;
	const int retcode = ms3_readtracelist(
		&traceList,
		path.string().c_str(),
		nullptr,
		0,
		MSF_VALIDATECRC | MSF_UNPACKDATA | MSF_RECORDLIST | MSF_SKIPADJACENTDUPLICATES,
		0);
	const int recordPointers = count_record_pointers(traceList);
	mstl3_free(&traceList, 1);
	std::filesystem::remove_all(path.parent_path());
	return retcode == MS_NOERROR && recordPointers == 2;
}

} // namespace

int main()
{
	struct Case {
		const char* name;
		bool (*fn)();
	};

	const Case cases[] = {
		{ "adjacent_segments_connect", test_adjacent_segments_connect },
		{ "real_gap_does_not_connect", test_real_gap_does_not_connect },
		{ "different_sample_rates_do_not_connect", test_different_sample_rates_do_not_connect },
		{ "skip_adjacent_duplicate_raw_records", test_skip_adjacent_duplicate_raw_records },
		{ "nonidentical_overlap_is_not_skipped", test_nonidentical_overlap_is_not_skipped },
	};

	for (const auto& test : cases)
	{
		if (!test.fn())
		{
			std::cerr << test.name << " failed\n";
			return 1;
		}
	}

	std::cout << "libmseed workflow tests passed\n";
	return 0;
}
