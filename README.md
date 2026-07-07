# ابزار Migration از MySQL به PostgreSQL

ابزاری مستقل و Dockerized برای انتقال کامل یک دیتابیس MySQL به PostgreSQL،
شامل ساختار جداول (schema)، داده‌ها، Index ها، Foreign Key ها و
Auto Increment / Sequence ها.

## ویژگی‌ها

- تبدیل خودکار انواع داده MySQL به معادل PostgreSQL
- انتقال Primary Key، Unique Constraint، Index و Foreign Key
- انتقال داده‌ها به صورت Batch با اندازه قابل تنظیم
- استفاده از Server-Side Cursor برای جداول بزرگ (بدون مصرف بالای RAM)
- قابلیت Resume: در صورت قطع شدن برنامه، از آخرین جدول/ردیف ادامه می‌دهد
- نمایش Progress Bar زنده برای هر جدول
- ثبت تمام خطاها در فایل لاگ
- تولید گزارش نهایی (تعداد جداول، ردیف‌ها، مدت زمان، خطاها)
- اجرای کامل با یک دستور: `docker compose up`

## پیش‌نیازها

- Docker و Docker Compose نصب شده باشد
- دسترسی شبکه‌ای به هر دو دیتابیس MySQL (مبدأ) و PostgreSQL (مقصد)
- کاربر MySQL باید دسترسی `SELECT` روی دیتابیس مبدأ و `information_schema` داشته باشد
- کاربر PostgreSQL باید دسترسی `CREATE`، `INSERT`، `ALTER` روی دیتابیس مقصد داشته باشد
- دیتابیس مقصد PostgreSQL باید از قبل ساخته شده باشد (خود ابزار دیتابیس نمی‌سازد، فقط جداول داخل آن را می‌سازد)

## نصب و راه‌اندازی

### ۱. کلون یا کپی پروژه

فایل‌های این پروژه را در یک پوشه (مثلاً `mysql-postgres-migrator/`) قرار دهید.

### ۲. تنظیم فایل `.env`

فایل `.env.example` را کپی کرده و به `.env` تغییر نام دهید:

```bash
cp .env.example .env
```

سپس مقادیر را با اطلاعات واقعی خود پر کنید:

```env
MYSQL_HOST=192.168.1.10
MYSQL_PORT=3306
MYSQL_DATABASE=my_source_db
MYSQL_USER=root
MYSQL_PASSWORD=my_password

POSTGRES_HOST=192.168.1.20
POSTGRES_PORT=5432
POSTGRES_DATABASE=my_target_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=my_password

BATCH_SIZE=1000
```

> **نکته درباره شبکه:** پیش‌فرض این پروژه فرض می‌کند MySQL و PostgreSQL
> مستقیماً روی هاست (نه داخل Docker) در حال اجرا هستند. برای این حالت،
> در `.env` مقدار `MYSQL_HOST` و `POSTGRES_HOST` را برابر
> `host.docker.internal` قرار دهید (نه `localhost`)، چون از داخل
> container، `localhost` به خود container اشاره می‌کند نه به هاست. این
> روش روی Windows، Mac و Linux (با Docker Engine 20.10 به بعد) کار
> می‌کند و از قبل در `docker-compose.yml` (بخش `extra_hosts`) تنظیم شده
> است.
>
> اگر MySQL و PostgreSQL شما در container های دیگری در همان محیط Docker
> هستند، به‌جای `host.docker.internal` از نام سرویس آن‌ها استفاده کنید
> و آن‌ها را در یک شبکه Docker مشترک با این سرویس قرار دهید (و در این
> صورت بخش `extra_hosts` در `docker-compose.yml` دیگر لازم نیست).

### ۳. اجرا

```bash
docker compose up --build
```

همین دستور، ابزار را build و اجرا می‌کند. با اتمام کار، container متوقف
می‌شود و گزارش نهایی هم در کنسول و هم در `logs/migration.log` نمایش
داده می‌شود.

## تنظیمات پیشرفته (`.env`)

