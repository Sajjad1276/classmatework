# ClassMate Work

سامانه مستقل مدیریت و عملیات ClassMate. این مخزن مالک منطق مدیریتی است؛ منطق آموزشی/دانش‌آموزی در مخزن جداگانه classmate2 باقی می‌ماند و فقط از طریق قراردادهای کنترل‌شده API/Event به آن متصل خواهد شد.

## تصمیم قطعی محصول

**رابط انسانی مدیریت فقط ربات اختصاصی تلگرام است.** پنل وب، سایت ادمین، React، Telegram Web App و ورود مدیریتی مرورگری خارج از دامنه محصول‌اند.

- Telegram Admin Bot: منوها، فرمان‌ها، فرم‌ها، تأییدها و گزارش‌های مدیر.
- FastAPI: Health Check و APIهای داخلی/یکپارچه‌سازی؛ نه پنل انسانی.
- Application Use Cases: منطق کسب‌وکار مستقل از Telegram Handler یا HTTP Route.
- PostgreSQL: منبع اصلی داده، مجوز و Audit.
- Redis: FSM، TTL، قفل Poller و کنترل نرخ درخواست.

## نقشه تحویل

کل پروژه دقیقاً در پنج فاز تحویل می‌شود. فاز جاری ۱ است. نقشه کامل و Gate هر فاز در:
docs/MASTER_ROADMAP_5_PHASES_FA.md

## وضعیت واقعی فاز ۱

شاخه کاری: phase-1/core-identity-access  
PR شماره ۲: Draft، باز و ادغام‌نشده.

### پیاده‌سازی‌شده در شاخه

