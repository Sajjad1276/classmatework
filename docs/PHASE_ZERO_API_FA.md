# قرارداد Health و API زیرساختی ClassMate Work — فاز صفر

**دامنه این سند:** ثبت قرارداد Health و Correlation ایجادشده در Phase 0 است؛ این سند فهرست کامل قابلیت‌های فعلی یا API محصول نهایی نیست.

Base path: /api/v1  
Format: JSON با UTF-8  
زمان‌ها: UTC در قراردادهای آتی.

## GET /health/live

برای سنجش زنده‌بودن Process و health probe پلتفرم.

پاسخ 200:

    {"status":"ok"}

این endpoint وابستگی‌های بیرونی را بررسی نمی‌کند.

## GET /health/ready

اتصال قابل استفاده به PostgreSQL و پاسخ Redis را بررسی می‌کند.

پاسخ 200:

    {"status":"ready","checks":{"database":"ok","redis":"ok"}}

پاسخ 503 در صورت در دسترس نبودن حداقل یکی از وابستگی‌ها:

    {"status":"not_ready","checks":{"database":"unavailable","redis":"ok"}}

جزئیات exception، hostname، username، password و URL اتصال در پاسخ نمایش داده نمی‌شوند.

## Correlation

هر پاسخ HTTP دارای هدر X-Request-ID است. مقدار ورودی فقط در صورت انطباق با الگوی
^[A-Za-z0-9._:-]{1,64}$ پذیرفته می‌شود؛ در غیر این صورت UUID جدید تولید می‌شود.

این شناسه برای ردیابی درخواست است و جایگزین actor ID، audit event یا idempotency key نیست.

## مرز API در معماری نهایی

FastAPI رابط انسانی مدیریت نیست. APIهای مدیریتی/Integration تنها با قرارداد Service-to-Service،
احراز هویت ماشین، Audience و Scope محدود، اعتبارسنجی Payload، Rate Limit، Idempotency و Audit
قابل ارائه‌اند.

کد فعلی PR2 چند Route مدیریتی و مسیر Login مرورگری دارد، اما این‌ها قرارداد نهایی نیستند؛
تا زمان انتقال منطق به Application Use Case، جایگزینی هویت مرورگری با Telegram Identity،
و افزودن Service Authentication، نباید به‌عنوان API عملیاتی نهایی منتشر شوند.

در معماری نهایی:
- Admin Bot تنها رابط انسانی مدیریت است.
- FastAPI برای Health و Integration کنترل‌شده باقی می‌ماند.
- Telegram User ID ارسالی از Header یا Body هرگز به تنهایی هویت احراز‌شده محسوب نمی‌شود.
