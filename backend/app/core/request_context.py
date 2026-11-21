from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar(
    "classmatework_request_id",
    default=None,
)
