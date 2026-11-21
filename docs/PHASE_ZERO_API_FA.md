# قرارداد API فاز صفر

Base path: /api/v1  
Format: JSON با UTF-8  
زمان‌ها: UTC در قراردادهای آتی؛ قالب تاریخ کسب‌وکار هنوز تعریف نشده است.

## GET /health/live

برای سنجش زنده‌بودن process و مناسب بودن health probe پلتفرم.

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

هر پاسخ HTTP دارای هدر X-Request-ID است. مقدار ورودی فقط در صورت انطباق با
الگوی ^[A-Za-z0-9._:-]{1,64}$ پذیرفته می‌شود؛ در غیر این صورت UUID جدید تولید می‌شود.

این شناسه برای ردیابی درخواست است و جایگزین actor ID، audit event یا idempotency key نیست.

## محدودیت قرارداد فعلی

هیچ endpoint ورود، مدیریت مدرسه، عضویت، انتشار محتوا یا عملیات مدیریتی هنوز فعال نیست.
قراردادهای آن‌ها بعد از تصویب مدل هویت، tenant و permission نسخه‌بندی می‌شوند.
