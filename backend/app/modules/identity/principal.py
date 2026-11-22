from dataclasses import dataclass

from app.db.models import TelegramAccount, User


@dataclass(frozen=True, slots=True)
class TelegramPrincipal:
    """Verified administrator identity derived from a private Telegram update."""

    user: User
    telegram_account: TelegramAccount
