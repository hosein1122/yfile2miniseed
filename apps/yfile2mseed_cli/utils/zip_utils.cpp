#include "zip_utils.hpp"

#include <utility>
#include <stdexcept>

namespace yfile2miniseed::cli::ziputils
{

	//========================================================
	// ZipHandle
	//========================================================

	ZipHandle::ZipHandle(zip_t* h) : handle(h) {}

	ZipHandle::~ZipHandle()
	{
		if (handle)
			zip_close(handle);
	}

	ZipHandle::ZipHandle(ZipHandle&& other) noexcept
		: handle(other.handle)
	{
		other.handle = nullptr;
	}

	ZipHandle& ZipHandle::operator=(ZipHandle&& other) noexcept
	{
		if (this != &other)
		{
			if (handle)
				zip_close(handle);

			handle = other.handle;
			other.handle = nullptr;
		}

		return *this;
	}

	zip_t* ZipHandle::get() const
	{
		return handle;
	}


	//========================================================
	// ZipFileHandle
	//========================================================

	ZipFileHandle::ZipFileHandle(zip_file_t* f)
		: file(f)
	{}

	ZipFileHandle::~ZipFileHandle()
	{
		if (file)
			zip_fclose(file);
	}

	ZipFileHandle::ZipFileHandle(ZipFileHandle&& other) noexcept
		: file(other.file)
	{
		other.file = nullptr;
	}

	ZipFileHandle& ZipFileHandle::operator=(ZipFileHandle&& other) noexcept
	{
		if (this != &other)
		{
			if (file)
				zip_fclose(file);

			file = other.file;
			other.file = nullptr;
		}

		return *this;
	}

	zip_file_t* ZipFileHandle::get() const
	{
		return file;
	}


	//========================================================
	// Utilities
	//========================================================

	bool IsZipFile(const std::string& zipPath)
	{
		int err = 0;

		zip_t* archive = zip_open(zipPath.c_str(), ZIP_RDONLY, &err);

		if (!archive)
			return false;

		zip_close(archive);

		return true;
	}


	std::vector<ExtractedFile>
		ExtractZipToMemory(const std::string& zipPath)
	{
		int err = 0;

		zip_t* rawArchive =
			zip_open(zipPath.c_str(), ZIP_RDONLY, &err);

		if (!rawArchive)
			throw std::runtime_error("Failed to open zip file");

		ZipHandle archive(rawArchive);

		zip_int64_t numEntries =
			zip_get_num_entries(archive.get(), 0);

		if (numEntries < 0)
			throw std::runtime_error("Failed to read zip entries");

		std::vector<ExtractedFile> result;

		result.reserve(static_cast<size_t>(numEntries));

		for (zip_uint64_t i = 0;
			i < static_cast<zip_uint64_t>(numEntries);
			++i)
		{
			zip_stat_t st;

			zip_stat_init(&st);

			if (zip_stat_index(archive.get(), i, 0, &st) != 0)
			{
				throw std::runtime_error(
					"Failed to stat zip entry");
			}

			std::string name = st.name ? st.name : "";

			if (!name.empty() && name.back() == '/')
				continue;

			if (static_cast<uint64_t>(st.size) > MAX_ENTRY_SIZE)
			{
				throw std::runtime_error(
					"Zip entry too large or invalid");
			}

			ZipFileHandle zf(
				zip_fopen_index(archive.get(), i, 0));

			if (!zf.get())
			{
				throw std::runtime_error(
					"Failed to open zip entry");
			}

			uint64_t size = static_cast<uint64_t>(st.size);

			ExtractedFile file;

			file.name = std::move(name);
			file.data.resize(static_cast<size_t>(size));

			if (size == 0)
			{
				result.emplace_back(std::move(file));
				continue;
			}

			uint8_t* buffer = file.data.data();

			uint64_t totalRead = 0;

			while (totalRead < size)
			{
				zip_int64_t n =
					zip_fread(
						zf.get(),
						buffer + totalRead,
						static_cast<zip_uint64_t>(
							size - totalRead));

				if (n < 0)
				{
					throw std::runtime_error(
						"Failed to read zip entry data");
				}

				if (n == 0)
					break;

				totalRead += static_cast<uint64_t>(n);
			}

			if (totalRead != size)
			{
				throw std::runtime_error(
					"Incomplete read from zip entry");
			}

			result.emplace_back(std::move(file));
		}

		return result;
	}

}
