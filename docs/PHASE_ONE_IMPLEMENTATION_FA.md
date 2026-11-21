# فاز ۱ از ۵ — هویت تلگرام و هسته مدیریت ClassMate Work

**شاخه:** phase-1/core-identity-access  
**PR:** شماره ۲، Draft، باز و ادغام‌نشده  
**وضعیت:** هسته کد و اولین Vertical Slice ساخته شده؛ فاز ۱ هنوز کامل نیست.  
**معیار ادغام:** تمام Gateهای پایانی این سند باید بسته شوند.

## 1. معماری اجراشده در شاخه

- رابط مدیریتی انسانی فقط Bot تلگرام است.
- FastAPI فعلاً فقط Health و APIهای زیرساختی را ارائه می‌کند؛ مدیریت انسانی از HTTP فعال نیست.
- Bot با aiogram 3 و Long Polling ساخته شده است.
- Application Use Caseها منطق مستقل از Handler/Route را اجرا می‌کنند.
- PostgreSQL منبع حقیقت و Redis محل FSM، TTL و قفل Poller است.
- classmate2 مستقل است و در این شاخه تغییر نکرده است.

## 2. پیاده‌سازی‌های موجود

### هویت و امنیت Bot
- User و TelegramAccount با Telegram ID عددی.
- Bootstrap مالک با CLI.
- Pairing مدیر جدید با Token یک‌بارمصرف، Hash-only، کوتاه‌عمر و محدود به Telegram ID هدف؛ دعوت‌نامه Role و Scope صریح دارد و پذیرش آن RoleBinding و در Scope Tenant عضویت را اتمیک می‌سازد.
- Private Chat guard، رد حساب ناشناس یا غیرفعال.
- Redis FSM و قفل Poller تک‌نمونه‌ای.
- Telegram Update dedup با Lease پایدار در PostgreSQL.
- Rate Limit اتمیک Redis برای پیام و Callback با رفتار Fail-Closed.
- Callbackهای Wizard به Flow Token متصل‌اند تا Callback قدیمی روی فرم جدید اثر نگذارد.
- Audit correlation با شناسه tg-UPDATE_ID.

### دسترسی و صحت داده
- RBAC + scope ancestry، ارث‌بری والد به فرزند و Deny-overrides-Allow.
- Membership فعال و بازه اعتبار نقش‌ها کنترل می‌شود.
- Migration 0002، قیود RoleBinding و Composite FKهای آموزشی را اضافه می‌کند.
- تست‌های Unit/Integration برای Scope و Tenant isolation اضافه شده‌اند.

### Application Layer و اولین قابلیت واقعی
- Use Caseهای مستقل Tenant، School، Academic Year، Class، Subject، Role و Membership ساخته شده‌اند.
- Tenant list/select و Tenant create در Bot متصل‌اند.
- فرم‌های School list/create، Academic Year list/create، Class list/create و Subject list/create در Bot متصل شده‌اند.
- فرمان‌های /roles، /role-create، /members و /revoke-invite برای مدیریت پایه نقش‌ها، اعضا و دعوت‌ها اضافه شده‌اند.
- Wizardهای ساخت شامل اعتبارسنجی ورودی، Preview، Confirm، Back و Cancel هستند؛ ایجاد کلاس با انتخاب سال تحصیلی انجام می‌شود.
- انتخاب Tenant/School/Academic Year فقط UI Context است؛ Use Case پیش از هر عملیات مالکیت و Permission را دوباره بررسی می‌کند.
- فرم در ورودی نامعتبر در همان مرحله می‌ماند.
- Duplicate slug در Application Layer و DB کنترل می‌شود.
- Audit برای Tenant create و Tenant directory read نوشته می‌شود.

### Migrationها
- 0001: هسته اولیه Tenant/Academic/Identity/RBAC/Audit.
- 0002: scope constraints و composite tenant integrity.
- 0003: TelegramAccount و Pairing Token؛ حذف Browser Sessions.
- 0004: processed_telegram_updates برای Replay suppression.
- 0005: Role/Scope در Pairing، قید مثبت بودن Telegram ID، یکتایی دعوت فعال و Seed نقش‌های پیش‌فرض Tenant.
- 0005: Role/Scope در دعوت‌نامه، قید مثبت بودن Telegram ID، جلوگیری از دعوت فعال تکراری و Seed نقش‌های پایه Tenantهای موجود.

## 3. مواردی که هنوز نباید پیاده‌سازی‌شده اعلام شوند

