#pragma once

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>
#include <vector>
#include <string>
// Platform headers for mmap
#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

#ifdef _MSC_VER
#include <stdlib.h>
#define Y5_BSWAP32(x) _byteswap_ulong(x)
#else
#define Y5_BSWAP32(x) __builtin_bswap32(x)
#endif



namespace y5
{
	enum class Endian
	{
		Little,
		Big
	};

	// برای اینکه در هر لحظه بدونیم فایل فعلی چه اندیانی داره
	// thread_local تضمین می‌کنه هر ترد مستقل باشه
	inline thread_local Endian g_currentYFileEndian = Endian::Little;

	// تابعی برای ست کردنش
	inline void setYFileEndian(Endian e) { g_currentYFileEndian = e; }
	inline Endian getYFileEndian() { return g_currentYFileEndian; }


	inline bool system_is_little()
	{
		uint16_t v = 1;
		return *(uint8_t*)&v == 1;
	}

	inline bool endian_match()
	{
		return (system_is_little() && getYFileEndian() == Endian::Little) ||
			(!system_is_little() && getYFileEndian() == Endian::Big);
	}

	inline uint16_t bswap16(uint16_t v)
	{
		return (v >> 8) | (v << 8);
	}

	inline uint32_t bswap32(uint32_t v)
	{
		return  (v >> 24) |
			((v >> 8) & 0x0000FF00) |
			((v << 8) & 0x00FF0000) |
			(v << 24);
	}

	inline uint64_t bswap64(uint64_t v)
	{
		return  (v >> 56) |
			((v >> 40) & 0x000000000000FF00ULL) |
			((v >> 24) & 0x0000000000FF0000ULL) |
			((v >> 8) & 0x00000000FF000000ULL) |
			((v << 8) & 0x000000FF00000000ULL) |
			((v << 24) & 0x0000FF0000000000ULL) |
			((v << 40) & 0x00FF000000000000ULL) |
			(v << 56);
	}

	template<typename T>
	inline T byteswap(T v)
	{
		if constexpr (sizeof(T) == 2) return (T)bswap16((uint16_t)v);
		if constexpr (sizeof(T) == 4) return (T)bswap32((uint32_t)v);
		if constexpr (sizeof(T) == 8) return (T)bswap64((uint64_t)v);
		return v;
	}

	template<typename T>
	inline void endian_fix(T& v)
	{
		if (system_is_little() && getYFileEndian() == Endian::Big)
			v = byteswap(v);

		if (!system_is_little() && getYFileEndian() == Endian::Little)
			v = byteswap(v);
	}


	// تست فوق سریع برای بررسی صحت زمان‌بندی
	inline bool is_leap_second_anomaly(double& start, double& end, uint32_t& numSamples, float& sampleRate)
	{
		// محاسبه تعداد نمونه‌های مورد انتظار با دقت بالا
		double duration = end - start;
		double expectedSamples = (duration * sampleRate) + 0.5; // +0.5 برای گرد کردن صحیح

		// اختلاف را چک می‌کنیم
		int64_t diff = std::abs((int64_t)expectedSamples - (int64_t)numSamples);

		// اگر اختلاف دقیقاً برابر با SampleRate باشد (یا نزدیک آن)، یعنی یک ثانیه خطا داریم
		// این یعنی سیستم در محاسبه زمان‌بندی Leap Second را لحاظ نکرده یا اشتباه کرده
		if (diff > 0 && std::abs(diff - (int64_t)sampleRate) < 5) // بازه 5 نمونه‌ای برای خطای ناچیز
		{
			return true;
		}
		return false;
	}



	// ===========================================================
	//  IReader (abstract base class)
	// ===========================================================

	class IReader
	{
	public:
		virtual ~IReader() = default;

		virtual bool read(void* dst, size_t sz) = 0;
		virtual bool skip(size_t sz) = 0;

		// Optional zero-copy fast path
		virtual const uint8_t* direct(size_t sz) { return nullptr; }
		virtual const uint8_t* peek(size_t sz) { return nullptr; }

	};


	// ===========================================================
	//  RAMReader (zero-copy capable)
	// ===========================================================

	class RAMReader : public IReader
	{
	public:
		RAMReader(const uint8_t* ptr, size_t sz)
			: data(ptr), size(sz) {}

		bool read(void* dst, size_t sz) override
		{
			if (pos + sz > size) return false;
			std::memcpy(dst, data + pos, sz);
			pos += sz;
			return true;
		}

