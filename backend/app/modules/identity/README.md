# Identity and sessions

- User identities are platform-level; tenant access is represented separately by memberships.
- Passwords use Argon2id through argon2-cffi. Passwords and raw session tokens are never stored.
- Session tokens are opaque random secrets; only SHA-256 digests are persisted.
- Browser sessions use HttpOnly cookies. Unsafe authenticated requests require a matching CSRF cookie/header pair bound to the session.
- Login has a Redis-backed IP rate limit and per-account lockout.
- Logout revokes the server-side session immediately.
- No self-registration, demo credentials, password reset endpoint, MFA or external identity provider is enabled yet.

## First platform owner

After applying migrations, run from the backend environment:

    python -m app.cli create-platform-owner

The command prompts for the email, display name and password. It refuses to create another platform owner once one exists. The password is only read interactively and is not placed in command arguments or environment variables.

For the initial bootstrap, the command grants only the permission registry declared in app.cli.PLATFORM_PERMISSIONS; additions to that registry are changes to the privileged role and must be reviewed.
