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


def test_identify_common_markets():
    assert identify_market("000001") == "A"
    assert identify_market("0700") == "HK"
    assert identify_market("AAPL") == "US"
