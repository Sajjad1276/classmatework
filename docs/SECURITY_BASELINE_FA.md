# خط مبنای امنیت ClassMate Work — Telegram-first

**وضعیت:** الزامات امنیتی معماری؛ وجود این سند به معنی پیاده‌سازی کنترل‌ها نیست.

## 1. مرزهای امنیتی

- رابط انسانی فقط ربات اختصاصی تلگرام است.
- FastAPI برای Health Check و Integration API است؛ نباید Telegram User ID دلخواه را بدون احراز منبع، هویت مدیر تلقی کند.
- منطق Authorization در Backend/Use Case است؛ Inline Keyboard و Callback Handler صرفاً ورودی غیرقابل اعتمادند.
- `classmate2` یک مرز اعتماد خارجی است و فقط با قرارداد Machine-to-Machine متصل می‌شود.

## 2. هویت و احراز مدیر

- هویت Telegram از `Update.from.id` در Update معتبر استخراج می‌شود.
- شناسه عددی Telegram پایدارتر از username است؛ username و نام نمایشی فقط metadata هستند.
- فقط Private Chat پذیرفته می‌شود؛ گروه و کانال برای عملیات مدیریتی ممنوع است.
- حساب Bot، فرستنده ناشناس، کاربر Pair نشده، کاربر تعلیق‌شده و اتصال ابطال‌شده رد می‌شوند.
- ثبت‌نام آزاد وجود ندارد.
- Bootstrap اولیه Platform Owner باید به Telegram User ID مشخص و تأییدشده متصل باشد.
- Pairing مدیر بعدی با Token تصادفی یک‌بارمصرف، hash‌شده، کوتاه‌عمر و قابل ممیزی انجام می‌شود.
- از ارسال Token/OTP در URL، Callback Data، Log یا Audit اجتناب می‌شود؛ هیچ secret خامی ذخیره نمی‌شود.
- قطع Telegram Link یا تعلیق کاربر باید پیش از Update بعدی اثر کند.

## 3. Callback، FSM و Replay

- تمام Updateها و Callbackها ورودی غیرقابل اعتماد محسوب می‌شوند.
- Callback Data کوتاه و opaque است؛ هرگز شامل Secret، Permission، اطلاعات حساس یا payload اعتمادشده نیست.
- هر Callback در Backend با Actor، Chat، Message، Resource، زمان انقضا و وضعیت مصرف تطبیق داده می‌شود.
- عملیات Mutation دارای Idempotency و جلوگیری از اجرای دوباره است.
- Telegram Update ID و شناسه Actionهای تأییدی برای دفع Replay ثبت می‌شوند.
- Callback قدیمی، منقضی، تکراری یا متعلق به کاربر دیگر بدون Mutation رد می‌شود.
- FSM در Redis با TTL و کلیدهای جدا برای Actor/Chat/Flow نگهداری می‌شود.
- State مهم، تأیید حساس و عملیات در حال انتظار باید در PostgreSQL durable باشد؛ از دست‌رفتن Redis نباید موجب اجرای دوباره یا تأیید ضمنی شود.
- هر Callback Query پاسخ داده می‌شود؛ پیام خطا جزئیات داخلی را افشا نمی‌کند.

## 4. Authorization و جداسازی داده

- RBAC با ABAC و Scope hierarchy اجرا می‌شود: Platform → Tenant → Organization → School → Class.
- مجوز والد فقط به فرزند پایین‌تر ارث می‌رسد و هرگز به والد یا شاخه/فرزند هم‌سطح دیگر منتقل نمی‌شود.
- Deny صریح بر Allow مقدم است.
- برای دسترسی Tenant-scoped عضویت فعال با بازه زمانی معتبر ضروری است، مگر سیاست Platform صریحاً مسیر متفاوتی تعریف کند.
- انتخاب Tenant از منو فقط Context است و مجوز ایجاد نمی‌کند.
- هر Read/Write باید Permission و Object Scope را در Backend کنترل کند.
- Foreign Keyهای ترکیبی/قواعد Domain باید مانع ترکیب School، Academic Year، Class و Subject متعلق به Tenantهای متفاوت شوند.
- Role Binding نباید با تغییر شناسه‌های Callback یا ورودی کاربر قابل جعل باشد.
- عملیات حساس در زمان اجرا Policy را دوباره بررسی می‌کند تا تغییر دسترسی میان Preview و Confirm باعث اجرای غیرمجاز نشود.

## 5. کنترل عملیات خطرناک

- عملیات دارای اثر مهم باید Preview، خلاصه اثر، تأیید صریح و شناسه Action یک‌بارمصرف داشته باشد.
- برای عملیات حساس، تأییدکننده دوم مستقل از درخواست‌دهنده است.
- حذف فیزیکی پیش‌فرض نیست؛ Archive، Suspend یا Compensating Action ترجیح دارد.
- تغییر Role، تعلیق مالک، دسترسی Support، Export و تغییر Integration باید دلیل اجباری و Audit داشته باشد.
- لغو/انقضای تأیید باید قطعی باشد؛ Callback منقضی نباید از مسیر دیگری دوباره قابل مصرف شود.

