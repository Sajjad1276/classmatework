# طراحی مرجع سامانه مدیریت ClassMate Work

**وضعیت سند:** پیشنهادی برای تأیید پیش از کدنویسی  
**نسخه:** 0.1  
**دامنه:** مدیریت کامل سامانه ClassMate، مستقل از `classmate2`  
**زبان محصول:** فارسی، با آمادگی کامل برای چندزبانه‌شدن

---

## 1. تصمیم کلیدی محصول

`classmate2` فقط لایه‌ی کاربری آموزشی و دانش‌آموزی است. تمام قابلیت‌های مدیریتی در `classmatework` پیاده‌سازی می‌شوند و از طریق API، رویداد و سرویس‌های مشترک با بخش آموزشی ارتباط می‌گیرند.

`classmatework` یک پنل مدیریتی ساده نیست؛ یک **سیستم عامل عملیاتی برای مدیریت مدرسه، کلاس، کاربران، محتوای آموزشی، گردش‌کارها، ارتباطات، امنیت و تحلیل سامانه** است.

اصل طراحی:

> هر عملیات مدیریتی باید قابل مشاهده، قابل کنترل، قابل بازگشت، قابل ممیزی و محدود به دامنه‌ی مجاز کاربر باشد.

---

## 2. اهداف محصول

### اهداف اصلی

1. مدیریت چندمستاجری مدارس، سازمان‌ها و کلاس‌ها در یک سامانه.
2. تعریف نقش‌های قابل تنظیم و سیاست‌های دسترسی دقیق، نه فقط چند نقش ثابت.
3. مدیریت چرخه‌ی عمر کاربر، عضویت، کلاس، درس، محتوا و ارتباطات.
4. جایگزین‌کردن عملیات حساس مستقیم با گردش‌کار، تأیید چندمرحله‌ای و ثبت audit.
5. ارائه‌ی داشبورد عملیاتی برای مدیر سامانه، سازمان، مدرسه و کلاس.
6. ایجاد یک مرکز کنترل برای اعلان‌ها، ربات تلگرام، گزارش‌ها و خطاها.
7. آماده‌بودن برای مقیاس بالا، چندمنطقه‌ای، چندزبانه و اتصال به سرویس‌های دیگر.

### موارد خارج از فاز نخست

- حسابداری کامل مدرسه و شهریه.
- حقوق و دستمزد.
- LMS کامل با تولید محتوای ویدئویی سنگین.
- هوش مصنوعی تصمیم‌گیرنده‌ی مستقل در عملیات حساس.
- حذف فیزیکی داده‌های عملیاتی بدون فرایند نگهداری و آرشیو.

---

## 3. ذی‌نفعان و نقش‌های پایه

نقش‌ها باید قابل ترکیب باشند؛ یک نفر می‌تواند در یک سازمان مدیر و در یک کلاس دبیر باشد.

| نقش | دامنه‌ی معمول | توانایی اصلی |
|---|---|---|
| مالک پلتفرم | کل سامانه | تنظیمات سراسری، سازمان‌ها، ظرفیت‌ها و سیاست‌های مادر |
| مدیر عملیات پلتفرم | کل سامانه | اجرای عملیات روزانه بدون دسترسی غیرضروری به اسرار |
| ناظر امنیت | کل سامانه | رخدادهای امنیتی، نشست‌ها، سیاست‌ها و پاسخ به حادثه |
| مدیر سازمان | یک سازمان | مدارس، کاربران سازمانی، سیاست‌های سازمان و گزارش تجمیعی |
| مدیر مدرسه | یک مدرسه | سال تحصیلی، کلاس‌ها، دروس، اعضا و گردش‌کارهای مدرسه |
| معاون/اپراتور مدرسه | مدرسه یا بخش مشخص | اجرای عملیات مجاز روزانه |
| مدیر کلاس | یک یا چند کلاس | اعضا، محتوای کلاس، اعلان و صف عملیات کلاس |
| دبیر | کلاس/درس | تولید محتوا، ارزیابی و بازخورد طبق سیاست مدرسه |
| ناظر/تأییدکننده | دامنه‌ی مشخص | بررسی و تصویب عملیات حساس |
| کارشناس پشتیبانی | دامنه‌ی پشتیبانی | مشاهده‌ی محدود، تیکت و رفع اشکال |
| حساب سرویس | یکپارچه‌سازی | دسترسی ماشینی محدود و قابل چرخش |
| دانش‌آموز/والد | مصرف آموزشی | در `classmate2`؛ در این ریپو فقط برای مدیریت هویت و عضویت دیده می‌شود |