- Bot هنوز با Bot Token واقعی در Telegram یا Railway آزمایش/Deploy نشده است.
- Invite/Pairing در تست‌ها بررسی شده؛ تحویل واقعی لینک به مدیر دعوت‌شده هنوز end-to-end تست نشده است.
- Unlink/recovery، MFA/step-up و مدیریت نشست/دستگاه هنوز کامل نیست.
- فهرست نقش‌ها (/roles) و دعوت نقش‌محور (/invite) آماده است؛ CRUD کامل Role، مدیریت مستقل Permission و فهرست/تعلیق/لغو عضویت هنوز باقی است.
- فرم‌های پایه فهرست/ایجاد School، Academic Year، Class و Subject در Bot پیاده‌سازی شده‌اند؛ Update/Archive، Search و Pagination کامل هنوز باقی است.
- Audit explorer، گزارش، جست‌وجوی سراسری و داشبورد در فازهای بعدی تکمیل می‌شوند.
- Machine-to-Machine authentication برای Integration API هنوز پیاده‌سازی نشده است.

## 4. محدوده تحویل فاز ۱

مدیر مجاز باید بتواند:

1. با Telegram ID متصل‌شده در Private Chat پذیرفته شود.
2. هویت مدیریتی خود را ببیند.
3. بر اساس Permission منوی درست را ببیند.
4. Tenantها را فهرست کند.
5. Tenant جدید را با Preview و Confirmation بسازد.
6. مدیر جدید را با Pairing یک‌بارمصرف همراه Role و Scope دعوت کند؛ در Scope Tenant، Membership و RoleBinding هنگام پذیرش ساخته می‌شوند.
7. سلامت اتصال Database و Redis را از Bot بررسی کند.
8. از Back/Cancel/Menu و رفتار Callback منقضی استفاده کند.

برای بستن کامل فاز، Update/Archive، Search/Pagination، لغو اتصال مدیر، مدیریت کامل Role/Membership، Integration identity و تست‌های عملی Bot نیز باید تکمیل شوند.

## 5. Gate پذیرش فاز ۱

- [x] CI برای Commit 4980a075f3b75979d17921fc70c487d7d4ada3a1: Ruff، پنج Migration PostgreSQL و ۴۰ تست سبز (یک Warning غیرمسدودکننده). Run: 44937126858.
- [ ] Bot با Token آزمایشی در Telegram اجرا و فرمان‌ها End-to-End بررسی شوند (نیازمند Token آزمایشی و اجرای واقعی).
- [x] تست‌های middleware برای Private Chat و رد مسیرهای خارج از هویت فعال اضافه شده‌اند؛ آزمون End-to-End حساب Unlinked/تعلیق‌شده با Bot واقعی هنوز لازم است.
- [x] Pairing با Role/Scope، جلوگیری از escalation، رد Telegram ID اشتباه، Token منقضی/مصرف‌شده و رد دعوت در صورت لغو اختیار دعوت‌کننده تست شده است.
- [x] Callback قدیمی از Flow قبلی با Flow Token در Wizard ساخت Tenant بی‌اثر است.
- [x] Update تکراری در تست Dedup اجرا نمی‌شود و Update شکست‌خورده پس از آزادشدن Lease قابل Retry است.
- [x] Role/Scope inheritance، Deny و Cross-tenant با تست‌های Policy و PostgreSQL اثبات شده‌اند.
- [x] فرم‌های پایه فهرست/ایجاد School، Academic Year، Class و Subject در Bot پیاده‌سازی شده‌اند.
- [x] فهرست نقش‌ها، ساخت نقش سفارشی با Permission ceiling، فهرست اعضای فعال و لغو دعوت‌نامهٔ پذیرفته‌نشده پیاده‌سازی شده‌اند.
- [x] مدیر Tenant فقط می‌تواند در محدودهٔ خودش نقش واگذار کند، اعضا را ببیند و دعوت صادر/لغو کند.
- [ ] اجرای End-to-End فرم‌های ساختار آموزشی با Bot Token آزمایشی و بررسی دستی خطاها/Back/Cancel.
- [ ] Unlink/recovery و حداقل Step-up برای عملیات حساس تعیین و تست شوند.
- [ ] Audit و correlation برای عملیات حساس پوشش داده شوند.
- [ ] API Integration به شکل امن از هویت Telegram جدا شود.
- [x] CI و Runbook بدون Frontend است؛ CI فعلی Backend/PostgreSQL/Redis را اجرا می‌کند.
- [ ] هیچ Deploy یا تغییری در classmate2 انجام نشده باشد.

**تا بسته‌شدن تمام Gateها PR2 باید Draft باقی بماند و Merge نشود.**
