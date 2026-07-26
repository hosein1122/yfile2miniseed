#include "data_availability_stats.hpp"
#include "../utils/time_utils.hpp"

#include <iostream>
#include <fstream>
#include <iomanip>
#include <algorithm>

namespace yfile2miniseed::cli::stats
{

	using yfile2miniseed::time::formatTime;
	using yfile2miniseed::time::PrintTime;

	static std::map<std::string, std::vector<Range>> myDictionary;
	static bool debug = false;
	static double ep = 0.00001;

	Range::Range(double _start, double _end)
	{
		if (_end < _start)
		{
			std::cerr << "Error: 'start' MUST be less than 'end'! Will Auto Correct.. !!!\n";
			start = _end;
			end = _start;
			return;
		}

		start = _start;
		end = _end;
	}

	//static void printV() {
	//	cout << "Elements in vector: ";
	//	for (int number : numbers) {
	//		cout << number << " ";
	//	}
	//	cout << endl;
	//	cout << "Size of vector: " << numbers.size() << endl;
	//	return;
	//}

	void WriteStats() {
		if (myDictionary.size() == 0)
			return;

		auto _time = std::time(nullptr);
		auto _timeStr = yfile2miniseed::time::PrintTime(_time);
		//01234567890123456789
		//2018-12-12 12:23:23
		_timeStr.erase(16, 1);	//:
		_timeStr.erase(13, 1);	//:
		_timeStr.erase(10, 1);	//" "
		_timeStr.insert(10, ".");
		_timeStr.erase(7, 1);	//-
		_timeStr.erase(4, 1);	//-

		// Construct the full file path
		std::string fileName = "Y-File_DataStats.txt";
		fileName.append("-").append(_timeStr).append(".txt");

		try {
			// Use fstream for file operations and ofstream for writing to the file
			std::ofstream outputFile;
			//outputFile.open(filePath, ios::app);
			outputFile.open(fileName, std::ios::out | std::ios::trunc);
			if (!outputFile.is_open()) {
				std::cerr << "Error: Could not open file: " << fileName << std::endl;
				return;
			}

			outputFile << std::endl << "            Input Y-Files Availability Contents:" << std::endl;
			for (const auto& pair : myDictionary) {
				double available = 0, gap = 0, _gap;
				bool haveGap = false;
				outputFile << "         SourceID                 Start sample                End sample              Gap	     Seconds" << std::endl;
				//outputFile << "  Key: " << pair.first << endl;
				for (size_t i = 0; i < pair.second.size(); i++)
				{
					haveGap = false;
					if (i > 0)
					{
						_gap = pair.second[i].start - pair.second[i - 1].end;
						gap += _gap;
						haveGap = true;
					}
					auto _available = pair.second[i].end - pair.second[i].start;
					available += _available;

					outputFile
						<< std::setw(23) << pair.first
						<< std::setw(28) << yfile2miniseed::time::formatTime(pair.second[i].start)
						<< std::setw(28) << yfile2miniseed::time::formatTime(pair.second[i].end);
					if (haveGap)
						outputFile << std::fixed << std::setprecision(2) << std::setw(10) << _gap;
					else
						outputFile << std::setw(10) << "===";

					outputFile << std::setw(15) << _available << std::endl;


					//if (showTime)
					//{
					//	auto _available = pair.second[i].end - pair.second[i].start;
					//	available += _available;
					//	outputFile << "    Start: " << yfile2miniseed::time::formatTime(myDictionary[pair.first][i].start) << ", End: " << pair.second[i].end << " (" << _available << " Sec)" << endl;
					//	if ((i + 1) < pair.second.size())
					//	{
					//		auto _gap = pair.second[i + 1].start - pair.second[i].end;
					//		gap += _gap;
					//		if (_gap != 0)
					//			outputFile << "      Gap: " << _gap << " Sec" << endl;
					//		if (gap < 0)
					//			cerr << "Gap is minus !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!";
					//	}
					//}
					//else
					//	outputFile << "    Start: " << fixed << setprecision(3) << pair.second[i].start << ", End: " << pair.second[i].end << endl;
				}

				//for (const auto& range : pair.second) {
				//	outputFile << "  Start: " << fixed << setprecision(3) << range.start << ", End: " << range.end << endl;
				//}
				//if (showTime)
				//{
				outputFile << "  ************************************************************************" << std::endl;
				outputFile << "  *     Start: " << yfile2miniseed::time::formatTime(myDictionary[pair.first][0].start) << "  End: " << yfile2miniseed::time::formatTime(myDictionary[pair.first][pair.second.size() - 1].end) << std::endl;
				outputFile << "  *     Availability: " << std::fixed << std::setprecision(2) << 100 * available / (available + gap) << "%     Gap: " << 100 * gap / (available + gap) << "%" << std::endl;
				outputFile << "  ************************************************************************" << std::endl << std::endl;
				//}
			}


			outputFile.close();
		}
		catch (const std::exception& e) {
			std::cerr << "Error writing to file: " << e.what() << std::endl;
		}
	}

