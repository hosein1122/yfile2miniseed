# راهنمای ابزارهای Availability Report

این ابزارها برای مقایسه ورودی Y-file، خروجی برنامه مرکز، و خروجی برنامه خودمان با یک قالب واحد ساخته شده‌اند.

## اجرای کامل

دستور اصلی:

```bat
cd /d D:\C++Code\yfile2miniseed
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2
```

در این حالت، همه Y-fileهای موجود در پوشه ورودی و زیرپوشه‌های آن پردازش می‌شوند.

برای محدود کردن به یک ایستگاه و پیشوند کانال:

```bat
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2 KAZ SP
```

برای همه ایستگاه‌ها و همه کانال‌ها، این دو دستور معادل‌اند:

```bat
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2 ALL ALL
```

نمونه‌های دیگر:

```bat
REM همه ایستگاه‌ها، فقط کانال‌های SP
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2 ALL SP

REM فقط ایستگاه KAZ، همه کانال‌ها
tools\run_full_availability_case.bat D:\MSeed_Test\Data\Kaz_Test2 KAZ ALL
```

## مسیر خروجی

اگر ورودی این باشد:

```text
D:\MSeed_Test\Data\Kaz_Test2
```

خروجی اینجا ساخته می‌شود:

```text
D:\MSeed_Test\Result\Kaz_Test2
```

فایل‌ها و پوشه‌های اصلی خروجی:

```text
D:\MSeed_Test\Result\Kaz_Test2\center_output
D:\MSeed_Test\Result\Kaz_Test2\our_output
D:\MSeed_Test\Result\Kaz_Test2\yfile_availability.txt
D:\MSeed_Test\Result\Kaz_Test2\center_availability.txt
D:\MSeed_Test\Result\Kaz_Test2\center_normalized_availability.txt
D:\MSeed_Test\Result\Kaz_Test2\our_availability.txt
D:\MSeed_Test\Result\Kaz_Test2\center_normalized_vs_our_differences.txt
```

## کارهایی که batch انجام می‌دهد

```text
1. هر Y-file موجود در پوشه ورودی و زیرپوشه‌های آن را پیدا می‌کند.
2. پوشه‌های کاری برنامه مرکز را خالی می‌کند.
3. ورودی‌ها را به FilesToConvert برنامه مرکز کپی می‌کند.
4. Run.bat برنامه مرکز را اجرا می‌کند.
5. خروجی MSeedDatabase مرکز را به center_output منتقل می‌کند.
6. برنامه yfile2mseed خودمان را اجرا می‌کند و خروجی را در our_output می‌گذارد.
7. گزارش availability ورودی Y-file را می‌سازد.
8. گزارش availability خروجی خام مرکز را می‌سازد.
9. گزارش normalized خروجی مرکز را با ObsPy می‌سازد.
10. گزارش availability خروجی برنامه ما را می‌سازد.
11. گزارش normalized مرکز را خط‌به‌خط با گزارش برنامه ما مقایسه می‌کند.
```

## قالب گزارش

همه گزارش‌های availability فقط همین ستون‌ها را دارند:

```text
SourceID                 Start sample                End sample                  GapSamples      DataSamples
```

معنی ستون‌ها:

```text
SourceID       شناسه شبکه، ایستگاه، کانال و لوکیشن
Start sample   زمان اولین نمونه واقعی سگمنت
End sample     زمان آخرین نمونه واقعی سگمنت
GapSamples     تعداد نمونه فاصله با سگمنت قبلی؛ عدد منفی یعنی overlap
DataSamples    تعداد نمونه‌های داده در همین سگمنت
```

برای اولین سگمنت هر `SourceID` مقدار `GapSamples` برابر صفر است. اختلاف‌های خیلی کوچک، در حد حدود یک نمونه، با گزینه `--tolerance-samples` به‌عنوان چسبیده در نظر گرفته می‌شوند و مقدار `GapSamples` آن‌ها صفر گزارش می‌شود.

## قرارداد زمان انتهایی

در این گزارش‌ها `End sample` یعنی زمان آخرین نمونه واقعی، نه زمان نمونه بعدی.

برای نرخ نمونه‌برداری `sample_rate` و تعداد نمونه `npts`:

```text
End sample = Start sample + (npts - 1) / sample_rate
```

این همان قراردادی است که MiniSEED و ObsPy برای `endtime` استفاده می‌کنند.

برای Y-file، حتی اگر مقدار `End Time` خام در خروجی `Y5DUMP -H` با تعداد نمونه‌ها ناسازگار باشد، گزارش `End sample` را از روی `Start Time`، `Number of Samples` و `Sample Rate` محاسبه می‌کند تا مقایسه با MiniSEED درست و یکدست باشد.

## گزارش اختلاف

فایل زیر خروجی normalized مرکز را با خروجی برنامه ما خط‌به‌خط مقایسه می‌کند:

```text
D:\MSeed_Test\Result\Kaz_Test2\center_normalized_vs_our_differences.txt
```

اگر اختلافی باشد، قالب آن این‌طور است:

```text
Line N
CENTER: ...
OUR   : ...
```

اگر اختلافی نباشد، داخل فایل نوشته می‌شود:

```text
No differences found.
```

## تنظیم مسیرها

مسیرهای پیش‌فرض در ابتدای این فایل قابل ویرایش هستند:

```text
tools\run_full_availability_case.bat
```

مهم‌ترین متغیرها:

```bat
set "CENTER_ROOT=D:\MSeed_Test\Any_To_Mseed_Windows"
set "OUR_CONVERTER_DIR=D:\MSeed_Test\yfile2mseed"
set "Y5DUMP=D:\MSeed_Test\NanometricsY5dump\y5dump.exe"
set "RESULT_ROOT=D:\MSeed_Test\Result"
```
