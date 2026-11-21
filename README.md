# ClassMate Work

سامانه‌ی مستقل مدیریت و عملیات ClassMate. این مخزن مالک منطق مدیریتی است؛
منطق آموزشی/دانش‌آموزی در مخزن جداگانه‌ی classmate2 باقی می‌ماند.

## وضعیت فعلی

- Phase 0 ادغام شده و CI پایه سبز است.
- Phase 1 در شاخه phase-1/core-identity-access در حال توسعه است.
- Backend: FastAPI modular monolith با API نسخه‌دار در /api/v1.
- Persistence: PostgreSQL/SQLAlchemy و migration با Alembic.
- Cache/rate limit foundation: Redis.
- Admin shell: React + TypeScript + RTL.
- Identity: Argon2id و نشست opaque سمت سرور.
- Authorization: RBAC scope-aware با تقدم deny بر allow.
- Audit: رویدادهای append-only با trigger دیتابیس در PostgreSQL.

## اجرای محلی

نیازمندی‌ها: Python 3.12+، Node.js 22+، npm و Docker Compose.

1. فایل نمونه را کپی کنید: cp .env.example .env
2. سرویس‌ها و API را اجرا کنید: docker compose up --build
3. migrationهای فعلی را اجرا کنید:

   alembic -c backend/alembic.ini upgrade head

4. اولین مالک پلتفرم را به‌صورت تعاملی ایجاد کنید:

   PYTHONPATH=backend python -m app.cli create-platform-owner

5. رابط وب را در ترمینال جداگانه اجرا کنید:

   cd frontend && npm install && npm run dev

API روی http://localhost:8000 و رابط وب روی http://localhost:5173 در دسترس خواهد بود.

در ویندوز، فرمان PYTHONPATH را با تنظیم معادل محیطی سیستم خود اجرا کنید یا از محیط مجازی backend استفاده کنید.

## APIهای فعلی

| روش | مسیر | کاربرد |
| --- | --- | --- |
| GET | /api/v1/health/live | زنده‌بودن process |
| GET | /api/v1/health/ready | آمادگی PostgreSQL و Redis |
| POST | /api/v1/auth/login | ورود با ایمیل و گذرواژه |
| POST | /api/v1/auth/logout | ابطال نشست جاری؛ نیازمند CSRF |
| GET | /api/v1/me | هویت و عضویت‌های فعال کاربر |
| GET/POST | /api/v1/tenants | فهرست/ایجاد tenant با مجوز platform |
| GET/POST | /api/v1/schools | فهرست/ایجاد مدرسه در tenant انتخاب‌شده |
| POST | /api/v1/academic-years | ایجاد سال تحصیلی |
| POST | /api/v1/classes | ایجاد کلاس |
| POST | /api/v1/subjects | ایجاد درس |
| GET | /api/v1/audit-events | خواندن audit در tenant |

Endpointهای tenant با هدر X-Tenant-ID tenant انتخابی را دریافت می‌کنند. Backend عضویت و permission را جداگانه ارزیابی می‌کند؛ مقدار ارسالی client به‌تنهایی مجوز نیست.

## Migration

برای ایجاد migration پس از تغییر مدل‌ها:

    alembic -c backend/alembic.ini revision --autogenerate -m "describe schema change"

فایل تولیدشده باید بازبینی و در محیط توسعه آزمایش شود. سپس:

    alembic -c backend/alembic.ini upgrade head

## کنترل کیفیت

    ruff check backend tests
    pytest
    cd frontend && npm install && npm run build

## مرزهای امنیتی

- ثبت‌نام عمومی یا حساب مدیر پیش‌فرض وجود ندارد.
- اولین مالک پلتفرم فقط با فرمان تعاملی CLI ساخته می‌شود.
- گذرواژه‌ها با Argon2id هش می‌شوند و توکن خام نشست در دیتابیس ذخیره نمی‌شود.
- نشست مرورگر HttpOnly است؛ درخواست‌های تغییردهنده‌ی احراز‌شده CSRF token می‌خواهند.
- ورود با Redis rate-limited و حساب پس از تلاش‌های مکرر ناموفق موقتاً قفل می‌شود.
- tenant context از X-Tenant-ID قابل انتخاب است، اما scope و membership در backend کنترل می‌شود.
- audit در PostgreSQL در برابر UPDATE و DELETE قفل شده است.
- MFA/TOTP، invitation lifecycle و مدیریت نقش از UI هنوز در صف فاز یک هستند.
- secret واقعی یا داده‌ی production نباید وارد Git شود.
- classmate2 مستقل باقی می‌ماند و از این مخزن تغییر نمی‌گیرد.

## اسناد

- طراحی مرجع: docs/SYSTEM_DESIGN_FA.md
- تصمیم‌های فاز صفر: docs/PHASE_ZERO_DECISIONS_FA.md
- خط مبنای امنیتی: docs/SECURITY_BASELINE_FA.md
- قرارداد API فاز صفر: docs/PHASE_ZERO_API_FA.md
- اجرای فاز یک: docs/PHASE_ONE_IMPLEMENTATION_FA.md