- هویت مدیر بر اساس Telegram User ID عددی؛ username فقط metadata است.
- Bootstrap مالک پلتفرم از CLI با Telegram ID عددی.
- Pairing یک‌بارمصرف مدیر جدید، محدود به Telegram ID مقصد، با Token خامِ ذخیره‌نشده و Audit.
- Bot Runtime با aiogram 3، Long Polling، Redis FSM و قفل توزیع‌شده برای جلوگیری از دو Poller هم‌زمان.
- Private Chat guard و رد حساب ناشناس/تعلیق‌شده.
- فرمان‌های /start، /menu، /back، /cancel، /invite، /help و /status.
- Inline menu پویا بر اساس Permission.
- فرم چندمرحله‌ای ساخت Tenant با اعتبارسنجی، Preview، تأیید، Back، Cancel و شناسه Flow برای رد Callbackهای قدیمی.
- Use Caseهای مستقل برای ساخت/فهرست Tenant و عملیات پایه مدرسه، سال تحصیلی، کلاس و درس.
- RBAC با ارث‌بری از والد به فرزند، Deny-overrides-Allow و کنترل Membership.
- Composite Foreign Key برای جلوگیری از اتصال School، Academic Year، Class و Subject به Tenant/School اشتباه.
- Migrationهای هویت تلگرام و وضعیت پردازش Telegram Update.
- جلوگیری از پردازش دوباره Update با ثبت Durable در PostgreSQL و Lease؛ Actionهای دامنه همچنان باید Idempotent بمانند.
- CI بدون Frontend Build و دارای PostgreSQL 16 و Redis 7 واقعی برای Migration و تست‌های یکپارچگی.
- آخرین CI برای Commit `d66416d76ad5b3220eae410ca85b2328190cab01` موفق است: Ruff، چهار Migration و ۲۵ تست با یک Warning غیرمسدودکننده. [مشاهده اجرای CI](https://github.com/Sajjad1276/classmatework/actions/runs/44931316097).

### هنوز انجام‌نشده یا نیازمند تأیید

- تست‌های واقعی علیه Telegram Bot API با Token اختصاصی و تأیید اجرای واقعی در محیط توسعه.
- اجرای واقعی Bot با Token آزمایشی در Telegram و آزمون End-to-End هنوز انجام نشده است؛ CI موفق، جایگزین این آزمون نیست.
- قطع اتصال Telegram، بازیابی مدیر، MFA/Step-up و سیاست تأیید دوم.
- تکمیل CRUD مدرسه، سال تحصیلی، کلاس و درس در رابط Bot؛ Use Case پایه وجود دارد اما فرم‌های Bot هنوز کامل نیستند.
- مدیریت Role/Permission از طریق Bot.
- API احراز هویت‌شده Machine-to-Machine؛ در حال حاضر API عمومی فقط Health را ارائه می‌کند.
- Audit correlation برای Telegram Update ایجاد شده، اما بازبینی کامل end-to-end و پوشش Readهای حساس باقی است.
- اتصال عملیاتی و Deploy در Railway انجام نشده است.

**وجود کد Bot به معنی Deploy یا آماده‌بودن Production نیست.** هیچ Bot Token یا Secret واقعی در Git قرار نگرفته است.

## معماری استقرار هدف در Railway

در همان Railway Project موجود، بدون ساخت Project جدید:

| Service | مسئولیت |
|---|---|
| classmatework-api | FastAPI، Health Check و APIهای سرویس‌به‌سرویس |
| classmatework-admin-bot | ربات مدیریت تلگرام با Long Polling |
| PostgreSQL | ذخیره‌سازی اصلی |
| Redis | FSM، Rate Limit، Lease و قفل Poller |

API و Bot دو فرایند با مسئولیت جدا هستند؛ دو Worker تکراری نیستند. در شروع فقط یک Replica از Bot Poller مجاز است. Worker عمومی سوم تا زمان وجود نیاز عملیاتی و صف Durable ساخته نمی‌شود.

هیچ Railway Service، Variable یا Deployment در این مرحله تغییر داده نشده است.

## اجرای محلی

نیازمندی‌ها: Python 3.12+ و Docker Compose.

1. فایل نمونه را به .env کپی کنید.
2. PostgreSQL، Redis و API را با docker compose up --build اجرا کنید.
3. Migrationها را با alembic -c backend/alembic.ini upgrade head اعمال کنید.
4. پس از آماده بودن دیتابیس، اولین مالک را با PYTHONPATH=backend python -m app.cli create-platform-owner بسازید. فرمان، Telegram User ID عددی و نام را می‌گیرد.
5. برای اجرای محلی Bot، مقدار TELEGRAM_BOT_TOKEN را فقط در .env محلی تنظیم و سرویس را با docker compose --profile bot up --build admin-bot اجرا کنید.

Token واقعی نباید در مخزن، Log یا پیام خطا قرار گیرد. اجرای Bot نیازمند Telegram Bot Token معتبر و یک Telegram User ID است که به‌صورت امن در CLI provision شده باشد.

API روی پورت 8000 ارائه می‌شود. مسیرهای فعلی API:
- GET /api/v1/health/live
- GET /api/v1/health/ready

## اصول غیرقابل نقض

1. رابط انسانی مدیریت فقط Bot تلگرام است.
2. Telegram User ID از Update معتبر استخراج می‌شود؛ username یا Callback Data هویت و مجوز نیست.
3. فقط Private Chat مجاز است.
4. هر Handler قبل از عملیات، هویت، وضعیت حساب، Permission و Scope را بررسی می‌کند.
5. Handler و Route منطق دامنه یا Query/Mutation مستقیم ORM را در خود نگه نمی‌دارند؛ Use Case مالک منطق است.
6. Callback قدیمی، تکراری یا متعلق به Flow قبلی نباید عملیات را اجرا کند.
7. هر عملیات تغییردهنده باید در برابر Replay و اجرای تکراری مقاوم باشد.
8. Audit append-only است و Secret یا Token خام در Audit/Log ذخیره نمی‌شود.
9. هیچ تغییر یا Deploy در classmate2 از این مخزن انجام نمی‌شود.
10. PR2 تا تکمیل Gate فاز ۱ Draft باقی می‌ماند.

## اسناد مرجع

- طراحی مرجع: docs/SYSTEM_DESIGN_FA.md
- نقشه پنج فاز: docs/MASTER_ROADMAP_5_PHASES_FA.md
- برنامه و موانع فاز یک: docs/PHASE_ONE_IMPLEMENTATION_FA.md
- بازبینی معماری: docs/ARCHITECTURE_REVIEW_FA.md
- یافته‌های بازبینی کد: docs/ARCHITECTURE_REVIEW_SECOND_PASS_FA.md
- خط مبنای امنیت: docs/SECURITY_BASELINE_FA.md
- قرارداد Health/API: docs/PHASE_ZERO_API_FA.md
