# بازبینی دو مرحله‌ای معماری ClassMate Work

**نسخه:** 1.0  
**دامنه:** طراحی، PR2 و زیرساخت در شاخه phase-1/core-identity-access  
**تصمیم نهایی:** Telegram-only برای رابط انسانی مدیریت  
**وضعیت:** بازبینی معماری انجام شده؛ موانع پیاده‌سازی و ادغام ثبت شده‌اند.

این سند نتیجه دو بازبینی مستقل است. دور اول از منظر محصول و معماری انجام شد؛ دور دوم با تمرکز بر امنیت، سازگاری داده، عملیات، پایداری و تست انجام شد. این بازبینی ادعای اجرای Bot یا آماده بودن Production ندارد.

---

## دور اول — محصول، تجربه کاربری و معماری

### 1. کشف انحراف

نسخه قبلی SYSTEM_DESIGN_FA.md معماری را بر اساس Admin Web UI، React، صفحات مرورگر و Login/MFA مرورگری تعریف کرده بود. PR2 نیز همین فرض را وارد کد کرده بود: Email/Password، Cookie Session، CSRF و Endpointهای مبتنی بر principal مرورگر.

این مسیر با خواسته محصول یکسان نیست. تصمیم درست:

**ClassMate Work فقط از طریق یک ربات مدیریتی مستقل تلگرام اداره می‌شود.**

### 2. مرزبندی نهایی اجزا

| جزء | مسئولیت | خارج از مسئولیت |
|---|---|---|
| Admin Telegram Bot | منو، فرم، دکمه، Navigation، پیام نتیجه و گزارش برای مدیر | قانون کسب‌وکار و دسترسی نهایی |
| Telegram Adapter | دریافت Update، استخراج Actor، Callback و FSM | Query/Mutation مستقیم ORM |
| Application Use Cases | منطق کسب‌وکار، اعتبارسنجی، مجوز، تراکنش و Audit | نمایش متن تلگرام |
| Domain/Repositories | مدل و قواعد دامنه، Persistence | وابستگی به Telegram یا HTTP |
| FastAPI | Health و Integration/Machine API | پنل وب انسانی |
| PostgreSQL | منبع حقیقت و تاریخچه | نگهداری State موقت Wizard |
| Redis | FSM کوتاه‌عمر، TTL، Rate Limit، Lock | منبع نهایی عملیات مدیریتی |
| Outbox/Job | تحویل قابل Retry و پردازش پایدار | اجرای مستقیم در Callback |

### 3. نتیجه بازبینی تجربه تلگرام

موارد ضروری که باید در طراحی و پیاده‌سازی پوشش داده شوند:

- Inline Keyboard به‌عنوان الگوی اصلی؛ نه Reply Keyboard دائمی.
- منوی اصلی و منوهای سطحی با Navigation Stack روشن.
- Back برای بازگشت یک مرحله؛ Cancel برای پاک‌کردن Flow جاری؛ Menu برای خروج کنترل‌شده به Root.
- /start، /menu، /back، /cancel، /help و /status رفتار مشخص و تست‌شده دارند.
- فرم چندمرحله‌ای پس از ورودی نامعتبر در مرحله جاری باقی می‌ماند.
- هر صفحه فرزند Back و هر Wizard Cancel دارد.
- Callback منقضی یا تکراری به Mutation دوباره منجر نمی‌شود.
- Callback Query همیشه پاسخ داده می‌شود.
- فهرست‌های بزرگ Search/Filter/Pagination دارند.
- عملیات طولانی Job ID و وضعیت واقعی خود را نمایش می‌دهد.
- منوی مدیر فقط قابلیت‌هایی را نمایش می‌دهد که Use Case، Policy، Audit و Test دارند.

### 4. استقرار

تصمیم پیشنهادی برای ساختار فعلی Railway:

- یک Service برای FastAPI.
- یک Service برای Admin Bot.
- همان Railway Project، Environment، PostgreSQL و Redis موجود.
- Long Polling در مرحله اول و فقط یک Replica فعال برای Token ربات.
- Worker سوم فعلاً ساخته نمی‌شود؛ تنها پس از تعریف صف Durable و نیاز واقعی.
- Build و Start Command سرویس API و Bot جدا می‌شوند.
- هیچ Railway variable یا deployment در این بازبینی تغییر نمی‌کند.

### 5. نتیجه دور اول

انحراف اصلی فقط ظاهری نبود. مدل ورود، Session، handler boundary، CI و استقرار نیز باید Telegram-first شوند. سند مرجع، دفتر تصمیم‌ها، README و مستندات فاز یک برای ثبت همین تصمیم اصلاح شده‌اند. حذف Web UI از شاخه PR نیز لازم است تا کد وب به‌اشتباه وارد محصول نشود.

---

## دور دوم — امنیت، داده، عملیات و کیفیت

این دور به‌صورت چک‌لیست تهدید/شکست انجام شد تا خطاهای پنهان معماری در انتقال رابط از وب به Bot باقی نمانند.

### 6. هویت و اعتماد

