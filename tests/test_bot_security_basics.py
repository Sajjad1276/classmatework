from aiogram.enums import ChatType

from app.bot.middleware import is_private_chat


def test_admin_bot_accepts_only_private_chats() -> None:
    assert is_private_chat(ChatType.PRIVATE)
    assert is_private_chat("private")
    assert not is_private_chat(ChatType.GROUP)
    assert not is_private_chat(ChatType.SUPERGROUP)
    assert not is_private_chat(ChatType.CHANNEL)
    assert not is_private_chat(None)
