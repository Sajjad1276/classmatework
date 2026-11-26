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


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        if self.value is None:
            raise AssertionError("No scalar row returned")
        return self.value


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



@pytest.mark.asyncio
async def test_list_product_class_records_rejects_invalid_status_before_database_read() -> None:
    from app.application.product_management import list_product_class_records

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await list_product_class_records(db, class_id=1, status="UNKNOWN")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_product_record_rejects_invalid_decision_before_database_read() -> None:
    from app.application.product_management import review_product_class_record

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await review_product_class_record(
            db,
            class_id=1,
            entity_type="homework",
            record_id=2,
            decision="publish",
            reviewer_telegram_id=10,
            reviewer_name="مدیر",
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_product_record_rejects_unknown_entity_before_database_read() -> None:
    from app.application.product_management import review_product_class_record

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await review_product_class_record(
            db,
            class_id=1,
            entity_type="attendance",
            record_id=2,
            decision="approve",
            reviewer_telegram_id=10,
            reviewer_name="مدیر",
        )
    db.execute.assert_not_awaited()



@pytest.mark.asyncio
async def test_issue_product_member_invite_code_rejects_invalid_input_before_database_read() -> None:
    from app.application.product_management import issue_product_member_invite_code

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await issue_product_member_invite_code(
            db,
            class_id=0,
            user_id=1,
            code="A1B2C3D4E5F60718",
        )
    with pytest.raises(InvalidApplicationInput):
        await issue_product_member_invite_code(
            db,
            class_id=1,
            user_id=2,
            code="short",
        )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_issue_product_member_invite_code_rejects_invalid_ttl_before_database_read() -> None:
    from app.application.product_management import issue_product_member_invite_code

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await issue_product_member_invite_code(
            db,
            class_id=1,
            user_id=2,
            code="A1B2C3D4E5F60718",
            ttl_hours=0,
        )
    db.execute.assert_not_awaited()



@pytest.mark.asyncio
async def test_create_product_class_creates_a_hashed_reusable_class_join_code() -> None:
    import hashlib

    from app.application.product_management import create_product_class

    db = AsyncMock()
    db.execute.side_effect = [
        FakeScalarResult(7),
        FakeScalarResult(11),
        FakeResult(
            [{
                "id": 41,
                "name": "دهم الف",
                "grade": "دهم",
                "section": "الف",
                "is_active": True,
            }]
        ),
    ]

    created = await create_product_class(
        db,
        school_id=7,
        name="دهم الف",
        grade="دهم",
        section="الف",
    )

    params = db.execute.await_args_list[2].args[1]
    code = created["member_join_code"]
    assert created["id"] == 41
    assert created["has_member_join_code"] is True
    assert len(code) == 10
    assert set(code) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")
    assert params["member_join_code_hash"] == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert "member_join_code_hash" in str(db.execute.await_args_list[2].args[0])
    assert params.get("member_join_code") is None


@pytest.mark.asyncio
async def test_issue_product_class_join_code_hashes_and_replaces_current_code() -> None:
    import hashlib

    from app.application.product_management import issue_product_class_join_code

    db = AsyncMock()
    db.execute.return_value = FakeResult(
        [{
            "id": 21,
            "name": "دهم ریاضی",
            "member_join_code_expires_at": None,
        }]
    )

    code = "ABCD2345XY"
    result = await issue_product_class_join_code(db, class_id=21, code=code)

    params = db.execute.await_args.args[1]
    sql = str(db.execute.await_args.args[0])
    assert result["member_join_code"] == code
    assert result["has_member_join_code"] is True
    assert params["code_hash"] == hashlib.sha256(code.encode("utf-8")).hexdigest()
    assert params["class_id"] == 21
    assert "member_join_code_hash" in sql
    assert "member_join_code_expires_at = NULL" in sql


@pytest.mark.asyncio
async def test_issue_product_class_join_code_rejects_invalid_input_without_database_access() -> None:
    from app.application.product_management import issue_product_class_join_code

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await issue_product_class_join_code(db, class_id=0, code="ABCD2345")
    with pytest.raises(InvalidApplicationInput):
        await issue_product_class_join_code(db, class_id=1, code="short")
    with pytest.raises(InvalidApplicationInput):
        await issue_product_class_join_code(db, class_id=1, code="ABCD 2345")
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_product_class_returns_school_and_class_join_code_status() -> None:
    from app.application.product_management import get_product_class

    db = AsyncMock()
    expected = {
        "id": 31,
        "name": "دهم الف",
        "grade": "دهم",
        "section": "الف",
        "is_active": True,
        "school_id": 9,
        "school_name": "مدرسه نمونه",
        "group_chat_id": None,
        "group_chat_title": None,
        "has_member_join_code": True,
        "member_join_code_expires_at": None,
    }
    db.execute.return_value = FakeResult([expected])

    result = await get_product_class(db, class_id=31)

    assert result == expected
    sql = str(db.execute.await_args.args[0])
    assert "ay.school_id" in sql
    assert "member_join_code_hash IS NOT NULL" in sql





@pytest.mark.asyncio
async def test_bulk_issue_missing_class_codes_only_writes_missing_hashes() -> None:
    import hashlib

    from app.application.product_management import issue_missing_product_class_join_codes

    db = AsyncMock()
    db.execute.side_effect = [
        FakeResult(
            [
                {"class_id": 31, "class_name": "دهم الف", "school_id": 7, "school_name": "دبیرستان نمونه"},
                {"class_id": 32, "class_name": "دهم ب", "school_id": 7, "school_name": "دبیرستان نمونه"},
            ]
        ),
        FakeScalarResult(31),
        FakeScalarResult(32),
    ]

    issued = await issue_missing_product_class_join_codes(db)

    assert [item["class_id"] for item in issued] == [31, 32]
    assert all(len(item["join_code"]) == 10 for item in issued)
    assert all(set(item["join_code"]) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for item in issued)
    first_update = db.execute.await_args_list[1].args[1]
    second_update = db.execute.await_args_list[2].args[1]
    assert first_update["code_hash"] == hashlib.sha256(issued[0]["join_code"].encode()).hexdigest()
    assert second_update["code_hash"] == hashlib.sha256(issued[1]["join_code"].encode()).hexdigest()
    assert "member_join_code_hash IS NULL" in str(db.execute.await_args_list[1].args[0])
    assert "member_join_code_expires_at = NULL" in str(db.execute.await_args_list[1].args[0])


@pytest.mark.asyncio
async def test_bulk_issue_missing_class_codes_skips_concurrently_issued_code() -> None:
    from app.application.product_management import issue_missing_product_class_join_codes

    db = AsyncMock()
    db.execute.side_effect = [
        FakeResult(
            [{"class_id": 31, "class_name": "دهم الف", "school_id": 7, "school_name": "دبیرستان نمونه"}]
        ),
        FakeScalarResult(None),
    ]

    issued = await issue_missing_product_class_join_codes(db)

    assert issued == []
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_bulk_issue_missing_class_codes_rejects_invalid_limit_before_database_access() -> None:
    from app.application.product_management import issue_missing_product_class_join_codes

    db = AsyncMock()
    with pytest.raises(InvalidApplicationInput):
        await issue_missing_product_class_join_codes(db, limit=0)
    with pytest.raises(InvalidApplicationInput):
        await issue_missing_product_class_join_codes(db, limit=5001)
    db.execute.assert_not_awaited()
