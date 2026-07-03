"""
股价数据获取器

获取指定标的的历史 K 线数据，计算常用技术指标，
打包为 Agent 可直接消费的字典格式。

支持市场：A股（akshare）、港股（akshare）、美股（yfinance）
"""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
import logging

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ================================================================
# 数据结构
# ================================================================


@dataclass
class PriceData:
    """股价 + 技术指标数据封装"""
    symbol: str
    market: str            # "A" / "HK" / "US"
    data_period: str       # 数据时间范围描述
    trading_days: int      # 有效交易日数
    price_current: float   # 最新收盘价
    price_20d_high: float  # 20日最高
    price_20d_low: float   # 20日最低
    change_5d_pct: float   # 5日涨跌幅(%)
    change_20d_pct: float  # 20日涨跌幅(%)
    indicators: dict       # 技术指标字典
    patterns: dict         # 形态描述
    recent_closes: list    # 最近10个交易日收盘价

    def to_agent_dict(self) -> dict:
        """输出给 Agent 的字典格式"""
        return {
            "symbol": self.symbol,
            "market": self.market,
            "data_period": self.data_period,
            "trading_days": self.trading_days,
            "price_summary": {
                "latest_close": round(self.price_current, 2),
                "period_20d_high": round(self.price_20d_high, 2),
                "period_20d_low": round(self.price_20d_low, 2),
                "change_5d_pct": round(self.change_5d_pct, 2),
                "change_20d_pct": round(self.change_20d_pct, 2),
            },
            "indicators": {k: self._format_value(v) for k, v in self.indicators.items()},
            "patterns": self.patterns,
            "recent_closes": [round(c, 2) for c in self.recent_closes],
        }

    @staticmethod
    def _format_value(v):
        """格式化值为可读形式"""
        if isinstance(v, float):
            return round(v, 4)
        return v


# ================================================================
# 价格获取器
# ================================================================