### مدل دسترسی

دسترسی نهایی از ترکیب این موارد محاسبه می‌شود:

```text
تصمیم = هویت کاربر
       + نقش‌ها
       + دامنه‌ی سازمانی
       + سیاست منبع
       + وضعیت گردش‌کار
       + شرایط زمانی/امنیتی
       + محدودیت محیط و IP
```

RBAC پایه با ABAC تکمیل می‌شود. برای عملیات حساس، اصل **چهارچشمی** و تفکیک وظایف اعمال می‌شود.

---

## 4. ماژول‌های سامانه

### 4.1 مرکز فرمان و داشبورد

- داشبورد متفاوت برای هر نقش و دامنه.
- کارت‌های وضعیت: کاربران، کلاس‌ها، صف تأیید، خطاها، اعلان‌های ناموفق، سلامت ربات و ظرفیت.
- فیلتر زمانی، سازمانی و مدرسه‌ای.
- ذخیره‌ی viewهای شخصی.
- drill-down از شاخص به فهرست و سپس به رخداد/منبع.
- داشبورد بدون داده‌ی جعلی؛ هر عدد باید منبع و زمان محاسبه داشته باشد.

### 4.2 مدیریت مستاجر و سازمان

- ایجاد، تعلیق، آرشیو و بازیابی سازمان.
- تنظیم منطقه زمانی، تقویم آموزشی، زبان، سیاست نگهداری داده و سقف منابع.
- نمای درختی: پلتفرم ← سازمان ← مدرسه ← سال تحصیلی ← کلاس ← درس.
- جداسازی کامل داده‌ی مستاجران در query، cache، فایل و رویداد.

### 4.3 مدیریت مدرسه و ساختار آموزشی

- سال تحصیلی و دوره‌ها.
- پایه، رشته، شعبه، کلاس و گروه‌های درسی.
- درس، معلم، اتاق، برنامه هفتگی و تقویم تعطیلات.
- انتقال دانش‌آموز و دبیر با تاریخ اثر.
- import گروهی با CSV/Excel به‌همراه preview و گزارش خطا.
- عملیات انبوه با صف job و امکان توقف/ادامه.

### 4.4 مدیریت هویت، کاربران و عضویت

- فهرست کاربران با جست‌وجوی سریع و فیلتر دامنه.
- دعوت، فعال‌سازی، تعلیق، ادغام حساب‌های تکراری و بازنشانی نشست‌ها.
- اتصال Telegram ID به حساب با فرایند احراز هویت.
- عضویت دارای تاریخ شروع، پایان، وضعیت و منبع تصمیم.
- تغییر نقش بدون ازبین‌رفتن تاریخچه.
- دسترسی پشتیبانی به داده‌های حساس فقط با دلیل، ticket و مدت محدود.

### 4.5 سازنده نقش و سیاست

- نقش‌های سیستمی غیرقابل حذف.
- نقش‌های سفارشی با مجموعه permission.
- permission با قالب `resource:action`، مانند:
  - `school:read`
  - `class:manage_members`
  - `content:publish`
  - `approval:decide`
  - `audit:read`
- شرط دامنه، زمان، IP، MFA و مالکیت منبع.
- deny صریح بر allow اولویت دارد.
- شبیه‌ساز دسترسی: «اگر این کاربر این عملیات را انجام دهد، چرا مجاز/غیرمجاز است؟»
- نسخه‌بندی policy و ثبت تغییرات آن.

### 4.6 مدیریت محتوا و گردش‌کار انتشار

- draft، review، revision، approved، scheduled، published، archived، rejected.
- قالب محتوا برای تکلیف، امتحان، گزارش، اطلاعیه، نظرسنجی و محتوای آموزشی.
- گردش‌کار قابل تنظیم در سطح سازمان/مدرسه/کلاس.
- تأیید یک‌مرحله‌ای یا چندمرحله‌ای.
- جلوگیری از تأیید خودکار توسط ایجادکننده در سیاست‌های حساس.
- زمان‌بندی انتشار و انقضا.
- نسخه‌ها، مقایسه‌ی تغییرات و rollback منطقی.
- ثبت علت رد، درخواست اصلاح و deadline بررسی.

