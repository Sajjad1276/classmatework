# نقشه اجرایی و تحویل پروژه ClassMate Work — پنج فاز

**نسخه:** 1.0  
**تاریخ تصویب نقشه:** 2026-11-21  
**تعداد فازهای پروژه:** دقیقاً 5  
**رابط انسانی مدیریت:** فقط ربات اختصاصی تلگرام  
**Repository:** Sajjad1276/classmatework  
**مرز غیرقابل تغییر:** classmate2 مستقل می‌ماند.

---

## 1. قانون برنامه

کل پروژه ClassMate Work در **پنج فاز تحویلی** تکمیل می‌شود. این تقسیم‌بندی، مبنای برنامه‌ریزی، توسعه، بازبینی، تست و تحویل است.

- فاز صفر جدیدی تعریف نمی‌شود. زیرساخت Phase 0 که قبلاً ساخته و در main ادغام شده، پیش‌نیاز موجود پروژه محسوب می‌شود.
- پنج فاز به‌ترتیب اجرا می‌شوند؛ هم‌زمان چند فاز را برای جلوه ظاهری شروع نمی‌کنیم.
- هر فاز باید کد قابل اجرا، تست، مستندات، مهاجرت دیتابیس و معیار پذیرش روشن داشته باشد.
- ثبت یک گزینه در منو یا تکمیل مستند، معادل تحویل قابلیت نیست.
- هر فاز فقط پس از قبولی Gate همان فاز بسته می‌شود.
- رابط انسانی همه قابلیت‌ها فقط Bot تلگرام است. پنل وب، React، سایت مدیریت و Telegram Web App در دامنه محصول نیستند.
- سرویس HTTP/FastAPI جداگانه در دامنه فعلی وجود ندارد؛ Botها مستقیماً از Application Use Case مشترک استفاده می‌کنند.
- پروژه و Serviceها در همان Railway Project موجود مدیریت می‌شوند؛ ایجاد Project جدید جزو این نقشه نیست.
- تغییر یا Deploy در classmate2 بدون دستور و قرارداد جداگانه ممنوع است.

## 2. نقشه پنج فاز در یک نگاه

| فاز | عنوان | حوزه‌های اصلی | تحویل قابل مشاهده |
|---|---|---|---|
| 1 | هسته امن و ربات مدیریتی | Telegram Identity، Bot Core، RBAC/Scope، Application Layer، Audit و داده پایه | مالک پلتفرم می‌تواند امن وارد Bot شود و عملیات پایه کنترل‌شده را انجام دهد |
| 2 | مدیریت ساختار و کاربران | Tenant، Organization، School، Academic، User، Membership، Invite و Role Management | چرخه اصلی مدیریت مدرسه و اعضا از داخل Bot کامل می‌شود |
| 3 | محتوا و گردش‌کار | Content، Revision، Workflow، Approval، Scheduling و Rollback | چرخه ایجاد تا بررسی، تصویب و انتشار محتوا کامل می‌شود |
| 4 | ارتباطات و خودکارسازی | Telegram Connections، Notifications، Outbox، classmate2 Integration، Jobs، Bulk Import/Export | ارتباطات و عملیات تکراری/گروهی پایدار و قابل پیگیری می‌شوند |
| 5 | گزارش، امنیت و تحویل نهایی | Dashboard، Reporting، Audit Explorer، Security Incident، Settings، Feature Flags، Observability، DR و Release | سامانه با معیارهای Production، مستندات عملیاتی و تست بازیابی تحویل می‌شود |

---

# فاز ۱ — هسته امن و ربات مدیریتی

**هدف:** ساخت پایه‌ای که مدیر واقعی بتواند از طریق Bot وارد شود و فقط عملیات مجاز را اجرا کند.  
**وضعیت فعلی:** فاز جاری. کد فعلی PR2 هنوز مانع ادغام دارد و Bot اجرایی وجود ندارد.

### دامنه توسعه