| متغیر | توضیح | پیش‌فرض |
|---|---|---|
| `BATCH_SIZE` | تعداد ردیف در هر batch انتقال داده | `1000` |
| `DROP_EXISTING_TABLES` | اگر `true`، جداول موجود در PostgreSQL قبل از ساخت مجدد حذف می‌شوند | `false` |
| `SCHEMA_ONLY` | اگر `true`، فقط ساختار جداول منتقل می‌شود (بدون داده) | `false` |
| `DATA_ONLY` | اگر `true`، فرض بر این است که جداول از قبل ساخته شده‌اند و فقط داده منتقل می‌شود | `false` |
| `EXCLUDE_TABLES` | لیست جداولی که باید نادیده گرفته شوند (comma separated) | خالی |
| `INCLUDE_TABLES` | اگر مقداردهی شود، فقط همین جداول منتقل می‌شوند | خالی |
| `STOP_ON_ERROR` | اگر `true`، با اولین خطا کل فرآیند متوقف می‌شود | `false` |
| `LOG_LEVEL` | سطح جزئیات لاگ: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |

## قابلیت Resume (ادامه بعد از قطعی)

وضعیت migration به صورت مداوم در فایل `logs/migration_state.json` ذخیره
می‌شود. اگر برنامه به هر دلیلی (قطعی برق، خطای شبکه، توقف دستی) متوقف
شود، کافی است دوباره اجرا کنید:

```bash
docker compose up
```

ابزار به صورت خودکار:
- جداولی که schema آن‌ها قبلاً ساخته شده را دوباره نمی‌سازد
- جدولی که داده‌هایش کامل منتقل شده را رد می‌کند
- جدولی که در وسط انتقال قطع شده را از همان ردیف (offset) ادامه می‌دهد
- Index، Foreign Key و Sequence هایی که قبلاً اعمال شده‌اند را دوباره اعمال نمی‌کند

### شروع migration کاملاً از نو

اگر می‌خواهید تاریخچه resume را پاک کرده و از ابتدا شروع کنید، فایل state
را حذف کنید:

```bash
rm logs/migration_state.json
```

و در صورت نیاز `DROP_EXISTING_TABLES=true` را در `.env` تنظیم کنید تا
جداول قبلی هم در PostgreSQL بازسازی شوند.

## نگاشت انواع داده (Data Type Mapping)

| MySQL | PostgreSQL |
|---|---|
| `tinyint(1)` | `boolean` |
| `tinyint` (غیر از `(1)`) | `smallint` |
| `smallint` | `smallint` |
| `mediumint` | `integer` |
| `int` | `integer` |
| `bigint` | `bigint` |
| `decimal` / `numeric` | `numeric(p,s)` |
| `float` | `real` |
| `double` | `double precision` |
| `varchar(n)` | `varchar(n)` |
| `char(n)` | `char(n)` |
| `text` / `tinytext` / `mediumtext` / `longtext` | `text` |
| `datetime` | `timestamp` |
| `timestamp` | `timestamptz` |
| `date` | `date` |
| `time` | `time` |
| `json` | `jsonb` |
| `enum(...)` | `varchar(n) + CHECK (col IN (...))` |
| `blob` / `tinyblob` / `mediumblob` / `longblob` | `bytea` |
| `binary` / `varbinary` | `bytea` |

### تصمیمات معماری مهم این پروژه

- **Primary Key**: مقادیر `INT`/`BIGINT` اصلی بدون تغییر منتقل می‌شوند
  (بدون تبدیل به UUID).
- **ENUM**: به `varchar` + یک `CHECK constraint` جداگانه تبدیل می‌شود
  (نه به نوع native `ENUM` در PostgreSQL)، تا تغییر مقادیر مجاز در آینده
  بدون نیاز به `ALTER TYPE` ممکن باشد.
- **Auto Increment**: به `GENERATED BY DEFAULT AS IDENTITY` تبدیل می‌شود
  (نه `ALWAYS`)، چون داده‌ها با مقدار اصلی خودشان درج می‌شوند و
  PostgreSQL نباید مقدار جدیدی تولید کند. بعد از اتمام انتقال، مقدار
  sequence با `MAX(column)` جدول همگام (sync) می‌شود تا رکوردهای بعدی که
  توسط برنامه واقعی درج می‌شوند دچار تداخل کلید نشوند.