### 4.7 مرکز تأیید و صف عملیات

- صف واحد برای تمام عملیات نیازمند تصمیم.
- اولویت، SLA، تخصیص به اپراتور و escalation.
- قفل منطقی برای جلوگیری از تصمیم هم‌زمان.
- تصمیم approve، reject، request-change، delegate و abstain.
- ثبت نظر، فایل و دلیل تصمیم.
- پشتیبانی از quorum و تأیید اکثریت برای عملیات حساس.
- هیچ تصمیمی بدون actor، زمان، policy version و snapshot قابل ثبت نیست.

### 4.8 مدیریت اتصال Telegram و کانال‌های ارتباطی

- اتصال امن گروه/کانال به کلاس با handshake و تأیید دوطرفه.
- نمایش وضعیت bot، webhook/polling، مجوزها و آخرین خطا.
- تولید و ابطال کدهای دعوت با عمر محدود و scope مشخص.
- مرکز template پیام و نسخه‌بندی متن‌ها.
- صف ارسال، retry، dead-letter، idempotency و گزارش delivery.
- پیش‌نمایش پیام قبل از ارسال عمومی.
- rate limit و quiet hours.

### 4.9 گزارش‌گیری و تحلیل

- گزارش کاربران و عضویت.
- گزارش فعالیت کلاس، محتوا، صف تأیید و اعلان.
- KPIهای عملیاتی: زمان متوسط تأیید، نرخ خطا، backlog، delivery rate و SLA.
- فیلتر و export با job غیرهمزمان.
- snapshot گزارش‌ها برای بازتولید عدد تاریخی.
- دسترسی row-level و masking برای داده‌ی حساس.

### 4.10 audit، امنیت و مرکز حادثه

- audit append-only برای ورود، تغییر نقش، مشاهده داده حساس، export، تصمیم و عملیات انبوه.
- correlation ID برای اتصال request، job، event و پیام Telegram.
- تشخیص رفتار غیرعادی: login ناموفق، export بزرگ، تغییر نقش پیاپی، دسترسی خارج از الگو.
- مرکز حادثه با severity، owner، timeline، containment و resolution.
- session management، MFA، device list، revoke all sessions.
- نگهداری زنجیره‌ای hash برای تشخیص دست‌کاری audit.

### 4.11 تنظیمات و feature flag

- تنظیمات در سطح پلتفرم، سازمان، مدرسه و کلاس.
- inheritance شفاف با نمایش منبع مقدار نهایی.
- feature flag با rollout درصدی و allowlist.
- تغییرات حساس نیازمند تأیید و زمان اثر.
- تنظیمات secrets هرگز در UI یا log خام نمایش داده نشوند.

### 4.12 موتور job و automation

- jobهای زمان‌بندی‌شده، رویدادمحور و تکرارشونده.
- صف‌های جدا برای import، export، اعلان، گزارش و sync.
- retry با backoff، timeout، lease و dead-letter.
- idempotency key اجباری برای jobهای اثرگذار.
- replay امن event فقط در محیط کنترل‌شده.
- صفحه‌ی وضعیت job برای اپراتور.

---

## 5. گردش‌کارهای حیاتی

### 5.1 ایجاد مدرسه

```text
درخواست ایجاد
→ اعتبارسنجی نام/دامنه/سقف سازمان
→ بررسی policy
→ تأیید مدیر سازمان
→ ساخت مدرسه در وضعیت setup
→ تکمیل سال تحصیلی و تقویم
→ فعال‌سازی
→ ثبت event و اعلان
```

### 5.2 انتشار تکلیف یا امتحان

```text
Draft
→ بررسی کامل‌بودن و دسترسی ایجادکننده
→ ارسال به صف review
→ تخصیص reviewer
→ تصمیم approve/reject/request-change
→ زمان‌بندی یا انتشار فوری
→ ارسال اعلان
→ ثبت نتیجه delivery
```

### 5.3 اضافه‌کردن عضو به کلاس

