# ClassMate Work

سامانه مستقل مدیریت و عملیات ClassMate. این مخزن مالک منطق مدیریتی است؛ منطق آموزشی/دانش‌آموزی در مخزن جداگانه `classmate2` باقی می‌ماند و از طریق قراردادهای کنترل‌شده API/Event متصل می‌شود.

## تصمیم قطعی رابط مدیریت

**رابط انسانی مدیریت فقط ربات اختصاصی تلگرام است.** این پروژه پنل وب مدیریتی، سایت ادمین، React، Telegram Web App یا ورود مدیریتی از مرورگر ندارد.

- ربات تلگرام: رابط منوها، فرم‌ها، تأییدها و گزارش‌های مدیر.
- FastAPI: Health Check و APIهای داخلی/یکپارچه‌سازی؛ نه پنل انسانی.
- Application Use Cases: منطق مشترک و مستقل از Telegram Handler یا HTTP Route.
- PostgreSQL: منبع اصلی داده و Audit.
- Redis: State فرم‌های ربات، TTL، قفل کوتاه‌مدت و Rate Limit.

## وضعیت پیاده‌سازی — شفاف

- Phase 0 زیرساخت اولیه را در `main` دارد.
- بازطراحی مرجع Telegram-first در شاخه `phase-1/core-identity-access` انجام شده است.
- PR شماره ۲ هنوز Draft و ادغام‌نشده است.
- **ربات مدیریتی هنوز پیاده‌سازی نشده است.**
- کد فعلی PR2 هنوز بخشی از احراز هویت مرورگری (ایمیل/گذرواژه، Cookie، CSRF و Session مرورگر) و منطق کسب‌وکار داخل Routeها دارد؛ این کد با تصمیم نهایی محصول هم‌راستا نیست و پیش از ادغام باید بازنویسی/جایگزین شود.
- تا زمان تکمیل Bot Adapter، Telegram Identity، Application Use Cases و تست‌های مربوطه، قابلیت عملیاتی مدیریتی آماده استفاده نیست.
- هیچ ادعایی مبنی بر اتصال یا Deploy ربات در Railway وجود ندارد.

## معماری استقرار هدف

در همان Railway Project موجود، بدون ساخت Project جدید:

| Service | وظیفه |
|---|---|
| `classmatework-api` | FastAPI، Health Check و APIهای سرویس‌به‌سرویس |
| `classmatework-admin-bot` | ربات مدیریتی تلگرام با Long Polling |
| PostgreSQL | ذخیره‌سازی اصلی |
| Redis | FSM، Rate Limit، TTL و قفل‌ها |

API و Bot دو فرایند با وظایف جدا هستند، نه دو Worker تکراری. هر دو از کد مشترک و دیتابیس مشترک استفاده می‌کنند. در فاز اولیه فقط یک Replica از Bot Poller مجاز است. Worker عمومی جداگانه تا وقتی نیاز واقعی و صف پایدار تعریف نشده اضافه نمی‌شود.

**توجه:** جدول بالا معماری هدف است؛ سرویس Bot و Start Command آن هنوز ایجاد/Deploy نشده‌اند. هیچ تنظیم Railway با این بازنگری تغییر نکرده است.

## اجرای محلی زیرساخت فعلی

نیازمندی‌ها: Python 3.12+، Docker Compose.

1. فایل نمونه محیط را کپی کنید: `cp .env.example .env`
2. زیرساخت و API فعلی را اجرا کنید: `docker compose up --build`
3. Migrationهای موجود را اجرا کنید:

   `alembic -c backend/alembic.ini upgrade head`

4. CLI فعلی ایجاد مالک پلتفرم هنوز بر پایه ایمیل/گذرواژه است و **Bootstrap نهایی Telegram-first محسوب نمی‌شود**؛ برای محیط واقعی از آن به‌عنوان روش نهایی ایجاد مدیر استفاده نکنید.

API روی پورت 8000 در دسترس است. در وضعیت فعلی، این اجرا به معنی آماده‌بودن ربات مدیریتی نیست.

## APIهای زیرساختی فعلی

| روش | مسیر | کاربرد |
|---|---|---|
| GET | `/api/v1/health/live` | زنده‌بودن Process |
| GET | `/api/v1/health/ready` | آمادگی PostgreSQL و Redis |

Routeهای مدیریتی فعلی در PR2 موقت‌اند و تا انتقال به Use Caseهای مشترک و هویت تلگرام نباید به‌عنوان قرارداد نهایی معرفی یا برای مدیریت واقعی استفاده شوند.

## اصول غیرقابل نقض

1. رابط انسانی مدیریت فقط Bot تلگرام است؛ وب‌پنل در دامنه محصول نیست.
2. Telegram User ID عددی و استخراج‌شده از Update معتبر، شناسه اتصال است؛ username هویت یا مجوز نیست.
3. فقط Private Chat مجاز است؛ مدیریت از گروه/کانال رد می‌شود.
4. دکمه، Callback Data، Tenant ID و پیام کاربر به‌تنهایی مجوز نیستند؛ Backend باید هر بار Policy و Scope را بررسی کند.
5. Telegram Handler و FastAPI Route مستقیماً منطق دامنه یا ORM را اجرا نمی‌کنند؛ Use Case مالک منطق است.
6. Update و عملیات حساس باید در برابر Replay و اجرای تکراری مقاوم باشند.
7. هر تغییر مدیریتی قابل ممیزی است؛ Audit append-only باقی می‌ماند.
8. هیچ secret، Bot Token یا داده واقعی کاربران وارد Git، Log یا Callback نمی‌شود.
9. هیچ تغییر یا Deploy در `classmate2` از این مخزن انجام نمی‌شود.
10. قابلیت ناقص یا بدون Handler، Use Case، مجوز و تست در منوی مدیر نمایش داده نمی‌شود.

## اسناد مرجع

- طراحی مرجع Telegram-first: `docs/SYSTEM_DESIGN_FA.md`
- بازبینی دو مرحله‌ای معماری: `docs/ARCHITECTURE_REVIEW_FA.md`
- تصمیم‌های فاز صفر: `docs/PHASE_ZERO_DECISIONS_FA.md`
- خط مبنای امنیت: `docs/SECURITY_BASELINE_FA.md`
- قرارداد API فاز صفر: `docs/PHASE_ZERO_API_FA.md`
- وضعیت و موانع فاز یک: `docs/PHASE_ONE_IMPLEMENTATION_FA.md`
