from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.bot import product_router


class ProductSessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return self


def _callback(data: str, *, chat_type: str = "private"):
    message = SimpleNamespace(
        chat=SimpleNamespace(type=chat_type),
        edit_text=AsyncMock(),
    )
    return SimpleNamespace(
        data=data,
        message=message,
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_class_button_opens_detail_and_displays_all_permitted_actions(monkeypatch):
    async def allowed(_db, _principal, permission_key):
        return permission_key in {
            "platform:classes:read",
            "platform:subjects:manage",
            "platform:timetable:manage",
            "platform:classes:manage",
            "platform:groups:manage",
            "platform:members:manage",
            "platform:announcements:manage",
            "platform:records:read",
        }

    monkeypatch.setattr(product_router, "_has_permission", allowed)
    monkeypatch.setattr(
        product_router,
        "get_product_class",
        AsyncMock(
            return_value={
                "id": 37,
                "name": "دهم الف",
                "school_id": 6,
                "school_name": "دبیرستان نمونه",
                "grade": "دهم",
                "section": "الف",
                "is_active": True,
                "has_member_join_code": True,
                "member_join_code_expires_at": None,
                "group_chat_id": None,
                "group_chat_title": None,
            }
        ),
    )
    monkeypatch.setattr(product_router, "list_class_subjects", AsyncMock(return_value=[]))
    monkeypatch.setattr(product_router, "list_class_timetable", AsyncMock(return_value=[]))

    callback = _callback("product:class:37")
    principal = SimpleNamespace(user=SimpleNamespace(id=42))

    await product_router.product_class_detail_callback(
        callback,
        principal=principal,
        db=object(),
        product_session_factory=lambda: ProductSessionContext(),
    )

    callback.answer.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    callback_data = {button.callback_data for button in buttons}
    labels = {button.text for button in buttons}

    assert "product:class:join-code:37" in callback_data
    assert "product:group:link:37" in callback_data
    assert "product:subject:add:37" in callback_data
    assert "product:timetable:view:37" in callback_data
    assert "product:timetable:add:37" in callback_data
    assert "product:members:37" in callback_data
    assert "product:announcements:37" in callback_data
    assert "♻️ تغییر کد ورود کلاس" in labels
    assert "➕ افزودن گروه" in labels
    assert "⬅️ بازگشت به مدرسه" in labels


@pytest.mark.asyncio
async def test_class_detail_hides_actions_without_their_permissions(monkeypatch):
    async def allowed(_db, _principal, permission_key):
        return permission_key == "platform:classes:read"

    monkeypatch.setattr(product_router, "_has_permission", allowed)
    monkeypatch.setattr(
        product_router,
        "get_product_class",
        AsyncMock(
            return_value={
                "id": 37,
                "name": "دهم الف",
                "school_id": 6,
                "school_name": "دبیرستان نمونه",
                "grade": "دهم",
                "section": "الف",
                "is_active": True,
                "has_member_join_code": False,
                "member_join_code_expires_at": None,
                "group_chat_id": None,
                "group_chat_title": None,
            }
        ),
    )
    monkeypatch.setattr(product_router, "list_class_subjects", AsyncMock(return_value=[]))
    monkeypatch.setattr(product_router, "list_class_timetable", AsyncMock(return_value=[]))

    callback = _callback("product:class:37")
    await product_router.product_class_detail_callback(
        callback,
        principal=SimpleNamespace(user=SimpleNamespace(id=42)),
        db=object(),
        product_session_factory=lambda: ProductSessionContext(),
    )

    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    callback_data = {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    }
    assert callback_data == {"product:school:6"}


@pytest.mark.asyncio
async def test_class_join_code_button_issues_and_displays_the_native_join_command(monkeypatch):
    monkeypatch.setattr(product_router, "_has_permission", AsyncMock(return_value=True))
    issue_code = AsyncMock(return_value={"id": 37, "name": "دهم الف"})
    record = AsyncMock()
    monkeypatch.setattr(product_router, "issue_product_class_join_code", issue_code)
    monkeypatch.setattr(product_router, "record_event", record)

    callback = _callback("product:class:join-code:37")
    principal = SimpleNamespace(user=SimpleNamespace(id=42))
    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    await product_router.product_class_join_code_issue_callback(
        callback,
        principal=principal,
        db=db,
        product_session_factory=lambda: ProductSessionContext(),
    )

    issue_code.assert_awaited_once()
    code = issue_code.await_args.kwargs["code"]
    assert len(code) == 10
    assert set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    db.commit.assert_awaited_once()
    record.assert_awaited_once()
    text = callback.message.edit_text.await_args.args[0]
    assert f"/join {code}" in text
    markup = callback.message.edit_text.await_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "product:class:37"


@pytest.mark.asyncio
async def test_class_join_code_button_rejects_group_chats_before_issuing_code(monkeypatch):
    issue_code = AsyncMock()
    monkeypatch.setattr(product_router, "_has_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(product_router, "issue_product_class_join_code", issue_code)

    callback = _callback("product:class:join-code:37", chat_type="supergroup")
    await product_router.product_class_join_code_issue_callback(
        callback,
        principal=SimpleNamespace(user=SimpleNamespace(id=42)),
        db=object(),
        product_session_factory=lambda: ProductSessionContext(),
    )

    issue_code.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_member_suspension_back_and_school_directory_routes_are_live():
    from app.bot.router import _member_suspend_callback_data

    token = "AbCdEf12"
    member_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
    callback_data = _member_suspend_callback_data(token, member_id)
    assert len(callback_data.encode("utf-8")) <= 64
    assert callback_data.startswith("m:s:start:")

    # These are the targets of the Work control-panel Back buttons.
    from app.bot.control_router import router as control_router
    from app.bot.router import router as root_router

    assert root_router.name == "admin-control"
    assert control_router.name == "control-center"