		bool skip(size_t sz) override
		{
			if (pos + sz > size) return false;
			pos += sz;
			return true;
		}

		const uint8_t* direct(size_t sz) override
		{
			if (pos + sz > size) return nullptr;
			const uint8_t* p = data + pos;
			pos += sz;
			return p;
		}

		const uint8_t* peek(size_t sz) override
		{
			if (pos + sz > size) return nullptr;
			return data + pos;
		}


	private:
		const uint8_t* data;
		size_t size;
		size_t pos = 0;
	};


	// ===========================================================
	//  MemoryMappedReader (cross‑platform mmap / MapViewOfFile)
	// ===========================================================

	class MemoryMappedReader : public IReader
	{
	public:
		MemoryMappedReader(const std::string& path)
		{
#ifdef _WIN32
			HANDLE hFile = CreateFileA(path.c_str(), GENERIC_READ, FILE_SHARE_READ,
				nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);

			if (hFile == INVALID_HANDLE_VALUE) return;

			LARGE_INTEGER sz;
			GetFileSizeEx(hFile, &sz);
			fileSize = (size_t)sz.QuadPart;

			hMap = CreateFileMappingA(hFile, nullptr, PAGE_READONLY, 0, 0, nullptr);
			if (!hMap) return;

			data = (uint8_t*)MapViewOfFile(hMap, FILE_MAP_READ, 0, 0, 0);

			CloseHandle(hFile);

#else
			fd = open(path.c_str(), O_RDONLY);
			if (fd < 0) return;

			struct stat st;
			if (fstat(fd, &st) < 0) return;

			fileSize = st.st_size;

			data = (uint8_t*)mmap(nullptr, fileSize, PROT_READ, MAP_PRIVATE, fd, 0);
			if (data == MAP_FAILED)
			{
				data = nullptr;
				return;
			}
#endif
		}

		~MemoryMappedReader()
		{
#ifdef _WIN32
			if (data) UnmapViewOfFile(data);
			if (hMap) CloseHandle(hMap);
#else
			if (data) munmap(data, fileSize);
			if (fd >= 0) close(fd);
#endif
		}

		bool is_open() const { return data != nullptr; }

		bool read(void* dst, size_t sz) override
		{
			if (pos + sz > fileSize) return false;
			std::memcpy(dst, data + pos, sz);
			pos += sz;
			return true;
		}

		bool skip(size_t sz) override
		{
			if (pos + sz > fileSize) return false;
			pos += sz;
			return true;
		}

		const uint8_t* direct(size_t sz) override
		{
			if (pos + sz > fileSize) return nullptr;
			const uint8_t* p = data + pos;
			pos += sz;
			return p;
		}

		const uint8_t* peek(size_t sz) override
		{
			if (pos + sz > fileSize) return nullptr;
			return data + pos;
		}


	private:
		size_t fileSize = 0;
		size_t pos = 0;
		uint8_t* data = nullptr;

#ifdef _WIN32
		HANDLE hMap = nullptr;
#else
		int fd = -1;
#endif
	};


	// ===========================================================
	//  Primitive read
	// ===========================================================

	template<typename T>
	bool read_value(IReader& r, T& v)
	{
		if (!r.read(&v, sizeof(T))) return false;
		endian_fix(v);
		return true;
	}


	//-----------------------  Y-Tags ------------------------------

#pragma pack(push,1)
	struct TagFormat
	{
		uint8_t  Format;
		uint8_t  Magic;			//Must be 31 !!
		uint16_t Type;
		int32_t  NextTag;
		int32_t  NextSame;
		int32_t  Spare;

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			if (!r.read(&Format, 1))
				return false;
			y5::setYFileEndian((Format == 'I') ? Endian::Little : Endian::Big);

			r.read(&Magic, 1);

			r.read(&Type, sizeof(Type));
			endian_fix(Type);

			r.read(&NextTag, sizeof(NextTag));
			endian_fix(NextTag);

			r.read(&NextSame, sizeof(NextSame));
			endian_fix(NextSame);

			r.read(&Spare, sizeof(Spare));
			endian_fix(Spare);

			return true;
		}

		bool Valid() const
		{
			return Magic == 31;
		}
	};