1. **حذف کامل مسیر هویت مرورگری**
   - حذف Email/Password و Browser Cookie/CSRF از مسیر مدیریت انسانی.
   - تعریف هویت User و Telegram Account با Telegram User ID عددی.
   - Bootstrap کنترل‌شده Platform Owner.
   - Pairing مدیران بعدی با Token یک‌بارمصرف، کوتاه‌عمر، hash‌شده و قابل Audit.
   - Unlink، تعلیق، قطع دسترسی و بازیابی کنترل‌شده.

2. **ساخت هسته Bot**
   - aiogram 3 و Long Polling.
   - فقط Private Chat.
   - Actor extraction، middleware و rate limit.
   - فرمان‌های /start، /menu، /back، /cancel، /help و /status.
   - Inline Keyboard، Callback Factory، Navigation Stack و Redis FSM.
   - پاسخ‌گویی به تمام Callback Queryها.
   - کنترل Callback منقضی، تکراری یا متعلق به مدیر دیگر.
   - Idempotency برای Telegram Update و Actionهای Mutation.
   - ادامه امن یا انقضای قابل پیش‌بینی Flowها بعد از Restart.

3. **Application Layer**
   - Use Case، Repository، Policy و Audit Service.
   - منطق دامنه فقط در Use Caseهای مشترک اجرا شود.
   - Telegram Handlerها صرفاً Adapter باشند.
   - Transaction boundary و خروجی ساخت‌یافته.

4. **هسته دسترسی و صحت داده**
   - RBAC + ABAC و Scope hierarchy.
   - ارث‌بری مجوز فقط از والد به فرزند.
   - Deny-overrides-Allow.
   - Membership و اعتبار زمانی Role Binding.
   - اصلاح اختلاف Constraint بین ORM و Migration.
   - Composite FK یا راهکار دیتابیسی معادل برای invariants.
   - تست cross-tenant و scope isolation.

5. **پایه عملیات**
   - حفظ PostgreSQL، Redis، Alembic و Audit append-only.
   - Correlation ID از Telegram Update تا Use Case.
   - یکپارچگی مستقیم Botها با Use Caseهای مشترک؛ بدون HTTP relay داخلی.
   - تنظیم اجرای محلی و CI بدون Frontend.

### تحویل‌های الزامی

- Bot قابل اجرا در محیط توسعه با Token محیطی.
- اتصال امن Platform Owner و امکان Pairing مدیر.
- منوی اصلی و Navigation پایدار.
- مشاهده هویت و سطح دسترسی.
- انتخاب Tenant مجاز.
- عملیات پایه Tenant و یک Vertical Slice واقعی برای مدرسه.
- Audit تمام عملیات حساس و ثبت Actor واقعی.
- قرارداد داخلی مستقیم برای فراخوانی Use Case از Botها.

### Gate خروج فاز ۱

- [ ] هیچ Browser Auth در مسیر مدیریت وجود ندارد.
- [ ] فقط User متصل‌شده و فعال در Private Chat پذیرفته می‌شود.
- [ ] Callback جعلی، منقضی، تکراری و متعلق به Actor دیگر بی‌اثر است.
- [ ] Back، Cancel، Menu و ورودی نامعتبر در تست پوشش دارند.
- [ ] Scope inheritance و cross-tenant integrity با تست PostgreSQL اثبات شده‌اند.
- [ ] Use Caseها از Telegram و HTTP مستقل هستند.
- [ ] Audit append-only و Correlation کامل است.
- [ ] Bot و API با Start Command جداگانه اجرا می‌شوند.
- [ ] CI مربوط به Backend، Bot و PostgreSQL سبز است.
- [ ] هیچ Service یا کدی در classmate2 تغییر نکرده است.

**خروجی تحویلی:** هسته امن و قابل استفاده ربات مدیریت؛ نه مجموعه‌ای از منوهای نمایشی.

---

# فاز ۲ — مدیریت ساختار و کاربران

**هدف:** تبدیل هسته Bot به سامانه کامل مدیریت سازمان، مدرسه، ساختار آموزشی، کاربران و دسترسی‌ها.

### دامنه توسعه

1. **Tenant و Organization**
   - ایجاد، فهرست، جزئیات، ویرایش، تعلیق، آرشیو و بازیابی کنترل‌شده.
   - تنظیم timezone، locale، تقویم و سقف منابع.
   - درخت ساختاری Platform → Tenant → Organization → School.

