#include <yfile2miniseed/yfile_reader.hpp>
//#include "Logger.h"

namespace yfile2miniseed
{

	bool Y5FileReader::Read(y5::IReader& reader)
	{
		bool t1_ok = false;
		bool t3_ok = false;
		bool t5_ok = false;
		bool t7_ok = false;

		y5::TagFormat tagFormat{};
		y5::TagFormat firstTag{};

		// First tag
		if (!firstTag.Read(reader))
			return false;

		if (firstTag.Type != 0 || !firstTag.Valid())
			return false;

		// Main loop
		while (!t1_ok || !t3_ok || !t5_ok || !t7_ok)
		{
			if (!tagFormat.Read(reader))
				break;

			if (!tagFormat.Valid())
				return false;

			const uint16_t type = tagFormat.Type;

			switch (type)
			{
			case 1:
				if (!t1.Read(reader)) return false;
				t1_ok = true;
				break;

			case 3:
				if (!t3.Read(reader)) return false;
				t3_ok = true;
				break;

			case 5:
				if (!t5.Read(reader)) return false;
				t5_ok = true;
				break;

			case 7:
				if (t5.NumSamples != 0)
				{
					if (!t7.Read(reader, t5.NumSamples, buffer))
						return false;

					t7_ok = true;
				}
				break;

			default:
			{
				const int32_t next = tagFormat.NextTag;
				if (next > 0 && !reader.skip(next))
					return false;

				break;
			}
			}
		}

		if (y5::is_leap_second_anomaly(t5.StartTime, t5.EndTime, t5.NumSamples, t3.SampleRate))
		{
			//spdlog::info("leap second is present in ...");
		}

		return t1_ok && t3_ok && t5_ok && t7_ok;
	}

	bool Y5FileReader::ReadFromRAM(const uint8_t* data, size_t size)
	{
		y5::RAMReader r(data, size);
		return Read(r);
	}

	bool Y5FileReader::ReadFromFile(const std::string& path)
	{
		y5::MemoryMappedReader r(path);

		if (!r.is_open())
			return false;

		return Read(r);
	}
}
