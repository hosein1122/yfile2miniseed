#pragma once

#include <string>
#include <vector>
#include <map>

namespace yfile2miniseed::cli::stats
{

	struct Range
	{
		double start;
		double end;

		Range(double _start, double _end);
	};

	void WriteStats();

	void printD(bool showTime = false);

	void checkNewData(std::string ID, Range newRange, float sampleRate);

	void test();

}
