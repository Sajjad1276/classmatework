import ast
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_BOT_MODULES = (
    "backend/app/bot/main.py",
    "backend/app/bot/router.py",
    "backend/app/bot/product_router.py",
    "backend/app/bot/control_router.py",
    "backend/app/bot/academic_router.py",
)


def test_only_start_is_published_and_no_other_command_handlers_exist() -> None:
    published_commands: list[str] = []
    command_filters: list[tuple[str, int]] = []

    for relative_path in _BOT_MODULES:
        path = _SOURCE_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            function_name = (
                node.func.id if isinstance(node.func, ast.Name) else None
            )
            if function_name == "BotCommand":
                command = next(
                    (
                        keyword.value.value
                        for keyword in node.keywords
                        if keyword.arg == "command"
                        and isinstance(keyword.value, ast.Constant)
                        and isinstance(keyword.value.value, str)
                    ),
                    None,
                )
                if command is not None:
                    published_commands.append(command)
            elif function_name == "Command":
                command_filters.append((relative_path, node.lineno))

    assert published_commands == ["start"]
    assert command_filters == []