2. **مدرسه و ساختار آموزشی**
   - چرخه کامل School.
   - Academic Year، وضعیت و بازه زمانی.
   - پایه، رشته، شعبه و Class Group.
   - Subject، گروه درسی، دبیر و ارتباط‌های ساختاری.
   - ایجاد، فهرست، جست‌وجو، جزئیات، ویرایش، آرشیو و تغییر وضعیت.
   - کنترل تداخل، یکتایی، تاریخ‌ها و مالکیت Tenant.
   - آماده‌سازی Import گروهی برای فاز ۴.

3. **مدیریت کاربران**
   - User Directory، جست‌وجو و فیلتر.
   - اتصال امن Telegram Account به User.
   - وضعیت حساب، تعلیق و فعال‌سازی.
   - مشاهده تاریخچه و ارتباط‌های کاربر.
   - مدیریت نشست/اتصال و قطع دسترسی.

4. **Membership و دعوت**
   - Invitation lifecycle.
   - عضویت با وضعیت و بازه اثر.
   - پذیرش، رد، لغو، تعلیق، پایان و تمدید عضویت.
   - جلوگیری از عضویت تکراری یا متداخل.
   - تغییر عضویت با Audit و تاریخچه.

5. **مدیریت نقش**
   - نقش‌های سیستمی محافظت‌شده.
   - ساخت، ویرایش و آرشیو Roleهای سفارشی.
   - Permission registry و Role Binding در Scope معتبر.
   - نمایش علت Allow/Deny و دامنه دسترسی.
   - جلوگیری از Escalation و واگذاری نقش بالاتر از اختیار مدیر.

### تجربه Bot

- فرم‌های چندمرحله‌ای برای ساخت/ویرایش.
- Preview قبل از Mutationهای مهم.
- Search و Pagination برای فهرست‌های بزرگ.
- Back و Cancel در تمام Wizardها.
- پیام خطای دقیق در همان مرحله.
- عدم نمایش گزینه‌ای که مجوز کاربر اجازه آن را نمی‌دهد.

### Gate خروج فاز ۲

- [ ] CRUD و چرخه وضعیت موجودیت‌های تعریف‌شده کامل است.
- [ ] هر عملیات در Bot قابل دسترسی و تست است.
- [ ] هیچ داده بین Tenantها نشت نمی‌کند.
- [ ] روابط School/Year/Class/Subject در DB و Domain سازگارند.
- [ ] عضویت و Role Binding تاریخ‌دار و Audit‌شده‌اند.
- [ ] Invite یک‌بارمصرف و قابل ابطال است.
- [ ] جست‌وجو و Pagination بدون بارگذاری بی‌حد کار می‌کنند.
- [ ] دسترسی‌ها با ماتریس نقش/Scope تست شده‌اند.

**خروجی تحویلی:** مدیریت کامل ساختار مدرسه و چرخه کاربران از Bot.

---

# فاز ۳ — محتوا و گردش‌کار

**هدف:** مدیریت چرخه عمر محتوای آموزشی و تصمیم‌گیری‌های چندمرحله‌ای با کنترل و تاریخچه.

### دامنه توسعه

1. **Content Management**
   - انواع محتوا: تکلیف، آزمون، اطلاعیه، گزارش، نظرسنجی و محتوای آموزشی.
   - Draft، Revision، Review، Approved، Scheduled، Published، Rejected و Archived.
   - ویرایش نسخه‌ای، مقایسه Revision و تاریخچه.
   - پیوست فایل با محدودیت نوع/حجم و Storage امن.
   - Preview محتوای نهایی در Bot پیش از انتشار.

2. **Workflow Engine**
   - Workflow Definition و Versioning.
   - Workflow Instance و Task.
   - تعریف مرحله‌ها، مهلت، مسئول و شرط انتقال.
   - State Machine با Transitionهای معتبر.
   - تخصیص و واگذاری مسئولیت با Audit.

3. **Approval Center**
   - Inbox واحد درخواست‌های در انتظار.
   - Approve، Reject، Request Changes، Delegate و Abstain.
   - یک‌مرحله‌ای و چندمرحله‌ای.
   - Quorum/تأیید اکثریت برای سیاست‌های منتخب.
   - جلوگیری از تصویب درخواست حساس توسط ایجادکننده.
   - قفل تصمیم هم‌زمان و جلوگیری از Double Decision.
   - دلیل اجباری در رد یا درخواست اصلاح.

