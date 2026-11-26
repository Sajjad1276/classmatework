import ast
from pathlib import Path

PRODUCT_ROUTER = (
    Path(__file__).parents[1] / "backend" / "app" / "bot" / "product_router.py"
)


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing async handler: {name}")


def _is_callback_message_method(node: ast.AST, method: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "message"
    )


def _has_html_parse_mode(call: ast.Call) -> bool:
    return any(
        keyword.arg == "parse_mode"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "HTML"
        for keyword in call.keywords
    )


def _uses_html_escape(function: ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "escape"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "html"
        for node in ast.walk(function)
    )


def test_class_join_code_message_uses_html_parse_mode_and_escapes_class_name() -> None:
    tree = ast.parse(PRODUCT_ROUTER.read_text(encoding="utf-8"))
    function = _function(tree, "product_class_join_code_issue_callback")
    message_edits = [
        node
        for node in ast.walk(function)
        if _is_callback_message_method(node, "edit_text")
    ]

    assert message_edits
    assert any(_has_html_parse_mode(call) for call in message_edits)
    assert _uses_html_escape(function)


def test_class_creation_join_code_message_uses_html_parse_mode() -> None:
    tree = ast.parse(PRODUCT_ROUTER.read_text(encoding="utf-8"))
    function = _function(tree, "product_class_create_confirm_callback")
    message_edits = [
        node
        for node in ast.walk(function)
        if _is_callback_message_method(node, "edit_text")
    ]

    assert message_edits
    assert any(_has_html_parse_mode(call) for call in message_edits)
    assert _uses_html_escape(function)


def test_bulk_join_code_messages_use_html_parse_mode_for_edit_and_followups() -> None:
    tree = ast.parse(PRODUCT_ROUTER.read_text(encoding="utf-8"))
    function = _function(tree, "product_missing_class_join_codes_callback")
    message_edits = [
        node
        for node in ast.walk(function)
        if _is_callback_message_method(node, "edit_text")
    ]
    body_answers = [
        node
        for node in ast.walk(function)
        if _is_callback_message_method(node, "answer")
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "body"
    ]

    assert message_edits
    assert any(_has_html_parse_mode(call) for call in message_edits)
    assert body_answers
    assert all(_has_html_parse_mode(call) for call in body_answers)
    assert _uses_html_escape(function)
