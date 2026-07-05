import pandas as pd

from src.data.technical_features import build_technical_snapshot


def make_trend_df(kind: str, days: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2026-03-16", periods=days, freq="B")
    rows = []
    for i, date in enumerate(dates):
        if kind == "up":
            close = 20 + i * 0.08
            volume = 900_000 + i * 8_000
        elif kind == "down":
            close = 28 - i * 0.09
            volume = 900_000 + i * 4_000
        else:
            close = 22 + (0.05 if i % 2 == 0 else -0.05)
            volume = 2_000_000 - i * 15_000
        rows.append({
            "date": date,
            "open": close - 0.08,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": volume,
        })
    df = pd.DataFrame(rows).set_index("date")
    if kind == "down":
        df.iloc[-1, df.columns.get_loc("close")] -= 0.4
        df.iloc[-1, df.columns.get_loc("volume")] = df["volume"].tail(20).mean() * 1.8
    return df


def ok_quality(df):
    return {
        "status": "ok",
        "score": 1.0,
        "issues": [],
        "trading_days": len(df),
        "latest_date": df.index[-1].date().isoformat(),
        "missing_columns": [],
    }


def test_technical_snapshot_identifies_uptrend():
    df = make_trend_df("up")
    snapshot = build_technical_snapshot(
        df,
        indicators={"RSI": 62, "MACD_signal": "bullish_holding", "MA20": 24.7, "MA60": 22.9},
        data_quality=ok_quality(df),
        symbol="002396",
        name="星网锐捷",
        market="A",
    )

    assert snapshot["trend_regime"]["short_term"] == "up"
    assert snapshot["momentum_signals"]["state"] == "bullish"
    assert snapshot["support_resistance"]["nearest_support"] is not None
    assert snapshot["confidence_model"]["technical_confidence"] > 0
    assert "短期趋势向上" in snapshot["evidence"]["bullish"]


def test_technical_snapshot_keeps_shrinking_range_neutral():
    df = make_trend_df("flat")
    snapshot = build_technical_snapshot(
        df,
        indicators={"RSI": 50, "MACD_signal": "bearish_holding", "MA20": 22.0, "MA60": 22.0},
        data_quality=ok_quality(df),
        symbol="002396",
        name="星网锐捷",
        market="A",
    )

    assert snapshot["trend_regime"]["short_term"] == "sideways"
    assert snapshot["volume_signals"]["volume_trend"] == "shrinking"
    assert snapshot["confidence_model"]["suggested_direction"] == "neutral"
    assert "成交量收缩，方向确认度不足" in snapshot["evidence"]["neutral"]


def test_technical_snapshot_flags_down_volume_pressure():
    df = make_trend_df("down")
    snapshot = build_technical_snapshot(
        df,
        indicators={"RSI": 38, "MACD_signal": "bearish_holding", "MA20": 22.0, "MA60": 24.0},
        data_quality=ok_quality(df),
        symbol="002396",
        name="星网锐捷",
        market="A",
    )

    assert snapshot["trend_regime"]["short_term"] == "down"
    assert snapshot["volume_signals"]["price_down_volume_up"] is True
    assert snapshot["confidence_model"]["suggested_direction"] == "bearish"
    assert "下跌伴随放量" in snapshot["evidence"]["bearish"]
