# راهنمای کار با `compare_mseed_outputs.py`

این ابزار برای مقایسه خروجی MiniSEED دو مسیر تبدیل ساخته شده است:

- مسیر مرجع: خروجی برنامه فعلی مرکز، مثلا `D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase`
- مسیر کاندید: خروجی برنامه جدید ما، یعنی `yfile2miniseed`

هدف این است که بعد از تبدیل یک مجموعه Y-file واقعی، خروجی دو مسیر را در چند سطح بررسی کنیم و بفهمیم آیا از نظر شناسه‌ها، بازه‌های زمانی، تعداد نمونه‌ها، gap/overlap و در سطح سنگین حتی مقدار نمونه‌ها با هم سازگار هستند یا نه.

## نکته مهم قبل از شروع

برنامه مرکز فایل‌های ورودی را از `FilesToConvert` و `Buffer` منتقل یا حذف می‌کند. برای تست با برنامه ما، از همان داده خام یک کپی جداگانه بسازید و برنامه جدید را روی آن کپی اجرا کنید.

پیشنهاد ساختار پوشه برای تست:

```text
D:\MSeed_Test\Validation
  input_yfiles_copy\     کپی مستقل از Y-file های خام
  output_new\            خروجی برنامه جدید ما
  reports\               گزارش‌های مقایسه
```

## پیش‌نیازها

روی همان محیطی که مقایسه را اجرا می‌کنید باید Python، ObsPy و NumPy نصب باشد.

برای اطمینان:

```bat
python -c "import obspy, numpy; print('ok')"
```

اگر خطای import گرفتید، اول ObsPy/NumPy را در همان Python نصب کنید.

## تولید خروجی برنامه جدید

از ریشه پروژه:

```bat
cd /d D:\C++Code\yfile2miniseed
out\build\x64-Debug\apps\yfile2mseed_cli\yfile2mseed.exe ^
  D:\MSeed_Test\Validation\input_yfiles_copy ^
  -o D:\MSeed_Test\Validation\output_new ^
  -V2
```

اگر نسخه Release ساخته باشید، مسیر فایل اجرایی ممکن است به جای `x64-Debug` شامل `x64-Release` باشد.

## اجرای راهنمای سریع ابزار

```bat
cd /d D:\C++Code\yfile2miniseed
python tools\compare_mseed_outputs.py --help
```

## خروجی‌های گزارش

هر بار اجرا در پوشه‌ای که با `--report` مشخص می‌کنید این فایل‌ها را می‌سازد:

- `comparison_summary.csv`: خلاصه قابل باز کردن در Excel
- `comparison_summary.json`: خلاصه کامل‌تر برای بررسی ماشینی یا آرشیو
- `mismatches.txt`: فهرست اختلاف‌ها، اگر اختلافی پیدا شود

## گزینه‌های مهم

- `--reference`: مسیر خروجی مرجع، یعنی خروجی برنامه مرکز.
- `--candidate`: مسیر خروجی برنامه جدید ما.
- `--report`: مسیر پوشه گزارش.
- `--level`: سطح مقایسه: `simple`، `medium` یا `deep`.
- `--id-mode strict`: شناسه کامل `NET.STA.LOC.CHA` را مقایسه می‌کند.
- `--id-mode component`: برای مقایسه، شبکه/ایستگاه/لوکیشن و حرف آخر کانال را در نظر می‌گیرد. این برای خروجی مرکز مهم است، چون برنامه مرکز کانال‌ها را به شکل `SH?` بازنویسی می‌کند.
- `--default-network IR`: اگر شبکه در داده خالی باشد، برای یکسان‌سازی از `IR` استفاده می‌شود.
- `--station`: فقط یک ایستگاه خاص را مقایسه می‌کند. می‌توانید چند بار تکرارش کنید.
- `--channel`: فقط یک کانال خاص را مقایسه می‌کند. می‌توانید چند بار تکرارش کنید.
- `--date-from` و `--date-to`: محدود کردن بازه تاریخی، با فرمت `YYYY-MM-DD`.
- `--max-deep-samples`: سقف تعداد نمونه برای مقایسه سنگین هر trace.
- `--fail-on-difference`: اگر اختلاف پیدا شود، برنامه با کد خطا تمام می‌شود. برای CI یا تست خودکار مفید است.

