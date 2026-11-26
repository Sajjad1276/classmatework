# Identity — Telegram-first

**Decision:** the only human administrator interface is the dedicated Telegram bot. Browser login, cookies and CSRF are not part of the product.

## Identity model

- User is the platform-level principal.
- TelegramAccount links one User to a numeric Telegram User ID.
- Telegram User ID is the identifier; username, display name and profile fields are metadata only.
- Administrative updates are accepted only in private chats.
- Every update resolves the active TelegramAccount and active User before routing.
- Tenant membership, RoleBinding scope and time validity are checked by the authorization layer for each domain use case.
- A disabled User or unlinked/blocked TelegramAccount is rejected on the next update.
- Bot Token is configured as a Pydantic SecretStr and must be present/non-empty before polling starts.

## Bootstrap and pairing

- Public self-registration is disabled.
- The first Platform Owner is provisioned by CLI using a numeric Telegram User ID verified outside the bot.
- Additional administrators are invited with a random, one-time, short-lived pairing token that is bound to an explicit Role and Scope.
- Only a SHA-256 digest of the pairing token is stored; the raw token is shown once in the generated deep link.
- Pairing is bound to the exact target Telegram User ID, selected role, platform/tenant scope and an unexpired pending User record.
- Accepting a platform invitation creates a RoleBinding; accepting a tenant invitation atomically creates TelegramAccount, TenantMembership and RoleBinding.
- Platform assignment requires platform user-management and role-assignment permissions. Tenant assignment requires membership-invite and role-manage permissions in that same tenant; every granted allow permission must also be held by the inviter at the destination scope.
- At acceptance, inviter status, assignment authority, role state and scope are revalidated. If authority was revoked, the invitation is rejected.
- Active invitations per Telegram ID are unique; expired invitations are revoked and their pending User is marked invitation_expired before a replacement is issued.
- Phase-one UI intentionally supports only platform and tenant invitation scopes; school/class-level invitations are not exposed yet.
- Pair creation and consumption are audited.
- Recovery of the platform owner after losing the Telegram account still requires a controlled out-of-band procedure; it is not implemented yet.

## Bot sessions and update state

- Browser Cookie Sessions and CSRF are removed.
- Redis stores FSM state with TTL, maintains the single-poller lease, and applies separate atomic per-sender message/callback rate limits (defaults: 30 messages and 45 callbacks per 60 seconds). Rate limiting fails closed if Redis is unavailable.
- PostgreSQL stores pending pairing tokens and processed Telegram Update status/leases.
- Wizard callbacks are bound to a per-flow token; an old confirmation or navigation button cannot affect a later flow.
- Telegram Update IDs are recorded to suppress duplicate deliveries. This is not a substitute for idempotent domain use cases: a crash after a domain commit but before Update completion can still require the use case's own idempotency protection.
- Audit events executed from a Telegram update carry a correlation ID in the form tg-UPDATE_ID.

## Implemented bot slice

- Private Chat guard and Telegram account resolution.
- فقط `/start` برای شروع فعال است؛ همهٔ عملیات دیگر از منوها، پنل‌ها و دکمه‌های inline/reply انجام می‌شود.
- Account details and a permission-filtered main menu.
- Role directory for platform owners and tenant managers (tenant managers see only their own tenant's roles).
- Tenant-local custom role creation; every allow permission must already be held by the creator at the same tenant scope.
- Active member directory with current tenant role bindings; Telegram IDs for blocked/unlinked accounts are hidden.
- Two-step member suspension with mandatory reason; self-suspension and suspension of the last active tenant admin are rejected. Only that tenant's membership and role bindings are ended.
- Two-step member reactivation with a new selected Tenant Role; the actor must hold every allow permission in that role, the target TelegramAccount must be active, and a fresh RoleBinding is created rather than reviving old grants.
- Role-aware invitation from the Platform or from the same Tenant; a tenant manager cannot invite into another Tenant.
- Pending invitation directory, scoped to platform administrators or the same Tenant; expired entries are clearly marked and raw tokens are never displayed.
- Revocation of pending invitations by the creator or an administrator with current assignment authority; a consumed invitation cannot be revoked through this command.
- Tenant list/select and a multi-step tenant creation wizard with validation, preview, confirm, back, cancel and stale-callback protection.
- Tenant-scoped school list/create forms.
- School-scoped academic-year list/create forms.
- School-scoped class list/create flows, including selecting an academic year before class creation.
- School-scoped subject list/create forms.
- Wizard contexts are retained in Redis FSM and every write use case rechecks the target ownership and permissions.

## Not yet complete

- Telegram unlink/recovery lifecycle and step-up/MFA.
- Role edit/archive, independent permission-registry management, membership cancellation and member-directory pagination.
- Update/archive forms, complete pagination/search and automated end-to-end Bot API tests.
- Complete school/academic CRUD forms in the bot.
- Live Telegram API verification with a real bot token and production deployment.
- Machine-to-machine identity for integration APIs.

A bot skeleton in the repository is not a claim that a bot is deployed or production-ready.