4. **Scheduling و انتشار**
   - زمان‌بندی انتشار و انقضا.
   - Quiet Hours و محدودیت‌های مدرسه.
   - وضعیت انتشار و نتیجه واقعی.
   - اعلان پس از انتشار از طریق صف فاز ۴.

5. **Rollback منطقی**
   - حذف تاریخچه ممنوع.
   - Compensating Action برای بازگرداندن اثر.
   - ثبت before/after، actor، دلیل و policy version.
   - نمایش اثر rollback پیش از تأیید.

### Gate خروج فاز ۳

- [ ] State Machine تمام Transitionهای مجاز و غیرمجاز را کنترل می‌کند.
- [ ] هر Revision حفظ و قابل بازیابی است.
- [ ] هیچ محتوای تأییدنشده طبق Policy منتشر نمی‌شود.
- [ ] تأییدکننده مستقل برای عملیات چهارچشمی اعمال می‌شود.
- [ ] تصمیم‌های هم‌زمان و Replay مسدود می‌شوند.
- [ ] تمام تصمیم‌ها Actor، زمان، دلیل و Policy Version دارند.
- [ ] انتشار زمان‌بندی‌شده و خطای آن قابل مشاهده و قابل Retry کنترل‌شده است.
- [ ] Rollback تاریخچه را حذف نمی‌کند و اثر معکوس قابل Audit است.

**خروجی تحویلی:** چرخه کامل ایجاد، بازبینی، تصویب، زمان‌بندی، انتشار و جبران محتوا.

---

# فاز ۴ — ارتباطات و خودکارسازی

**هدف:** اتصال امن ClassMate Work به کانال‌های ارتباطی و سیستم آموزشی و اجرای عملیات تکراری/گروهی با قابلیت پیگیری.

### دامنه توسعه

1. **Telegram Communication Connections**
   - اتصال امن گروه/کانال به School/Class با Handshake.
   - تأیید دوطرفه مالکیت و مجوز.
   - نمایش وضعیت Botهای ارتباطی، Permissionها و خطاها.
   - کد دعوت محدود و قابل ابطال.
   - قطع و اتصال مجدد امن.

2. **Notification Center**
   - Message Template با Versioning.
   - Preview پیام.
   - صف ارسال و Delivery Receipt.
   - Retry با Backoff، Timeout و Dead Letter.
   - Rate Limit، Quiet Hours و منع ارسال تکراری.
   - گزارش تحویل و شکست ارسال.

3. **Integration با classmate2**
   - قرارداد Versioned API/Event.
   - Machine-to-Machine Authentication با Scope/Audience محدود.
   - Service Credential، Rotation و Revocation.
   - Outbox Pattern و مصرف Idempotent.
   - Correlation ID و Replay Protection.
   - Sync وضعیت عضویت، کلاس و محتوا طبق قرارداد مصوب.
   - هیچ دسترسی مستقیم به دیتابیس یا کد داخلی classmate2 ایجاد نمی‌شود.

4. **Job Center و Automation**
   - Jobهای زمان‌بندی‌شده، رویدادمحور و تکرارشونده.
   - Retry، Backoff، Timeout، Lease و Dead Letter.
   - Job ID و وضعیت قابل مشاهده در Bot.
   - Cancel/Retry کنترل‌شده و محدود به مجوز.
   - Worker مستقل فقط در صورت نیاز واقعی، با معیار ظرفیت و صف durable.

5. **Bulk Operations**
   - Import CSV/Excel با Preview.
   - اعتبارسنجی هر ردیف و نمایش خطاهای قابل اصلاح.
   - تأیید عملیات گروهی و تأیید دوم برای موارد حساس.
   - پردازش Chunked و ثبت نتیجه هر ردیف.
   - Retry فقط برای ردیف‌های شکست‌خورده.
   - Export به‌صورت Job با کنترل دسترسی، Masking و Audit.

### Gate خروج فاز ۴

