#pragma once

#include <zip.h>
#include <string>
#include <vector>
#include <cstdint>

namespace yfile2miniseed::cli::ziputils
{

	// Holds one extracted file from the zip archive
	struct ExtractedFile {
		std::string name;
		std::vector<uint8_t> data;
	};

	// Maximum allowed extracted file size (protection against zip bombs)
	inline constexpr std::uint64_t MAX_ENTRY_SIZE =
		512ull * 1024ull * 1024ull; // 512MB



	// RAII wrapper for zip_t*
	// Automatically closes the archive using zip_close()
	class ZipHandle {
	public:

		// h:
		//     Raw zip archive handle returned by zip_open()
		explicit ZipHandle(zip_t* h);

		~ZipHandle();

		// Move support
		ZipHandle(ZipHandle&& other) noexcept;
		ZipHandle& operator=(ZipHandle&& other) noexcept;

		// Returns underlying zip_t*
		zip_t* get() const;

		ZipHandle(const ZipHandle&) = delete;
		ZipHandle& operator=(const ZipHandle&) = delete;

	private:
		zip_t* handle = nullptr;
	};



	// RAII wrapper for zip_file_t*
	// Automatically closes the file using zip_fclose()
	class ZipFileHandle {
	public:

		// f:
		//     Raw zip file handle returned by zip_fopen_index()
		explicit ZipFileHandle(zip_file_t* f);

		~ZipFileHandle();

		// Move support
		ZipFileHandle(ZipFileHandle&& other) noexcept;
		ZipFileHandle& operator=(ZipFileHandle&& other) noexcept;

		// Returns underlying zip_file_t*
		zip_file_t* get() const;

		ZipFileHandle(const ZipFileHandle&) = delete;
		ZipFileHandle& operator=(const ZipFileHandle&) = delete;

	private:
		zip_file_t* file = nullptr;
	};



	// Checks whether the given file can be opened as a ZIP archive
	//
	// zipPath:
	//     Path to the zip file
	//
	// Returns:
	//     true  -> valid/openable zip
	//     false -> invalid zip or open failed
	bool IsZipFile(const std::string& zipPath);



	// Extracts all files from a ZIP archive into memory
	//
	// zipPath:
	//     Path to the zip archive
	//
	// Returns:
	//     Vector containing extracted file names and binary data
	//
	// Throws:
	//     std::runtime_error on open/read/extract errors
	std::vector<ExtractedFile>
		ExtractZipToMemory(const std::string& zipPath);
}



///----example----

//if (ziputils::IsZipFile(path))
//{
//    auto files = ziputils::ExtractZipToMemory(path);
//    for (const auto& f : files)
//    {
//        MyYFile y;
//        if (!y.ReadFromRAM(f.data.data(), f.data.size()))
//        {
//            // handle read error
//            continue;
//        }
//        // process parsed y-file here
//    }
//}
//else
//{
//    // handle non-zip input
//}