```text
دعوت یا import
→ تطبیق هویت
→ اعتبارسنجی ظرفیت و تداخل عضویت
→ تأیید policy مدرسه
→ عضویت با تاریخ اثر
→ sync با classmate2
→ audit و اعلان
```

### 5.4 عملیات انبوه

```text
تعریف عملیات و فایل/فیلتر
→ preview تعداد و نمونه تغییرات
→ تأیید دوم در عملیات حساس
→ اجرای chunked
→ ثبت موفق/ناموفق هر ردیف
→ امکان retry فقط ردیف‌های ناموفق
→ گزارش نهایی و audit
```

### 5.5 لغو یا rollback

Rollback به معنی حذف تاریخچه نیست. سیستم باید:

- تغییر معکوس تولید کند.
- وضعیت قبلی و جدید را ثبت کند.
- تأثیر روی اعلان‌ها و عضویت‌ها را نشان دهد.
- برای عملیات خطرناک تأیید دوم بخواهد.

---

## 6. معماری فنی پیشنهادی

### انتخاب پیشنهادی فاز اول

**Modular Monolith با مرزهای ماژولی سخت، API versioned و worker جدا.**

در شروع microservice کامل انتخاب نمی‌کنیم؛ چون پیچیدگی توزیع‌شده، consistency و عملیات را زودتر از نیاز وارد می‌کند. مرزهای ماژولی طوری طراحی می‌شوند که بعداً قابل استخراج به سرویس مستقل باشند.

### اجزا

```text
Admin Web UI
      │
API Gateway / BFF
      │
Application Modules
 ├─ Identity & Access
 ├─ Tenant & Academic Structure
 ├─ Membership
 ├─ Content & Workflow
 ├─ Approval
 ├─ Communication
 ├─ Reporting
 ├─ Audit & Security
 └─ Automation
      │
PostgreSQL ─ Redis ─ Object Storage ─ Event Bus/Outbox
                                      │
                             classmate2 / Telegram
```

### اصول کدنویسی

- domain logic در service/use-case، نه در route handler.
- command و query جدا، حتی داخل monolith.
- transaction boundary روشن.
- eventهای domain از طریق outbox منتشر شوند.
- هیچ handlerای مستقیماً جدول مستاجر دیگر را query نکند.
- policy check قبل از mutation و دوباره داخل transaction برای عملیات حساس.
- schema migration فقط forward و قابل rollback منطقی.

### فناوری پیشنهادی

- Backend: Python 3.12، FastAPI، SQLAlchemy 2، Alembic، Pydantic.
- Admin UI: React + TypeScript، با RTL و design system اختصاصی.
- Database: PostgreSQL.
- Cache/lock: Redis.
- فایل: S3-compatible object storage با signed URL.
- Job: Redis-backed queue در فاز اول؛ abstraction قابل انتقال به broker مستقل.
- Observability: structured logging، metrics، tracing و error tracking.
- قرارداد API: OpenAPI، versioned از `/api/v1`.

---

## 7. مدل داده‌ی اصلی

جداول/aggregateهای اصلی:

- `tenants`
- `organizations`
- `schools`
- `academic_years`
- `class_groups`
- `subjects`
- `users`
- `identities`
- `sessions`
- `memberships`
- `roles`
- `permissions`
- `role_bindings`
- `policies`
- `policy_versions`
- `content_items`
- `content_revisions`
- `workflow_definitions`
- `workflow_instances`
- `workflow_tasks`
- `approval_decisions`
- `invitations`
- `channel_connections`
- `message_templates`
- `notification_jobs`
- `job_runs`
- `exports`
- `audit_events`
- `security_incidents`
- `feature_flags`
- `integration_credentials`
- `outbox_events`

### قواعد داده

- تمام aggregateهای tenant-owned دارای `tenant_id` هستند.
- کلیدها UUID هستند؛ شناسه‌ی قابل نمایش جدا نگهداری می‌شود.
- timestampها UTC و timezone نمایش در تنظیمات tenant است.
- soft delete فقط برای داده‌هایی که نیاز به بازسازی دارند؛ برای audit append-only.
- unique constraintها با دامنه‌ی tenant ترکیب می‌شوند.
- status transitionها با جدول policy یا state machine کنترل می‌شوند، نه رشته‌های پراکنده.