## سطح اول: `simple`

این سطح سریع‌ترین حالت است و برای داده حجیم، مثلا کل داده یک ماه همه ایستگاه‌ها، مناسب است.

در این سطح خود مقدار sampleها خوانده و مقایسه نمی‌شود. بررسی اصلی روی metadata، شناسه traceها، زمان شروع/پایان، sample rate، تعداد sample و تعداد فایل‌ها است.

نمونه اجرا:

```bat
cd /d D:\C++Code\yfile2miniseed
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\simple ^
  --level simple ^
  --id-mode component ^
  --default-network IR
```

اگر این سطح اختلاف جدی نشان دهد، معمولا یعنی یکی از این موارد رخ داده است:

- بخشی از داده در یکی از مسیرها تبدیل نشده است.
- نام شبکه/ایستگاه/کانال متفاوت شده است.
- زمان‌بندی یا تعداد sampleها تغییر کرده است.
- مسیر خروجی اشتباه داده شده یا تبدیل یکی از دو مسیر هنوز کامل نشده است.

## سطح دوم: `medium`

این سطح برای بررسی عمیق‌تر بدون ورود به مقایسه sample-by-sample است. برای کل داده یک ماه هم معمولا منطقی‌تر از `deep` است.

در این سطح علاوه بر موارد سطح ساده، وضعیت پوشش زمانی، gap و overlap هم بررسی می‌شود. این سطح برای پیدا کردن مشکل‌هایی مثل حذف بخشی از trace، تکرار بازه، هم‌پوشانی، یا جابه‌جایی قطعه‌ها مناسب است.

نمونه اجرا:

```bat
cd /d D:\C++Code\yfile2miniseed
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\medium ^
  --level medium ^
  --id-mode component ^
  --default-network IR
```

پیشنهاد عملی این است که بعد از پاس شدن `simple`، همین سطح را روی کل داده یک ماه اجرا کنید.

## سطح سوم: `deep`

این سطح سنگین‌ترین حالت است. در این حالت ابزار داده‌ها را با ObsPy می‌خواند، traceهای هم‌نام را merge می‌کند و مقدار sampleها را هم مقایسه می‌کند.

برای کل داده یک ماه همه ایستگاه‌ها، اجرای `deep` ممکن است بسیار طولانی شود یا به حافظه زیادی نیاز داشته باشد. بهتر است اول روی چند ایستگاه و چند روز نمونه اجرا شود.

نمونه اجرا برای یک ایستگاه:

```bat
cd /d D:\C++Code\yfile2miniseed
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\deep_SHI ^
  --level deep ^
  --station SHI ^
  --id-mode component ^
  --default-network IR ^
  --max-deep-samples 2000000
```

نمونه اجرا برای یک بازه تاریخی کوتاه:

```bat
cd /d D:\C++Code\yfile2miniseed
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\deep_2026-01-01 ^
  --level deep ^
  --date-from 2026-01-01 ^
  --date-to 2026-01-02 ^
  --id-mode component ^
  --default-network IR ^
  --max-deep-samples 2000000
```

اگر در گزارش دیدید موردی با `SKIPPED_TOO_LARGE` آمده، یعنی تعداد sampleهای آن trace از سقف `--max-deep-samples` بیشتر بوده و برای جلوگیری از مصرف زیاد حافظه مقایسه sample آن trace رد شده است. در این حالت یا بازه را کوچک‌تر کنید، یا ایستگاه/کانال را محدود کنید، یا مقدار `--max-deep-samples` را بالاتر ببرید.

## تفاوت `strict` و `component`

برنامه مرکز در کدی که بررسی شد، کانال‌ها را به شکل `SH` به علاوه حرف آخر کانال تبدیل می‌کند. مثلا اگر کانال اصلی `BHZ` یا `HHZ` باشد، ممکن است در خروجی مرکز به `SHZ` تبدیل شود.

بنابراین برای مقایسه عملی خروجی دو مسیر، معمولا این گزینه بهتر است:

```bat
--id-mode component --default-network IR
```

اما برای ممیزی metadata و پیدا کردن تفاوت دقیق نام شبکه/کانال/لوکیشن، یک بار هم `strict` را اجرا کنید:

