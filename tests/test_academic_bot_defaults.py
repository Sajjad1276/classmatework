from datetime import date, timedelta
import re

from app.bot.academic_router import _default_academic_year_range, _generated_code, _jalali_to_gregorian


def test_jalali_academic_year_defaults_to_gregorian_boundaries() -> None:
    starts_on, ends_on = _default_academic_year_range("۱۴۰۵-۱۴۰۶")

    assert starts_on == date(2026, 3, 21)
    assert ends_on == date(2027, 3, 20)


def test_ascii_jalali_academic_year_label_is_supported() -> None:
    starts_on, ends_on = _default_academic_year_range("1404/1405")

    assert starts_on == date(2025, 3, 21)
    assert ends_on == date(2026, 3, 20)


def test_jalali_conversion_handles_next_year_boundary() -> None:
    assert _jalali_to_gregorian(1406, 1, 1) == date(2027, 3, 21)


def test_custom_academic_year_label_uses_one_year_default() -> None:
    starts_on, ends_on = _default_academic_year_range("سال آموزشی ویژه")

    assert ends_on - starts_on == timedelta(days=364)


def test_internal_codes_are_generated_without_manual_input() -> None:
    code = _generated_code("CLS")

    assert re.fullmatch(r"CLS-[A-F0-9]{16}", code)
