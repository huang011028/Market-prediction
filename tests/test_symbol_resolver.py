from src.data.symbol_resolver import resolve_symbol, identify_market


def test_resolve_star_net_ruijie_to_a_share():
    info = resolve_symbol("星网锐捷")

    assert info.symbol == "002396"
    assert info.market == "A"
    assert info.name == "星网锐捷"
    assert "002396" in info.display_name


def test_resolve_a_share_suffix():
    info = resolve_symbol("002396.SZ")

    assert info.symbol == "002396"
    assert info.market == "A"


def test_resolve_a_share_common_short_name():
    info = resolve_symbol("美的")

    assert info.symbol == "000333"
    assert info.market == "A"
    assert info.name == "美的"


def test_resolve_a_share_common_broker_alias():
    info = resolve_symbol("中信", market_hint="A")

    assert info.symbol == "600030"
    assert info.market == "A"
    assert info.source == "hint_a"


def test_resolve_hk_common_citic_alias():
    info = resolve_symbol("中信", market_hint="HK")

    assert info.symbol == "00267"
    assert info.market == "HK"
    assert info.source == "hint_hk"


def test_resolve_a_share_fuzzy_name_from_market_table(monkeypatch):
    from src.data import symbol_resolver

    monkeypatch.setattr(
        symbol_resolver,
        "_a_share_name_table",
        lambda: {
            "招商银行": ("600036", "招商银行"),
            "招商证券": ("600999", "招商证券"),
        },
    )

    info = resolve_symbol("招商证", market_hint="A")

    assert info.symbol == "600999"
    assert info.market == "A"
    assert info.name == "招商证券"
    assert info.source == "akshare"


def test_resolve_a_share_ambiguous_short_name_stays_unresolved(monkeypatch):
    from src.data import symbol_resolver

    monkeypatch.setattr(
        symbol_resolver,
        "_a_share_name_table",
        lambda: {
            "招商银行": ("600036", "招商银行"),
            "招商证券": ("600999", "招商证券"),
        },
    )

    info = resolve_symbol("招商", market_hint="A")

    assert info.symbol == "招商"
    assert info.market == "A"
    assert info.source == "hint_a"


def test_identify_common_markets():
    assert identify_market("000001") == "A"
    assert identify_market("0700") == "HK"
    assert identify_market("AAPL") == "US"


def test_market_hint_overrides_unsuffixed_numeric_code():
    hk_info = resolve_symbol("700", market_hint="HK")
    a_info = resolve_symbol("0700", market_hint="A")

    assert hk_info.symbol == "0700"
    assert hk_info.market == "HK"
    assert a_info.symbol == "000700"
    assert a_info.market == "A"


def test_market_hint_keeps_us_ticker_in_us_mode(monkeypatch):
    from src.data import symbol_resolver

    def fail_lookup():
        raise AssertionError("US hint should not query A-share name table")

    monkeypatch.setattr(symbol_resolver, "_a_share_name_table", fail_lookup)

    info = resolve_symbol("000333", market_hint="US")

    assert info.symbol == "000333"
    assert info.market == "US"
    assert info.source == "hint_us"


def test_us_ticker_does_not_load_a_share_name_table(monkeypatch):
    from src.data import symbol_resolver

    def fail_lookup():
        raise AssertionError("US ticker should not query A-share name table")

    monkeypatch.setattr(symbol_resolver, "_a_share_name_table", fail_lookup)

    info = resolve_symbol("NVDA")

    assert info.symbol == "NVDA"
    assert info.market == "US"
    assert info.source == "fallback_us"
