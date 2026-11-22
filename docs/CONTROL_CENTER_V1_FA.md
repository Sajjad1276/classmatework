# کنترل‌سنتر ClassMate — اتصال گروه کلاس و گزارش رویدادمحور

## محدوده این برش

این نسخه پایهٔ عملیاتی دو قابلیت را می‌سازد: اتصال امن هر ClassGroup به گروه Telegram و نگهداری دادهٔ واقعی برای گزارش‌های مدیریتی. این برش، کل کنترل‌سنتر یا مدیریت همه قابلیت‌های ربات اصلی را تکمیل نمی‌کند.

## اتصال گروه به کلاس

### قواعد داده

- هر ClassGroup حداکثر یک رکورد ClassTelegramGroup دارد.
- شناسه Telegram Chat در کل سامانه یکتا است.
- کلاس و Tenant در دیتابیس با Foreign Key مرکب به هم متصل‌اند.
- گروه‌های مجاز فقط `group` و `supergroup` هستند.
- وضعیت اتصال: `active`، `revoked` یا `attention`.

### جریان اتصال

1. مدیر وارد صفحه جزئیات کلاس و سپس «مدیریت گروه تلگرام» می‌شود.
2. ClassMate Work کد تصادفی با عمر ۱۰ دقیقه صادر می‌کند. فقط SHA-256 کد در دیتابیس ذخیره می‌شود.
3. صدور کد تازه، کدهای مصرف‌نشده قبلی همان کلاس را لغو می‌کند؛ برای هر کلاس حداکثر یک کد فعال باقی می‌ماند.
4. مدیر ربات اصلی را به گروه اضافه می‌کند و دسترسی لازم را می‌دهد.
5. همان مدیر که کد را صادر کرده، دستور `/classmate_connect CODE` را در گروه ارسال می‌کند.
6. ربات اصلی باید با Telegram Bot API بررسی کند فرستنده مدیر گروه است و خود ربات نیز مدیر گروه است.
7. فقط پس از این بررسی، ربات اصلی از مسیر M2M برای تکمیل اتصال استفاده می‌کند.
8. Use Case زمان اعتبار، Hash کد، شناسه مدیر صادرکننده، نوع گروه و یکتایی گروه/کلاس را بررسی و اتصال را در یک تراکنش ثبت می‌کند.

### تکمیل اتصال M2M

`POST /api/v1/integrations/telegram/group-links/complete`

Header:
- `X-ClassMate-Integration-Key: <shared secret>`

Payload:
```json
{
  "code": "one-time-code",
  "telegram_chat_id": -1001234567890,
  "chat_title": "دهم الف",
  "chat_username": "grade10a",
  "chat_type": "supergroup",
  "actor_telegram_user_id": 123456789,
  "integration_bot_id": 900000000,
  "bot_is_admin": true
}
```

مقدار `bot_is_admin` صرفاً پس از بررسی واقعی Telegram Bot API باید توسط ربات اصلی ارسال شود. این مسیر به علت Secret مشترک، فقط برای سرویس مورداعتماد قابل فراخوانی است. در نبود `CLASSMATE_INTEGRATION_TOKEN` Endpoint با پاسخ 503 غیرفعال می‌ماند.

## گزارش‌های واقعی

### جدول‌های اصلی

- `usage_events`: رویدادهای یکتای مصرف؛ با External Event ID برای Idempotency.
- `usage_identities`: یک رکورد حداقلی برای هر شناسه ناشناس‌شده؛ وضعیت حساب و زمان اولین/آخرین مشاهده را نگه می‌دارد.
- `audit_events`: گزارش ممیزی عملیات مدیریتی؛ از Telemetry مصرف جدا می‌ماند.

### حریم خصوصی

- ربات اصلی می‌تواند Telegram User ID را در درخواست M2M ارسال کند؛ API آن را با HMAC-SHA256 و Secret سمت سرور به `actor_key_hash` تبدیل می‌کند.
- شناسه خام در جدول UsageEvent یا UsageIdentity ذخیره نمی‌شود.
- `CLASSMATE_TELEMETRY_HASH_KEY` باید یک Secret پایدار و جدا از Integration Token باشد تا Rotation کلید دسترسی، هویت‌های آماری قبلی را عوض نکند. در نبود آن، Integration Token fallback است و Rotation آن شمارش تاریخی کاربران را دوپاره می‌کند.
- فقط کلیدهای غیرمحتوایی مجاز در Attributes ذخیره می‌شوند: `feature`، `action`، `result`، `duration_ms`، `class_code`، `subject_code` و `source_screen`.
- متن پیام، فایل، محتوای درسی، رمز، Token و اطلاعات شخصی نباید وارد Telemetry شوند.

### رویدادها و وضعیت کاربر

- رویدادهای عادی مانند `class.opened` یا `lesson.viewed` اولین مشاهده را ایجاد می‌کنند و زمان آخرین مشاهده را به‌روز می‌کنند.
- `user.registered`، `user.activated` و `user.reactivated` وضعیت را فعال می‌کنند.
- `user.deactivated` وضعیت را غیرفعال و `user.blocked` وضعیت را مسدود می‌کند.
- Event ID تکراری دوباره پردازش نمی‌شود.

### تعریف شاخص‌ها

- کل کاربران: تعداد UsageIdentityهایی که در Telemetry شناخته شده‌اند.
- کاربران فعال حساب: UsageIdentityهایی با status فعال.
- کاربران جدید روز/هفته/ماه: first_seen_at در ۱/۷/۳۰ روز گذشته.
- کاربران فعال یک/چهار/دوازده ساعت: تعداد actor_key_hash یکتای دارای حداقل یک رویداد در هر بازه.
- مدارس/کلاس‌ها/درس‌ها: شمارش داده‌های واقعی جداول آموزشی.
- گروه‌های متصل: شمارش ClassTelegramGroup با status فعال.

آمار تاریخی قبل از اتصال Telemetry خودبه‌خود قابل بازیابی نیست. برای شمار کامل کاربران موجود، ربات اصلی باید یک Backfill کنترل‌شده از رویداد `user.registered` برای حساب‌های قبلی انجام دهد. بدون آن، گزارش «کل کاربران» فقط کاربرانی را می‌شمارد که از شروع Telemetry مشاهده شده‌اند.

## دریافت رویدادهای مصرف

`POST /api/v1/integrations/usage-events`

Payload نمونه:
```json
{
  "event_id": "b7f6fcd4-97ef-4d6a-917b-bf9752ca9f32",
  "source_service": "main-bot",
  "event_name": "lesson.viewed",
  "actor_telegram_user_id": 123456789,
  "tenant_id": null,
  "aggregate_type": "lesson",
  "aggregate_id": null,
  "attributes": {
    "feature": "lessons",
    "action": "open",
    "result": "success"
  },
  "occurred_at": "2026-11-22T18:00:00+03:30"
}
```

## مرز استقرار

کد API در این برش اضافه می‌شود، ولی Railway Service فعلی `classmatework` فقط Bot را اجرا می‌کند. این Endpointها تا زمان Deploy جداگانهٔ FastAPI و تنظیم Secretهای مشترک در دسترس شبکه نیستند. در این برش هیچ تغییر یا Deploy روی `classmate2`، `classmate-worker` یا ربات اصلی انجام نمی‌شود.
