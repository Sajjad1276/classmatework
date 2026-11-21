# یافته‌های تکمیلی دور دوم — بررسی کد و Migration

این الحاقیه یافته‌هایی را ثبت می‌کند که با تطبیق مدل ORM، Migration و مسیر اجرای واقعی کد مشخص شدند. این‌ها صرفاً توصیه عمومی نیستند؛ مستقیماً در شاخه فعلی PR2 مشاهده شده‌اند.

## 1. اختلاف Constraint بین ORM و PostgreSQL Migration

در مدل RoleBinding، Check Constraint سه حالت مجاز را مشخص می‌کند:

- Platform: مقدار tenant_id و scope_id باید NULL باشد.
- Tenant: مقدار tenant_id باید دقیقاً برابر scope_id باشد.
- Organization/School/Class: هر دو شناسه Tenant و Scope باید مقدار داشته باشند.

اما Migration فعلی فقط Platform را از سایر مقدارها جدا می‌کند و برای Scopeهای غیر Platform صرفاً non-NULL بودن tenant_id را الزام می‌کند. بنابراین محدودیت دیتابیس از مدل ضعیف‌تر است و مقادیر نامعتبر scope_type یا ناسازگاری tenant_id/scope_id می‌توانند از لایه SQL عبور کنند.

**اولویت: بحرانی.** Migration باید با مدل هم‌سطح شود و تست مستقیم PostgreSQL برای هر حالت مجاز و نامجاز نوشته شود.

## 2. روابط آموزشی و مرز Tenant

در جداول School، AcademicYear، ClassGroup و Subject کلیدهای خارجی UUID به‌صورت جداگانه وجود دارند. این روابط به‌تنهایی اثبات نمی‌کنند که:

- School انتخاب‌شده متعلق به همان Tenant رکورد فرزند است.
- AcademicYear متعلق به همان School و Tenant کلاس است.
- Organization متصل به School متعلق به همان Tenant است.

اعتبارسنجی Route در چند مسیر این موارد را کنترل می‌کند، اما DB باید از invariantهای اصلی هم محافظت کند. Composite Foreign Key/Unique Constraint یا راهکار معادل همراه با Domain validation و تست cross-tenant لازم است.

**اولویت: بحرانی.**

## 3. سیاست Scope و ارث‌بری

Permission evaluator فعلی فقط Platform binding، Tenant binding دقیق و Scope binding دقیق را می‌بیند. این رفتار با ساختار سلسله‌مراتبی طراحی نهایی یکسان نیست. برای مثال، نقش Tenant باید طبق Policy اجازه دسترسی به School فرزند را داشته باشد؛ در عین حال نباید به Tenant والد دیگر، School هم‌سطح یا Scope خارج از شاخه گسترش یابد.

**اولویت: بحرانی.** قرارداد inheritance باید در یک تابع Policy مرکزی پیاده‌سازی شود و برای تمام مرزها تست مثبت/منفی داشته باشد؛ راه‌حل با اضافه کردن شرط موردی داخل Router قابل قبول نیست.

## 4. هویت مدیر و Bootstrap

مدل فعلی User ایمیل و Password Hash را الزامی کرده، Session شامل Browser Cookie/CSRF data است و CLI مالک اولیه را با ایمیل/گذرواژه ایجاد می‌کند. این رفتارها با مدل Telegram Identity نهایی هم‌راستا نیستند.

نیاز به مدل Telegram Account مجزا، Pairing یک‌بارمصرف و قابل ابطال، Bootstrap مبتنی بر Telegram User ID عددی و امکان تعلیق/Unlink وجود دارد. Username نباید کلید هویت شود.

**اولویت: بحرانی.**

## 5. مرزهای Application و Adapter

Router فعلی عملیات پایگاه داده، اعتبارسنجی منابع، ثبت Audit و Commit را در همان Endpoint انجام می‌دهد. انتقال این کد به Bot Handler فقط نوع وابستگی را عوض می‌کند و مشکل معماری را باقی می‌گذارد.

Use Case باید مالک مجوز، قواعد دامنه، تراکنش، Audit و خروجی ساخت‌یافته باشد. Bot و API فقط Adapterهایی هستند که همان Use Case را صدا می‌زنند.

**اولویت: بالا.**

## 6. Integration API

کد فعلی الگوی Machine-to-Machine مستقلی برای API Integration تعریف نکرده است. هویت Telegram و هویت Service باید مستقل بمانند. Header حاوی Telegram User ID نباید قابل جعل و تبدیل به احراز هویت شود.

**اولویت: بالا.** قبل از اتصال عملیاتی به classmate2، Service Credential محدود، Audience/Scope، چرخش/ابطال، Correlation، Idempotency و Replay Protection لازم است.

## 7. Audit و عملیات

Audit append-only در ORM و PostgreSQL Trigger وجود دارد و باید حفظ شود؛ با این حال ثبت Actor مبتنی بر Telegram، اتصال Correlation ID از Update تا Use Case و ثبت کنترل‌شده readهای حساس هنوز در مسیر اجرایی Bot وجود ندارند.

ثبت Event باید allowlist-based باشد. قرار دادن پیام آزاد، Token، Password یا Callback Secret داخل event_data ممنوع است.

## 8. اصلاحات انجام‌شده در این بازنگری

- مرجع طراحی و اسناد کلیدی به Telegram-first تغییر کردند.
- فایل‌های React/Vite از شاخه PR حذف شدند.
- Makefile دیگر Frontend command ندارد.
- CI دیگر Node/Frontend Build ندارد.
- README و مستندات وضعیت واقعی را آشکار می‌کنند: Bot هنوز پیاده‌سازی نشده و Browser Auth فعلی مانع ادغام است.
- PR در وضعیت Draft باقی مانده است.

## 9. ترتیب اجرای موانع

پیش از توسعه منوها و قابلیت‌های بیشتر، ترتیب کار باید چنین باشد:

1. اصلاح Migration/Model invariants و تست‌های Postgres.
2. اصلاح Scope hierarchy و تست Tenant isolation.
3. طراحی Telegram Account و Pairing و جایگزینی Browser Auth.
4. ساخت Application Use Case و Repository boundary.
5. ساخت Bot Adapter، Private Chat middleware، Callback validation و Redis FSM.
6. افزودن Idempotency، Audit correlation و Integration service auth.
7. افزودن تست End-to-End ربات و بررسی نهایی PR.

تا زمانی که بندهای 1 تا 6 تکمیل نشده‌اند، اضافه کردن تعداد زیادی منو ارزش ندارد؛ رابط تلگرام روی هسته ناامن یا ناسازگار فقط خطاها را سریع‌تر در دسترس قرار می‌دهد.
