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
- Additional administrators are invited with a random, one-time, short-lived pairing token.
- Only a SHA-256 digest of the pairing token is stored; the raw token is shown once in the generated deep link.
- Pairing is bound to the exact target Telegram User ID and an unexpired pending User record.
- Pair creation and consumption are audited.
- Recovery of the platform owner after losing the Telegram account still requires a controlled out-of-band procedure; it is not implemented yet.

## Bot sessions and update state

- Browser Cookie Sessions and CSRF are removed.
- Redis stores FSM state with TTL, maintains the single-poller lease, and applies separate atomic per-sender message/callback rate limits (defaults: 30 messages and 45 callbacks per 60 seconds). Rate limiting fails closed if Redis is unavailable.
- PostgreSQL stores pending pairing tokens and processed Telegram Update status/leases.
- Wizard callbacks are bound to a per-flow token; an old confirmation/back/cancel button cannot affect a later flow.
- Telegram Update IDs are recorded to suppress duplicate deliveries. This is not a substitute for idempotent domain use cases: a crash after a domain commit but before Update completion can still require the use case's own idempotency protection.
- Audit events executed from a Telegram update carry a correlation ID in the form tg-UPDATE_ID.

## Implemented bot slice

- Private Chat guard and Telegram account resolution.
- /start, /menu, /back, /cancel, /invite, /help and /status.
- Account details and a permission-filtered main menu.
- Tenant list and a multi-step tenant creation wizard with validation, preview, confirm, back, cancel and stale-callback protection.

## Not yet complete

- Telegram unlink/recovery lifecycle and step-up/MFA.
- Full role assignment and user/membership management.
- Complete school/academic CRUD forms in the bot.
- Live Telegram API verification with a real bot token and production deployment.
- Machine-to-machine identity for integration APIs.

A bot skeleton in the repository is not a claim that a bot is deployed or production-ready.
