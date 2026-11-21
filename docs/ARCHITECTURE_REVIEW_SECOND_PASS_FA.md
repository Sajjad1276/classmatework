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
- README و مستندات وضعیت واقعی را آشکار می‌کنند: هسته Bot و اولین فرم‌های مدیریتی پیاده‌سازی شده‌اند؛ اجرای زنده Telegram و Gateهای باقی‌مانده مانع ادغام‌اند.
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


## 10. وضعیت موارد اصلاح‌شده بعد از ثبت بازبینی

- مورد CI وابسته به Frontend رفع شد: Job مربوط به Node/Frontend حذف شده است.
- مصنوعات Web UI حذف شدند: فایل‌های React/Vite در این شاخه حذف شده‌اند.
- دستور توسعه Frontend از Makefile حذف شد.
- README، سند تصمیم‌ها، Security Baseline، Phase 1 و قرارداد API به‌روز شدند.
- این اصلاحات فقط اسناد و ساختار Web را حذف/هم‌راستا کرده‌اند؛ Bot و Telegram Identity همچنان پیاده‌سازی نشده‌اند.


## 11. به‌روزرسانی پس از شروع اجرای فاز ۱

پس از ثبت یافته‌های اولیه، این اصلاحات در شاخه انجام شده‌اند:

- Browser Auth endpoints و Browser Session model از مسیر اجرایی حذف شدند؛ Migration 0003 جدول sessions را حذف و Email/Password را اختیاری می‌کند.
- TelegramAccount، AdminPairingToken و CLI مالک مبتنی بر Telegram ID افزوده شدند.
- aiogram Bot Runtime، Private Chat guard، Telegram Identity middleware، Pairing و فرمان‌های پایه اضافه شدند.
- Use Caseهای Application برای Tenant و عملیات پایه آموزشی ایجاد شدند.
- منوی Bot اکنون Tenant list/create را نمایش می‌دهد و Wizard ساخت Tenant دارای Preview، Confirm، Back، Cancel و Flow Token اختصاصی است.
- RBAC ارث‌بری والد به فرزند و Composite Foreign Keyهای جلوگیری از cross-tenant اضافه شدند.
- Migration 0004 و Update Dedup Middleware برای ثبت Lease و جلوگیری از پردازش مجدد Telegram Update اضافه شده‌اند.
- Correlation ID هر Update به Audit context منتقل می‌شود.
- اجرای CI برای Commit `2e3f0d2c572afa938add772f09cbedb062a36e6d` موفق شد: Ruff، چهار Migration PostgreSQL و 27 تست با یک Warning غیرمسدودکننده. Run: `44932471573`.
- Bot در Telegram واقعی یا Railway هنوز Deploy/تست نشده و فاز ۱ همچنان باز است.


## 12. وضعیت تأیید کیفیت پس از سخت‌سازی Bot

برای Commit `2e3f0d2c572afa938add772f09cbedb062a36e6d`، اجرای CI شماره 242 با موفقیت تمام شد:
- Ruff: All checks passed.
- Migrationهای PostgreSQL: موفق.
- Tests: 27 passed, 1 warning.
- Redis 7 و PostgreSQL 16 در CI به‌عنوان سرویس واقعی اجرا شدند.
- Warning مربوط به Redis memory overcommit در کانتینر CI غیرمسدودکننده بود.
- Bot هنوز با Token واقعی در Telegram یا Railway اجرا نشده؛ این آزمون خارج از CI و یکی از Gateهای باز فاز یک است.

کنترل Rate Limit نیز به‌صورت Lua atomic counter در Redis افزوده شده است: سقف پیش‌فرض 30 پیام و 45 Callback برای هر Telegram ID در هر پنجره 60 ثانیه؛ هنگام قطعی Redis درخواست رد می‌شود.


## 13. وضعیت به‌روزشده پس از تکمیل فرم‌های ساختار آموزشی

