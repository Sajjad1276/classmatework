# فاز یک — هویت تلگرام و هسته مدیریت ClassMate Work

**شاخه:** \`phase-1/core-identity-access\`  
**وضعیت PR:** Draft، باز و ادغام‌نشده  
**وضعیت این پیاده‌سازی:** برش موقت؛ با مرجع Telegram-first هنوز هم‌راستا نیست.  
**معیار ادغام:** تکمیل موانع بخش پایانی این سند؛ سبز بودن CI به‌تنهایی کافی نیست.

## 1. تصمیم معماری

- رابط مدیریتی انسانی فقط ربات اختصاصی تلگرام است.
- FastAPI فقط API سلامت و Integration/Machine-to-Machine را ارائه می‌کند.
- منطق دامنه باید در Application Use Case باشد؛ Bot Handler و HTTP Route فقط Adapter هستند.
- هویت مدیر از Telegram User ID عددی و Pairing امن ساخته می‌شود؛ ایمیل/گذرواژه و Browser Cookie مدل نهایی نیست.
- PostgreSQL منبع اصلی و Redis محل FSM/TTL/Rate Limit است.
- \`classmate2\` مستقل است و این شاخه هیچ تغییری در آن ایجاد نمی‌کند.

## 2. آنچه در PR فعلی وجود دارد

- مدل‌های Tenant، Organization، School، AcademicYear، ClassGroup، Subject، User، Membership، Session، Role، Permission، RoleBinding و Audit.
- Argon2id Password Login، Cookie Session، CSRF و Rate Limit ورود مرورگری.
- ایجاد Tenant، School، Academic Year، Class و Subject از Routeهای FastAPI.
- Permission پایه با Deny-overrides-Allow و کنترل عضویت فعال Tenant.
- Migration اولیه و Audit Append-only در ORM و PostgreSQL.
- Bootstrap CLI با ایمیل/گذرواژه.
- تست‌های Policy، Password و Trigger دیتابیس.

این موارد به‌عنوان پیشرفت فنی قابل استفاده‌اند، اما **هویت مرورگری، Session Cookie/CSRF و Routeهای ضخیم باید جایگزین شوند**. Bot هنوز وجود ندارد.

## 3. کارهای لازم برای خروج از وضعیت موقت

### A. حذف مدل ورود وب

- حذف Login/Logout مرورگری به‌عنوان مسیر اصلی مدیریت.
- حذف Cookie Session و CSRF مخصوص Browser از مسیر هویت مدیر.
- بازطراحی Session/Identity حول Telegram Account و ابطال اتصال.
- اصلاح User model تا Email اجباری و Password Hash شرط لازم هر مدیر نباشد؛ Email در صورت نیاز metadata/contact است.
- Bootstrap امن Platform Owner با Telegram User ID عددی از CLI کنترل‌شده.
- Pairing مدیران بعدی با Token یک‌بارمصرف، hash‌شده و Expiring.

### B. ساخت Bot Adapter

- ساخت \`backend/app/bot/main.py\` و Application/Dispatcher.
- انتخاب aiogram 3، Long Polling و تک Replica در Railway.
- Middlewareهای Private Chat، Actor Extraction، Account Status، Rate Limit و Correlation.
- Routerهای فرمان عمومی، Navigation، Tenant/School، Academic و Admin Identity.
- Inline Keyboard، Callback Factory کوتاه، FSM Redis و Navigation Stack.
- Back، Cancel، Menu، Help، Timeout و خطاهای ورودی.
- جلوگیری از اجرای Update/Callback تکراری و ثبت وضعیت Action.
- هیچ Handler مستقیماً به ORM دسترسی نداشته باشد.

### C. جداسازی Application Layer

- Use Caseهای Create/List Tenant، School، Academic Year، Class و Subject.
- Repository برای Persistence و Policy Service برای Authorization.
- FastAPI و Bot هر دو همان Use Case را فراخوانی کنند.
- Transaction، Audit و Outbox boundary روشن باشد.
- پاسخ Bot از Result ساخت‌یافته ساخته شود؛ متن خطا/موفقیت در Handler مالک منطق نباشد.

### D. اصلاح Authorization و Integrity

- ارث‌بری Scope از Platform به Tenant و Scopeهای فرزند، با مرزهای روشن و تست‌شده.
- عدم انتقال مجوز به Scope والد، هم‌سطح یا Tenant دیگر.
- Deny-overrides-Allow در تمام مسیرهای ارث‌بری.
- عضویت Tenant و بازه‌های Role Binding هر بار کنترل شوند.
- قیود ترکیبی دیتابیس/Domain برای تضمین هم‌خوانی Tenant، School، Academic Year و Class.
- تست‌های منفی cross-tenant و دسترسی غیرمجاز با Callback دستکاری‌شده.

### E. Telegram Safety و Audit

- Pairing، Unlink، Account Suspend و Role Change با Audit.
- Private Chat فقط؛ Group/Channel Update رد شود.
- Callback actor/resource/expiry/status validation.
- Update ID/Action ID idempotency.
- داده حساس در FSM/Callback/Log قرار نگیرد.
- Audit شامل Actor واقعی Telegram-linked، Context، Correlation و نتیجه باشد.

### F. تست و CI

- Unit Tests برای Policy/Use Case/Navigation/FSM.
- Bot Tests برای Unknown User، Non-private chat، stale callback، double click، invalid input، Back/Cancel و duplicate Update.
- PostgreSQL Integration برای migration، audit immutability و cross-tenant integrity.
- Ruff و pytest برای Backend و Bot.
- هیچ Frontend/Node Build در CI نهایی وجود ندارد.

## 4. محدوده عملیاتی فاز یک

پس از موانع بالا، مدیر مجاز باید بتواند در ربات:

1. هویت خود و سطح دسترسی را ببیند.
2. فهرست Tenantهای قابل دسترس را ببیند و Context را انتخاب کند.
3. Tenant جدید بسازد (فقط Platform Permission).
4. مدرسه ایجاد/فهرست کند.
5. سال تحصیلی ایجاد و فهرست کند.
6. کلاس ایجاد و فهرست کند.
7. درس ایجاد و فهرست کند.
8. Audit مجاز را با فیلتر و Pagination ببیند.
9. از هر Flow یک Back و Cancel قابل پیش‌بینی داشته باشد.

هر قابلیت تا زمان وجود Handler، Use Case، Policy، Audit و Test «پیاده‌سازی‌شده» محسوب نمی‌شود.

## 5. معیارهای پذیرش

- هیچ رابط وب انسانی یا Login مرورگری در معماری باقی نمانده باشد.
- Bot فقط در Private Chat و برای حساب Pairشده فعال باشد.
- هیچ عملیات مدیریتی بدون Policy check انجام نشود.
- هیچ Callback قابل جعل یا Replay نباشد.
- فرم‌ها بعد از خطای ورودی در مرحله صحیح باقی بمانند.
- Back و Cancel در همه Wizardهای چندمرحله‌ای درست عمل کنند.
- Scope hierarchy و Tenant isolation با تست‌های مثبت و منفی اثبات شود.
- API Integration دارای Service Authentication مستقل باشد.
- Audit برای تمام عملیات حساس ثبت شود.
- CI بدون Frontend اجرا و سبز باشد.
- Railway Deploy و classmate2 هیچ تغییری بدون درخواست مستقل نداشته باشند.

**تا وقتی تمام معیارهای بالا برآورده نشده‌اند، PR2 نباید Merge شود.**
