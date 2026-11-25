"""Guard every inline callback emitted by the admin panel against missing handlers/Telegram limits."""

import ast
import re
from pathlib import Path

BOT_ROUTER_DIRECTORY = Path(__file__).parents[1] / "backend" / "app" / "bot"
ACTIVE_ROUTER_PATHS = (
    BOT_ROUTER_DIRECTORY / "router.py",
    BOT_ROUTER_DIRECTORY / "control_router.py",
    BOT_ROUTER_DIRECTORY / "product_router.py",
)
PRODUCT_ROUTER_PATH = BOT_ROUTER_DIRECTORY / "product_router.py"
INTEGER_ID_PLACEHOLDER = "2147483647"


def _sample_callback(node: ast.expr, router_name: str = "") -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                expression = ast.unparse(value.value)
                if any(key in expression for key in ("token", "code")):
                    parts.append("AbCdEf12345")
                elif "entity_type" in expression:
                    parts.append("homework")
                elif "decision" in expression:
                    parts.append("approve")
                elif router_name == "control_router.py" and any(
                    key in expression for key in ("class_id", "class_group.id")
                ):
                    parts.append("123e4567-e89b-12d3-a456-426614174000")
                elif router_name == "router.py" and "tenant_id" in expression:
                    parts.append("123e4567-e89b-12d3-a456-426614174000")
                elif any(key in expression for key in ("class_id", "school_id", "user_id", "record_id", "announcement_id", "subject_id", "item['id']")):
                    parts.append(INTEGER_ID_PLACEHOLDER)
                else:
                    parts.append("1")
        return "".join(parts)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_member_suspend_callback_data"
    ):
        return "m:s:start:AbCdEf12:0123456789abcdef0123456789abcdef"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _sample_callback(node.left, router_name) + _sample_callback(node.right, router_name)
    raise AssertionError(f"Unsupported callback_data expression: {ast.dump(node)}")


def _callback_filters(tree: ast.Module) -> list[tuple[str, object]]:
    filters: list[tuple[str, object]] = []
    for item in tree.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in item.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not (
                isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "callback_query"
            ):
                continue
            for expression in decorator.args:
                if (
                    isinstance(expression, ast.Compare)
                    and isinstance(expression.left, ast.Attribute)
                    and isinstance(expression.left.value, ast.Name)
                    and expression.left.value.id == "F"
                    and expression.left.attr == "data"
                    and len(expression.ops) == 1
                    and isinstance(expression.ops[0], ast.Eq)
                    and isinstance(expression.comparators[0], ast.Constant)
                ):
                    filters.append(("equals", expression.comparators[0].value))
                    continue
                if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Attribute):
                    continue
                base = expression.func.value
                if not (
                    isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "F"
                    and base.attr == "data"
                    and expression.args
                    and isinstance(expression.args[0], ast.Constant)
                ):
                    continue
                if expression.func.attr in {"startswith", "regexp"}:
                    filters.append((expression.func.attr, expression.args[0].value))
    return filters


def _matches(sample: str, callback_filter: tuple[str, object]) -> bool:
    kind, value = callback_filter
    if kind == "equals":
        return sample == value
    if kind == "startswith":
        return sample.startswith(str(value))
    if kind == "regexp":
        return re.search(str(value), sample) is not None
    return False


def test_every_inline_button_callback_has_a_registered_handler_and_fits_telegram_limit() -> None:
    router_trees = [
        ast.parse(path.read_text(encoding="utf-8"))
        for path in ACTIVE_ROUTER_PATHS
    ]
    filters = [
        callback_filter
        for tree in router_trees
        for callback_filter in _callback_filters(tree)
    ]
    callbacks: list[tuple[str, str, int]] = []

    for path in ACTIVE_ROUTER_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if not (
                isinstance(function, ast.Name)
                and function.id == "InlineKeyboardButton"
            ):
                continue
            for keyword in node.keywords:
                if keyword.arg == "callback_data":
                    callbacks.append((str(path), _sample_callback(keyword.value, path.name), node.lineno))

    assert callbacks, "No inline keyboard callback buttons were found."
    unmatched = [
        (path, sample, line)
        for path, sample, line in callbacks
        if not any(_matches(sample, callback_filter) for callback_filter in filters)
    ]
    oversized = [
        (path, sample, len(sample.encode("utf-8")), line)
        for path, sample, line in callbacks
        if len(sample.encode("utf-8")) > 64
    ]

    assert not unmatched, f"Buttons without a callback handler: {unmatched}"
    assert not oversized, f"Telegram callback_data exceeds 64 bytes: {oversized}"


def test_class_list_button_routes_to_the_class_detail_handler() -> None:
    tree = ast.parse(PRODUCT_ROUTER_PATH.read_text(encoding="utf-8"))
    filters = _callback_filters(tree)
    assert any(
        kind == "regexp" and str(value) == r"^product:class:[0-9]+$"
        for kind, value in filters
    )
    class_button = "product:class:123"
    assert any(_matches(class_button, callback_filter) for callback_filter in filters)



def test_member_suspend_callback_stays_within_telegram_limit() -> None:
    from uuid import UUID

    from app.bot.router import _member_suspend_callback_data

    callback_data = _member_suspend_callback_data(
        "AbCdEf12",
        UUID("01234567-89ab-cdef-0123-456789abcdef"),
    )

    assert callback_data == "m:s:start:AbCdEf12:0123456789abcdef0123456789abcdef"
    assert len(callback_data.encode("utf-8")) <= 64
