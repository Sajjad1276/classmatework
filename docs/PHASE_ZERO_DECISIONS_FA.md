# دفتر تصمیم‌های معماری و اجرایی ClassMate Work

**نسخه بازنگری:** 1.0  
**وضعیت:** تصمیم رابط مدیریتی قطعی؛ جزئیات عملیاتی مشخص‌شده در این سند مبنای ادامه توسعه‌اند.

## تصمیم‌های قطعی

| موضوع | تصمیم | پیامد اجرایی |
|---|---|---|
| مرز پروژه | ClassMate Work مستقل از `classmate2` است | هیچ کد، تنظیم یا داده‌ای در classmate2 تغییر نمی‌کند |
| رابط انسانی مدیریت | فقط ربات اختصاصی تلگرام | پنل وب، React، سایت مدیریت و Telegram Web App خارج از دامنه‌اند |
| معماری | Modular Monolith با Adapterهای ورودی | منطق مشترک در Use Caseها؛ Handler و Route نازک |
| Backend | Python 3.12 و Application Layer مشترک | عملیات از Use Caseهای مشترک اجرا می‌شوند؛ HTTP/FastAPI مستقل وجود ندارد |
| Bot Framework | aiogram 3 به‌عنوان انتخاب اجرایی پیشنهادی | ساختار Router/Handler/Middleware و FSM؛ تا ساخت کد، پیاده‌سازی‌شده محسوب نمی‌شود |
| دریافت Update | Long Polling در فاز اول | تنها یک Bot Poller فعال برای هر Bot Token |
| استقرار | همان Railway Project موجود | یک Bot Service به‌علاوه PostgreSQL و Redis؛ API Service مجزا وجود ندارد |
| داده | PostgreSQL و SQLAlchemy 2 | منبع حقیقت داده |
| State و Rate Limit | Redis | FSM با TTL، قفل کوتاه‌مدت و محدودسازی درخواست |
| زبان رابط | فارسی | پیام‌ها، خطاها و منوها فارسی و قابل توسعه به چندزبان |
| هویت مدیر | Telegram User ID عددی | username و نام نمایشی مبنای شناسایی/مجوز نیست |
| سطح چت | فقط Private Chat | Updateهای گروه/کانال برای مدیریت رد می‌شوند |
| ثبت‌نام | ممنوع | ایجاد مالک اولیه با Bootstrap کنترل‌شده؛ سایر مدیران با Pairing/Invitation یک‌بارمصرف |
| دسترسی | RBAC + ABAC و Scope hierarchy | دکمه و Context انتخابی مجوز محسوب نمی‌شود |
| Audit | Append-only | هر عملیات مدیریتی و امنیتی دارای Actor و Correlation ID است |

## معماری Service در Railway

- `classmatework-admin-bot`: تنها سرویس اجرایی برنامه برای رابط مدیریتی Telegram.
- PostgreSQL و Redis منابع مشترک Bot هستند.
- سرویس FastAPI/HTTP جداگانه وجود ندارد؛ Use Caseهای Application مستقیماً از آداپترهای مجاز فراخوانی می‌شوند.
- Worker مستقل فقط بعد از تعریف Jobهای durable، صف، Retry، Lease، Dead Letter و Idempotency قابل توجیه است.
- پیکربندی یا Deploy سرویس‌ها بخشی از این سند نیست و بدون دستور جداگانه انجام نمی‌شود.

## قرارداد تعامل Bot

- منوها با Inline Keyboard ساخته می‌شوند.
- هر دکمه Handler واقعی، Use Case، کنترل مجوز و تست دارد.
- هر صفحه فرزند مسیر Back؛ هر Wizard مسیر Cancel دارد.
- `/menu`، `/back`، `/cancel`، `/help` و `/status` رفتار سراسری مشخص دارند.
- ورودی نامعتبر مرحله جاری را حفظ می‌کند و پیام خطای همان مرحله را می‌دهد.
- Callback منقضی، تکراری یا متعلق به Actor دیگر بی‌اثر و رد می‌شود.
- State فرم با TTL و کلید مجزای کاربر/چت در Redis نگهداری می‌شود؛ عملیات مهم و تأییدها در PostgreSQL رکورد durable دارند.
- Callback Data فقط کد/شناسه opaque کوتاه است و حداکثر داده لازم برای Routing را حمل می‌کند؛ Token، Secret یا مجوز داخل آن قرار نمی‌گیرد.
- هر Callback Query باید پاسخ داده شود.
- هیچ پیام موفقیت پیش از Commit یا تأیید نتیجه سرویس نمایش داده نمی‌شود.

## تصمیم‌های امنیتی هویت

1. هویت از `Update.from.id` معتبر تلگرام استخراج می‌شود.
2. username فقط برای نمایش است و تغییر آن هویت کاربر را تغییر نمی‌دهد.
3. هر درخواست، وضعیت Account، Telegram Link، Role Binding، Membership و Scope را دوباره بررسی می‌کند.
4. اتصال مدیر از طریق Pairing Token تصادفی، یک‌بارمصرف، هش‌شده و زمان‌دار انجام می‌شود.
5. Bootstrap اولیه به Telegram User ID عددی نیاز دارد و باید از مسیر امن و کنترل‌شده انجام شود.
6. تغییر Roleهای حساس و عملیات پرخطر نیازمند تأیید مجدد/چهارچشمی خواهند بود؛ جزئیات Policy در فاز Identity تثبیت می‌شود.
7. Token ربات فقط Secret محیطی سرویس Bot است و نباید در Git یا Log ثبت شود.

## موارد باز باقیمانده

این موارد عمداً بدون فرض محصولی باقی می‌مانند و باید هنگام رسیدن به ماژول مربوطه با تصمیم مستند بسته شوند:

1. آیا چند Bot Token محیطی/تجاری لازم است یا فقط یک Bot مدیریتی سراسری؟
2. فرایند تأیید Pairing اولیه مدیران و بازیابی دسترسی مالک در صورت از دست‌رفتن حساب Telegram.
3. سیاست نهایی MFA/Step-up برای Platform Owner و عملیات حساس.
4. قواعد دقیق Retention، آرشیو، حذف و Legal Hold.
5. سیاست انتشار و سازگاری نسخهٔ بک‌اند مشترک برای اجزای سامانه.
6. میزان دسترس‌پذیری، Backup/Restore و SLAهای عملیاتی.
7. سیاست Quota، Billing و Multi-tenant تجاری در صورت نیاز آینده.
8. مسیر بازیابی/Resume فرم بعد از Restart ربات و مدت TTL هر نوع Wizard.

## قواعد ممنوع

- افزودن پنل وب یا احراز هویت مدیریتی مرورگری.
- ساخت مدیر با username یا Telegram ID ارسالی از متن/Callback.
- اعتماد به Callback Data یا Tenant Context برای Authorization.
- اتصال مستقیم Handler به ORM و تکثیر منطق در آداپترهای ورودی.
- Deploy چند Poller هم‌زمان برای یک Bot Token.
- ساخت Worker اضافی بدون صف و نیاز عملیاتی روشن.
- اضافه‌کردن منو برای قابلیت پیاده‌سازی‌نشده.
- تغییر یا Deploy در classmate2 از این Repository.
