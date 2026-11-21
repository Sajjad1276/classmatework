# خط مبنای امنیتی فاز صفر

## آنچه در bootstrap برقرار شده است

- هیچ حساب ادمین یا credential پیش‌فرض ایجاد نمی‌شود.
- health endpointها فاقد داده‌ی کسب‌وکار و فاقد جزئیات اتصال هستند.
- request ID ورودی با الگوی محدود اعتبارسنجی می‌شود؛ مقدار نامعتبر با UUID جدید جایگزین می‌شود.
- خطاهای readiness فقط نام وابستگی و وضعیت را گزارش می‌کنند.
- secretهای واقعی از Git مستثنا هستند و .env.example فقط مقدار محلی غیرحساس دارد.
- تصویر Docker با کاربر غیرریشه اجرا می‌شود.
- شناسه‌ی tenant از هدر یا body کلاینت به‌عنوان مرجع مجوز پذیرفته نمی‌شود.

## مواردی که هنوز پیاده‌سازی نشده‌اند

Authentication، MFA، session management، RBAC/ABAC، tenant isolation در query،
CSRF/CORS policy، rate limiting، audit append-only، encryption at rest، object storage،
backup/restore و security incident workflow.

تا زمان پیاده‌سازی و تست این کنترل‌ها، API نباید داده یا عملیات مدیریتی واقعی را عرضه کند.

## قاعده‌ی tenant context

پس از پیاده‌سازی identity، tenant فعال باید از principal احراز‌شده و عضویت معتبر او استخراج شود.
درخواست client می‌تواند tenant موردنظر را به‌عنوان انتخاب اعلام کند، اما backend باید عضویت،
scope و policy آن را مستقل بررسی کند. تغییر tenant ID در URL یا body نباید دسترسی ایجاد کند.

## مدیریت محیط

- مقدارهای داخل docker-compose صرفاً برای توسعه‌ی محلی هستند.
- برای Railway یا هر محیط اشتراکی، DATABASE_URL و REDIS_URL باید از متغیرهای محیطی امن تزریق شوند.
- هیچ secret در log، پاسخ health، commit یا screenshot قرار نگیرد.
- دسترسی مستقیم به production database از لپ‌تاپ توسعه‌دهنده مجاز فرض نمی‌شود.
