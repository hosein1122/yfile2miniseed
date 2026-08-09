#include <yfile2miniseed/yfile_reader.hpp>

#include "utils/zip_utils.hpp"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace fs = std::filesystem;

namespace {

constexpr const char* kMagic = "Y2OBSBR1\n";

struct Args {
	std::string inputRoot;
	bool recursive = false;
	std::string pattern = "*";
};

struct InputSource {
	fs::path container;
	std::string member;
	std::vector<uint8_t> payload;
	uint64_t sourceBytes = 0;

	bool isZipMember() const { return !member.empty(); }

	std::string displayName() const
	{
		if (member.empty())
			return container.string();
		return container.string() + "!/" + member;
	}
};

void print_usage()
{
	std::cerr
		<< "usage:\n"
		<< "\tyfile2obspy_bridge --input-root PATH [--recursive] [--pattern PATTERN]\n";
}

Args parse_args(int argc, char* argv[])
{
	Args args;
	for (int i = 1; i < argc; ++i)
	{
		const std::string arg = argv[i];
		if ((arg == "--input-root" || arg == "--input") && i + 1 < argc)
		{
			args.inputRoot = argv[++i];
		}
		else if (arg == "--recursive")
		{
			args.recursive = true;
		}
		else if (arg == "--pattern" && i + 1 < argc)
		{
			args.pattern = argv[++i];
		}
		else if (arg == "-h" || arg == "--help")
		{
			print_usage();
			std::exit(0);
		}
		else
		{
			throw std::runtime_error("unknown or incomplete argument: " + arg);
		}
	}

	if (args.inputRoot.empty())
		throw std::runtime_error("--input-root is required");
	return args;
}

std::string lower_copy(std::string value)
{
	std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
		return static_cast<char>(std::tolower(c));
	});
	return value;
}

bool wildcard_match(const std::string& text, const std::string& pattern)
{
	size_t t = 0;
	size_t p = 0;
	size_t star = std::string::npos;
	size_t match = 0;

	while (t < text.size())
	{
		if (p < pattern.size() && (pattern[p] == '?' || pattern[p] == text[t]))
		{
			++t;
			++p;
		}
		else if (p < pattern.size() && pattern[p] == '*')
		{
			star = p++;
			match = t;
		}
		else if (star != std::string::npos)
		{
			p = star + 1;
			t = ++match;
		}
		else
		{
			return false;
		}
	}

	while (p < pattern.size() && pattern[p] == '*')
		++p;
	return p == pattern.size();
}

std::string normalize_slashes(std::string value)
{
	std::replace(value.begin(), value.end(), '\\', '/');
	while (!value.empty() && value.front() == '/')
		value.erase(value.begin());
	return value;
}

bool matches_pattern(const std::string& name, const std::string& pattern)
{
	const std::string normalizedName = normalize_slashes(name);
	const std::string normalizedPattern = normalize_slashes(pattern);
	if (normalizedPattern.find('/') != std::string::npos)
		return wildcard_match(normalizedName, normalizedPattern);

	const auto slash = normalizedName.find_last_of('/');
	const std::string basename = slash == std::string::npos
		? normalizedName
		: normalizedName.substr(slash + 1);
	return wildcard_match(basename, normalizedPattern);
}

std::vector<fs::path> collect_paths(const fs::path& root, bool recursive)
{
	if (fs::is_regular_file(root))
		return { root };

	std::vector<fs::path> paths;
	if (recursive)
	{
		for (const auto& entry : fs::recursive_directory_iterator(root))
		{
			if (entry.is_regular_file())
				paths.push_back(entry.path());
		}
	}
	else
	{
		for (const auto& entry : fs::directory_iterator(root))
		{
			if (entry.is_regular_file())
				paths.push_back(entry.path());
		}
	}

	std::sort(paths.begin(), paths.end(), [](const fs::path& left, const fs::path& right) {
		return lower_copy(left.string()) < lower_copy(right.string());
	});
	return paths;
}

std::vector<InputSource> iter_input_sources(const Args& args)
{
	const fs::path root(args.inputRoot);
	if (!fs::exists(root))
		throw std::runtime_error("input-root does not exist: " + root.string());

	const auto paths = collect_paths(root, args.recursive);
	std::vector<InputSource> sources;

	for (const fs::path& path : paths)
	{
		const std::string ext = lower_copy(path.extension().string());
		if (ext != ".zip")
		{
			const std::string relative = fs::is_regular_file(root)
				? path.filename().string()
				: fs::relative(path, root).generic_string();
			if (matches_pattern(relative, args.pattern))
				sources.push_back({ path, {}, {}, static_cast<uint64_t>(fs::file_size(path)) });
			continue;
		}

		auto extracted = yfile2miniseed::cli::ziputils::ExtractZipToMemory(path.string());
		std::sort(extracted.begin(), extracted.end(), [](const auto& left, const auto& right) {
			return lower_copy(left.name) < lower_copy(right.name);
		});

		for (auto& item : extracted)
		{
			std::string member = normalize_slashes(item.name);
			if (member.empty())
				continue;
			if (!args.recursive && member.find('/') != std::string::npos)
				continue;
			if (lower_copy(fs::path(member).extension().string()) == ".zip")
				continue;
			if (!matches_pattern(member, args.pattern))
				continue;
			const uint64_t sourceBytes = static_cast<uint64_t>(item.data.size());
			sources.push_back({ path, member, std::move(item.data), sourceBytes });
		}
	}

	std::sort(sources.begin(), sources.end(), [](const InputSource& left, const InputSource& right) {
		const auto leftKey = lower_copy(left.container.string()) + "\n" + lower_copy(left.member);
		const auto rightKey = lower_copy(right.container.string()) + "\n" + lower_copy(right.member);
		return leftKey < rightKey;
	});
	return sources;
}