| مورد | الزام | وضعیت در PR2 |
|---|---|---|
| شناسه مدیر | Telegram User ID عددی از Update معتبر | پیاده‌سازی نشده |
| اعتماد به username | ممنوع؛ فقط metadata | باید در طراحی هویت اعمال شود |
| Private Chat | الزام برای عملیات مدیریت | پیاده‌سازی نشده |
| منع Group/Channel | Update مدیریتی از چت گروهی رد شود | پیاده‌سازی نشده |
| Pairing | Token یک‌بارمصرف، hash‌شده، کوتاه‌عمر و Audit‌شده | پیاده‌سازی نشده |
| ثبت‌نام آزاد | ممنوع | فعلاً ممنوع است |
| لغو دسترسی | تعلیق User یا Unlink باید فوراً اثر کند | نیازمند مدل Telegram Account |

### 7. Callback و FSM

خطرهای بررسی‌شده:

- جعل Callback Data.
- مصرف Callback متعلق به مدیر دیگر.
- Replay پس از موفقیت یا انقضا.
- Double-click و اجرای دوباره Mutation.
- Restart ربات وسط Wizard.
- تداخل State دو مدیر یا دو چت.
- درخواست قدیمی که به Message/Resource جدید اشاره می‌کند.
- از دست‌رفتن Redis و فرض اشتباه درباره وضعیت عملیات.

کنترل‌های الزامی:

1. Callback فقط شناسه کوتاه و opaque است؛ هیچ Secret یا Permission در آن نیست.
2. مالک، Chat، Message، Resource، Expiry و وضعیت Action در Backend بررسی می‌شوند.
3. هر Mutation دارای Idempotency است.
4. Update ID و Action مصرف‌شده قابل تشخیص هستند.
5. FSM در Redis با TTL و کلیدهای مجزا نگهداری می‌شود.
6. عملیات حساس و تأیید نهایی در PostgreSQL durable است.
7. از دست‌رفتن Redis هرگز به معنی تأیید یا اجرای عملیات نیست.
8. Callback منقضی یا مصرف‌شده فقط پیام خطای کنترل‌شده می‌دهد.

**وضعیت:** هیچ‌کدام از کنترل‌های Bot هنوز در کد فعلی ساخته نشده‌اند.

### 8. Authorization و Tenant Isolation

مشکلاتی که در کد موجود مشاهده شدند:

- Permission evaluator فعلی سلسله‌مراتب Scope کامل ندارد؛ نقش Tenant باید در سیاست مشخص بتواند به School فرزند دسترسی داشته باشد، اما نباید به Parent یا sibling scope گسترش یابد.
- وجود tenant_id در درخواست صرفاً انتخاب Context است؛ مجوز نیست.
- Membership فعال و بازه شروع/پایان باید برای هر درخواست بررسی شود.
- FKهای مجزای UUID به‌تنهایی تضمین نمی‌کنند School، Academic Year و Class همگی متعلق به Tenant واحد باشند.
- Routeهای فعلی مستقیماً عملیات و Query/Mutation دامنه را اجرا می‌کنند.

اصلاح لازم:

- سلسله‌مراتب صریح Platform → Tenant → Organization → School → Class.
- ارث‌بری فقط از والد به فرزند، با مرزبندی دقیق؛ Deny در تمام مسیرها مقدم است.
- تست مثبت/منفی برای هر سطح و sibling/tenant دیگر.
- Composite Foreign Key یا قیود داده معادل، همراه با Validation در Domain.
- Use Caseها و Repositoryها؛ Handler و Route فقط Adapter.
- Policy در زمان اجرا و نزدیک به Transaction برای عملیات حساس دوباره بررسی شود.

### 9. عملیات حساس و تأیید

مواردی مانند تغییر نقش حساس، تعلیق مالک، بستن Tenant، Export داده، تغییر Integration و تغییر سیاست سراسری باید:

- اثر و دامنه خود را قبل از اجرا Preview کنند.
- دلیل و تأیید صریح بگیرند.
- Action ID کوتاه‌عمر و غیرقابل Replay داشته باشند.
- در صورت ریسک بالا تأییدکننده دوم مستقل داشته باشند.
- با Compensating Action/Archive قابل برگشت منطقی باشند؛ نه حذف تاریخچه.
- نتیجه واقعی پس از Commit ثبت و نمایش داده شود.

### 10. Audit و Integration

- Audit Append-only فعلی ارزش حفظ دارد.
- Actor باید به User متصل‌شده به Telegram اشاره کند، نه username.
- Correlation باید از Telegram Update به Handler، Use Case، DB، Outbox و API ادامه یابد.
- Token، Password، Callback Secret و داده خصوصی در Event/Log ذخیره نشوند.
- Bot Identity و Service Identity برای API Integration دو هویت جدا هستند.
- API نباید صرفاً با یک Header قابل جعل مثل Telegram User ID، Caller را مدیر تلقی کند.
- Integration با classmate2 نیازمند Service Credential محدود، Audience/Scope، Versioned Contract، Idempotency و Replay Protection است.
- هیچ تغییری در classmate2 در این کار مجاز یا انجام نشده است.

### 11. پایداری و عملیات

موارد لازم:

