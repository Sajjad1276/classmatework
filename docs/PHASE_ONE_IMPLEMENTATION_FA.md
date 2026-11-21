# فاز یک — هسته مدیریت ClassMate Work

**شاخه:** phase-1/core-identity-access  
**وضعیت:** در حال پیاده‌سازی  
**مبنای طراحی:** چندمستاجری، Modular Monolith و هویت سراسری با عضویت‌های tenant-aware.

## تصمیم‌های اجرایی موقت

- هویت کاربر در سطح پلتفرم یکتا است؛ عضویت در هر tenant یک رکورد مستقل و تاریخ‌دار است.
- ورود اولیه با ایمیل و گذرواژه‌ی Argon2 انجام می‌شود؛ نشست مرورگر opaque و سمت سرور قابل ابطال است.
- نشست از cookie امن HttpOnly استفاده می‌کند؛ عملیات تغییردهنده به CSRF token وابسته است.
- ثبت‌نام عمومی ممنوع است. اولین مالک پلتفرم فقط با فرمان تعاملی CLI ساخته می‌شود.
- هر tenant دارای مدرسه و ساختار آموزشی مستقل است.
- role binding دارای scope است؛ binding پلتفرم، tenant یا school/class را هدف می‌گیرد.
- deny بر allow مقدم است؛ عضویت فعال tenant برای مجوزهای tenant-scoped لازم است.
- فارسی زبان پیش‌فرض است؛ locale و timezone در tenant نگهداری می‌شود.
- MFA و تأیید هویت بیرونی تا انتخاب سیاست محصول غیرفعال‌اند؛ نقش‌های حساس نباید قبل از فعال‌شدن MFA به اپراتورهای دیگر واگذار شوند.

## APIهای پیاده‌سازی‌شده در این برش

- POST /api/v1/auth/login
- POST /api/v1/auth/logout
- GET /api/v1/me
- GET /api/v1/tenants
- POST /api/v1/tenants
- GET /api/v1/schools
- POST /api/v1/schools
- POST /api/v1/academic-years
- POST /api/v1/classes
- POST /api/v1/subjects
- GET /api/v1/audit-events

Tenant context برای endpointهای tenant با هدر X-Tenant-ID انتخاب می‌شود، اما دسترسی فقط پس از ارزیابی permission و membership معتبر است.

## کنترل‌های امنیتی

- Argon2id password hashing.
- Opaque session token؛ ذخیره‌ی digest به‌جای مقدار خام.
- CSRF double-submit cookie/header با binding به session.
- Rate limit ورود با Redis و lockout حساب.
- عدم افشای اینکه ایمیل وجود دارد یا حساب قفل/غیرفعال است.
- ثبت ورود موفق/ناموفق و تغییرات دامنه‌ای در audit.
- Audit ORM-level immutable؛ migration قفل update/delete را در PostgreSQL نیز اعمال می‌کند.
- Tenant ownership در queryهای ایجاد و خواندن منابع مدرسه بررسی می‌شود.

## موارد باقی‌مانده پیش از پایان فاز یک

- MFA/TOTP و سیاست step-up برای نقش‌های حساس.
- invitation lifecycle و پذیرش عضویت با token یک‌بارمصرف.
- سازنده‌ی نقش و مدیریت role binding از UI/API.
- CRUD کامل کاربران/عضویت، انتقال عضویت با تاریخ اثر و ادغام هویت‌ها.
- فهرست/جزئیات/به‌روزرسانی سال تحصیلی، کلاس و درس.
- داشبورد واقعی و جست‌وجوی سراسری مبتنی بر داده.
- تست‌های integration با PostgreSQL و تست‌های منفی جداسازی tenant.
- ممیزی کامل mutation شامل before/after و policy version.

این شاخه تنها یک برش اجرایی از فاز یک است؛ تا بسته‌شدن موارد بالا، فاز یک کامل محسوب نمی‌شود.
