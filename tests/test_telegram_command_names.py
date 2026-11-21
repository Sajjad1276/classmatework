import ast
import re
from pathlib import Path

_TELEGRAM_COMMAND = re.compile(r"^[a-z0-9_]{1,32}$")
_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _registered_commands(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    commands: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        function_name = (
            node.func.id if isinstance(node.func, ast.Name) else None
        )
        if function_name == "BotCommand":
            value = next(
                (
                    keyword.value.value
                    for keyword in node.keywords
                    if keyword.arg == "command"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            if value is not None:
                commands.append(value)
        elif (
            function_name == "Command"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            commands.append(node.args[0].value)

    return commands


def test_admin_bot_commands_follow_telegram_command_constraints() -> None:
    paths = [
        _SOURCE_ROOT / "backend/app/bot/main.py",
        _SOURCE_ROOT / "backend/app/bot/router.py",
        _SOURCE_ROOT / "backend/app/bot/academic_router.py",
    ]
    commands = [
        command
        for path in paths
        for command in _registered_commands(path)
    ]

    assert commands, "Expected at least one registered Telegram command."
    invalid = [command for command in commands if not _TELEGRAM_COMMAND.fullmatch(command)]
    assert not invalid, f"Invalid Telegram command names: {invalid}"