---

## 8. API و event contract

### APIهای اصلی فاز اول

```text
POST   /api/v1/auth/login
POST   /api/v1/auth/mfa/verify
GET    /api/v1/me
GET    /api/v1/tenants/{tenant_id}/dashboard
GET    /api/v1/schools
POST   /api/v1/schools
GET    /api/v1/classes
POST   /api/v1/classes
GET    /api/v1/memberships
POST   /api/v1/invitations
GET    /api/v1/workflows/inbox
POST   /api/v1/workflows/{id}/decisions
GET    /api/v1/audit-events
GET    /api/v1/jobs/{id}
```

### eventهای پایه

```text
school.created
school.activated
class.created
membership.invited
membership.activated
content.submitted
workflow.task.assigned
approval.decided
content.published
notification.queued
notification.delivered
notification.failed
security.incident.opened
```

هر event شامل `event_id`, `event_type`, `occurred_at`, `actor_id`, `tenant_id`, `aggregate_type`, `aggregate_id`, `correlation_id`, `schema_version` و payload است.

---

## 9. امنیت و حریم خصوصی

- MFA برای نقش‌های حساس و عملیات پرخطر.
- session rotation، refresh token rotation و revoke فوری.
- CSRF protection، secure cookie، CSP، HSTS و rate limit.
- عدم اعتماد به tenant/user id ارسالی از client.
- authorization در backend و داخل object scope.
- رمزنگاری secretها و فایل‌های حساس.
- masking داده در log و export.
- تأیید مجدد هویت برای تغییر نقش، export و اتصال Telegram.
- audit برای read حساس، نه فقط write.
- سیاست retention قابل تنظیم و legal hold.
- backup رمزنگاری‌شده، تست restore و disaster recovery.
- threat model برای invitation، impersonation، bulk operation، webhook و file upload.

### impersonation پشتیبانی

Impersonation فقط با این شروط مجاز است:

1. permission جداگانه.
2. دلیل اجباری.
3. زمان انقضا.
4. banner واضح در UI.
5. ثبت تمام عملیات با actor واقعی و subject impersonated.
6. ممنوعیت export و تغییر credential در حالت impersonation.

---

## 10. طراحی رابط مدیریت

### ساختار اصلی

- نوار بالایی: tenant، مدرسه فعال، جست‌وجوی سراسری، اعلان، حساب.
- نوار کناری: داشبورد، ساختار آموزشی، کاربران، محتوا، صف من، ارتباطات، گزارش‌ها، امنیت، تنظیمات.
- breadcrumb در همه‌ی صفحات.
- صفحه‌ی detail با تب‌های Overview، Activity، Members، Workflow، Audit.
- جدول‌های بزرگ با فیلتر URL-persisted، ستون قابل تنظیم، bulk selection و export کنترل‌شده.
- رنگ وضعیت فقط همراه با متن و icon؛ دسترس‌پذیری WCAG AA.
- عملیات خطرناک در drawer/confirmation با نمایش impact واقعی.
- empty state عملیاتی، نه تصویر تزئینی.

### صفحات فاز اول

1. Login/MFA.
2. Dashboard.
3. Organization/School tree.
4. School setup wizard.
5. Class and subject management.
6. User directory.
7. Membership/invitation center.
8. Approval inbox.
9. Content workflow detail.
10. Telegram connection center.
11. Audit explorer.
12. Settings and policy viewer.

---

## 11. فازبندی اجرا

### فاز صفر — قرارداد و اسکلت

- تأیید همین سند.
- تعیین مالک محصول و نقش‌های واقعی.
- قرارداد API/event.
- ساختار monorepo، lint، test، migration و CI.
- طراحی design token و shell پنل.

**خروجی:** پروژه قابل اجرا با login آزمایشی، tenant context و health checks.

### فاز یک — هسته مدیریت

- هویت، session و MFA پایه.
- tenant/organization/school/year/class/subject.
- کاربران و membership.
- RBAC + scope.
- audit پایه.
- dashboard و جست‌وجوی سراسری.

**معیار عبور:** هیچ query مدیریتی بدون tenant و permission اجرا نشود.

### فاز دو — workflow و محتوا