	static void printD(bool showTime) {
		std::cout << std::endl << "            Dictionary Contents:" << std::endl;
		for (const auto& pair : myDictionary) {
			double available = 0, gap = 0;
			std::cout << "  Key: " << pair.first << std::endl;
			for (size_t i = 0; i < pair.second.size(); i++)
			{
				if (showTime)
				{
					auto _available = pair.second[i].end - pair.second[i].start;
					available += _available;
					std::cout << "    Start: " << std::fixed << std::setprecision(3) << pair.second[i].start << ", End: " << pair.second[i].end << " (" << _available << " Sec)" << std::endl;
					if ((i + 1) < pair.second.size())
					{
						auto _gap = pair.second[i + 1].start - pair.second[i].end;
						gap += _gap;
						if (_gap != 0)
							std::cout << "      Gap: " << _gap << " Sec" << std::endl;
						if (gap < 0)
							std::cerr << "Gap is minus !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!";
					}

				}
				else
					std::cout << "    Start: " << std::fixed << std::setprecision(3) << pair.second[i].start << ", End: " << pair.second[i].end << std::endl;
			}
			//for (const auto& range : pair.second) {
			//	cout << "  Start: " << fixed << setprecision(3) << range.start << ", End: " << range.end << endl;
			//}
			if (showTime)
			{
				std::cout << "  ************************************************************************" << std::endl;
				std::cout << "  *     Start: " << yfile2miniseed::time::formatTime(myDictionary[pair.first][0].start) << "  End: " << yfile2miniseed::time::formatTime(myDictionary[pair.first][pair.second.size() - 1].end) << std::endl;
				std::cout << "  *     Availability: " << std::fixed << std::setprecision(2) << 100 * available / (available + gap) << "%     Gap: " << 100 * gap / (available + gap) << "%" << std::endl;
				std::cout << "  ************************************************************************" << std::endl << std::endl;
			}
		}
		//cout << "Size of Dictionary: " << myDictionary.size() << endl << endl;

		return;
	}

	void test() {
		debug = true;
		auto sampleRate = 50;
		auto dt = (double)1 / sampleRate;
		//auto dt_f = 1.0f / sampleRate;

		std::cout << "------------------" << std::endl;
		auto key1 = "IR.SHI.BHZ";
		checkNewData(key1, { 3.0f, 4.0f }, 50);
		printD();
		checkNewData(key1, { 1.0f, 2.0f }, 50);	//insert new data
		printD();
		checkNewData(key1, { 0.5f,1 }, 50);		//Merge with first index
		checkNewData(key1, { 0.5f,1.5 }, 50);	//Merge with first index
		checkNewData(key1, { 0.5f,2 }, 50);		//Merge with first index

		checkNewData(key1, { 1.5f,1.8f }, 50);	//Drop
		checkNewData(key1, { 1,2 }, 50);		//Drop

		checkNewData(key1, { 0.5,2.5 }, 50);	//Merge
		checkNewData(key1, { 1,2.5 }, 50);		//Merge
		checkNewData(key1, { 2,2.5 }, 50);		//Merge
		checkNewData(key1, { 2 + (float)dt,2.5 }, 50);		//Merge

		checkNewData(key1, { 2.3,2.5 }, 50);		//new segment

		checkNewData(key1, { 0.5,3 - (float)dt }, 50);		//Merge
		checkNewData(key1, { 1,3 - (float)dt }, 50);		//Merge
		checkNewData(key1, { 2,3 - (float)dt }, 50);		//Merge
		checkNewData(key1, { 2 + (float)dt,3 - (float)dt }, 50);		//Merge

		checkNewData(key1, { 0.5,3 }, 50);				//Merge
		checkNewData(key1, { 1,3 }, 50);				//Merge
		checkNewData(key1, { 2,3 }, 50);				//Merge
		checkNewData(key1, { 2 + (float)dt,3 }, 50);	//Merge

		checkNewData(key1, { 0.5,3 + (float)dt }, 50);		//Merge
		checkNewData(key1, { 1,3 + (float)dt }, 50);		//Merge
		checkNewData(key1, { 2,3 + (float)dt }, 50);		//Merge
		checkNewData(key1, { 2 + (float)dt,3 + (float)dt }, 50);		//Merge

		checkNewData(key1, { 0.5,5 }, 50);		//Merge
		checkNewData(key1, { 1,5 }, 50);		//Merge
		checkNewData(key1, { 2,5 }, 50);		//Merge
		checkNewData(key1, { 2 + (float)dt,5 }, 50);		//Merge

		printD();


		myDictionary.clear();
		return;
	}

