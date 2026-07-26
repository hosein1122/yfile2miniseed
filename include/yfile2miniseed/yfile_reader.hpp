#pragma once

#include <yfile2miniseed/detail/y5_binary_parser.hpp>
#include <cstdint>
#include <string>



namespace yfile2miniseed
{
	class Y5FileReader
	{

	public:
		// Parsed tags
		y5::Tag1_StationInfo t1{};
		y5::Tag3_StationParameters t3{};
		y5::Tag5_SeriesInfo t5{};
		y5::Tag7_DataSamples t7{};

		// Sample buffer (used if zero-copy is not possible)
		std::vector<int32_t> buffer;

	public:
		bool Read(y5::IReader& reader);
		bool ReadFromRAM(const uint8_t* data, size_t size);
		bool ReadFromFile(const std::string& path);

	};

}