- [ ] اتصال کانال بدون اثبات مالکیت/مجوز ممکن نیست.
- [ ] Secretهای Integration رمزگذاری/محدود و قابل Rotation هستند.
- [ ] پیام تکراری یا Job تکراری اثر دوباره ندارد.
- [ ] Outbox و Sync در شکست سرویس بیرونی قابل بازیابی‌اند.
- [ ] هیچ API به‌وسیله Telegram ID دلخواه هویت مدیر جعل نمی‌کند.
- [ ] Import قبل از اجرا Preview و اعتبارسنجی دارد.
- [ ] Bulk Job نتیجه ردیفی، Retry محدود و Audit دارد.
- [ ] وضعیت Notification/Job در Bot قابل مشاهده است.
- [ ] اتصال classmate2 با تست Contract و عدم نشت Tenant تأیید شده است.

**خروجی تحویلی:** ارتباطات، Sync و عملیات خودکار/گروهی قابل مشاهده و مقاوم در برابر شکست.

---

# فاز ۵ — گزارش، امنیت و تحویل نهایی

**هدف:** تبدیل مجموعه قابلیت‌ها به سامانه عملیاتی قابل اتکا، قابل نظارت، قابل بازیابی و آماده استفاده واقعی.

### دامنه توسعه

1. **Command Center و Dashboard**
   - نمای نقش‌محور در Bot.
   - آمار واقعی Tenant، School، User، Class، Workflow، Approval، Job و Notification.
   - Drill-down از شاخص به فهرست و جزئیات.
   - نمایش منبع و زمان به‌روزرسانی هر شاخص.
   - عدم استفاده از عدد ساختگی.

2. **Reporting و Export Governance**
   - گزارش کاربران، عضویت، کلاس، محتوا، تأیید، اعلان و Job.
   - KPI زمان تأیید، خطا، Backlog، Delivery Rate و SLA.
   - Snapshot برای گزارش تاریخی.
   - فیلتر، Pagination و خروجی Job-based.
   - Masking و مجوز ردیفی/دامنه‌ای.

3. **Audit Explorer و Security Center**
   - جست‌وجوی Audit با فیلتر Actor، Scope، زمان، نوع عملیات و Correlation.
   - Read Audit برای دسترسی‌های حساس.
   - مدیریت نشست‌ها و لغو دسترسی‌ها.
   - Security Incident با Severity، Owner، Timeline، Containment و Resolution.
   - کشف رفتار غیرعادی و هشدارهای عملیاتی.
   - Policy Simulator: نمایش علت Allow/Deny.
   - Hash chaining و کنترل یکپارچگی Audit در سخت‌سازی نهایی.

4. **Settings و Feature Flags**
   - تنظیمات Platform، Tenant، Organization، School و Class.
   - Inheritance شفاف و منبع مقدار مؤثر.
   - Feature Flag با Rollout و Allowlist.
   - تغییر حساس با Preview، Approval و زمان اثر.
   - عدم افشای Secret در Bot، API یا Log.

5. **Reliability و Production Hardening**
   - Structured Logging، Metrics و Correlation End-to-End.
   - Rate Limit و کنترل Abuse.
   - Health/Readiness تفکیک‌شده برای API و Bot.
   - تست بار، Timeout، Retry و رفتار Restart.
   - Backup رمزنگاری‌شده و Restore آزمایش‌شده.
   - سیاست Retention، Archive و Legal Hold.
   - Runbook برای Incident، Rollback، Rotation و Recovery.
   - Deployment کنترل‌شده در Railway و بررسی نهایی متغیرها/Secrets.
   - محدود کردن Bot Poller به یک Replica و تأیید سلامت بعد از Deploy.

6. **تحویل نهایی**
   - راهنمای مدیران و Operatorها از داخل Bot.
   - Runbook فنی و نقشه بازیابی.
   - مستند API/Event و مدل داده.
   - CI نهایی و نتایج تست.
   - گزارش امنیت و Tenant Isolation.
   - تأیید سلامت Deploy و Backup/Restore.
   - فهرست Known Limitations و نسخه Release.

### Gate خروج فاز ۵ — تحویل نهایی