- Long Polling با تنها یک Poller برای Bot Token.
- رفتار Restart/Graceful Shutdown و ادامه Updateهای باقی‌مانده.
- Retry محدود با Backoff؛ نه Retry نامحدود.
- Timeout برای Telegram API و Integration.
- Dead Letter و بررسی خطاهای Jobهای durable در فاز بعد.
- Readiness شامل وابستگی‌های واقعی هر Service.
- Metrics برای Update، Latency، Handler Failure، Rate Limit، Telegram API Error و Job Lag.
- Token Bot فقط در Environment Secret سرویس Bot.
- Backup رمزنگاری‌شده و Restore آزمایش‌شده قبل از Production.

### 12. CI و تست

CI فعلی PR2 فقط Backend و Frontend Build دارد. پس از حذف Frontend:

- Backend lint/test حفظ شود.
- Migration روی PostgreSQL واقعی CI اجرا شود.
- تست Audit Trigger حفظ شود.
- تست Use Case و Policy افزوده شود.
- با اضافه‌شدن Bot، تست‌های Unknown User، Private Chat، stale callback، replay، double-click، state isolation، Back، Cancel و duplicate Update به CI اضافه شوند.
- CI نهایی نباید Node/Frontend Build داشته باشد.
- موفقیت CI بدون تست‌های معماری Telegram به معنی تکمیل فاز یک نیست.

---

## 13. موانع صریح پیش از ادغام PR2

| شناسه | مانع | اولویت | شرط رفع |
|---|---|---|---|
| B-01 | ورود مرورگری و Cookie/CSRF در مسیر اصلی | بحرانی | جایگزینی با Telegram Identity |
| B-02 | نبود Admin Bot و Bot Token configuration | بحرانی | Bot Adapter قابل اجرا و تست |
| B-03 | نبود Pairing امن مدیر | بحرانی | Bootstrap و Pairing یک‌بارمصرف |
| B-04 | منطق دامنه داخل Routeها | بالا | انتقال به Use Case مشترک |
| B-05 | Scope inheritance ناقص | بحرانی | اصلاح Policy و تست‌های سلسله‌مراتب |
| B-06 | قیود cross-tenant ناکافی در روابط آموزشی | بحرانی | Composite FK/Domain guard و تست |
| B-07 | نبود Update/Callback idempotency | بالا | registry و تست Replay |
| B-08 | نبود FSM/Navigation/Back/Cancel | بالا | Redis FSM و Bot tests |
| B-09 | CI وابسته به Frontend | متوسط | حذف Frontend Job |
| B-10 | README/Runbook وب‌محور | متوسط | اسناد Bot-first |
| B-11 | نبود Service Authentication برای Integration | بالا | قرارداد M2M و تست منفی |
| B-12 | تست‌های Bot و Security منفی ناکافی | بالا | اضافه‌شدن تست‌ها به CI |

**PR2 تا رفع موانع بحرانی و تمام شروط پذیرش فاز یک نباید Merge شود.**

---

## 14. بازبینی نهایی ضدتناقض

پیش از بستن این بازنگری، موارد زیر مجدداً با اسناد و کد مقایسه می‌شوند:

- [x] سند مرجع، Bot را تنها رابط انسانی اعلام می‌کند.
- [x] FastAPI به‌عنوان Integration API تعریف شده، نه سایت مدیریت.
- [x] تصمیم‌های فاز صفر فرض React/Web UI را لغو می‌کنند.
- [x] README وضعیت Bot اجرا‌نشده و PR غیرقابل Merge را آشکار می‌کند.
- [x] خط مبنای امنیت، تهدیدهای Telegram و تفاوت هویت مدیر/سرویس را ثبت می‌کند.
- [x] مستند Phase 1 مسیر جایگزینی Browser Auth را مشخص می‌کند.
- [x] مسئولیت Bot، API، Use Case، DB و Redis تفکیک شده است.
- [x] Railway Project موجود حفظ می‌شود؛ دو Service نقش جدا دارند.
- [x] Worker سوم بدون نیاز عملیاتی اضافه نشده است.
- [x] مرز classmate2 صریح و دست‌نخورده باقی مانده است.
- [ ] Browser Auth از کد PR2 حذف/جایگزین شده است.
- [ ] Bot Adapter و Telegram Identity واقعاً پیاده‌سازی شده‌اند.
- [ ] Scope hierarchy و Cross-tenant constraints با تست اثبات شده‌اند.
- [ ] تست‌های Bot و CI مربوط به آن سبز شده‌اند.
- [ ] PR2 پس از بازبینی کد و CI جدید آماده Merge شده است.

موارد تیک‌نخورده موانع اجرایی‌اند، نه نقص سند. تا بسته شدنشان نباید این پروژه «ربات مدیریتی آماده» معرفی شود.

---

## نتیجه

بازنگری معماری و اسناد به Telegram-first تغییر یافته است؛ اما کد فعلی PR2 هنوز در بخش هویت و Routeها وب‌محور است و Bot هنوز وجود ندارد. این تفاوت عمداً شفاف نگه داشته شده است: **تصویب معماری، معادل اجرای نرم‌افزار نیست.**
