def test_academic_bot_router_imports_with_tenant_workflows() -> None:
    from app.bot.academic_router import router

    assert router.name == "academic-structure"
    assert len(router.callback_query.handlers) >= 1
