from unittest.mock import AsyncMock

import pytest

from app.application.errors import InvalidApplicationInput
from app.application.product_management import create_product_school, list_product_schools


class FakeMappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def one(self):
        return self.rows[0]

    def one_or_none(self):
        return self.rows[0] if self.rows else None


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return FakeMappings(self.rows)


@pytest.mark.asyncio
async def test_list_product_schools_returns_product_records() -> None:
    db = AsyncMock()
    db.execute.return_value = FakeResult(
        [{"id": 7, "name": "مدرسه نمونه", "is_active": True, "class_count": 3}]
    )

    schools = await list_product_schools(db)

    assert schools == [
        {"id": 7, "name": "مدرسه نمونه", "is_active": True, "class_count": 3}
    ]
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_create_product_school_normalizes_whitespace() -> None:
    db = AsyncMock()
    db.execute.return_value = FakeResult(
        [{"id": 9, "name": "مدرسه نمونه", "is_active": True}]
    )

    school = await create_product_school(db, name="  مدرسه   نمونه  ")

    assert school["id"] == 9
    params = db.execute.await_args.args[1]
    assert params == {"name": "مدرسه نمونه"}


@pytest.mark.asyncio
async def test_create_product_school_rejects_short_names_before_database_write() -> None:
    db = AsyncMock()

    with pytest.raises(InvalidApplicationInput):
        await create_product_school(db, name=" ")

    db.execute.assert_not_awaited()



@pytest.mark.asyncio
async def test_create_product_class_rejects_invalid_grade_before_database_write() -> None:
    from app.application.product_management import create_product_class

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await create_product_class(
            db,
            school_id=1,
            name="دهم الف",
            grade="",
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_timetable_entry_rejects_invalid_weekday_before_database_write() -> None:
    from app.application.product_management import create_product_timetable_entry

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await create_product_timetable_entry(
            db,
            class_id=1,
            subject_id=1,
            weekday=7,
            period=1,
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_timetable_entry_rejects_invalid_time_order_before_database_write() -> None:
    from app.application.product_management import create_product_timetable_entry

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await create_product_timetable_entry(
            db,
            class_id=1,
            subject_id=1,
            weekday=0,
            period=1,
            starts_at="09:00",
            ends_at="08:00",
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_class_member_rejects_invalid_telegram_id_before_database_write() -> None:
    from app.application.product_management import add_product_class_member

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await add_product_class_member(
            db,
            class_id=1,
            telegram_id=0,
            full_name="کاربر",
        )
    db.execute.assert_not_awaited()



@pytest.mark.asyncio
async def test_update_product_school_normalizes_whitespace() -> None:
    from app.application.product_management import update_product_school

    db = AsyncMock()
    db.execute.return_value = FakeResult(
        [{"id": 12, "name": "دبیرستان جدید", "timezone": "Asia/Tehran", "is_active": True}]
    )

    school = await update_product_school(db, school_id=12, name=" دبیرستان   جدید ")

    assert school["name"] == "دبیرستان جدید"
    assert db.execute.await_args.args[1] == {
        "school_id": 12,
        "name": "دبیرستان جدید",
    }


@pytest.mark.asyncio
async def test_update_product_school_rejects_invalid_input_before_database_write() -> None:
    from app.application.product_management import update_product_school

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await update_product_school(db, school_id=0, name="مدرسه")
    with pytest.raises(InvalidApplicationInput):
        await update_product_school(db, school_id=1, name=" ")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_product_class_rejects_invalid_grade_before_database_write() -> None:
    from app.application.product_management import update_product_class

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await update_product_class(
            db,
            class_id=1,
            name="دهم الف",
            grade="",
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_product_class_active_rejects_invalid_id_before_database_write() -> None:
    from app.application.product_management import set_product_class_active

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await set_product_class_active(db, class_id=0, is_active=False)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_product_school_active_rejects_invalid_id_before_database_write() -> None:
    from app.application.product_management import set_product_school_active

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await set_product_school_active(db, school_id=0, is_active=False)
    db.execute.assert_not_awaited()



@pytest.mark.asyncio
async def test_issue_product_group_link_code_hashes_raw_code_and_sets_expiry() -> None:
    from datetime import datetime, timezone

    from app.application.product_management import issue_product_group_link_code

    db = AsyncMock()
    db.execute.side_effect = [
        FakeResult(
            [
                {
                    "id": 31,
                    "name": "دهم الف",
                    "grade": "دهم",
                    "section": "الف",
                    "is_active": True,
                    "school_id": 7,
                    "school_name": "دبیرستان",
                }
            ]
        ),
        FakeResult(
            [
                {
                    "id": 31,
                    "name": "دهم الف",
                    "group_chat_id": None,
                    "group_chat_title": None,
                    "group_link_code_expires_at": datetime.now(timezone.utc),
                }
            ]
        ),
    ]

    raw_code = "A1B2C3D4E5F60718293A4B5C"
    result = await issue_product_group_link_code(db, class_id=31, code=raw_code)

    update_params = db.execute.await_args_list[1].args[1]
    assert result["id"] == 31
    assert update_params["code_hash"] != raw_code
    assert len(update_params["code_hash"]) == 64
    assert update_params["expires_at"].tzinfo is not None


@pytest.mark.asyncio
async def test_issue_product_group_link_code_rejects_short_code_before_database_read() -> None:
    from app.application.product_management import issue_product_group_link_code

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await issue_product_group_link_code(db, class_id=1, code="short")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_product_class_member_role_rejects_privileged_role_values() -> None:
    from app.application.product_management import set_product_class_member_role

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await set_product_class_member_role(
            db,
            class_id=1,
            user_id=2,
            role="platform_admin",
        )
    db.execute.assert_not_awaited()
