#include <yfile2miniseed/mseed_processor.hpp>

#include <iostream>

int main()
{
	yfile2miniseed::MSeedProcessor processor;

	if (!processor.PropertyTestComputeOkSeg())
	{
		std::cerr << "PropertyTestComputeOkSeg failed\n";
		return 1;
	}

	if (!processor.SimulationTestComputeOkSeg())
	{
		std::cerr << "SimulationTestComputeOkSeg failed\n";
		return 1;
	}

	if (!processor.TestBuildSegDayMidnightSplit())
	{
		std::cerr << "TestBuildSegDayMidnightSplit failed\n";
		return 1;
	}

	return 0;
}