- [ ] تمامی منوها فقط قابلیت‌های پیاده‌سازی‌شده را نشان می‌دهند.
- [ ] تمام مسیرهای حیاتی Bot از ابتدا تا انتها تست شده‌اند.
- [ ] تست‌های امنیتی منفی و Tenant Isolation سبز هستند.
- [ ] هیچ Secret یا داده حساس در Git، Log یا پیام Bot افشا نمی‌شود.
- [ ] Dashboard و Report فقط داده واقعی و دارای منبع دارند.
- [ ] Audit، Incident و Revocation قابل استفاده‌اند.
- [ ] Backup و Restore واقعی آزمایش شده‌اند.
- [ ] Deploy Railway در وضعیت سالم و قابل مشاهده است.
- [ ] راهنمای بهره‌برداری و بازیابی تحویل شده است.
- [ ] محدودیت‌ها و بدهی فنی باقیمانده مستند شده‌اند.
- [ ] هیچ تغییر ناخواسته‌ای در classmate2 رخ نداده است.

**خروجی تحویلی:** نسخه عملیاتی ClassMate Work با ربات تلگرام به‌عنوان تنها رابط انسانی مدیریت، همراه با مستندات و Runbook.

---

## 3. قواعد ثابت تحویل در تمام فازها

هر قابلیت، بدون استثنا، باید این مسیر را کامل کند:

1. منوی Bot و محل دسترسی آن.
2. Handler و ورودی‌های معتبر.
3. Application Use Case مستقل.
4. Permission، Scope و Tenant Isolation.
5. Transaction و قواعد داده.
6. Audit/Event و Correlation.
7. خطا، Back، Cancel، Timeout و Idempotency.
8. تست Unit، Integration و در صورت نیاز End-to-End.
9. مستندات کاربری و فنی.
10. CI موفق و بازبینی تغییرات.

### استاندارد کیفیت

- هیچ دکمه بی‌عملکرد یا منوی نمایشی باقی نمی‌ماند.
- هیچ Handler مستقیماً به ORM یا منطق دامنه متصل نیست.
- هیچ مسیر حساس بدون Preview/Confirmation/Approval مناسب اجرا نمی‌شود.
- هیچ موفقیتی پیش از Commit یا تأیید سرویس نمایش داده نمی‌شود.
- حذف فیزیکی و از بین بردن Audit ممنوع است.
- Tenant ID و Callback Data مبنای Authorization نیستند.
- تمام زمان‌ها UTC ذخیره و مطابق timezone هر Tenant نمایش داده می‌شوند.
- Secretها فقط در Environment امن نگهداری می‌شوند.
- سرویس HTTP مستقل، جایگزین یا پیش‌نیاز Bot نیست.
- هیچ قابلیت ناقص برای پر کردن منو به فاز بعد منتقل‌شده و «تحویل‌شده» اعلام نمی‌شود.

## 4. ترتیب و کنترل تغییرات

- **فاز جاری: ۱.** تمام کارهای بعدی باید به Gate فاز ۱ برگردند.
- PR2 فعلی Draft می‌ماند و تا رفع موانع Telegram Identity، Bot، Scope و Data Integrity ادغام نمی‌شود.
- هر فاز پس از Gate خود به شاخه اصلی ادغام می‌شود و فاز بعد از آخرین وضعیت تأییدشده شروع خواهد شد.
- تغییر دامنه یا حذف یک قابلیت فقط با ثبت دلیل و به‌روزرسانی این نقشه مجاز است.
- تعداد فازها ثابت است؛ ریزکارها و Milestoneهای داخلی باعث افزایش تعداد فازهای اصلی نمی‌شوند.

---

## 5. تعریف «پروژه تحویل شد»

پروژه زمانی تحویل‌شده محسوب می‌شود که هر پنج Gate بسته شده باشد، Bot در Railway سالم اجرا شود، عملیات مدیریت واقعی از تلگرام انجام شود، Tenant Isolation و Audit با تست اثبات شوند، Integrationهای مصوب کار کنند و Backup/Restore و Runbook عملیاتی موجود باشد.

**سبز بودن CI، وجود منو یا تکمیل مستندات به‌تنهایی تحویل پروژه نیست.**
