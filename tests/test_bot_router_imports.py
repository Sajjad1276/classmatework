def test_product_management_router_imports() -> None:
    from app.bot.product_router import router

    assert router.name == "product-management"
    assert len(router.message.handlers) >= 1
    assert len(router.callback_query.handlers) >= 1
