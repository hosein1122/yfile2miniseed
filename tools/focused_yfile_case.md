# راهنمای ساخت پرونده focused برای Y-file مشکوک

این ابزار برای ساخت یک پرونده کوچک و قابل تکرار از یک ایستگاه خاص است. هدف این است که برای یک بازه مشکوک، ورودی Y-file، خروجی برنامه مرکز، خروجی برنامه ما و گزارش Nanometrics/ObsPy کنار هم قرار بگیرند.

## ساختار پوشه پیشنهادی

```text
D:\MSeed_Test\Focused_KAZ_20100107_08
  raw\                 zipهای ورودی خام
  center_output\       خروجی MiniSEED/SDS برنامه مرکز
  our_output\          خروجی MiniSEED/SDS برنامه ما
  raw_kaz\             خروجی extract شده ابزار، ساخته می‌شود
  nanometrics_dump\    خروجی y5dump، ساخته می‌شود
  reports\             گزارش‌ها، ساخته می‌شود
```

برای ایستگاه‌های دیگر، نام پوشه `raw_kaz` متناسب با ایستگاه ساخته می‌شود؛ مثلا `raw_par`.

## پیش‌نیازها

- فایل‌های zip خام را داخل `raw` بگذارید.
- خروجی برنامه مرکز را داخل `center_output` بگذارید.
- خروجی برنامه ما را داخل `our_output` بگذارید.
- ابزار Nanometrics باید اینجا باشد، مگر اینکه مسیر دیگری به دستور بدهید:

```text
D:\MSeed_Test\Tools\Nanometrics\y5dump.exe
```

## اجرای ساده با فایل batch

از ریشه پروژه:

```bat
cd /d D:\C++Code\yfile2miniseed
tools\run_focused_yfile_case.bat D:\MSeed_Test\Focused_KAZ_20100107_08 KAZ SP D:\MSeed_Test\Tools\Nanometrics\y5dump.exe
```

پارامترها:

```text
CASE_ROOT       پوشه پرونده focused
STATION         کد ایستگاه، مثلا KAZ
CHANNEL_PREFIX  پیشوند کانال در نام Y-file، معمولا SP
Y5DUMP_EXE      مسیر y5dump.exe
```

اگر پارامتر سوم و چهارم را ندهید، مقدارهای پیش‌فرض `SP` و `D:\MSeed_Test\Tools\Nanometrics\y5dump.exe` استفاده می‌شوند.

## اجرای مستقیم با Python

```bat
python tools\focused_yfile_case.py ^
  --case-root D:\MSeed_Test\Focused_KAZ_20100107_08 ^
  --station KAZ ^
  --channel-prefix SP ^
  --y5dump D:\MSeed_Test\Tools\Nanometrics\y5dump.exe ^
  --clean
```

گزینه `--clean` پوشه‌های generated زیر را دوباره می‌سازد:

```text
raw_kaz\
nanometrics_dump\
reports\
```

این گزینه به `raw`، `center_output` و `our_output` دست نمی‌زند.

## خروجی گزارش‌ها

بعد از اجرا، گزارش‌ها اینجا ساخته می‌شوند:

```text
CASE_ROOT\reports
```

فایل‌های مهم:

```text
raw_station_manifest.csv              فهرست فایل‌های Y استخراج‌شده
raw_station_duplicate_names.csv       نام‌های تکراری در ورودی، اگر وجود داشته باشد
nanometrics_yfile_headers.csv         خلاصه هدر همه Y-fileها طبق y5dump
nanometrics_yfile_coverage_summary.csv
                                      تعداد فایل، sample sum، gap و overlap ورودی خام
compare_center_vs_ours\mismatches.txt اختلاف خروجی مرکز و خروجی ما
focused_case_verdict.md               جمع‌بندی اینکه خروجی مرکز یا ما به ورودی خام نزدیک‌تر است
focused_case_verdict.csv              نسخه جدولی همان جمع‌بندی
focused_case_summary.md               خلاصه پرونده
focused_case_summary.json             خلاصه ماشینی پرونده
```

## نکته مهم

این ابزار converterها را اجرا نمی‌کند. اول باید خروجی مرکز و خروجی برنامه ما را در پوشه‌های مربوطه بگذارید. سپس ابزار فقط پرونده اثباتی را می‌سازد.

برای مقایسه با برنامه مرکز، حالت پیش‌فرض `--id-mode component` استفاده می‌شود، چون برنامه مرکز ممکن است کانال‌هایی مثل `SPE` را به `SHE` تبدیل کند.