#pragma pack(pop)


	struct STNID
	{
		uint8_t Station[6];     //required (BLANKPAD) 5 + 1(tail terminating 0)
		uint8_t Location[3];    //required (BLANKPAD) 2 + 1(tail terminating 0)
		uint8_t Channel[4];     //required (BLANKPAD) 3 + 1(tail terminating 0)

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			uint8_t buf[10];

			if (!r.read(buf, sizeof(buf)))
				return false;

			memcpy(Station, buf + 0, 5);
			memcpy(Location, buf + 5, 2);
			memcpy(Channel, buf + 7, 3);

			Station[5] = 0;
			Location[2] = 0;
			Channel[3] = 0;

			return true;
		}
	};

	struct Tag1_StationInfo
	{
		STNID StationID;            //required	
		uint8_t NetworkID[51];      // (ASCIIZ)
		uint8_t SiteName[61];       // (ASCIIZ)
		uint8_t Comment[31];        // (ASCIIZ)
		uint8_t SensorType[51];     // (ASCIIZ)
		uint8_t DataFormat[7];      // (ASCIIZ)

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			uint8_t buf[219];

			if (!r.read(buf, sizeof(buf)))
				return false;

			const uint8_t* p = buf;

			p += 8; // skip

			memcpy(StationID.Station, p, 5); p += 5;
			memcpy(StationID.Location, p, 2); p += 2;
			memcpy(StationID.Channel, p, 3); p += 3;

			StationID.Station[5] = 0;
			StationID.Location[2] = 0;
			StationID.Channel[3] = 0;

			memcpy(NetworkID, p, sizeof(NetworkID));  p += sizeof(NetworkID);
			memcpy(SiteName, p, sizeof(SiteName));   p += sizeof(SiteName);
			memcpy(Comment, p, sizeof(Comment));    p += sizeof(Comment);
			memcpy(SensorType, p, sizeof(SensorType)); p += sizeof(SensorType);
			memcpy(DataFormat, p, sizeof(DataFormat));

			return true;
		}
	};

	struct Tag2_StationLocation
	{
		float Latitude;     //required
		float Longitude;    //required
		float Elevation;
		float Depth;
		float Azimuth;
		float Dip;

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			if (!r.skip(8)) return false;

			float buf[6];

			if (!r.read(buf, sizeof(buf)))
				return false;

			if (!endian_match())
			{
				uint32_t* p = reinterpret_cast<uint32_t*>(buf);

				for (int i = 0; i < 6; ++i)
					p[i] = byteswap(p[i]);
			}

			Latitude = buf[0];
			Longitude = buf[1];
			Elevation = buf[2];
			Depth = buf[3];
			Azimuth = buf[4];
			Dip = buf[5];

			return true;
		}
	};

	struct Tag3_StationParameters
	{
		double StartValidTime;      //number of seconds Since 1970-01-01 
		double EndValidTime;        //number of seconds Since 1970-01-01 
		float Sensitivity;          //Nanometers per bit
		float SensFreq;
		float SampleRate;           //required
		float MaxClkDrift;
		uint8_t SensUnits[24];      //(ASCIIZ)
		uint8_t CalibUnits[24];     //(ASCIIZ)
		uint8_t ChanFlags[28];      //(BLANKPAD) 27 + 1(tail terminating 0)
		uint8_t UpdateFlag;
		uint8_t Filter[4];

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			uint8_t buf[128];

			if (!r.read(buf, sizeof(buf)))
				return false;

			const uint8_t* p = buf;

			p += 16; // skip

			uint64_t t0, t1;
			memcpy(&t0, p, 8); p += 8;
			memcpy(&t1, p, 8); p += 8;

			if (!endian_match())
			{
				t0 = byteswap(t0);
				t1 = byteswap(t1);
			}

			memcpy(&StartValidTime, &t0, 8);
			memcpy(&EndValidTime, &t1, 8);

			uint32_t f[4];
			memcpy(f, p, sizeof(f));
			p += sizeof(f);

			if (!endian_match())
			{
				f[0] = byteswap(f[0]);
				f[1] = byteswap(f[1]);
				f[2] = byteswap(f[2]);
				f[3] = byteswap(f[3]);
			}

			memcpy(&Sensitivity, &f[0], 4);
			memcpy(&SensFreq, &f[1], 4);
			memcpy(&SampleRate, &f[2], 4);
			memcpy(&MaxClkDrift, &f[3], 4);

			memcpy(SensUnits, p, sizeof(SensUnits));  p += sizeof(SensUnits);
			memcpy(CalibUnits, p, sizeof(CalibUnits)); p += sizeof(CalibUnits);

			memcpy(ChanFlags, p, 27); p += 27;
			ChanFlags[27] = 0;

			UpdateFlag = *p++;
			memcpy(Filter, p, sizeof(Filter));

			return true;
		}
	};

	struct Tag4_StationDatabase
	{
		double LoadDate;                //number of seconds Since 1970-01-01 
		uint8_t Key[16];

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			if (!r.skip(8)) return false;

			if (!read_value(r, LoadDate)) return false;
			if (!r.read(Key, sizeof(Key)))    return false;

			return true;
		}
	};

	struct Tag5_SeriesInfo
	{
		double StartTime;               //required	//number of seconds Since 1970-01-01
		double EndTime;                 //required	//number of seconds Since 1970-01-01
		uint32_t NumSamples;            //required
		int32_t  DCOffset;              //required
		int32_t  MaxAmplitude;          //required
		int32_t  MinAmplitude;          //required
		uint8_t  Format[8];             //(ASCIIZ)
		uint8_t  FormatVersion[8];      //(ASCIIZ)

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			uint8_t buf[64];

			if (!r.read(buf, sizeof(buf)))
				return false;

			const uint8_t* p = buf;

			p += 16; // skip

			uint64_t t0, t1;

			memcpy(&t0, p, 8); p += 8;
			memcpy(&t1, p, 8); p += 8;

			if (!endian_match())
			{
				t0 = byteswap(t0);
				t1 = byteswap(t1);
			}

			memcpy(&StartTime, &t0, 8);
			memcpy(&EndTime, &t1, 8);

			uint32_t v[4];
			memcpy(v, p, sizeof(v));
			p += sizeof(v);

			if (!endian_match())
			{
				v[0] = byteswap(v[0]);
				v[1] = byteswap(v[1]);
				v[2] = byteswap(v[2]);
				v[3] = byteswap(v[3]);
			}

			NumSamples = v[0];
			DCOffset = (int32_t)v[1];
			MaxAmplitude = (int32_t)v[2];
			MinAmplitude = (int32_t)v[3];

			memcpy(Format, p, sizeof(Format)); p += sizeof(Format);
			memcpy(FormatVersion, p, sizeof(FormatVersion));

			return true;
		}
	};

	struct Tag6_SeriesDatabase
	{
		double LoadDate;    //number of seconds Since 1970-01-01
		uint8_t Key[16];

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			if (!r.skip(8)) return false;

			if (!read_value(r, LoadDate)) return false;
			if (!r.read(Key, sizeof(Key)))    return false;

			return true;
		}
	};

	struct Tag7_DataSamples
	{
		const int32_t* samples = nullptr;
		uint32_t count = 0;

		void Reset()
		{
			samples = nullptr;
			count = 0;
		}

		bool Read(IReader& r,
			uint32_t sampleCount,
			std::vector<int32_t>& scratch)
		{
			Reset();

			count = sampleCount;
			const size_t bytes = size_t(count) * sizeof(int32_t);

			// ---- 1) Try Zero Copy ----
			const int32_t* p =
				reinterpret_cast<const int32_t*>(r.peek(bytes));

			if (p && endian_match() &&
				((reinterpret_cast<uintptr_t>(p) &
					(alignof(int32_t) - 1)) == 0))
			{
				r.skip(bytes);
				samples = p;
				return true;
			}

			// ---- 2) Use scratch buffer (NO allocation if capacity enough) ----
			if (scratch.size() < count)
				scratch.resize(count);  // allocate only if needed

			if (!r.read(scratch.data(), bytes))
				return false;

			//if (system_is_little() && getYFileEndian() == Endian::Big)
			if (!endian_match())
			{
				size_t i = 0;
				size_t n = count;

				for (; i + 4 <= n; i += 4)
				{
					scratch[i + 0] = Y5_BSWAP32(scratch[i + 0]);
					scratch[i + 1] = Y5_BSWAP32(scratch[i + 1]);
					scratch[i + 2] = Y5_BSWAP32(scratch[i + 2]);
					scratch[i + 3] = Y5_BSWAP32(scratch[i + 3]);
				}

				for (; i < n; ++i)
					scratch[i] = Y5_BSWAP32(scratch[i]);
			}

			samples = scratch.data();
			return true;
		}
	};


	struct Tag26_StationResponse
	{
		uint8_t PathName[260];

		void Reset()
		{
			*this = {};
		}

		bool Read(IReader& r)
		{
			Reset();

			if (!r.skip(8)) return false;
			return r.read(PathName, sizeof(PathName));
		}
	};

} // namespace y5
