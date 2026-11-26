import ast
from pathlib import Path

PRODUCT_ROUTER = (
    Path(__file__).parents[1] / "backend" / "app" / "bot" / "product_router.py"
)


def _tree() -> ast.Module:
    return ast.parse(PRODUCT_ROUTER.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Missing function: {name}")


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


def _calls(function: ast.AST, name: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(function)
    )


def test_class_join_code_message_uses_html_and_stable_code_lookup() -> None:
    tree = _tree()
    function = _function(tree, "product_class_join_code_issue_callback")
    message_edits = [
        node
        for node in ast.walk(function)
        if _is_callback_message_method(node, "edit_text")
    ]

    assert message_edits
    assert any(_has_html_parse_mode(call) for call in message_edits)
    assert _calls(function, "get_class_join_code_secret")
    assert _calls(function, "compare_digest")
    assert "saved_secret" in ast.unparse(function)
    assert "join_code" in ast.unparse(function)
    assert "saved_code_is_current" in ast.unparse(function)


def test_class_creation_stores_the_first_stable_code() -> None:
    tree = _tree()
    function = _function(tree, "product_class_create_confirm_callback")
    message_edits = [
        node
        for node in ast.walk(function)
        if _is_callback_message_method(node, "edit_text")
    ]

    assert message_edits
    assert any(_has_html_parse_mode(call) for call in message_edits)
    assert _calls(function, "store_class_join_code_secret")
    assert _calls(function, "_class_join_code_message")


def test_bulk_backfill_persists_codes_and_renders_html_after_confirmation() -> None:
    tree = _tree()
    confirm_function = _function(tree, "product_missing_class_join_codes_confirm_callback")
    render_function = _function(tree, "_render_bulk_join_code_result")
    message_edits = [
        node
        for node in ast.walk(render_function)
        if _is_callback_message_method(node, "edit_text")
    ]
    body_answers = [
        node
        for node in ast.walk(render_function)
        if _is_callback_message_method(node, "answer")
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "body"
    ]

    assert message_edits
    assert any(_has_html_parse_mode(call) for call in message_edits)
    assert body_answers
    assert all(_has_html_parse_mode(call) for call in body_answers)
    assert _calls(confirm_function, "store_class_join_code_secret")
    assert _calls(render_function, "_class_join_code_summary")


def test_rotation_is_separate_and_replaces_the_product_hash_and_saved_secret_after_confirmation() -> None:
    tree = _tree()
    preview_function = _function(tree, "product_class_join_code_rotate_callback")
    confirm_function = _function(tree, "product_class_join_code_rotate_confirm_callback")
    save_function = _function(tree, "_save_new_member_join_code")
    preview_literals = " ".join(
        node.value
        for node in ast.walk(preview_function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    confirm_source = ast.unparse(confirm_function)

    assert _calls(save_function, "issue_product_class_join_code")
    assert _calls(save_function, "store_class_join_code_secret")
    assert "product.class.member_join_code_rotated" in confirm_source
    assert "تغییر کد ورود کلاس" in preview_literals
    assert "کد فعلی و لینک‌های عضویت قبلی فوراً نامعتبر می‌شوند" in preview_literals
    assert "product:class:join-code:rotate:" in PRODUCT_ROUTER.read_text(encoding="utf-8")


def test_join_message_has_start_link_and_manual_code_fallback() -> None:
    tree = _tree()
    message_function = _function(tree, "_class_join_code_message")
    url_function = _function(tree, "_class_join_start_url")
    message_source = ast.unparse(message_function)
    url_source = ast.unparse(url_function)

    assert "?start=join_" in url_source
    assert "عضویت با کد" in message_source
    assert "CLASSMATE_BOT_USERNAME" in message_source
    assert _calls(message_function, "escape")


def test_class_details_show_code_button_without_rotation_label() -> None:
    source = PRODUCT_ROUTER.read_text(encoding="utf-8")
    assert 'text="🔑 کد ورود به کلاس"' in source
    assert 'text="♻️ تغییر کد ورود"' in source
    assert 'text="♻️ تغییر کد ورود کلاس"' not in source


def test_join_code_secret_migration_is_chained_after_0012() -> None:
    migration = (
        Path(__file__).parents[1]
        / "backend"
        / "alembic"
        / "versions"
        / "0013_class_join_code_secrets.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "0013_class_join_code_secrets"' in migration
    assert 'down_revision: Union[str, None] = "0012_product_record_review"' in migration
    assert '"class_join_code_secrets"' in migration