```bat
python tools\compare_mseed_outputs.py ^
  --reference D:\MSeed_Test\Any_To_Mseed_Windows\MSeedDatabase ^
  --candidate D:\MSeed_Test\Validation\output_new ^
  --report D:\MSeed_Test\Validation\reports\strict_metadata ^
  --level simple ^
  --id-mode strict ^
  --default-network IR
```

در این حالت اختلاف کانال‌هایی مثل `SHZ` با `BHZ` طبیعی است، ولی برای فهمیدن تفاوت دقیق metadata مفید است.

## معنی اختلاف‌ها

در `mismatches.txt` یا `comparison_summary.csv` ممکن است با این وضعیت‌ها روبه‌رو شوید:

- `ONLY_REFERENCE`: این trace فقط در خروجی مرکز وجود دارد و در خروجی برنامه ما پیدا نشده است.
- `ONLY_CANDIDATE`: این trace فقط در خروجی برنامه ما وجود دارد و در خروجی مرکز پیدا نشده است.
- `DIFF`: هر دو trace وجود دارند ولی حداقل یکی از معیارها فرق دارد.
- `OK`: در سطح انتخاب‌شده اختلافی پیدا نشده است.
- `SKIPPED_TOO_LARGE`: فقط در سطح `deep`؛ داده برای مقایسه sample-by-sample بزرگ‌تر از سقف تعیین‌شده بوده است.

## ترتیب پیشنهادی برای اطمینان عملی

1. صبر کنید برنامه مرکز تبدیل را کامل کند و خروجی نهایی در `MSeedDatabase` ساخته شود.
2. همان داده خام را از یک کپی مستقل با برنامه جدید ما تبدیل کنید.
3. سطح `simple` را روی کل ماه و همه ایستگاه‌ها اجرا کنید.
4. اگر نتیجه خوب بود، سطح `medium` را روی کل ماه اجرا کنید.
5. سطح `deep` را روی چند ایستگاه مهم، چند روز مختلف، و چند کانال اجرا کنید.
6. چند تست `deep` را عمدا روی روزهایی انتخاب کنید که gap، overlap، قطعی، شروع/پایان روز، یا حجم زیاد دارند.
7. یک بار `strict` را برای بررسی metadata اجرا کنید.
8. اگر اختلافی باقی ماند، اول `mismatches.txt` را بررسی کنید و بعد روی همان ایستگاه/روز یک اجرای محدودتر انجام دهید.

## پیشنهاد برای تست داده واقعی شیراز

برای شروع، این ترکیب خوب است:

- کل ماه، همه ایستگاه‌ها: `simple`
- کل ماه، همه ایستگاه‌ها: `medium`
- سه تا پنج ایستگاه مهم، هر کدام یک یا دو روز: `deep`
- یک روز آرام، یک روز شلوغ، یک روز دارای قطعی یا gap: `deep`
- یک اجرای `strict` کوتاه برای فهمیدن تفاوت metadata

اگر این‌ها تمیز باشند، اعتماد به تبدیل خیلی بیشتر می‌شود. با این حال برای استفاده رسمی در سطح مرکز، بهتر است چند batch واقعی دیگر از شبکه‌ها و ماه‌های متفاوت هم با همین روش آرشیو و مقایسه شود.

## خطاهای رایج

اگر گزارش تقریبا خالی است یا هیچ trace پیدا نشده:

- مسیر `--reference` یا `--candidate` را دوباره بررسی کنید.
- مطمئن شوید برنامه مرکز هنوز در حال تبدیل نیست.
- مطمئن شوید خروجی برنامه جدید در مسیر مورد نظر ساخته شده است.

اگر اختلاف کانال زیاد است:

- ابتدا با `--id-mode component` اجرا کنید.
- بعد برای ممیزی دقیق metadata از `--id-mode strict` استفاده کنید.

اگر اجرای `deep` خیلی کند است:

- با `--station`، `--channel`، `--date-from` و `--date-to` محدوده را کوچک‌تر کنید.
- فقط بعد از پاس شدن `simple` و `medium` سراغ `deep` بزرگ‌تر بروید.

اگر فایل‌های خروجی مرکز پسوند `.mseed` ندارند:

- مشکلی نیست. این ابزار فایل‌ها را بر اساس قابلیت خوانده شدن با ObsPy بررسی می‌کند، نه فقط بر اساس پسوند.