## 6. Rate Limit و حفاظت ورودی

- Rate Limit در سطح Telegram User ID، Chat، نوع Update و عملیات حساس اعمال می‌شود.
- Flood/Callback spam باید بدون مسدودسازی بی‌دلیل همه مدیران مهار شود.
- متن ورودی پیش از نمایش escape می‌شود.
- ورودی طولانی، متن قالب‌بندی‌شده، فایل و شناسه باید محدودیت اندازه/نوع داشته باشد.
- خطاهای SQL، Stack Trace، Token، Secret و اطلاعات خصوصی به Telegram ارسال نمی‌شوند.
- Token ربات فقط در Environment Variables سرویس Bot نگهداری می‌شود.

## 7. Audit و مشاهده‌پذیری

- Audit Append-only باقی می‌ماند؛ Update/Delete عادی ممنوع است.
- حداقل Actor، Tenant/Scope، Event Type، Resource، زمان، Correlation ID و نتیجه ثبت می‌شود.
- داده حساس در Audit با Allowlist ثبت می‌شود؛ متن آزاد، Token و رمز ذخیره نمی‌شود.
- ثبت ورود، Pairing، قطع اتصال، تغییر نقش، رد مجوز، تأیید، Export و عملیات حساس الزامی است.
- Correlation ID مسیر Update → Handler → Use Case → DB/Outbox → Integration را پیوند می‌دهد.
- Log باید ساختاریافته و Redacted باشد.

## 8. Machine-to-Machine و classmate2

- Telegram User Identity و Service Identity دو هویت جدا هستند.
- هر Service Credential دارای Audience، Scope، زمان اعتبار، چرخش و امکان ابطال است.
- API نباید با Header دلخواه `X-Telegram-User-ID` به Caller هویت مدیریتی بدهد.
- Webhook دارای Signature/تاریخ/Replay Protection است.
- Payload و Event نسخه‌بندی و اعتبارسنجی می‌شوند.
- هیچ secret یا تغییر تنظیمی در classmate2 از این پروژه اعمال نمی‌شود.

## 9. وضعیت پیاده‌سازی فعلی در شاخه فاز یک

**کنترل‌های موجود در کد (نیازمند قبولی CI و آزمون عملی Bot):**

- مسیرهای Browser Auth از API فعال حذف شده‌اند؛ جدول Browser Session در Migration 0003 حذف می‌شود و Email/Password دیگر هویت مدیریتی اجباری نیست.
- TelegramAccount با Telegram User ID عددی، وضعیت اتصال و metadata ایجاد شده است.
- Bootstrap مالک پلتفرم با Telegram ID عددی و Pairing یک‌بارمصرف مدیران وجود دارد.
- Private Chat Guard و رد User/TelegramAccount غیرفعال پیاده‌سازی شده است.
- Redis FSM، قفل Poller، شناسه Flow برای Callbackهای Wizard، ثبت Durable پردازش Update و Rate Limit اتمیک پیام/Callback پیاده‌سازی شده‌اند؛ اختلال Redis باعث رد درخواست می‌شود.
- Telegram Update correlation به Audit context منتقل می‌شود.
- RBAC ارث‌بری والد به فرزند و Deny-overrides-Allow دارد؛ Composite FK و RoleBinding Check Constraint در Migration 0002 تعریف شده‌اند.
- Use Caseهای Tenant/School/Academic Year/Class/Subject در Application Layer وجود دارند؛ Bot فعلاً فهرست/ایجاد Tenant را به UI متصل کرده است.
- Migration 0004 جدول پردازش Updateها را اضافه می‌کند.

**موارد باز:**

- CI برای Commit `d66416d76ad5b3220eae410ca85b2328190cab01` سبز شده است (Ruff، چهار Migration، ۲۵ تست و یک Warning غیرمسدودکننده).
- اجرای واقعی Bot با Token معتبر و تست end-to-end هنوز انجام نشده است.
- لغو اتصال، بازیابی مدیر و Step-up/MFA کامل نیست.
- مدیریت Role/Permission و فرم‌های کامل CRUD آموزشی در Bot هنوز تکمیل نشده‌اند.
- Integration API و Service Authentication برای اتصال آینده به classmate2 هنوز ساخته نشده‌اند.
- Audit readهای حساس و Correlation end-to-end هنوز نیازمند بازبینی گسترده‌اند.

هیچ Railway Deployment یا تغییر در classmate2 در این مرحله انجام نشده است.

## 10. کنترل‌های پایه محیط

- Secret واقعی در Git نیست؛ `.env.example` فقط مقدار توسعه غیرحساس دارد.
- تصویر Runtime با کاربر غیرریشه اجرا می‌شود.
- Readiness فقط وضعیت وابستگی‌ها را می‌دهد، نه جزئیات Secret.
- Migrationها قبل از اعمال در محیط اشتراکی بازبینی و در CI آزمایش می‌شوند.
- Backup رمزنگاری‌شده و Restore تست‌شده شرط آمادگی Production است؛ صرف وجود PostgreSQL کافی نیست.
