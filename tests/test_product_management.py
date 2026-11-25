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