	static void checkNextSegment(std::string ID, Range newRange, float sampleRate, size_t index) {
		//if we dont have next index
		//Merge End with current index
		if (index + 1 >= myDictionary[ID].size())
		{
			myDictionary[ID][index].end = newRange.end;
			if (debug)
				std::cout << " '" << ID << "' Merge End with current index " << index << std::endl;
			return;
		}

		auto dt = (double)1 / sampleRate;

		//	!*********!				newRange
		//		      !  '.......'	newIndex
		//Merge End with current index
		if ((myDictionary[ID][index + 1].start - dt - ep) > newRange.end)
		{
			myDictionary[ID][index].end = newRange.end;
			if (debug)
				std::cout << " '" << ID << "' Merge End with current index " << index << std::endl;
			return;
		}

		//	!*********!			newRange
		//		'.....!....'	newIndex
		//		'.....'			newIndex
		//			  !'....'	newIndex
		//Merge End with current index and remove next index(multiple merge)
		if ((myDictionary[ID][index + 1].start - dt - ep) <= newRange.end
			&& (myDictionary[ID][index + 1].end - ep) >= newRange.end)
		{
			//مرج هر دو ایندکس به یک بازه
			myDictionary[ID][index].end = myDictionary[ID][index + 1].end;
			//حذف ایندکس بعدی
			myDictionary[ID].erase(myDictionary[ID].begin() + index + 1);
			if (debug)
				std::cout << " '" << ID << "' Merge End with current index (" << index << ") and remove the next index(multiple merge)!" << std::endl;
			return;
		}

		//	!*********!		newRange
		//		'....'!		newIndex
		//رنج جدید بزرگتر از داده ایندکس بعدی است
		if ((myDictionary[ID][index + 1].end + ep) < newRange.end)
		{
			//حذف ایندکس جلوتر و اجرای مججد همین تابع برای ایندکس بعدی
			myDictionary[ID].erase(myDictionary[ID].begin() + index + 1);
			if (debug)
				std::cout << " '" << ID << "' The next index (" << index + 1 << ") removed!" << std::endl;
			checkNextSegment(ID, newRange, sampleRate, index);
		}
	}

	void checkNewData(std::string ID, Range newRange, float sampleRate)
	{
		if (debug)
			std::cout << std::fixed << std::setprecision(3) << "Checking new Range: (" << newRange.start << ", " << newRange.end << ")" << std::endl;

		auto dt = (double)1 / sampleRate;

		if (myDictionary[ID].size() == 0)
		{
			myDictionary[ID].push_back(newRange);
			//myDictionary[ID][0].start = newRange.start;
			//myDictionary[ID][0].end = newRange.end;

			if (debug)
				std::cout << "The first segment of '" << ID << "' inserted!" << std::endl;

			return;
		}

		for (size_t i = 0; i < myDictionary[ID].size(); i++)
		{
			//	!*********!				newRange
			//			  !   '....'	current Index
			//Insert before current index
			if ((newRange.end + dt + ep) < myDictionary[ID][i].start)
			{
				myDictionary[ID].insert(myDictionary[ID].begin() + i, newRange);
				if (debug)
					std::cout << "Inserted before " << i << " index. '" << ID << "' " << std::endl;

				return;
			}

			//	!*********!				newRange
			//		'.....!.......'	current Index
			//		'.....'			current Index
			//Merge Start with current index
			if ((newRange.start + ep) < myDictionary[ID][i].start
				&& (newRange.end + ep) <= myDictionary[ID][i].end)
			{
				myDictionary[ID][i].start = newRange.start;
				if (debug)
					std::cout << "Merged Start with " << i << " index. '" << ID << "' " << std::endl;

				return;
			}

			//	!*********!		newRange
			//	   '...'		current Index
			//دیتای جدید بزرگتر از ایندکس فعلی است. هم در ابتدا و هم در انتها
			if ((newRange.start + ep) < myDictionary[ID][i].start
				&& (newRange.end - ep) > myDictionary[ID][i].end)
			{
				//ابتدای ایندکس فعلی رو تنظیم میکنیم
				myDictionary[ID][i].start = newRange.start;
				if (debug)
					std::cout << "Merged Start with " << i << " index. '" << ID << "' " << std::endl;
				checkNextSegment(ID, newRange, sampleRate, i);

				return;
			}

			//	   !***!		newRange
			//	!*********!		newRange
			//	'.........'		current Index
			//Drop NewRange! It is a repeated data!
			if ((newRange.start - ep) >= myDictionary[ID][i].start
				&& (newRange.end + ep) <= myDictionary[ID][i].end)
			{
				if (debug)
					std::cout << "Dropped! It is a repeated data! '" << ID << "' " << std::endl;

				return;
			}

			//	   !********!		newRange
			//	'......'		current Index
			//Merge Check With Next index!
			if ((newRange.start - ep) >= myDictionary[ID][i].start
				&& (newRange.start - dt - ep) <= myDictionary[ID][i].end
				&& (newRange.end - ep) > myDictionary[ID][i].end)
			{
				checkNextSegment(ID, newRange, sampleRate, i);

				return;
			}
		}

		//new Data should be append at last
		myDictionary[ID].push_back(newRange);
		if (debug)
			std::cout << "New Data appended at last! '" << ID << "' " << std::endl;
	}

}