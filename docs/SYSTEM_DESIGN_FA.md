# طراحی مرجع ClassMate Work — نسخه ربات‌محور

**نسخه:** 1.0  
**وضعیت:** مبنای معماری برای ادامه توسعه  
**رابط مدیریتی انسان:** فقط ربات اختصاصی تلگرام  
**دامنه:** مخزن \`Sajjad1276/classmatework\`  
**مرز مستقل:** مخزن \`classmate2\` بدون تغییر

---

## 1. تصمیم محصول و خط قرمزها

ClassMate Work سامانه مرکزی مدیریت و عملیات ClassMate است؛ **سایت یا پنل وب مدیریتی نیست**. تمام تعاملات انسانی مدیران و اپراتورها با ClassMate Work از طریق یک ربات تلگرام اختصاصی انجام می‌شود.

این تصمیم در تمام لایه‌ها الزام‌آور است:

1. رابط اصلی مدیریت، ربات تلگرام است؛ نه React، نه وب‌اپ تلگرام و نه داشبورد مرورگری.
2. Backend مالک منطق، داده، سیاست دسترسی و اجرای عملیات است. ربات فقط رابط مکالمه‌ای و تبدیل‌کننده Update تلگرام به درخواست کاربردی است.
3. FastAPI برای Health Check و APIهای داخلی/یکپارچه‌سازی باقی می‌ماند؛ API به معنی وجود پنل وب نیست.
4. هیچ منطق دامنه‌ای نباید فقط داخل Telegram Handler یا FastAPI Route نوشته شود. هر دو باید Application Service/Use Case مشترک را صدا بزنند.
5. \`classmate2\` مالک رابط و منطق آموزشی/دانش‌آموزی خودش باقی می‌ماند. این مخزن فقط از طریق قرارداد API و Event با آن ارتباط می‌گیرد؛ کد، تنظیمات یا دیتابیس آن از این پروژه تغییر نمی‌کند.
6. قابلیت منویی که Use Case، مجوز، اعتبارسنجی، Audit و تست ندارد نباید به مدیر نمایش داده شود.
7. هیچ عدد، وضعیت، گزارش یا موفقیتی با داده ساختگی نمایش داده نمی‌شود.

**اصل طراحی:** هر عملیات مدیریتی باید قابل مشاهده، قابل کنترل، قابل پیگیری، قابل لغو یا جبران منطقی، قابل ممیزی و محدود به دامنه دسترسی کاربر باشد.

### موارد صریحاً خارج از محصول فعلی

- وب‌سایت مدیریتی، React، Vite، پنل مرورگری و Telegram Web App.
- احراز هویت مبتنی بر رمز عبور مرورگر، Cookie و CSRF برای مدیر انسانی.
- ثبت‌نام عمومی مدیران.
- اجرای عملیات مدیریتی از طریق پیام آزاد بدون منوی مشخص، اعتبارسنجی و مجوز.
- تغییر مستقیم مخزن یا زیرساخت \`classmate2\`.

---

## 2. معماری کلان

انتخاب اصلی **Modular Monolith با مرزهای ماژولی سخت و چند Adapter ورودی** است؛ در این مرحله Microserviceهای متعدد ساخته نمی‌شوند.

\`\`\`text
                   Telegram Bot API
                          │
                  Admin Bot Service
                 aiogram 3 / Long Polling
                          │
              Telegram Adapter / Middleware
        (private chat, sender, callback, FSM, rate limit)
                          │
                          ▼
                 Application Layer
       Use Cases / Commands / Queries / Validators
                          │
             Authorization + Tenant Scope
                          │
                          ▼
                 Domain / Repositories
                          │
             ┌────────────┴────────────┐
             ▼                         ▼
        PostgreSQL                  Redis
  (source of truth, audit,      (FSM, short-lived
   memberships, policies)       locks, rate limit)
             │
             ▼
       Outbox / Integration Events
             │
             ▼
        Internal FastAPI API
             │
             ▼
          classmate2
     (فقط از قرارداد رسمی)
\`\`\`

### 2.1 لایه رابط تلگرام

مسئولیت‌ها:

- دریافت Update از Telegram Bot API.
- تشخیص پیام، فرمان، Callback Query و ورودی مرحله‌ای.
- استخراج هویت از \`from.id\`؛ نه از username، نام نمایشی یا داده ارسالی در Callback.
- محدودکردن ربات مدیریتی به گفت‌وگوی خصوصی.
- نمایش منو، صفحه جزئیات، فهرست صفحه‌بندی‌شده و فرم‌های چندمرحله‌ای.
- تبدیل ورودی به درخواست معتبر و فراخوانی Use Case.
- نمایش نتیجه واقعی، خطای کنترل‌شده و شناسه پیگیری.

Handler مجاز به اجرای Query یا Mutation مستقیم روی ORM نیست.

### 2.2 لایه Application

هر قابلیت مدیریتی در این لایه یک یا چند Use Case مستقل دارد؛ نمونه:

- \`CreateTenant\`
- \`ListTenants\`
- \`CreateSchool\`
- \`CreateAcademicYear\`
- \`CreateClass\`
- \`InviteMember\`
- \`AssignRole\`
- \`SuspendMembership\`
- \`SubmitApprovalDecision\`
- \`ReadAuditEvents\`

Use Case باید ورودی نوع‌دار، اعتبارسنجی، کنترل مجوز، محدوده تراکنش، ثبت Audit و خروجی مشخص داشته باشد. هیچ منطق کسب‌وکار نباید در Callback Handler یا Route Handler تکثیر شود.

### 2.3 لایه API

FastAPI برای موارد زیر حفظ می‌شود:

- Liveness و Readiness.
- APIهای داخلی مورد نیاز یکپارچه‌سازی‌ها.
- قرارداد versioned برای ارتباط کنترل‌شده با \`classmate2\`.
- دریافت/انتشار Eventهای تعریف‌شده با احراز هویت ماشینی.

این API رابط مدیریتی انسانی نیست. Endpointهای داخلی باید احراز هویت سرویس‌به‌سرویس، scope محدود، اعتبارسنجی payload، rate limit و Audit مناسب داشته باشند. هیچ Endpoint مدیریتی نباید به دلیل قابل حدس بودن URL یا Telegram ID قابل فراخوانی عمومی باشد.

---

## 3. استقرار در Railway

**نیازی به ساخت Railway Project جدید نیست.** سرویس‌ها در همان Railway Project و همان Environment فعلی قرار می‌گیرند.

| سرویس | وظیفه | فرمان اجرا |
|---|---|---|
| \`classmatework-api\` | FastAPI، Health Check و API یکپارچه‌سازی | \`uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port $PORT\` |
| \`classmatework-admin-bot\` | دریافت Update و اجرای رابط مدیریتی تلگرام | \`python -m app.bot.main\` |
| PostgreSQL | منبع اصلی داده | سرویس دیتابیس موجود |
| Redis | FSM، قفل کوتاه‌مدت و Rate Limit | سرویس Redis موجود |

قواعد استقرار:

- API و ربات از همان Repository و کد مشترک استفاده می‌کنند؛ اما Start Command و چرخه عمرشان جدا است.
- هر دو سرویس به همان PostgreSQL و Redis متصل می‌شوند.
- توکن ربات فقط در متغیر محیطی سرویس ربات قرار می‌گیرد و وارد Git یا Log نمی‌شود.
- در شروع از Long Polling استفاده می‌شود؛ تعداد Replica سرویس ربات باید **یک** باشد. اجرای هم‌زمان چند Poller با یک Bot Token مجاز نیست.
- ربات هنگام Restart نباید Updateهای معلق را بی‌دلیل حذف کند.
- در این فاز Worker عمومی سومی اضافه نمی‌شود. API و Bot دو سرویس با دو وظیفه متفاوت‌اند، نه دو Worker تکراری.
- Worker مستقل فقط وقتی اضافه می‌شود که Jobهای سنگین و پایدار واقعاً ایجاد شده باشند و صف، Retry، Lease و Dead Letter آن طراحی شده باشد.
- هیچ متغیر یا سرویس Railway در این بازنگری تغییر داده یا Deploy نمی‌شود؛ این سند قرارداد معماری است.

برای جلوگیری از استقرار اشتباه، سرویس API فقط فرمان API و سرویس Bot فقط فرمان Bot را اجرا می‌کند. هر دو باید Build یکسان و نسخه کد سازگار داشته باشند.

---

## 4. طراحی تجربه کار با ربات

### 4.1 الگوی اصلی تعامل

رابط اصلی از **Inline Keyboard** استفاده می‌کند. منوها و صفحه‌ها با ویرایش همان پیام یا ارسال پیام کنترل‌شده جابه‌جا می‌شوند. Reply Keyboard دائمی و پنل وب در دامنه محصول نیستند.

فرمان‌های سراسری:

- \`/start\` — آغاز و کنترل دسترسی.
- \`/menu\` — بازگشت به منوی اصلی.
- \`/back\` — بازگشت دقیقاً یک مرحله.
- \`/cancel\` — لغو فرم یا عملیات در حال انجام.
- \`/help\` — راهنمای فرمان‌ها و الگوی کار.
- \`/status\` — وضعیت واقعی سرویس‌ها، فقط برای نقش مجاز.

### 4.2 قواعد قطعی دکمه‌ها و هندلرها

1. هر دکمه باید Handler متناظر و تست داشته باشد؛ دکمه بی‌عملکرد ممنوع است.
2. هر صفحه فرزند باید دکمه «بازگشت» داشته باشد؛ هر Wizard علاوه بر آن دکمه «لغو عملیات» دارد.
3. «بازگشت» فقط یک سطح در Navigation Stack حرکت می‌کند؛ «لغو» وضعیت فرم جاری را پاک می‌کند و عملیات را اجرا نمی‌کند.
4. \`/menu\` از هر مرحله قابل اجرا است و وضعیت جاری را طبق سیاست همان Flow می‌بندد.
5. پس از ورود متن نامعتبر، کاربر باید همان مرحله را ببیند و بداند چه چیزی اشتباه است؛ سیستم نباید بی‌دلیل به پیام قبلی یا ابتدای فرم برگردد.
6. هر Callback Query باید پاسخ داده شود؛ Callback منقضی یا تکراری نباید عملیات را دوباره اجرا کند.
7. Callback قدیمی پس از انقضا یا تغییر وضعیت باید پیام «این مرحله منقضی شده؛ از منو شروع کنید» بدهد.
8. هر مرحله از فرم باید ورودی مورد انتظار، محدوده معتبر، نمونه و مسیر بازگشت را روشن کند.
9. فهرست‌های بزرگ باید جست‌وجو، فیلتر و صفحه‌بندی داشته باشند؛ صدها دکمه در یک پیام ساخته نمی‌شود.
10. عملیات طولانی باید Job ID و وضعیت واقعی اجرا را نشان دهد؛ پیام «موفق» قبل از Commit یا تأیید سرویس ممنوع است.

### 4.3 ساختار منوی اصلی

منو در نهایت این بخش‌ها را پوشش می‌دهد؛ اما تا زمان پیاده‌سازی کامل هر بخش، فقط گزینه‌های عملیاتی و تست‌شده نمایش داده می‌شوند:

1. نمای کلی و وضعیت سامانه.
2. سازمان‌ها و مدارس.
3. ساختار آموزشی: سال تحصیلی، کلاس و درس.
4. کاربران، عضویت‌ها و دعوت‌ها.
5. نقش‌ها، مجوزها و سیاست دسترسی.
6. محتوا و گردش‌کار.
7. صف تأیید و عملیات در انتظار تصمیم.
8. ارتباطات و اتصال‌های Telegram.
9. گزارش‌ها و خروجی‌ها.
10. امنیت، نشست‌ها و ممیزی.
11. تنظیمات و Feature Flag.
12. Jobها و عملیات انبوه.

### 4.4 فرم‌های چندمرحله‌ای

فرم نمونه ایجاد مدرسه:

\`\`\`text
منوی اصلی
→ سازمان‌ها و مدارس
→ ایجاد مدرسه
→ انتخاب سازمان (در صورت نیاز)
→ ورود نام مدرسه
→ ورود کد مدرسه
→ نمایش Preview
→ تأیید نهایی
→ اجرای Use Case
→ نمایش نتیجه و شناسه
\`\`\`

قواعد فرم:

- هر مرحله state مشخص و ورودی اعتبارسنجی‌شده دارد.
- State فقط در حافظه Process نگهداری نمی‌شود؛ Redis با TTL و کلید مجزای actor/chat استفاده می‌شود.
- State باید شامل actor، chat، نوع عملیات، مرحله، داده لازم و زمان انقضا باشد.
- وضعیت گفت‌وگوی یک مدیر نباید با مدیر دیگر یا چت دیگر مخلوط شود.
- داده حساس در State یا Callback Data خام قرار نمی‌گیرد.
- برای داده‌ای که ارزش ماندگاری یا تأیید دارد، رکورد Pending Action در PostgreSQL ایجاد می‌شود؛ Redis جایگزین منبع حقیقت نیست.

### 4.5 Callback Data

- Callback Data نباید حاوی رمز، Token، متن محرمانه یا مجوز باشد.
- به دلیل محدودیت طول Callback Data در Telegram، فقط action code و شناسه کوتاه/opaque استفاده می‌شود.
- جزئیات عملیات در Backend نگهداری می‌شود و با هر کلیک مجوز و مالکیت دوباره بررسی می‌شود.
- کلیک دوباره روی Callback مصرف‌شده، نباید Mutation تکراری ایجاد کند.

---

## 5. هویت و مدل دسترسی

### 5.1 هویت Telegram

- شناسه اصلی اتصال مدیر به ربات، عدد \`Telegram User ID\` است.
- \`username\`، نام نمایشی و شماره تلفن فقط اطلاعات توصیفی‌اند و مبنای احراز هویت نیستند.
- کاربر فقط در Private Chat مجاز است؛ استفاده از ربات مدیریتی در گروه یا کانال رد می‌شود.
- پیام از Bot Account، حساب ناشناخته یا حساب مسدودشده پذیرفته نمی‌شود.
- ثبت‌نام آزاد وجود ندارد.
- اولین Platform Owner فقط از مسیر Bootstrap کنترل‌شده ایجاد می‌شود.
- افزودن مدیران دیگر فقط با دعوت/Pairing یک‌بارمصرف، محدود به زمان و قابل ممیزی انجام می‌شود.
- قطع اتصال Telegram، تعلیق User یا لغو نقش باید دسترسی را فوراً متوقف کند.

### 5.2 جداسازی هویت و عضویت

User هویت سراسری پلتفرم است؛ Telegram Account فقط روش ورود به رابط مدیریتی است. Membership مشخص می‌کند کاربر در کدام Tenant و با چه بازه زمانی عضو است. اتصال Telegram نباید جایگزین عضویت Tenant شود.

### 5.3 مجوزها

مدل دسترسی RBAC + ABAC است:

- نقش‌ها قابل ترکیب‌اند.
- هر Role Binding دارای Scope است: Platform، Tenant، Organization، School یا Class.
- نقش والد می‌تواند به Scopeهای فرزند ارث برسد، مگر اینکه Policy آن permission را محدود کرده باشد.
- Scope فرزند هرگز به والد یا هم‌سطح دیگر مجوز نمی‌دهد.
- عضویت فعال و بازه اعتبار زمانی برای دسترسی Tenant لازم است.
- Deny صریح بر Allow مقدم است.
- Platform Owner مجوز Platform دارد؛ این امتیاز از نقش Tenant یا School به‌صورت ضمنی ساخته نمی‌شود.
- Tenant انتخاب‌شده در ربات صرفاً Context است، نه مجوز.
- هر Use Case قبل از خواندن حساس یا Mutation، Policy را در Backend ارزیابی می‌کند؛ Callback Data و دکمه جایگزین Authorization نیستند.

نمونه permission:

- \`platform:tenants:read\`
- \`platform:tenants:create\`
- \`school:read\`
- \`school:create\`
- \`class:manage_members\`
- \`content:publish\`
- \`approval:decide\`
- \`audit:read\`
- \`security:revoke_sessions\`

### 5.4 عملیات حساس

عملیات با ریسک بالا باید Preview، دلیل، تأیید مجدد و Audit داشته باشند. در عملیات حساس‌تر، اصل چهارچشمی اعمال می‌شود: ایجادکننده مجاز به تأیید نهایی درخواست خودش نیست.

نمونه عملیات نیازمند کنترل ویژه:

- تغییر نقش Platform Owner یا نقش‌های امنیتی.
- تعلیق مدیر یا تغییر مالکیت.
- اتصال/قطع Integration و چرخش Secret.
- Export انبوه یا دسترسی به داده حساس.
- آرشیو مدرسه یا بستن Tenant.
- تغییر Policyهای سراسری و Feature Flagهای حساس.

حذف فیزیکی پیش‌فرض نیست؛ آرشیو، تعلیق یا عملیات جبرانی بر حذف مستقیم ترجیح دارد.

---

## 6. دامنه ماژول‌های کسب‌وکار

### 6.1 مدیریت Tenant و سازمان

- ایجاد، تعلیق، آرشیو و بازیابی کنترل‌شده.
- تنظیم locale، timezone، تقویم، سقف منابع و سیاست نگهداری.
- ساختار Platform ← Tenant ← Organization ← School ← Academic Year ← Class ← Subject.
- جداسازی Tenant در Database Query، Cache، Job، File، Event و Report.

### 6.2 مدرسه و ساختار آموزشی

- سال تحصیلی، دوره، پایه، رشته، شعبه، کلاس و گروه آموزشی.
- درس، دبیر، اتاق، برنامه هفتگی و تقویم تعطیلات.
- انتقال عضو با تاریخ اثر.
- ورود گروهی CSV/Excel با Preview و گزارش خطا.
- عملیات گروهی فقط با Preview و ثبت نتیجه هر ردیف.

### 6.3 کاربران و عضویت

- فهرست و جست‌وجوی کاربران.
- دعوت، پذیرش، فعال‌سازی، تعلیق و پایان عضویت.
- اتصال حساب Telegram به هویت از مسیر امن.
- تغییر نقش بدون حذف تاریخچه.
- پشتیبانی محدود و زمان‌دار با دلیل و Ticket.

### 6.4 نقش و سیاست

- Roleهای سیستمی محافظت‌شده و Roleهای سفارشی.
- Permission با قالب \`resource:action\`.
- Scope، زمان، وضعیت عضویت، شرط امنیتی و مالکیت منبع.
- نسخه‌بندی Policy و ثبت تغییر آن.
- شبیه‌ساز تصمیم دسترسی در فاز بعد.

### 6.5 محتوا و گردش‌کار

- وضعیت‌های Draft، Review، Revision، Approved، Scheduled، Published، Archived و Rejected.
- قالب تکلیف، آزمون، گزارش، اطلاعیه، نظرسنجی و محتوای آموزشی.
- تأیید یک یا چندمرحله‌ای.
- جلوگیری از تأیید خودکار سازنده در Policyهای حساس.
- نسخه‌بندی محتوا، مقایسه و Rollback منطقی.
- ثبت دلیل رد و مهلت اصلاح.

### 6.6 مرکز تأیید

- Inbox واحد درخواست‌های نیازمند تصمیم.
- اولویت، SLA، تخصیص، Escalation و قفل تصمیم هم‌زمان.
- Approve، Reject، Request Changes، Delegate و Abstain.
- ثبت actor، زمان، دلیل، Policy Version و Snapshot.
- پشتیبانی از Quorum در مرحله پیشرفته.

### 6.7 ارتباطات

- اتصال امن گروه/کانال به کلاس با Handshake و تأیید دوطرفه.
- نمایش سلامت Botهای ارتباطی و دسترسی‌های لازم.
- دعوت‌نامه‌های محدود، قابل ابطال و دارای Scope.
- Template پیام با نسخه‌بندی.
- Queue ارسال، Retry، Dead Letter، Idempotency و Delivery Report.
- Preview قبل از ارسال عمومی، Rate Limit و Quiet Hours.

**نکته:** ربات مدیریتی رابط کنترل سامانه است؛ قابلیت اتصال ربات‌های ارتباطی به گروه‌ها/کلاس‌ها یک ماژول کسب‌وکار جدا است. این دو مفهوم یکی نیستند.

### 6.8 گزارش و تحلیل

- گزارش کاربران، عضویت، کلاس، محتوا، تأیید و اعلان.
- KPI عملیاتی: زمان تأیید، نرخ خطا، Backlog، Delivery Rate و SLA.
- فیلتر و Export با Job.
- Snapshot برای بازتولید گزارش تاریخی.
- Masking و کنترل دسترسی در سطح ردیف.

### 6.9 Audit و امنیت

- Audit Append-only برای ورود، اتصال Telegram، تغییر Role، مشاهده داده حساس، Export، تصمیم و عملیات انبوه.
- Correlation ID مشترک بین Update، Use Case، API، Job و Event.
- ثبت علت رد دسترسی و رخدادهای امنیتی مهم.
- Session/Account revocation، Device/Account status و پاسخ به حادثه.
- زنجیره Hash برای کشف دست‌کاری Audit در مرحله سخت‌سازی.

### 6.10 تنظیمات و Automation

- تنظیمات Platform، Tenant، Organization، School و Class با Inheritance شفاف.
- Feature Flag و Rollout کنترل‌شده.
- Job زمان‌بندی‌شده، رویدادمحور و تکرارشونده.
- Retry با Backoff، Timeout، Lease و Dead Letter.
- Idempotency Key برای عملیات اثرگذار.
- Replay فقط در محیط کنترل‌شده.

---

## 7. مدل داده و قواعد سازگاری

موجودیت‌های اصلی در طول فازها:

- \`tenants\`, \`organizations\`, \`schools\`
- \`academic_years\`, \`class_groups\`, \`subjects\`
- \`users\`, \`telegram_accounts\`, \`admin_pairing_tokens\`
- \`memberships\`, \`roles\`, \`permissions\`, \`role_bindings\`
- \`policies\`, \`policy_versions\`
- \`content_items\`, \`content_revisions\`
- \`workflow_definitions\`, \`workflow_instances\`, \`workflow_tasks\`
- \`approval_decisions\`, \`invitations\`
- \`channel_connections\`, \`message_templates\`, \`notification_jobs\`
- \`job_runs\`, \`exports\`, \`audit_events\`
- \`security_incidents\`, \`feature_flags\`, \`integration_credentials\`
- \`outbox_events\`, \`processed_telegram_updates\`, \`pending_actions\`

قواعد:

- تمام Aggregateهای Tenant-owned دارای \`tenant_id\` هستند.
- شناسه‌های داخلی UUID و زمان‌ها UTC هستند.
- Telegram User ID با نوع عدد صحیح 64 بیتی ذخیره می‌شود.
- Unique Constraint و Foreign Key باید ترکیب Tenant/School/Academic Year را نیز محافظت کنند؛ صرف FK روی UUID به‌تنهایی جلوی اتصال رکوردهای دو Tenant را نمی‌گیرد.
- تغییر State باید از Transition معتبر عبور کند؛ رشته‌های آزاد و تغییر وضعیت مستقیم مجاز نیست.
- Audit append-only است و نباید از مسیر ORM یا SQL عادی Update/Delete شود.
- اطلاعات محرمانه و Token خام در Audit، Error، Callback و Log ذخیره نمی‌شود.

---

## 8. جریان‌های حیاتی

### 8.1 ایجاد Tenant

\`\`\`text
درخواست از منوی ربات
→ احراز هویت Telegram و Private Chat
→ استخراج Principal
→ ارزیابی platform:tenants:create
→ اعتبارسنجی نام، slug، timezone و سقف‌ها
→ Preview
→ تأیید کاربر
→ اجرای CreateTenant در Transaction
→ Audit و Outbox
→ نمایش شناسه و نتیجه واقعی
\`\`\`

### 8.2 ایجاد مدرسه

\`\`\`text
انتخاب Tenant مجاز
→ ارزیابی school:create در Scope همان Tenant
→ انتخاب Organization در صورت وجود
→ ورود نام و کد
→ بررسی یکتایی و مالکیت Organization
→ Preview
→ تأیید
→ CreateSchool
→ Audit و نمایش نتیجه
\`\`\`

### 8.3 افزودن عضو

\`\`\`text
دعوت یا Import
→ تطبیق User و Telegram Identity در صورت نیاز
→ بررسی ظرفیت، تداخل و تاریخ اثر
→ ارزیابی Policy و Scope
→ تأیید لازم
→ ایجاد Membership
→ ثبت Outbox برای Sync
→ Audit و گزارش نتیجه
\`\`\`

### 8.4 عملیات انبوه

\`\`\`text
تعریف فایل/فیلتر
→ اعتبارسنجی و Preview تعداد و نمونه
→ گزارش خطاهای پیش‌بینی‌شده
→ تأیید اول و در صورت نیاز تأیید دوم
→ اجرای Chunked با Idempotency
→ ثبت نتیجه هر ردیف
→ Retry فقط موارد شکست‌خورده
→ گزارش نهایی و Audit
\`\`\`

### 8.5 Rollback

Rollback به معنی حذف تاریخچه نیست. یک عملیات جبرانی جدید ساخته می‌شود؛ اثر قبلی، اثر معکوس، actor و دلیل هر دو باقی می‌مانند.

---

## 9. امنیت Telegram و اجرای Update

- Token ربات فقط Secret محیطی است؛ در Git، Screenshot، Exception یا Log ظاهر نمی‌شود.
- فقط Updateهای مجاز و از نوع Private Chat پردازش می‌شوند.
- شناسه کاربر از Update احراز‌شده تلگرام گرفته می‌شود؛ نه از متن یا Callback Data.
- دسترسی User، Telegram Account، Role Binding، Membership و Scope هنگام هر عملیات دوباره بررسی می‌شود.
- Flood Control بر اساس Telegram User ID و نوع عملیات اعمال می‌شود.
- Update ID و Pending Action برای جلوگیری از اجرای دوباره پردازش می‌شوند.
- Callback دارای TTL و مالک مشخص است؛ کاربر دیگری نمی‌تواند Callback شخص دیگر را مصرف کند.
- ورودی متنی پیش از نمایش با HTML/Markdown escape می‌شود.
- خطاهای داخلی، SQL، Stack Trace، Secret و داده خصوصی به کاربر نشان داده نمی‌شود.
- پیام‌های عمومی/گروهی برای مدیریت رد می‌شوند.
- Long Polling فقط با یک Replica فعال اجرا می‌شود؛ قفل استقرار/اجرای تکراری نیز باید بررسی شود.
- تغییر Role، لغو دسترسی و تعلیق User باید پیش از اجرای درخواست بعدی مؤثر شود.
- دسترسی‌های حساس در مرحله بعد با TOTP/Step-up و Approval چندنفره سخت‌سازی می‌شوند.

### تفاوت با احراز هویت وب

مدل Cookie، Session مرورگر و CSRF برای رابط مدیریتی این محصول حذف می‌شود. این به معنی حذف Authorization نیست؛ تمام مجوزهای دامنه‌ای در Backend باقی می‌مانند. ارتباط Bot Service با API نیز در صورت استفاده از HTTP باید Service Authentication مستقل و امن داشته باشد.

---

## 10. API و قرارداد Integration

API نسخه‌دار \`/api/v1\` حفظ می‌شود، اما کاربرد آن Integration و عملیات سیستمی کنترل‌شده است، نه رابط انسانی.

قواعد قرارداد:

- احراز هویت Machine-to-Machine مستقل از Telegram User Identity.
- Scope و Audience مشخص برای هر سرویس مصرف‌کننده.
- هر درخواست دارای Correlation ID و Idempotency Key متناسب با نوع عملیات.
- امضای Webhook، Timestamp و جلوگیری از Replay.
- Versioning در Schema و Event.
- هیچ Endpoint عمومی نباید با ارسال Telegram ID دلخواه، خود را به‌جای مدیر جا بزند.

Eventهای پایه:

- \`tenant.created\`
- \`school.created\`
- \`school.activated\`
- \`class.created\`
- \`membership.invited\`
- \`membership.activated\`
- \`content.submitted\`
- \`workflow.task.assigned\`
- \`approval.decided\`
- \`content.published\`
- \`notification.queued\`
- \`notification.delivered\`
- \`notification.failed\`
- \`security.incident.opened\`

Event باید شامل \`event_id\`, \`event_type\`, \`occurred_at\`, \`actor_id\`, \`tenant_id\`, \`aggregate_type\`, \`aggregate_id\`, \`correlation_id\`, \`schema_version\` و payload کنترل‌شده باشد.

---

## 11. مشاهده‌پذیری، پایداری و بازیابی

- Log ساختاریافته با حذف Token، PII غیرضروری و Payloadهای حساس.
- Correlation ID از Telegram Update تا Use Case، Audit، Outbox و API.
- متریک تعداد Update، خطای Handler، زمان پاسخ، Rate Limit، خطای Telegram API و Lag صف.
- Health Check جدا برای Process و وابستگی‌ها.
- Timeout و Retry محدود با Backoff؛ Retry نامحدود ممنوع.
- خطای موقت Telegram نباید باعث تکرار Mutation شود.
- Backup رمزنگاری‌شده، سیاست Retention و تست Restore.
- خطای سرویس بیرونی باید وضعیت قابل مشاهده داشته باشد؛ پیام موفقیت کاذب ممنوع است.

---

## 12. راهبرد تست و معیار پذیرش

### تست واحد

- Policy و ارث‌بری Scope.
- Deny-overrides-Allow.
- اعتبار تاریخ شروع/پایان Role و Membership.
- اعتبارسنجی Form و Transition.
- Idempotency و انقضای Pending Action.

### تست Integration

- PostgreSQL Migration روی نسخه واقعی PostgreSQL.
- جداسازی Tenant در خواندن و نوشتن.
- عدم پذیرش School/AcademicYear/Class متعلق به Tenant دیگر.
- Audit Append-only در سطح Database.
- ثبت Actor و Correlation ID در Audit.
- رفتار Use Case در صورت شکست Outbox یا DB.

### تست Bot

- کاربر ناشناس اجازه ورود نمی‌گیرد.
- حساب تعلیق‌شده یا Telegram اتصال‌لغوشده رد می‌شود.
- Group Chat رد می‌شود.
- Username تغییر کند اما هویت عددی ثابت بماند.
- Callback منقضی، تکراری یا متعلق به کاربر دیگر اثری ندارد.
- Back، Cancel، Menu و ورودی نامعتبر از هر مرحله رفتار قطعی دارند.
- Update تکراری Mutation تکراری ایجاد نمی‌کند.
- هیچ دکمه بدون Handler باقی نمی‌ماند.
- متن کاربر نمی‌تواند HTML/Markdown یا Callback را تزریق کند.

### CI

CI باید Backend lint/test، Bot lint/test، Migration و Integration Testهای PostgreSQL را اجرا کند. در معماری نهایی هیچ Frontend Build یا Node.js Build شرط انتشار نیست.

---

## 13. فازبندی اجرایی بازنگری‌شده

### فاز صفر — اصلاح مبنای معماری

- تبدیل سند مرجع به معماری Telegram-first.
- حذف Frontend وب و تست Build آن.
- تثبیت قرارداد Bot Adapter، API و Use Case.
- تعیین سیاست Telegram identity، private chat و polling.
- تعیین ساختار Railway Serviceها بدون ساخت Project جدید.

**معیار خروج:** هیچ سند، CI یا دستور اجرای اصلی پروژه وب‌محور نباشد.

### فاز یک — هویت، دسترسی و هسته ربات

- Telegram Account و Bootstrap امن Platform Owner.
- Private Chat Guard و Bot Middleware.
- منوی اصلی، Navigation Stack، Back/Cancel و Redis FSM.
- Use Caseهای Tenant، School، Academic Year، Class و Subject.
- RBAC/ABAC با Scope Hierarchy صحیح.
- Audit و جلوگیری از Duplicate Update.
- تست Integration و Bot.

**معیار خروج:** مدیر مجاز بتواند عملیات پایه را از ابتدا تا انتها در ربات انجام دهد و مدیر غیرمجاز نتواند با جعل Callback یا تغییر Tenant به داده دسترسی پیدا کند.

### فاز دو — مدیریت کامل کاربران و عضویت

- Invite/Pairing یک‌بارمصرف.
- چرخه کامل Membership و Role Binding.
- مدیریت حساب‌ها و قطع اتصال Telegram.
- جست‌وجو، صفحه‌بندی و گزارش‌های کاربران.

### فاز سه — Workflow و Approval

- Content، Revision، Task، Approval و Quorum.
- Preview، تأیید دوم، رد و Rollback منطقی.

### فاز چهار — اتصال‌ها و عملیات

- Telegram Communication Connections.
- Template، Notification Queue، Retry و Delivery Report.
- Outbox/Integration با \`classmate2\`.
- Import/Export و Jobهای قابل پیگیری.

### فاز پنج — گزارش، امنیت و Automation پیشرفته

- گزارش‌های عملیاتی و Export Governance.
- Security Incident، Policy Simulator و Anomaly Detection.
- Feature Flag، Retention، Backup/Restore و Hardening.

### فاز شش — مقیاس

- جداسازی سرویس فقط بر اساس بار واقعی.
- Worker مستقل فقط در صورت نیاز واقعی به پردازش غیرهمزمان.
- Multi-region و Disaster Recovery پیشرفته.

---

## 14. معیار نهایی جلوگیری از انحراف

پیش از افزودن هر قابلیت، این موارد باید پاسخ مستند داشته باشند:

1. این قابلیت در کدام منوی ربات دیده می‌شود؟
2. کدام Callback/Message Handler آن را به Use Case متصل می‌کند؟
3. ورودی، State، Back، Cancel، Timeout و خطای آن چیست؟
4. چه کسی و در چه Scopeی مجاز است؟
5. آیا Tenant Isolation در Backend و Database حفظ می‌شود؟
6. در صورت تکرار Update چه اتفاقی می‌افتد؟
7. آیا Preview، Confirmation یا Approval لازم است؟
8. چه Audit/Eventی ثبت می‌شود؟
9. چطور تست می‌شود؟
10. روی کدام Railway Service اجرا می‌شود و Secretهایش کجا نگهداری می‌شوند؟

**قاعده نهایی:** اگر قابلیت فقط در سند یا منو وجود دارد اما Use Case، مجوز، Audit، تست و نتیجه واقعی ندارد، پیاده‌سازی‌شده محسوب نمی‌شود.