class PriceFetcher:
    """股价 + 技术指标数据获取器"""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir

    async def fetch(self, symbol: str, period: str = "3mo") -> PriceData:
        """获取股价数据并计算技术指标

        Args:
            symbol: 股票代码。A股: 6位数字如"000001"，港股: 4位数字如"0700"（不带.HK也能识别），
                    也可带后缀如"0700.HK"、"AAPL"
            period: 数据长度，1mo/3mo/6mo/1y/2y

        Returns:
            PriceData 对象
        """
        # 清洗 symbol
        symbol = symbol.strip().upper()
        original_symbol = symbol

        # 识别市场
        market = self._identify_market(symbol)

        # 获取原始 K 线
        logger.info(f"获取 {symbol} ({market}) 的 K 线数据，周期={period}")
        df = self._fetch_ohlcv(symbol, market, period)

        if df.empty:
            raise ValueError(f"无法获取 {original_symbol} 的行情数据")

        # 计算技术指标（含新增）
        indicators = self._compute_indicators(df)

        # 识别形态
        patterns = self._identify_patterns(df, indicators)

        # 🆕 Round1: 计算高级指标
        advanced = self._compute_advanced_indicators(df)
        indicators.update(advanced)

        # 🆕 Round1: K线形态识别
        candlestick_patterns = self._detect_candlestick_patterns(df)
        patterns["candlestick"] = candlestick_patterns

        # 🆕 Round1: 获取周线背景
        weekly_context = await self._fetch_weekly_context(symbol, market)
        if weekly_context:
            patterns["weekly_context"] = weekly_context

        # 最近 10 个交易日收盘价
        recent_closes = df["close"].tail(10).tolist()

        # 组装结果
        close = df["close"]
        high_20 = df["high"].tail(20)
        low_20 = df["low"].tail(20)

        chg_5d = 0.0
        if len(close) >= 6:
            chg_5d = (close.iloc[-1] / close.iloc[-6] - 1) * 100

        chg_20d = 0.0
        if len(close) >= 21:
            chg_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100

        return PriceData(
            symbol=original_symbol,
            market=market,
            data_period=f"近{len(df)}个交易日",
            trading_days=len(df),
            price_current=float(close.iloc[-1]),
            price_20d_high=float(high_20.max()),
            price_20d_low=float(low_20.min()),
            change_5d_pct=round(chg_5d, 2),
            change_20d_pct=round(chg_20d, 2),
            indicators=indicators,
            patterns=patterns,
            recent_closes=recent_closes,
        )

    # ================================================================
    # 市场识别
    # ================================================================

    def _identify_market(self, symbol: str) -> str:
        """识别股票所属市场"""
        symbol = symbol.replace(".HK", "").replace(".SZ", "").replace(".SS", "")

        if symbol.isdigit():
            if len(symbol) <= 5:
                # 4-5位数字，港股（如 0700, 9988）
                return "HK"
            else:
                # 6位数字，A股
                return "A"
        else:
            # 含字母，美股或其他
            return "US"

    # ================================================================
    # 数据获取
    # ================================================================

    def _fetch_ohlcv(self, symbol: str, market: str, period: str) -> pd.DataFrame:
        """获取 K 线数据"""
        if market == "A":
            return self._fetch_a_share(symbol, period)
        elif market == "HK":
            return self._fetch_hk_share(symbol, period)
        else:
            return self._fetch_us_share(symbol, period)

    def _fetch_a_share(self, symbol: str, period: str) -> pd.DataFrame:
        """获取 A 股日 K 线

        优先使用 stock_zh_a_daily（更稳定），
        降级到 stock_zh_a_hist。
        """
        import akshare as ak

        # A 股代码需要加交易所前缀
        prefixed = self._a_share_prefix(symbol)

        start_date = self._start_date(period)

        # === 方案 1: stock_zh_a_daily（更稳定，返回全量日线）===
        try:
            df = ak.stock_zh_a_daily(
                symbol=prefixed,
                adjust="qfq",
            )
            if df is not None and not df.empty:
                df = self._normalize_dataframe(df)
                # 按日期过滤
                if "date" not in df.columns and df.index.name != "date":
                    pass  # _normalize_dataframe 已设置 date 索引
                start_dt = pd.to_datetime(start_date)
                if isinstance(df.index, pd.DatetimeIndex):
                    df = df[df.index >= start_dt]
                elif "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    df = df[df["date"] >= start_dt]
                if not df.empty:
                    logger.info(f"stock_zh_a_daily 获取 {len(df)} 条数据")
                    return df
        except Exception as e:
            logger.debug(f"stock_zh_a_daily 失败: {e}")

        # === 方案 2: stock_zh_a_hist（降级） ===
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date="2099-12-31",
                adjust="qfq",
            )
            if df is not None and not df.empty:
                logger.info(f"stock_zh_a_hist 获取 {len(df)} 条数据")
                return self._normalize_dataframe(df)
        except Exception as e:
            logger.debug(f"stock_zh_a_hist 失败: {e}")

        raise ValueError(
            f"无法获取 {symbol} 的 A 股行情数据，"
            f"请检查股票代码是否正确或网络是否可达"
        )

    @staticmethod
    def _a_share_prefix(symbol: str) -> str:
        """给 A 股代码加交易所前缀

        深交所: 000xxx, 001xxx, 002xxx, 003xxx, 300xxx, 301xxx → sz
        上交所: 600xxx, 601xxx, 603xxx, 605xxx, 688xxx → sh
        """
        code = symbol.zfill(6)
        if code.startswith(("000", "001", "002", "003", "300", "301")):
            return f"sz{code}"
        elif code.startswith(("600", "601", "603", "605", "688")):
            return f"sh{code}"
        else:
            # 默认尝试深圳
            return f"sz{code}"

    def _fetch_hk_share(self, symbol: str, period: str) -> pd.DataFrame:
        """获取港股日 K 线

        腾讯API(免费实时) → akshare → yfinance
        """
        hk_code = symbol.zfill(5)

        # === 方案 1: 腾讯行情 API（免费、稳定） ===
        df = self._fetch_from_tencent(hk_code, market="hk")
        if df is not None and not df.empty:
            # 按日期过滤到指定 period
            start_date = self._start_date(period)
            start_dt = pd.to_datetime(start_date)
            if isinstance(df.index, pd.DatetimeIndex):
                df = df[df.index >= start_dt]
            if not df.empty:
                logger.info(f"腾讯API 获取港股 {len(df)} 条K线")
                return df

        # === 方案 2: akshare ===
        try:
            import akshare as ak
            df = ak.stock_hk_hist(
                symbol=symbol, period="daily",
                start_date=self._start_date(period),
                end_date="2099-12-31", adjust="qfq",
            )
            if df is not None and not df.empty:
                logger.info(f"akshare 获取港股 {len(df)} 条")
                return self._normalize_dataframe(df)
        except Exception as e:
            logger.debug(f"港股 akshare 失败: {e}")

        # === 方案 3: yfinance（带延迟防限流）===
        import time
        time.sleep(2)
        try:
            return self._fetch_yfinance(symbol + ".HK", period)
        except Exception as e:
            logger.debug(f"港股 yfinance 失败: {e}")

        raise ValueError(f"所有数据源均无法获取港股 {symbol} 的行情")

    def _fetch_us_share(self, symbol: str, period: str) -> pd.DataFrame:
        """获取美股日 K 线

        腾讯API(中概股) → yfinance
        """
        # 方案 1: 腾讯（中概股可能支持）
        df = self._fetch_from_tencent(symbol, market="us")
        if df is not None and not df.empty:
            start_date = self._start_date(period)
            start_dt = pd.to_datetime(start_date)
            if isinstance(df.index, pd.DatetimeIndex):
                df = df[df.index >= start_dt]
            if not df.empty:
                logger.info(f"腾讯API 获取美股 {len(df)} 条K线")
                return df

        # 方案 2: yfinance
        import time
        time.sleep(2)
        return self._fetch_yfinance(symbol, period)

    # ================================================================
    # 腾讯行情 API（支持 A股/港股/部分美股）
    # ================================================================

    def _fetch_from_tencent(self, code: str, market: str = "hk") -> Optional[pd.DataFrame]:
        """从腾讯行情 API 获取日K线数据

        API: https://web.ifzq.gtimg.cn/appstock/app/fqkline/get
        免费、无需 API Key、支持前复权
        """
        try:
            import requests

            # 确定腾讯 API 的股票代码格式
            if market == "hk":
                qt_code = f"hk{code}"
            elif market == "us":
                qt_code = code.lower()
            else:
                qt_code = code

            # 获取日K线（前复权，取最近 200 个交易日）
            url = (
                f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                f"?param={qt_code},day,,,200,qfq"
            )
            resp = requests.get(url, timeout=15, verify=False)
            data = resp.json()

            if data.get("code") != 0:
                return None

            # 解析K线数据
            stock_data = data.get("data", {}).get(qt_code, {})
            klines = stock_data.get("qfqday") or stock_data.get("day") or []

            if not klines:
                return None

            rows = []
            for item in klines:
                parts = item if isinstance(item, list) else item.split()
                if len(parts) >= 6:
                    rows.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                    })

            if not rows:
                return None

            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return df

        except Exception as e:
            logger.debug(f"腾讯K线API失败({market} {code}): {e}")
            return None

    def _fetch_yfinance(self, symbol: str, period: str) -> pd.DataFrame:
        """通过 yfinance 获取数据（带重试+退避）"""
        import time
        import yfinance as yf

        last_error = None
        for attempt in range(3):
            try:
                ticker = yf.Ticker(symbol)
                df = ticker.history(period=period)

                if df.empty:
                    raise ValueError(f"yfinance 返回空数据: {symbol}")

                df = df.rename(columns={
                    "Open": "open", "High": "high",
                    "Low": "low", "Close": "close",
                    "Volume": "volume",
                })
                return self._normalize_dataframe(df)

            except ImportError:
                raise
            except Exception as e:
                last_error = e
                if "Rate limited" in str(e) or "Too Many Requests" in str(e):
                    wait = 5 * (2 ** attempt)
                    logger.debug(f"yfinance 限流，{wait}s 后重试 ({attempt+1}/3)...")
                    time.sleep(wait)
                elif attempt < 2:
                    time.sleep(2)
                else:
                    raise

        raise last_error or RuntimeError(f"yfinance 获取 {symbol} 失败")

    # ================================================================
    # 数据处理
    # ================================================================

    def _normalize_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 DataFrame 列名"""
        # 统一列名（不同数据源的列名不同）
        col_mapping = {
            "日期": "date", "Date": "date",
            "开盘": "open", "Open": "open",
            "收盘": "close", "Close": "close",
            "最高": "high", "High": "high",
            "最低": "low", "Low": "low",
            "成交量": "volume", "Volume": "volume",
            "成交额": "amount",
        }

        df = df.rename(columns=col_mapping)

        # 确保必需列存在
        required = ["close", "high", "low"]
        for col in required:
            if col not in df.columns:
                raise ValueError(f"数据缺少必需列: {col}")

        # 设置日期索引
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")

        # 确保 volume 列存在
        if "volume" not in df.columns:
            df["volume"] = 0

        # 按日期排序
        df = df.sort_index()

        return df

    def _start_date(self, period: str) -> str:
        """根据 period 返回起始日期"""
        from datetime import datetime, timedelta
        now = datetime.now()
        mapping = {
            "1mo": now - timedelta(days=45),
            "3mo": now - timedelta(days=120),
            "6mo": now - timedelta(days=220),
            "1y": now - timedelta(days=500),
            "2y": now - timedelta(days=900),
        }
        start = mapping.get(period, now - timedelta(days=120))
        return start.strftime("%Y%m%d")

    # ================================================================
    # 技术指标计算
    # ================================================================

    def _compute_indicators(self, df: pd.DataFrame) -> dict:
        """计算常用技术指标（纯 pandas 实现，无外部依赖）"""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df.get("volume", pd.Series([0] * len(df), index=df.index))

        indicators = {}

        # === 均线 ===
        for period in [5, 10, 20, 60]:
            ma = close.rolling(window=period).mean()
            if len(ma) >= period and not ma.empty:
                indicators[f"MA{period}"] = float(ma.iloc[-1])
                if len(ma) >= 2 and pd.notna(ma.iloc[-1]) and pd.notna(ma.iloc[-2]):
                    indicators[f"MA{period}_trend"] = "up" if ma.iloc[-1] > ma.iloc[-2] else "down"
                else:
                    indicators[f"MA{period}_trend"] = "unknown"
            else:
                indicators[f"MA{period}"] = None
                indicators[f"MA{period}_trend"] = "insufficient_data"

        # === MACD (12, 26, 9) ===
        if len(close) >= 26:
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9, adjust=False).mean()
            macd_bar = 2 * (dif - dea)

            indicators["MACD_DIF"] = float(dif.iloc[-1])
            indicators["MACD_DEA"] = float(dea.iloc[-1])
            indicators["MACD_BAR"] = float(macd_bar.iloc[-1])

            # MACD 信号
            if len(dif) >= 2:
                if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]:
                    indicators["MACD_signal"] = "golden_cross"
                elif dif.iloc[-1] < dea.iloc[-1] and dif.iloc[-2] >= dea.iloc[-2]:
                    indicators["MACD_signal"] = "death_cross"
                elif dif.iloc[-1] > dea.iloc[-1]:
                    indicators["MACD_signal"] = "bullish_holding"
                else:
                    indicators["MACD_signal"] = "bearish_holding"
            else:
                indicators["MACD_signal"] = "insufficient_data"
        else:
            for key in ["MACD_DIF", "MACD_DEA", "MACD_BAR"]:
                indicators[key] = None
            indicators["MACD_signal"] = "insufficient_data"

        # === RSI(14) ===
        if len(close) >= 15:
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta.where(delta < 0, 0.0))
            avg_gain = gain.rolling(window=14).mean()
            avg_loss = loss.rolling(window=14).mean()

            # 使用 Wilder's smoothing（后续窗口）
            for i in range(14, len(avg_gain)):
                avg_gain.iloc[i] = (avg_gain.iloc[i-1] * 13 + gain.iloc[i]) / 14
                avg_loss.iloc[i] = (avg_loss.iloc[i-1] * 13 + loss.iloc[i]) / 14

            rs = avg_gain / avg_loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            indicators["RSI"] = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None
        else:
            indicators["RSI"] = None

        # === 布林带 (20, 2) ===
        if len(close) >= 20:
            ma20 = close.rolling(window=20).mean()
            std20 = close.rolling(window=20).std()
            indicators["BOLL_upper"] = float(ma20.iloc[-1] + 2 * std20.iloc[-1])
            indicators["BOLL_mid"] = float(ma20.iloc[-1])
            indicators["BOLL_lower"] = float(ma20.iloc[-1] - 2 * std20.iloc[-1])
        else:
            for key in ["BOLL_upper", "BOLL_mid", "BOLL_lower"]:
                indicators[key] = None

        # === 成交量 ===
        if len(volume) >= 5 and volume.iloc[-5:].mean() > 0:
            vol_ma5 = volume.rolling(window=5).mean()
            indicators["VOL_ratio"] = float(volume.iloc[-1] / vol_ma5.iloc[-1])
        else:
            indicators["VOL_ratio"] = 1.0

        return indicators

    # ================================================================
    # 形态识别
    # ================================================================

    def _identify_patterns(self, df: pd.DataFrame, indicators: dict) -> dict:
        """识别价格形态和特征"""
        patterns = {}
        close = df["close"]

        # === 均线排列 ===
        ma_values = []
        for p in [5, 10, 20, 60]:
            val = indicators.get(f"MA{p}")
            if val is not None:
                ma_values.append((p, val))

        if len(ma_values) >= 2:
            sorted_ma = sorted(ma_values, key=lambda x: x[1], reverse=True)
            if sorted_ma == sorted(ma_values, key=lambda x: x[0]):
                patterns["ma_arrangement"] = "多头排列（短期>长期），看涨"
            elif sorted_ma == sorted(ma_values, key=lambda x: x[0], reverse=True):
                patterns["ma_arrangement"] = "空头排列（短期<长期），看跌"
            else:
                patterns["ma_arrangement"] = "均线缠绕，方向不明"

        # === 价格与均线关系 ===
        if len(close) >= 1:
            latest = close.iloc[-1]
            above = []
            below = []
            for p in [5, 10, 20, 60]:
                val = indicators.get(f"MA{p}")
                if val is not None:
                    if latest > val:
                        above.append(f"MA{p}")
                    else:
                        below.append(f"MA{p}")

            patterns["price_vs_ma"] = (
                f"价格站上 {','.join(above)}，" if above else "价格在所有短期均线下方，"
            ) + (f"低于 {','.join(below)}" if below else "站上所有均线")

        # === RSI 区间 ===
        rsi = indicators.get("RSI")
        if rsi is not None:
            if rsi > 80:
                patterns["rsi_zone"] = "严重超买(>80)，回调风险大"
            elif rsi > 70:
                patterns["rsi_zone"] = "超买区(70-80)，注意回调"
            elif rsi > 50:
                patterns["rsi_zone"] = "中性偏强(50-70)"
            elif rsi > 30:
                patterns["rsi_zone"] = "中性偏弱(30-50)"
            elif rsi > 20:
                patterns["rsi_zone"] = "超卖区(20-30)，反弹机会"
            else:
                patterns["rsi_zone"] = "严重超卖(<20)，反弹概率大"

        # === 布林带位置 ===
        boll_upper = indicators.get("BOLL_upper")
        boll_mid = indicators.get("BOLL_mid")
        boll_lower = indicators.get("BOLL_lower")
        if all(v is not None for v in [boll_upper, boll_mid, boll_lower]):
            latest = close.iloc[-1]
            if latest >= boll_upper:
                patterns["boll_position"] = "价格触及/突破上轨，短期可能承压"
            elif latest <= boll_lower:
                patterns["boll_position"] = "价格触及/跌破下轨，短期可能获支撑"
            elif latest > boll_mid:
                patterns["boll_position"] = "价格在中轨与上轨之间，偏强运行"
            else:
                patterns["boll_position"] = "价格在中轨与下轨之间，偏弱运行"

        # === 金叉/死叉 ===
        macd_signal = indicators.get("MACD_signal")
        if macd_signal == "golden_cross":
            patterns["macd_event"] = "⚠️ MACD 刚刚金叉，短期看涨信号"
        elif macd_signal == "death_cross":
            patterns["macd_event"] = "⚠️ MACD 刚刚死叉，短期看跌信号"

        return patterns

    # ================================================================
    # 🆕 Round1: 高级技术指标
    # ================================================================

    def _compute_advanced_indicators(self, df: pd.DataFrame) -> dict:
        """计算高级技术指标: ATR, OBV, KDJ, ADX"""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df.get("volume", pd.Series([0] * len(df), index=df.index))

        adv = {}

        # === ATR(14) — 真实波幅，衡量波动率 ===
        if len(close) >= 15:
            tr = pd.DataFrame({
                "hl": high - low,
                "hc": abs(high - close.shift(1)),
                "lc": abs(low - close.shift(1)),
            }).max(axis=1)
            atr = tr.rolling(14).mean()
            adv["ATR"] = round(float(atr.iloc[-1]), 4)
            adv["ATR_pct"] = round(float(atr.iloc[-1] / close.iloc[-1] * 100), 2)
            # ATR趋势：扩大/缩小
            if len(atr) >= 5:
                adv["ATR_trend"] = "expanding" if atr.iloc[-1] > atr.iloc[-5] else "contracting"

        # === OBV — 能量潮，检测量价背离 ===
        if len(close) >= 2 and volume.sum() > 0:
            obv = [0]
            for i in range(1, len(close)):
                if close.iloc[i] > close.iloc[i-1]:
                    obv.append(obv[-1] + volume.iloc[i])
                elif close.iloc[i] < close.iloc[i-1]:
                    obv.append(obv[-1] - volume.iloc[i])
                else:
                    obv.append(obv[-1])
            obv_series = pd.Series(obv, index=close.index)
            obv_ma = obv_series.rolling(10).mean()
            adv["OBV_divergence"] = "none"
            if len(obv_series) >= 10:
                # 价格涨OBV跌 = 顶背离
                if close.iloc[-1] > close.iloc[-10] and obv_series.iloc[-1] < obv_series.iloc[-10]:
                    adv["OBV_divergence"] = "bearish_divergence"
                # 价格跌OBV涨 = 底背离
                elif close.iloc[-1] < close.iloc[-10] and obv_series.iloc[-1] > obv_series.iloc[-10]:
                    adv["OBV_divergence"] = "bullish_divergence"

        # === KDJ(9,3,3) — 随机指标，A股常用 ===
        if len(close) >= 9:
            low_9 = low.rolling(9).min()
            high_9 = high.rolling(9).max()
            rsv = (close - low_9) / (high_9 - low_9).replace(0, np.nan) * 100

            k = rsv.ewm(com=2, adjust=False).mean()
            d = k.ewm(com=2, adjust=False).mean()
            j = 3 * k - 2 * d

            adv["KDJ_K"] = round(float(k.iloc[-1]), 2)
            adv["KDJ_D"] = round(float(d.iloc[-1]), 2)
            adv["KDJ_J"] = round(float(j.iloc[-1]), 2)

            if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2]:
                adv["KDJ_signal"] = "golden_cross"
            elif k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2]:
                adv["KDJ_signal"] = "death_cross"
            elif j.iloc[-1] > 100:
                adv["KDJ_zone"] = "overbought"
            elif j.iloc[-1] < 0:
                adv["KDJ_zone"] = "oversold"
            elif k.iloc[-1] > 50:
                adv["KDJ_zone"] = "strong"
            else:
                adv["KDJ_zone"] = "weak"

        # === ADX(14) — 趋势强度，>25有趋势，<20震荡 ===
        if len(close) >= 30:
            tr = pd.DataFrame({
                "hl": high - low,
                "hc": abs(high - close.shift(1)),
                "lc": abs(low - close.shift(1)),
            }).max(axis=1)

            plus_dm = (high - high.shift(1)).where(
                (high - high.shift(1) > low.shift(1) - low) & (high - high.shift(1) > 0), 0.0
            )
            minus_dm = (low.shift(1) - low).where(
                (low.shift(1) - low > high - high.shift(1)) & (low.shift(1) - low > 0), 0.0
            )

            atr_14 = tr.rolling(14).mean()
            plus_di = 100 * (plus_dm.rolling(14).mean() / atr_14.replace(0, np.nan))
            minus_di = 100 * (minus_dm.rolling(14).mean() / atr_14.replace(0, np.nan))
            dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
            adx = dx.rolling(14).mean()

            adv["ADX"] = round(float(adx.iloc[-1]), 2)
            adv["ADX_plus_DI"] = round(float(plus_di.iloc[-1]), 2)
            adv["ADX_minus_DI"] = round(float(minus_di.iloc[-1]), 2)

            if adx.iloc[-1] > 40:
                adv["ADX_trend"] = "very_strong"
            elif adx.iloc[-1] > 25:
                adv["ADX_trend"] = "strong"
            elif adx.iloc[-1] > 20:
                adv["ADX_trend"] = "weak"
            else:
                adv["ADX_trend"] = "ranging"

            if plus_di.iloc[-1] > minus_di.iloc[-1]:
                adv["ADX_direction"] = "bullish"
            else:
                adv["ADX_direction"] = "bearish"

        return adv

    # ================================================================
    # 🆕 Round1: K线形态识别
    # ================================================================

    def _detect_candlestick_patterns(self, df: pd.DataFrame) -> list[str]:
        """识别最近K线的经典形态"""
        patterns = []
        if len(df) < 3:
            return patterns

        o, c, h, l = df["open"], df["close"], df["high"], df["low"]
        body = abs(c - o)
        upper_shadow = h - pd.concat([c, o], axis=1).max(axis=1)
        lower_shadow = pd.concat([c, o], axis=1).min(axis=1) - l

        # 取最近3根K线
        last = -1
        prev = -2

        # 锤子线 (Hammer)
        if (lower_shadow.iloc[last] > 2 * body.iloc[last] and
            upper_shadow.iloc[last] < body.iloc[last] * 0.3 and
            body.iloc[last] > 0):
            # 在下跌趋势中出现更有效
            if c.iloc[last] < c.iloc[-20] if len(c) >= 20 else True:
                patterns.append("hammer")
            else:
                patterns.append("hammer_weak")

        # 吞没形态 (Engulfing)
        if body.iloc[prev] > 0 and body.iloc[last] > 0:
            if (c.iloc[last] > o.iloc[prev] and o.iloc[last] < c.iloc[prev] and
                c.iloc[prev] < o.iloc[prev]):  # 前阴后阳
                patterns.append("bullish_engulfing")
            elif (c.iloc[last] < o.iloc[prev] and o.iloc[last] > c.iloc[prev] and
                  c.iloc[prev] > o.iloc[prev]):  # 前阳后阴
                patterns.append("bearish_engulfing")

        # 十字星 (Doji)
        if body.iloc[last] < (h.iloc[last] - l.iloc[last]) * 0.15:
            if upper_shadow.iloc[last] > body.iloc[last] * 3 and lower_shadow.iloc[last] > body.iloc[last] * 3:
                patterns.append("doji_cross")
            else:
                patterns.append("doji")

        return patterns

    # ================================================================
    # 🆕 Round1: 周线背景
    # ================================================================

    async def _fetch_weekly_context(self, symbol: str, market: str) -> Optional[dict]:
        """获取周线数据，提供中期趋势背景"""
        try:
            # 复用腾讯K线API取更长时间范围
            code = symbol.replace(".HK","").replace(".SZ","").replace(".SS","").zfill(5 if market=="HK" else 6)
            prefix = ""
            if market == "HK":
                prefix = "hk"
            elif market == "A":
                code_clean = code.zfill(6)
                prefix = "sz" if code_clean.startswith(("000","001","002","003","300","301")) else "sh"

            import requests
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},week,,,52,qfq"
            resp = requests.get(url, timeout=10, verify=False)
            data = resp.json()

            if data.get("code") != 0:
                return None

            stock_data = data.get("data", {}).get(f"{prefix}{code}", {})
            weeks = stock_data.get("qfqweek") or stock_data.get("week") or []

            if len(weeks) < 10:
                return None

            # 提取最近几周数据
            recent = []
            for item in weeks[-8:]:
                parts = item if isinstance(item, list) else item.split()
                if len(parts) >= 5:
                    recent.append({
                        "week": parts[0], "open": float(parts[1]),
                        "close": float(parts[2]), "high": float(parts[3]),
                        "low": float(parts[4]),
                    })

            if not recent:
                return None

            # 计算周线简单指标
            closes = [w["close"] for w in recent]
            curr = closes[-1]
            ma5_w = sum(closes[-5:]) / min(5, len(closes[-5:])) if len(closes) >= 5 else curr
            ma10_w = sum(closes[-8:]) / min(8, len(closes[-8:])) if len(closes) >= 8 else curr

            # 周线趋势
            if len(closes) >= 3:
                trend = "up" if closes[-1] > closes[-3] else "down"
            else:
                trend = "unknown"

            return {
                "weekly_closes": [round(c, 2) for c in closes[-6:]],
                "weekly_ma5": round(ma5_w, 2),
                "weekly_ma10": round(ma10_w, 2),
                "weekly_trend": trend,
                "weekly_change_pct": round((closes[-1]/closes[-2]-1)*100, 2) if len(closes)>=2 else 0,
                "price_vs_weekly_ma5": "above" if curr > ma5_w else "below",
            }

        except Exception as e:
            logger.debug(f"周线数据获取失败: {e}")
            return None
