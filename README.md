# ClassMate Work

سامانه‌ی مستقل مدیریت و عملیات ClassMate. این مخزن مالک منطق مدیریتی است؛
منطق آموزشی/دانش‌آموزی در مخزن جداگانه‌ی classmate2 باقی می‌ماند.
این پروژه در فاز صفر قرار دارد و هنوز هیچ قابلیت مدیریتیِ احراز هویت‌شده‌ای
برای استفاده‌ی عملیاتی ارائه نمی‌کند.

## وضعیت فعلی

- اسکلت Modular Monolith با FastAPI و مرزهای اولیه‌ی ماژول‌ها
- API نسخه‌دار در /api/v1
- health check زنده‌بودن و آمادگی وابستگی‌ها
- شناسه‌ی هم‌بستگی درخواست در هدر X-Request-ID
- اتصال زیرساختی PostgreSQL و Redis
- پیکربندی Alembic برای migrationهای بعدی
- پوسته‌ی اولیه‌ی رابط فارسی و راست‌به‌چپ
- تست و CI پایه

## اجرای محلی

نیازمندی‌ها: Python 3.12+، Node.js 22+، npm و Docker Compose.

1. فایل نمونه‌ی محیط را کپی کنید: cp .env.example .env
2. سرویس‌های زیرساخت و API را اجرا کنید: docker compose up --build
3. رابط مدیریتی را در ترمینال جداگانه اجرا کنید:
   cd frontend && npm install && npm run dev

API روی http://localhost:8000 و رابط وب روی http://localhost:5173 در دسترس خواهد بود.

### اجرای API بدون Docker

python -m venv .venv  
فعال‌سازی محیط مجازی و سپس:

pip install -r requirements-dev.txt  
uvicorn app.main:app --app-dir backend --reload

PostgreSQL و Redis باید مطابق مقادیر .env در دسترس باشند.

## مسیرهای زیرساختی

| روش | مسیر | کاربرد |
| --- | --- | --- |
| GET | /api/v1/health/live | بررسی زنده‌بودن process |
| GET | /api/v1/health/ready | بررسی اتصال PostgreSQL و Redis |
| GET | /docs | مستندات OpenAPI در محیط توسعه |

پاسخ readiness فقط وضعیت کلی وابستگی‌ها را می‌دهد و جزئیات اتصال یا secretها را افشا نمی‌کند.

## ساختار مخزن

- backend/app/api: لایه‌ی HTTP و قراردادهای API
- backend/app/core: تنظیمات، logging و request context
- backend/app/db: زیرساخت persistence و base مدل‌ها
- backend/app/modules: مرزهای دامنه‌ای مستقل
- backend/alembic: migrationهای پایگاه داده
- frontend: پوسته‌ی React/TypeScript با RTL
- tests: تست‌های backend
- docs: اسناد تصمیم، معماری و خط مبنای امنیت

## کنترل کیفیت

ruff check backend tests  
pytest  
cd frontend && npm install && npm run build

## اصول غیرقابل نقض

1. این مخزن مستقل از classmate2 است؛ تغییرات آن پروژه خارج از دامنه‌ی این مخزن است.
2. شناسه‌ی tenant ارسال‌شده از client هویت یا مجوز محسوب نمی‌شود.
3. هیچ endpoint مدیریتی پیش از پیاده‌سازی identity و authorization امن فعال نمی‌شود.
4. health check جایگزین authentication یا کنترل دسترسی نیست.
5. secret واقعی، داده‌ی مدرسه یا اطلاعات کاربر وارد Git نمی‌شود.
6. آمار داشبورد تا زمان اتصال به منبع معتبر نمایش داده نمی‌شود.

## وضعیت تصمیم‌های محصول

تصمیم‌های باز و فرض‌های موقت در docs/PHASE_ZERO_DECISIONS_FA.md ثبت شده‌اند.
تا تعیین تکلیف آن‌ها، فقط زیرساخت و قراردادهای کم‌ریسک توسعه می‌یابند.