std::string clean_string(const uint8_t* value)
{
	std::string text(reinterpret_cast<const char*>(value));
	const auto nul = text.find('\0');
	if (nul != std::string::npos)
		text.resize(nul);
	while (!text.empty() && std::isspace(static_cast<unsigned char>(text.front())))
		text.erase(text.begin());
	while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back())))
		text.pop_back();
	return text;
}

std::string json_escape(const std::string& value)
{
	std::ostringstream out;
	for (unsigned char c : value)
	{
		switch (c)
		{
		case '\\': out << "\\\\"; break;
		case '"': out << "\\\""; break;
		case '\b': out << "\\b"; break;
		case '\f': out << "\\f"; break;
		case '\n': out << "\\n"; break;
		case '\r': out << "\\r"; break;
		case '\t': out << "\\t"; break;
		default:
			if (c < 0x20)
				out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c);
			else
				out << static_cast<char>(c);
		}
	}
	return out.str();
}

int64_t seconds_to_ns(double seconds)
{
	return static_cast<int64_t>(seconds * 1'000'000'000.0);
}

std::string build_header_json(
	const InputSource& source,
	const yfile2miniseed::Y5FileReader& reader)
{
	std::ostringstream out;
	out << std::setprecision(17);
	out << "{";
	out << "\"kind\":\"trace\",";
	out << "\"source\":\"" << json_escape(source.displayName()) << "\",";
	out << "\"source_bytes\":" << source.sourceBytes << ",";
	out << "\"network\":\"" << json_escape(clean_string(reader.t1.NetworkID)) << "\",";
	out << "\"station\":\"" << json_escape(clean_string(reader.t1.StationID.Station)) << "\",";
	out << "\"location\":\"" << json_escape(clean_string(reader.t1.StationID.Location)) << "\",";
	out << "\"channel\":\"" << json_escape(clean_string(reader.t1.StationID.Channel)) << "\",";
	out << "\"start_ns\":" << seconds_to_ns(reader.t5.StartTime) << ",";
	out << "\"end_ns\":" << seconds_to_ns(reader.t5.EndTime) << ",";
	out << "\"sample_rate\":" << static_cast<double>(reader.t3.SampleRate) << ",";
	out << "\"npts\":" << static_cast<uint64_t>(reader.t5.NumSamples);
	out << "}";
	return out.str();
}

std::string build_run_header_json(const std::vector<InputSource>& sources)
{
	uint64_t totalSourceBytes = 0;
	for (const auto& source : sources)
		totalSourceBytes += source.sourceBytes;

	std::ostringstream out;
	out << "{";
	out << "\"kind\":\"run\",";
	out << "\"protocol\":2,";
	out << "\"total_sources\":" << static_cast<uint64_t>(sources.size()) << ",";
	out << "\"total_source_bytes\":" << totalSourceBytes;
	out << "}";
	return out.str();
}

template <typename T>
void write_little(std::ostream& out, T value)
{
	static_assert(std::is_integral<T>::value, "integral type required");
	for (size_t i = 0; i < sizeof(T); ++i)
	{
		const char byte = static_cast<char>((static_cast<uint64_t>(value) >> (8 * i)) & 0xffu);
		out.write(&byte, 1);
	}
}

void emit_trace(const InputSource& source, const yfile2miniseed::Y5FileReader& reader)
{
	const std::string header = build_header_json(source, reader);
	write_little<uint32_t>(std::cout, static_cast<uint32_t>(header.size()));
	std::cout.write(header.data(), static_cast<std::streamsize>(header.size()));

	const uint64_t sampleBytes =
		static_cast<uint64_t>(reader.t5.NumSamples) * static_cast<uint64_t>(sizeof(int32_t));
	write_little<uint64_t>(std::cout, sampleBytes);
	std::cout.write(
		reinterpret_cast<const char*>(reader.t7.samples),
		static_cast<std::streamsize>(sampleBytes));
	if (!std::cout)
		throw std::runtime_error("failed to write bridge output");
}

bool read_source(const InputSource& source, yfile2miniseed::Y5FileReader& reader)
{
	if (source.isZipMember())
	{
		if (source.payload.empty())
			return false;
		return reader.ReadFromRAM(source.payload.data(), source.payload.size());
	}
	return reader.ReadFromFile(source.container.string());
}

} // namespace

int main(int argc, char* argv[])
{
#ifdef _WIN32
	_setmode(_fileno(stdout), _O_BINARY);
#endif
	try
	{
		const Args args = parse_args(argc, argv);
		const auto sources = iter_input_sources(args);
		if (sources.empty())
			throw std::runtime_error("no input sources matched");

		std::cout.write(kMagic, std::strlen(kMagic));
		const std::string runHeader = build_run_header_json(sources);
		write_little<uint32_t>(std::cout, static_cast<uint32_t>(runHeader.size()));
		std::cout.write(runHeader.data(), static_cast<std::streamsize>(runHeader.size()));

		yfile2miniseed::Y5FileReader reader;
		for (const auto& source : sources)
		{
			if (!read_source(source, reader))
				throw std::runtime_error("cannot read Y source: " + source.displayName());
			emit_trace(source, reader);
		}

		write_little<uint32_t>(std::cout, 0);
		return 0;
	}
	catch (const std::exception& ex)
	{
		std::cerr << "ERROR: " << ex.what() << "\n";
		return 1;
	}
}