- Bot اکنون علاوه بر Tenant، جریان‌های فهرست/ایجاد School، Academic Year، Class و Subject را نیز دارد.
- Tenant/School/Academic Year در Redis FSM به‌عنوان Context انتخابی نگهداری می‌شوند؛ هر Use Case پیش از تغییر دوباره Permission و مالکیت منبع را بررسی می‌کند.
- فهرست Tenant قابل انتخاب است؛ فهرست School، Academic Year، Class و Subject از منوهای زمینه‌ای ارائه می‌شود.
- عملیات ایجاد دارای Preview/Confirm و Callbackهای Back/Cancel وابسته به Flow Token است.
- CI برای Commit `fcd542e67eab873bfb8b9bca626ce3d7388b2b27` سبز است: Ruff، چهار Migration و 27 passed, 1 warning؛ [Run #249](https://github.com/Sajjad1276/classmatework/actions/runs/44932613037).
- این موفقیت شامل اجرای واقعی Bot در Telegram نیست. Bot Token آزمایشی/اجرای End-to-End هنوز فراهم نشده است.
- Update/Archive، Search/Pagination کامل، Role assignment، Unlink/recovery، Step-up/MFA و M2M integration در Gateهای بعدی باقی می‌مانند.


## 14. وضعیت مرجع و نهایی پس از بازبینی آخر

این بخش بر snapshotهای تاریخی بالاتر اولویت دارد:

- Bot Adapter و Runtime با aiogram 3 در شاخه وجود دارد؛ اجرای زنده در Telegram هنوز انجام نشده است.
- منوی مدیریتی Tenant/School و Wizardهای پایه فهرست/ایجاد مدرسه، سال تحصیلی، کلاس و درس در شاخه پیاده‌سازی شده‌اند.
- Application Use Case و Policy قبل از هر Write مالکیت منبع و Permission را دوباره بررسی می‌کنند.
- Browser Auth و Frontend از محصول حذف شده‌اند.
- CI برای Head فعلی `fcd542e67eab873bfb8b9bca626ce3d7388b2b27` موفق است: Ruff، چهار Migration و 27 تست موفق با یک Warning. [Run #249](https://github.com/Sajjad1276/classmatework/actions/runs/44932613037).
- PR2 همچنان Draft است و Merge نشده است.
- Gateهای باز: آزمون واقعی Bot با Token آزمایشی، Unlink/recovery، Step-up/MFA، مدیریت نقش/عضویت، Update/Archive و Search/Pagination کامل، M2M integration auth و مرور کامل Audit readها.
- هیچ Deploy یا تغییر تنظیمی در Railway و هیچ تغییری در `classmate2` انجام نشده است.


## 15. دعوت نقش‌محور و واگذاری امن مجوز

- نقص Pairing اولیه اصلاح شد: پذیرش دعوت فقط TelegramAccount را فعال نمی‌کند؛ RoleBinding و در Scope Tenant، TenantMembership نیز در همان تراکنش ساخته می‌شوند.
- دعوت‌نامه با Role ID، Scope Type/ID و Tenant ID ذخیره می‌شود؛ Token خام فقط یک بار در deep-link برگردانده می‌شود و در DB فقط digest وجود دارد.
- واگذاری از اصل no privilege escalation پیروی می‌کند: inviter باید permissionهای allow نقش هدف را در Scope مقصد داشته باشد.
- در Platform scope، platform:users:manage و platform:roles:assign لازم است. در Tenant scope، membership:invite و role:manage در همان Tenant لازم است.
- در زمان پذیرش دعوت نیز وضعیت و اختیار فعلی دعوت‌کننده و وجود/مجوزهای نقش دوباره بررسی می‌شوند؛ پس از سلب اختیار، لینک قبلی قابل استفاده نیست.
- نقش‌های پایه Tenant عبارت‌اند از tenant_admin و tenant_viewer. Admin مجوزهای عملیاتی موجود مدرسه/ساختار آموزشی را دارد و viewer فقط school:read دارد؛ عضویت و Role management فقط به admin داده می‌شود.
- /roles برای Platform با دسترسی سراسری و /roles TENANT_UUID برای مدیر Tenant با نمایش فقط نقش‌های همان Tenant اضافه شد.
- /invite TELEGRAM_ID ROLE_KEY SCOPE_TYPE SCOPE_ID NAME نقش و محدوده را صریح می‌گیرد. رابط فاز یک عمداً فقط platform و tenant را قبول می‌کند.
- Migration 0005 ستون‌های Role/Scope، قید positive Telegram ID، قید scope shape، unique active invitation per target و Seed نقش‌های پیش‌فرض Tenantهای قبلی را اضافه می‌کند؛ دعوت‌نامه‌های قدیمی فاقد Role invalidated می‌شوند.
- CI برای Commit 746c1be44488df498737bcf5b2dd385ea0799f93 سبز است: Ruff، پنج Migration PostgreSQL و 34 passed, 1 warning. [Run #317](https://github.com/Sajjad1276/classmatework/actions/runs/44936217541).
- Gateهای باز همچنان شامل آزمون Bot واقعی در Telegram، MFA/Step-up، unlink/recovery، CRUD کامل Role/Permission و Membership، M2M auth و live E2E هستند.