- content revision.
- workflow definition/instance/task.
- approval inbox.
- انتشار، rollback منطقی و اعلان.
- اتصال event/outbox به `classmate2`.

### فاز سه — ارتباطات و عملیات

- Telegram connection center.
- invitation lifecycle.
- notification templates، retry و delivery report.
- job center و عملیات انبوه.

### فاز چهار — تحلیل و امنیت پیشرفته

- گزارش‌های تجمیعی.
- anomaly detection.
- security incident center.
- policy simulator.
- export governance و retention.

### فاز پنج — مقیاس و اکوسیستم

- plugin/integration SDK.
- webhooks امن.
- استخراج سرویس‌های پرمصرف در صورت نیاز واقعی.
- multi-region و disaster recovery پیشرفته.

---

## 12. معیارهای پذیرش کلان

- مدیر سازمان نمی‌تواند داده‌ی سازمان دیگر را بخواند، حتی با تغییر URL یا body.
- هر mutation حساس دارای actor، policy version، before/after و correlation ID است.
- هیچ محتوای نیازمند تأیید بدون workflow معتبر منتشر نمی‌شود.
- تصمیم تأییدکننده‌ی ایجادکننده در سیاست‌های چهارچشمی پذیرفته نمی‌شود.
- عملیات انبوه preview، progress، retry و report ردیفی دارد.
- قطع Telegram باعث ازبین‌رفتن mutation اصلی نمی‌شود؛ اعلان به outbox می‌رود.
- خطای worker باعث duplicate قابل مشاهده برای کاربر نمی‌شود؛ idempotency برقرار است.
- export بزرگ synchronous نیست و دارای مجوز، watermark، expiry و audit است.
- تغییر permission در زمان اجرا قابل ردیابی و قابل بازگشت است.
- restore backup به‌صورت دوره‌ای آزمایش و نتیجه ثبت می‌شود.

---

## 13. تصمیم‌هایی که قبل از کدنویسی باید تأیید شوند

1. آیا محصول فقط برای یک مالک/مجموعه است یا SaaS چندمستاجری واقعی؟
2. اولین کاربر پنل مدیر مدرسه است یا مدیر کل سامانه؟
3. احراز هویت مدیران با رمز، OTP، Google/Microsoft یا ترکیبی انجام می‌شود؟
4. آیا والدین در دامنه‌ی مدیریتی حضور دارند یا فقط در `classmate2`؟
5. کدام عملیات حتماً نیازمند تأیید دوم هستند؟
6. سیاست نگهداری داده و حذف/آرشیو برای مدارس چیست؟
7. اتصال رسمی Telegram با webhook خواهد بود یا polling هم لازم است؟
8. گزارش‌ها باید real-time باشند یا تأخیر چند دقیقه‌ای قابل قبول است؟
9. آیا پرداخت، اشتراک یا quota در همین محصول لازم است؟
10. زبان اول فقط فارسی است یا English از ابتدا باید در schema و UI پشتیبانی شود؟

تا قبل از پاسخ این موارد، کدنویسی featureهای اصلی شروع نمی‌شود؛ فقط می‌توان زیرساخت آزمایشی و قراردادهای تأییدشده را ساخت.

---

## 14. پیشنهاد تصمیم برای شروع

برای جلوگیری از پراکندگی، پیشنهاد اجرایی من این است:

- محصول را از ابتدا **چندمستاجری** طراحی کنیم.
- فاز اول را روی «هسته مدیریت مدرسه + هویت + دسترسی + audit» متمرکز کنیم.
- نقش‌ها را قابل ترکیب و scope-aware بسازیم.
- workflow و approval را از ابتدا به‌صورت ماژول مستقل تعریف کنیم، اما UI پیچیده‌ی quorum را به فاز دو ببریم.
- `classmate2` را فقط مصرف‌کننده‌ی event/API نگه داریم و منطق مدیریت را دوباره به آن برنگردانیم.
- قبل از هر feature، contract آن را با این پنج سؤال بررسی کنیم:
  1. مالک این داده کیست؟
  2. چه کسی می‌تواند بخواند؟
  3. چه کسی می‌تواند تغییر دهد؟
  4. آیا نیاز به تأیید یا rollback دارد؟
  5. audit و event آن چگونه ثبت می‌شود؟
