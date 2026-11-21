# Identity — Telegram-first

**Decision:** مدیر انسانی از طریق Telegram Identity به پلتفرم متصل می‌شود؛ Browser Login در محصول نهایی وجود ندارد.

## مدل هویت هدف

- User هویت سراسری پلتفرم است.
- Telegram Account به User متصل می‌شود و Telegram User ID عددی شناسه اصلی اتصال است.
- username، display name و عکس فقط metadata هستند و مبنای Authorization نیستند.
- فقط Private Chat برای مدیریت پذیرفته می‌شود.
- وضعیت User، Telegram Link، Role Binding، Tenant Membership و بازه اعتبار در هر عملیات بررسی می‌شوند.
- تعلیق حساب یا قطع اتصال باید دسترسی Update بعدی را فوراً ببندد.

## Bootstrap و Pairing

- ثبت‌نام عمومی ممنوع است.
- Platform Owner اولیه از مسیر CLI کنترل‌شده و با Telegram User ID عددی از قبل تأییدشده provision می‌شود.
- افزودن مدیران بعدی با Pairing Token تصادفی، یک‌بارمصرف، hash‌شده، کوتاه‌عمر و Audit‌شده انجام می‌شود.
- Token خام فقط یک بار نمایش داده می‌شود؛ در DB، Log، Callback یا Audit ذخیره نمی‌شود.
- بازیابی مالکیت در صورت از دست‌رفتن حساب Telegram باید فرایند کنترل‌شده و خارج از پیام آزاد ربات داشته باشد.

## نشست و وضعیت گفت‌وگو

- Browser Cookie Session و CSRF در محصول نهایی استفاده نمی‌شود.
- Redis برای FSM با TTL، Navigation State و Rate Limit استفاده می‌شود.
- عملیات حساس/تأییدشده در PostgreSQL به‌صورت Durable ثبت می‌شود.
- State با Actor ID، Chat ID، Flow ID و زمان انقضا جداسازی می‌شود.
- Callback کوتاه و opaque است و هر بار Actor، Resource، Expiry و وضعیت مصرف دوباره بررسی می‌شوند.
- عملیات Mutation باید Idempotent باشد تا Double-click یا تکرار Update دوباره اجرا نشود.

## وضعیت واقعی کد این شاخه

کد فعلی هنوز Browser Auth، Argon2id password، Cookie Session و CSRF دارد و CLI آن مالک را با ایمیل/گذرواژه می‌سازد. این‌ها پیاده‌سازی هدف Telegram-first نیستند و باید قبل از ادغام حذف یا جایگزین شوند.

ربات، Middleware، Telegram Account Link، Pairing، FSM و Idempotency هنوز در این شاخه پیاده‌سازی نشده‌اند. این سند معماری هدف را تعریف می‌کند و ادعای آماده‌بودن Bot ندارد.