## ترتیب اجرای مراحل

برای جلوگیری از افت شدید سرعت هنگام INSERT و رعایت وابستگی‌های FK، مراحل
به این ترتیب اجرا می‌شوند:

1. خواندن schema تمام جداول از MySQL
2. ساخت جداول در PostgreSQL (بدون Index/FK)
3. انتقال داده‌ها (batch به batch)
4. اعمال Index ها و Unique Constraint ها
5. اعمال Foreign Key ها
6. تبدیل Auto Increment به Identity و sync سازی Sequence ها

## ساختار پروژه

```
migration/
├── docker-compose.yml       # تعریف سرویس Docker
├── Dockerfile               # image ابزار migration
├── requirements.txt         # وابستگی‌های پایتون
├── .env.example              # نمونه فایل تنظیمات
├── README.md                 # همین فایل
│
├── app/
│   ├── main.py                    # نقطه ورود برنامه
│   ├── config.py                  # خواندن و اعتبارسنجی تنظیمات از .env
│   ├── mysql_reader.py            # خواندن schema/data از MySQL
│   ├── postgres_writer.py         # اجرای DDL و درج داده در PostgreSQL
│   ├── schema_converter.py        # تبدیل انواع داده و تولید DDL جدول
│   ├── data_migrator.py           # هماهنگ‌کننده اصلی کل فرآیند
│   ├── index_converter.py         # تولید DDL برای Index/Unique
│   ├── fk_converter.py            # تولید DDL برای Foreign Key
│   ├── sequence_converter.py      # تبدیل Auto Increment به Identity
│   ├── logger.py                  # راه‌اندازی متمرکز لاگینگ
│   └── utils.py                   # توابع کمکی و مدیریت state
└── sql/
    └── mysql_full_backup.sql      # دقیقا با همین نام جایگزاری شود
    
└── logs/                     # فایل‌های لاگ و state (mount شده از هاست)
    ├── migration.log
    ├── errors.log
    └── migration_state.json
```

## مثال خروجی

```
users
52341/410000 [00:42<02:15, 1234.56ردیف/s]

posts
91000/700000 [01:10<05:30, 987.65ردیف/s]

========================================
خلاصه Migration
========================================
Tables   : 128
Rows     : 5,432,192
Duration : 00:38:11
Errors   : 0
========================================
```

## محدودیت‌های شناخته‌شده

- انواع داده مکانی (Spatial: `geometry`, `point`, و غیره) به `text` تبدیل
  می‌شوند؛ برای پشتیبانی کامل مکانی، PostgreSQL باید افزونه PostGIS
  داشته باشد که خارج از محدوده این ابزار است.
- نوع `SET` در MySQL (که چند مقدار همزمان دارد) به `text` ساده تبدیل
  می‌شود، نه به آرایه یا نوع اختصاصی.
- View ها، Trigger ها، Stored Procedure ها و Event های MySQL منتقل
  نمی‌شوند؛ فقط جداول و داده‌های آن‌ها هدف این ابزار است.
- برای جداول با کلید مرکب (composite primary key) بسیار بزرگ، ORDER BY
  روی چند ستون ممکن است سرعت خواندن batch را کمی کاهش دهد؛ در صورت نیاز
  می‌توان روی آن ستون‌ها Index موقت در MySQL ساخت.

## عیب‌یابی (Troubleshooting)

**خطای اتصال به MySQL/PostgreSQL:**
مطمئن شوید مقادیر `.env` صحیح‌اند و از داخل container قابل دسترسی هستند.
برای تست، می‌توانید داخل container یک shell باز کنید:

```bash
docker compose run --rm migration bash
```

**Migration کند است:**
مقدار `BATCH_SIZE` را افزایش دهید (مثلاً به `5000`) تا تعداد رفت‌وبرگشت
شبکه کمتر شود. توجه کنید افزایش بیش از حد ممکن است مصرف RAM را بالا ببرد.

**می‌خواهم فقط چند جدول خاص را منتقل کنم:**
از `INCLUDE_TABLES=table1,table2,table3` در `.env` استفاده کنید.