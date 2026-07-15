"""
行业对比数据获取器 v2

获取标的所属行业的平均估值、盈利能力等对比数据，
判断标的在行业中的相对位置。

v2 改进:
- 东方财富行业板块API替代硬编码常量
- 扩展行业分类映射（100+标的）
- 行业分类缓存
- 港股行业映射
- 趋势数据获取
- 集成预处理管线
- 结构化输出
"""

from dataclasses import dataclass, field
from typing import Optional
import logging

from src.data import us_fallbacks
from src.data.industry_preprocessor import (
    IndustryClassifierCache,
    IndustryReferenceCache,
    EXTENDED_KNOWN_INDUSTRIES,
    KNOWN_HK_INDUSTRIES,
    HK_PEER_REFERENCE,
    infer_industry_from_name,
    process_industry_data,
)

logger = logging.getLogger(__name__)


A_SHARE_BOARD_ALIASES: dict[str, list[str]] = {
    "白酒": ["酿酒行业", "食品饮料"],
    "新能源": ["电池", "光伏设备", "风电设备", "能源金属"],
    "医药": ["化学制药", "生物制品", "医药商业", "医疗器械", "中药"],
    "家电": ["家电行业", "消费电子"],
    "电子": ["电子元件", "消费电子", "光学光电子"],
    "半导体": ["半导体"],
    "计算机": ["软件开发", "计算机设备", "互联网服务"],
    "通信": ["通信设备", "通信服务"],
    "证券": ["证券"],
    "保险": ["保险"],
    "银行": ["银行"],
    "房地产": ["房地产开发", "房地产服务"],
    "电力": ["电力行业"],
    "有色金属": ["有色金属", "小金属", "贵金属"],
    "水泥": ["水泥建材"],
    "食品饮料": ["食品饮料", "酿酒行业", "食品加工"],
    "汽车": ["汽车整车", "汽车零部件"],
    "军工": ["航天航空", "船舶制造", "军工电子"],
    "传媒": ["文化传媒", "游戏"],
    "化工": ["化学制品", "化学原料", "化肥行业"],
}


A_SHARE_PEER_BASKETS: dict[str, list[str]] = {
    "银行": ["000001", "600036", "601398", "601939", "601328", "601166", "600000", "601818"],
    "白酒": ["600519", "000858", "000568", "002304", "600809", "603589", "603198"],
    "证券": ["300059", "600030", "601066", "600837", "601211", "600999"],
    "保险": ["601318", "601628", "601601", "601319"],
    "新能源": ["300750", "002594", "601012", "300014", "002459", "002460", "603799"],
    "医药": ["600276", "000538", "300760", "603259", "300347", "600436", "000963"],
    "家电": ["000333", "000651", "600690", "002032", "002035", "002508"],
    "电子": ["002475", "002415", "603501", "603986", "688981", "300661", "002371"],
    "半导体": ["688981", "603501", "603986", "300661", "002371", "002049"],
    "计算机": ["300033", "002230", "600588", "002410", "300454", "300271"],
    "通信": ["000063", "600522", "002281", "300136", "300628", "002396"],
    "食品饮料": ["600519", "000858", "000568", "002304", "600887", "603288"],
    "汽车": ["002594", "600104", "000625", "601127", "601633", "000800"],
}


_A_SHARE_PEER_FINANCIAL_LIMIT = 6
_A_SHARE_CURATED_PEER_LIMIT = 14


@dataclass
class IndustryData:
    """行业对比数据封装"""
    symbol: str
    company_name: str = ""
    industry_name: str = ""

    # 标的自身
    stock_pe: Optional[float] = None
    stock_pb: Optional[float] = None
    stock_roe: Optional[float] = None
    stock_revenue_growth: Optional[float] = None
    stock_profit_growth: Optional[float] = None
    stock_net_margin: Optional[float] = None
    stock_market_cap: Optional[float] = None

    # 行业平均
    industry_pe: Optional[float] = None
    industry_pb: Optional[float] = None
    industry_roe: Optional[float] = None

    # 行业中位数
    industry_pe_median: Optional[float] = None
    industry_pb_median: Optional[float] = None

    # 行业内公司数量
    industry_stock_count: int = 0

    # 行业近期涨跌幅
    industry_change_5d: Optional[float] = None
    industry_change_20d: Optional[float] = None

    # 数据来源
    data_source: str = "none"
    missing_fields: list = field(default_factory=list)

    def to_agent_dict(self) -> dict:
        def fmt(v):
            if v is None: return "N/A"
            if isinstance(v, float): return round(v, 2)
            return v

        # PE 对比分析（亏损公司不比较 PE）
        pe_analysis = "N/A"
        if self.stock_pe and self.stock_pe > 0 and self.industry_pe and self.industry_pe > 0:
            diff = (self.stock_pe / self.industry_pe - 1) * 100
            if diff < -30:
                pe_analysis = f"远低于行业均值（低{abs(diff):.0f}%），明显低估"
            elif diff < -10:
                pe_analysis = f"低于行业均值（低{abs(diff):.0f}%），相对便宜"
            elif diff < 10:
                pe_analysis = f"与行业均值接近（{'高' if diff>0 else '低'}{abs(diff):.0f}%）"
            elif diff < 30:
                pe_analysis = f"高于行业均值（高{abs(diff):.0f}%），相对偏贵"
            else:
                pe_analysis = f"远高于行业均值（高{abs(diff):.0f}%），明显高估"

        # PB 对比
        pb_analysis = "N/A"
        if self.stock_pb and self.industry_pb and self.industry_pb > 0:
            diff = (self.stock_pb / self.industry_pb - 1) * 100
            if diff < -20:
                pb_analysis = f"低于行业均值{abs(diff):.0f}%，估值偏低"
            elif diff < 20:
                pb_analysis = f"与行业均值接近"
            else:
                pb_analysis = f"高于行业均值{diff:.0f}%，估值偏贵"

        # ROE 对比
        roe_analysis = "N/A"
        if self.stock_roe and self.industry_roe and self.industry_roe > 0:
            diff = self.stock_roe - self.industry_roe
            if diff > 10:
                roe_analysis = "远高于行业均值，盈利能力突出"
            elif diff > 3:
                roe_analysis = "高于行业均值，盈利能力良好"
            elif diff > -3:
                roe_analysis = "与行业均值相当"
            elif diff > -10:
                roe_analysis = "低于行业均值，盈利能力偏弱"
            else:
                roe_analysis = "远低于行业均值，盈利能力堪忧"

        return {
            "symbol": self.symbol,
            "company_name": self.company_name,
            "industry_name": self.industry_name,
            "data_source": self.data_source,

            "stock_metrics": {
                "pe": fmt(self.stock_pe), "pb": fmt(self.stock_pb),
                "roe_pct": fmt(self.stock_roe),
                "revenue_growth_pct": fmt(self.stock_revenue_growth),
                "profit_growth_pct": fmt(self.stock_profit_growth),
                "net_margin_pct": fmt(self.stock_net_margin),
                "market_cap_100m": fmt(self.stock_market_cap),
            },

            "industry_average": {
                "pe": fmt(self.industry_pe), "pb": fmt(self.industry_pb),
                "roe_pct": fmt(self.industry_roe),
                "pe_median": fmt(self.industry_pe_median),
                "pb_median": fmt(self.industry_pb_median),
                "stock_count": self.industry_stock_count,
                "change_5d_pct": fmt(self.industry_change_5d),
                "change_20d_pct": fmt(self.industry_change_20d),
            },

            "comparison": {
                "pe_vs_industry": pe_analysis,
                "pb_vs_industry": pb_analysis,
                "roe_vs_industry": roe_analysis,
            },
        }


class IndustryFetcher:
    """行业对比数据获取器 v2"""

    def __init__(self):
        self._classifier_cache = IndustryClassifierCache()
        self._ref_cache = IndustryReferenceCache()
        self._a_share_board_cache: dict[str, list[dict]] = {}
        self._a_share_peer_financial_cache: dict[str, dict] = {}
        self._a_share_board_api_disabled = False

    async def fetch(self, symbol: str, market: str = "A") -> IndustryData:
        """
        获取行业对比数据

        策略：
        1. 获取标的自身估值（复用 fundamental_fetcher 的逻辑）
        2. 从 akshare 获取行业板块数据
        3. 降级：至少给出标的数据，行业数据标记缺失
        """
        symbol = symbol.strip().upper()
        result = IndustryData(symbol=symbol)

        # Step 1: 获取标的数据
        await self._fetch_stock_data(result, symbol, market)

        # Step 2: 获取行业数据
        await self._fetch_industry_data(result, symbol, market)

        return result

    async def fetch_enhanced(self, symbol: str, market: str = "A") -> dict:
        """获取增强版行业对比数据（含预处理管线）

        返回结构化字典，包含排名、趋势、性价比等信息。

        Args:
            symbol: 股票代码
            market: "A" / "HK" / "US"

        Returns:
            增强版行业对比数据字典
        """
        symbol = symbol.strip().upper()
        normalized_hk_symbol = self._normalize_hk_symbol(symbol) if market == "HK" else symbol

        # Step 1: 获取标的数据
        industry_data = IndustryData(symbol=symbol)
        await self._fetch_stock_data(industry_data, symbol, market)
        if market == "HK":
            await self._fetch_hk_financial_supplement(industry_data, normalized_hk_symbol)
            self._apply_hk_stock_reference(industry_data, normalized_hk_symbol)

        # Step 2: 确定行业分类
        industry_name = await self._find_industry_improved(
            normalized_hk_symbol if market == "HK" else symbol,
            industry_data.company_name,
            market,
        )

        if industry_name:
            industry_data.industry_name = industry_name

        # Step 3: 获取行业成分股数据（仅A股）
        industry_peers = []
        industry_trend = None
        us_ref_data = None
        us_peer_source = ""

        if market == "A" and industry_name:
            industry_peers = await self._fetch_industry_constituents(industry_name, symbol)
            industry_trend = await self._fetch_industry_trend(industry_name)

        # 港股：尝试获取行业信息
        hk_peer_source = ""
        if market == "HK":
            hk_info = KNOWN_HK_INDUSTRIES.get(normalized_hk_symbol)
            if hk_info:
                industry_name = industry_name or hk_info["name"]
                industry_data.industry_name = industry_name
                industry_peers = await self._fetch_hk_peer_constituents(
                    normalized_hk_symbol, hk_info,
                )
                hk_peer_source = self._classify_hk_peer_source(industry_peers)

        if market == "US":
            industry_from_ref, industry_peers, us_ref_data = us_fallbacks.build_us_industry_peers(symbol)
            if industry_from_ref:
                industry_name = industry_name or industry_from_ref
                industry_data.industry_name = industry_name
            if industry_peers:
                us_peer_source = "us_peer_reference"

        # Step 4: 确定行业参考估值
        if market == "US":
            ref_data = us_ref_data or us_fallbacks.get_us_industry_reference(industry_name or "")
        else:
            ref_data = self._ref_cache.get(industry_name) if industry_name else None
        if ref_data:
            if industry_data.industry_pe is None:
                industry_data.industry_pe = ref_data.get("pe")
            if industry_data.industry_pb is None:
                industry_data.industry_pb = ref_data.get("pb")
            if industry_data.industry_roe is None:
                industry_data.industry_roe = ref_data.get("roe")
        if ref_data and not industry_peers:
            # 使用缓存/硬编码的参考值（当无法获取实时成分股时）
            industry_data.industry_pe = ref_data.get("pe")
            industry_data.industry_pb = ref_data.get("pb")
            industry_data.industry_roe = ref_data.get("roe")

        if industry_peers:
            # 有实时成分股数据，计算行业平均
            from src.data.industry_preprocessor import calculate_industry_metrics
            metrics = calculate_industry_metrics(industry_peers)
            industry_data.industry_pe = metrics.avg_pe or industry_data.industry_pe
            industry_data.industry_pb = metrics.avg_pb or industry_data.industry_pb
            industry_data.industry_roe = metrics.avg_roe or industry_data.industry_roe
            industry_data.industry_pe_median = metrics.median_pe or industry_data.industry_pe
            industry_data.industry_pb_median = metrics.median_pb or industry_data.industry_pb
            industry_data.industry_stock_count = metrics.sample_size

        # Step 5: 运行预处理管线
        stock_info = {
            "pe": industry_data.stock_pe,
            "pb": industry_data.stock_pb,
            "roe": industry_data.stock_roe,
            "revenue_growth": industry_data.stock_revenue_growth,
            "profit_growth": industry_data.stock_profit_growth,
        }

        processed = process_industry_data(
            stock_data=stock_info,
            industry_peers=industry_peers,
            industry_trend=industry_trend,
            reference_metrics=ref_data,
        )

        # Step 6: 构建增强输出
        base = industry_data.to_agent_dict()

        # 合并预处理结果
        base["industry_peers_top"] = processed.get("industry_peers_top", [])
        base["rank_in_industry"] = processed.get("rank_in_industry", {})
        base["value_score"] = processed.get("value_score", {})
        base["industry_trend"] = processed.get("industry_trend", {})
        base["data_quality"] = processed.get("data_quality", {})
        base["anomaly_flags"] = processed.get("anomaly_flags", {})

        # 标注数据来源
        if market == "US" and industry_peers:
            base["data_source"] = us_peer_source or "us_peer_reference"
        elif market == "US" and ref_data:
            base["data_source"] = "us_reference_cached"
        elif market == "HK" and industry_peers:
            base["data_source"] = hk_peer_source or "hk_peer_reference"
        elif market == "A" and industry_peers:
            base["data_source"] = self._classify_a_share_peer_source(industry_peers)
        elif self._ref_cache.is_using_cached(industry_name):
            base["data_source"] = "eastmoney_realtime"
        elif ref_data and not industry_peers:
            base["data_source"] = "reference_cached"
        else:
            base["data_source"] = base.get("data_source", "none")
        base["market"] = market

        return base

    async def _find_industry_improved(
        self, symbol: str, company_name: str = "", market: str = "A"
    ) -> Optional[str]:
        """改进的行业分类方法

        优先级:
        1. 缓存（本地 JSON）
        2. 扩展已知映射（100+）
        3. 名称关键词推断
        4. 逐个 API 尝试（仅A股）
        """
        clean_symbol = symbol.strip().upper()

        if market == "US":
            ref = us_fallbacks.get_us_company_reference(clean_symbol)
            if ref.get("industry"):
                return ref["industry"]

        code = clean_symbol.zfill(6)

        if market == "HK":
            hk_symbol = self._normalize_hk_symbol(clean_symbol)
            hk_info = KNOWN_HK_INDUSTRIES.get(hk_symbol)
            if hk_info:
                return hk_info["name"]

        # 1. 查缓存
        cached = self._classifier_cache.get(code)
        if cached:
            return cached

        # 2. 扩展映射
        if market == "A" and code in EXTENDED_KNOWN_INDUSTRIES:
            industry = EXTENDED_KNOWN_INDUSTRIES[code]
            self._classifier_cache.put(code, industry)
            return industry

        # 3. 名称推断
        if company_name:
            guessed = infer_industry_from_name(company_name)
            if guessed:
                self._classifier_cache.put(code, guessed)
                return guessed

        # 4. 逐个 API 尝试（仅A股，且有超时）
        if market == "A":
            industry = await self._find_industry_api_scan(code)
            if industry:
                self._classifier_cache.put(code, industry)
                return industry

        return None

    async def _fetch_hk_peer_constituents(self, symbol: str, hk_info: dict) -> list[dict]:
        """把港股已知 peer 映射转成可用于预处理的同行样本。

        优先尝试腾讯实时行情；网络或字段缺失时用低置信参考快照补齐。
        """
        peer_symbols = [
            self._normalize_hk_symbol(peer)
            for peer in hk_info.get("peers", [])
            if self._normalize_hk_symbol(peer) != symbol
        ]
        peers: list[dict] = []
        seen: set[str] = set()

        for peer_symbol in peer_symbols:
            peer = await self._fetch_hk_peer_snapshot(peer_symbol)
            if peer:
                peers.append(peer)
                seen.add(peer_symbol)

        for peer_symbol in peer_symbols:
            if peer_symbol in seen:
                continue
            ref = HK_PEER_REFERENCE.get(peer_symbol)
            if not ref:
                continue
            peers.append({
                "code": peer_symbol,
                "name": ref.get("name", peer_symbol),
                "pe": ref.get("pe"),
                "pb": ref.get("pb"),
                "roe": ref.get("roe"),
                "source": "reference",
            })

        return peers

    async def _fetch_hk_peer_snapshot(self, symbol: str) -> Optional[dict]:
        """获取单个港股 peer 快照；失败时返回 None，由参考快照兜底。"""
        try:
            import requests

            code = self._normalize_hk_symbol(symbol).zfill(5)
            resp = requests.get(
                f"https://qt.gtimg.cn/q=hk{code}",
                timeout=6,
                verify=False,
            )
            if "~" not in resp.text:
                return None
            fields = resp.text.split("~")
            if len(fields) < 44:
                return None
            pe = self._safe_float(fields[39])
            pb = self._safe_float(fields[43]) if fields[43] else None
            if pe is None and pb is None:
                return None
            ref = HK_PEER_REFERENCE.get(symbol, {})
            return {
                "code": symbol,
                "name": fields[1] or ref.get("name", symbol),
                "pe": pe,
                "pb": pb,
                "roe": ref.get("roe"),
                "source": "tencent",
            }
        except Exception as e:
            logger.debug(f"港股 peer 快照失败 ({symbol}): {e}")
            return None

    async def _fetch_hk_financial_supplement(self, result: IndustryData, symbol: str) -> None:
        """用东方财富港股财务补齐 ROE/成长性，避免行业面只用参考 ROE。"""
        try:
            from src.data.hk_financial_fetcher import fetch_hk_financials_em

            fin = await fetch_hk_financials_em(symbol)
        except Exception as e:
            logger.debug(f"港股行业财务补充失败 ({symbol}): {e}")
            return

        if not fin or fin.get("data_source") == "none":
            return

        if fin.get("company_name") and not result.company_name:
            result.company_name = fin["company_name"]
        if fin.get("pe") is not None:
            result.stock_pe = fin["pe"]
        if fin.get("pb") is not None:
            result.stock_pb = fin["pb"]
        if fin.get("roe") is not None:
            result.stock_roe = fin["roe"]
        if fin.get("revenue_yoy") is not None:
            result.stock_revenue_growth = fin["revenue_yoy"]
        if fin.get("profit_yoy") is not None:
            result.stock_profit_growth = fin["profit_yoy"]
        if fin.get("net_margin") is not None:
            result.stock_net_margin = fin["net_margin"]
        if fin.get("market_cap") is not None:
            result.stock_market_cap = fin["market_cap"]
        result.data_source = (
            f"{result.data_source}+eastmoney_financial"
            if result.data_source not in ("", "none")
            else "eastmoney_financial"
        )

    def _apply_hk_stock_reference(self, result: IndustryData, symbol: str) -> None:
        """港股实时标的数据失败时，用参考快照补齐最低可分析字段。"""
        ref = HK_PEER_REFERENCE.get(symbol)
        if not ref:
            return
        if not result.company_name:
            result.company_name = ref.get("name", "")
        if result.stock_pe is None:
            result.stock_pe = ref.get("pe")
        if result.stock_pb is None:
            result.stock_pb = ref.get("pb")
        if result.stock_roe is None:
            result.stock_roe = ref.get("roe")
        if result.data_source in ("", "none"):
            result.data_source = "hk_stock_reference"

    def _apply_us_stock_reference(self, result: IndustryData, symbol: str) -> None:
        """美股 yfinance 失败或字段缺失时，用参考快照补齐最低可分析字段。"""
        ref = us_fallbacks.get_us_company_reference(symbol)
        if not ref:
            return

        filled_any = False

        def fill(attr: str, key: str):
            nonlocal filled_any
            value = ref.get(key)
            if getattr(result, attr) in (None, "") and value not in (None, ""):
                setattr(result, attr, value)
                filled_any = True

        fill("company_name", "name")
        fill("industry_name", "industry")
        fill("stock_pe", "pe")
        fill("stock_pb", "pb")
        fill("stock_roe", "roe")

        if filled_any:
            if result.data_source in ("", "none"):
                result.data_source = "us_stock_reference"
            elif "us_stock_reference" not in result.data_source:
                result.data_source = f"{result.data_source}+us_stock_reference"

    @staticmethod
    def _classify_hk_peer_source(peers: list[dict]) -> str:
        if not peers:
            return ""
        sources = {peer.get("source") for peer in peers}
        if sources == {"tencent"}:
            return "hk_peer_realtime"
        if "tencent" in sources:
            return "hk_peer_mixed"
        return "hk_peer_reference"

    @staticmethod
    def _normalize_hk_symbol(symbol: str) -> str:
        clean = str(symbol).strip().upper().replace(".HK", "")
        if not clean:
            return clean
        return clean.zfill(4 if len(clean) <= 4 else 5)

    async def _find_industry_api_scan(self, code: str) -> Optional[str]:
        """逐个扫描东方财富行业板块（带短超时）"""
        try:
            import akshare as ak

            all_industries = [
                "银行", "白酒", "证券", "保险", "医药", "新能源",
                "家电", "电子", "房地产", "电力", "有色金属",
                "水泥", "计算机", "食品饮料", "汽车", "半导体",
                "军工", "传媒", "通信", "化工", "钢铁", "煤炭",
            ]

            for ind in all_industries:
                try:
                    df = ak.stock_board_industry_cons_em(symbol=ind)
                    if df is not None and not df.empty and "代码" in df.columns:
                        if code in df["代码"].astype(str).tolist():
                            logger.info(f"行业分类 API 命中: {code} → {ind}")
                            return ind
                except Exception:
                    continue

        except Exception as e:
            logger.debug(f"行业 API 扫描失败: {e}")

        return None

    @staticmethod
    def _classify_a_share_peer_source(peers: list[dict]) -> str:
        if not peers:
            return ""
        sources = {str(peer.get("source") or "") for peer in peers}
        base = "tencent_peer_realtime" if any("tencent_peer_realtime" in source for source in sources) else "eastmoney_constituents"
        if any("ths_financial" in source for source in sources):
            return f"{base}+ths_financial"
        return base

    def _a_share_board_candidates(self, industry_name: str) -> list[str]:
        """把内部行业名映射成东方财富真实行业板块名候选。"""
        clean = str(industry_name or "").strip()
        candidates: list[str] = []
        for item in [clean, *A_SHARE_BOARD_ALIASES.get(clean, [])]:
            if item and item not in candidates:
                candidates.append(item)
        return candidates

    async def _fetch_industry_constituents(
        self,
        industry_name: str,
        target_symbol: str = "",
    ) -> list[dict]:
        """获取 A 股行业成分股列表，并对 peer ROE 做有限补强。"""
        target_code = str(target_symbol or "").strip().zfill(6) if target_symbol else ""
        fallback_peers: list[dict] = []

        curated_peers = self._fetch_a_share_curated_peers(industry_name, target_code)
        if curated_peers:
            self._supplement_a_share_peer_financials(curated_peers)
            logger.info(f"行业 {industry_name} 使用腾讯实时同行篮子: {len(curated_peers)} 家")
            return curated_peers

        for board_name in self._a_share_board_candidates(industry_name):
            peers = self._fetch_industry_constituents_from_board(board_name)
            if not peers:
                continue
            if not fallback_peers:
                fallback_peers = peers
            if not target_code or any(str(peer.get("code", "")).zfill(6) == target_code for peer in peers):
                self._supplement_a_share_peer_financials(peers)
                logger.info(f"行业 {industry_name} 使用板块 {board_name}: {len(peers)} 家")
                return peers

        if fallback_peers:
            self._supplement_a_share_peer_financials(fallback_peers)
            board_name = fallback_peers[0].get("board", industry_name)
            logger.info(f"行业 {industry_name} 使用候选板块 {board_name}: {len(fallback_peers)} 家")
            return fallback_peers

        logger.debug(f"行业成分股所有候选均失败: {industry_name}")
        return []

    def _fetch_a_share_curated_peers(self, industry_name: str, target_code: str = "") -> list[dict]:
        """东方财富板块接口不可用时，用行业同行篮子 + 腾讯实时行情兜底。"""
        codes = self._a_share_peer_codes(industry_name, target_code)
        peers: list[dict] = []
        for code in dict.fromkeys(codes):
            snapshot = self._fetch_a_share_peer_quote_snapshot(code, industry_name)
            if snapshot:
                peers.append(snapshot)
        return peers

    def _a_share_peer_codes(self, industry_name: str, target_code: str = "") -> list[str]:
        """从静态种子、扩展行业映射和分类缓存自动生成同行代码。"""
        clean_industry = str(industry_name or "").strip()
        target = str(target_code or "").strip().zfill(6) if target_code else ""
        codes: list[str] = []

        def add(code: str) -> None:
            clean_code = str(code or "").strip().zfill(6)
            if not clean_code or clean_code == "000000" or clean_code in codes:
                return
            codes.append(clean_code)

        if target:
            add(target)
        for code in A_SHARE_PEER_BASKETS.get(clean_industry, []):
            add(code)
        for code, mapped_industry in EXTENDED_KNOWN_INDUSTRIES.items():
            if mapped_industry == clean_industry:
                add(code)
        for code, mapped_industry in getattr(self._classifier_cache, "cache", {}).items():
            if mapped_industry == clean_industry:
                add(code)

        return codes[:_A_SHARE_CURATED_PEER_LIMIT]

    def _fetch_a_share_peer_quote_snapshot(self, code: str, industry_name: str) -> Optional[dict]:
        """用腾讯实时行情获取单个 A 股 peer 的 PE/PB 快照。"""
        try:
            import requests

            clean = str(code).zfill(6)
            prefix = "sz" if clean.startswith(("000", "001", "002", "003", "300", "301")) else "sh"
            resp = requests.get(
                f"https://qt.gtimg.cn/q={prefix}{clean}",
                timeout=6,
                verify=False,
            )
            if "~" not in resp.text:
                return None
            fields = resp.text.split("~")
            if len(fields) < 47:
                return None
            pe = self._safe_float(fields[39])
            pb = self._safe_float(fields[46])
            price = self._safe_float(fields[3])
            if pe is None and pb is None and price is None:
                return None
            return {
                "code": clean,
                "name": fields[1],
                "price": price,
                "pe": pe,
                "pb": pb,
                "market_cap": self._safe_float(fields[44]),
                "change_pct": self._safe_float(fields[32]),
                "source": "tencent_peer_realtime",
                "board": f"{industry_name}同行篮子",
            }
        except Exception as e:
            logger.debug(f"A股 peer 腾讯快照失败 ({code}): {e}")
            return None

    def _fetch_industry_constituents_from_board(self, board_name: str) -> list[dict]:
        """从东方财富行业板块获取实时成分股快照。"""
        if self._a_share_board_api_disabled:
            return []
        if board_name in self._a_share_board_cache:
            return [dict(peer) for peer in self._a_share_board_cache[board_name]]

        try:
            import akshare as ak

            df = ak.stock_board_industry_cons_em(symbol=board_name)
            if df is None or df.empty:
                return []

            peers = []
            for _, row in df.head(60).iterrows():
                pe = self._safe_float(row.get("市盈率-动态"))
                pb = self._safe_float(row.get("市净率"))
                price = self._safe_float(row.get("最新价"))
                peers.append({
                    "code": str(row.get("代码", "")).zfill(6),
                    "name": str(row.get("名称", "")),
                    "price": price,
                    "pe": pe,
                    "pb": pb,
                    "change_pct": self._safe_float(row.get("涨跌幅")),
                    "source": "eastmoney_constituents",
                    "board": board_name,
                })

            self._a_share_board_cache[board_name] = [dict(peer) for peer in peers]
            logger.info(f"板块 {board_name} 成分股: {len(peers)} 家")
            return [dict(peer) for peer in peers]

        except Exception as e:
            if self._is_board_api_transport_error(e):
                self._a_share_board_api_disabled = True
            logger.debug(f"获取行业成分股失败 ({board_name}): {e}")
            return []

    @staticmethod
    def _is_board_api_transport_error(error: Exception) -> bool:
        text = repr(error)
        return any(
            marker in text
            for marker in ("RemoteDisconnected", "Connection aborted", "Empty reply")
        )

    def _supplement_a_share_peer_financials(self, peers: list[dict]) -> None:
        """用同花顺财务摘要为有限数量 peer 补 ROE/PB 兜底。"""
        supplemented = 0
        for peer in peers[:_A_SHARE_PEER_FINANCIAL_LIMIT]:
            code = str(peer.get("code", "")).zfill(6)
            if not code or code == "000000":
                continue
            snapshot = self._fetch_a_share_peer_financial_snapshot(code)
            if not snapshot:
                continue
            changed = False
            if peer.get("roe") is None and snapshot.get("roe") is not None:
                peer["roe"] = snapshot["roe"]
                changed = True
            price = self._safe_float(peer.get("price"))
            bvps = self._safe_float(snapshot.get("bvps"))
            if peer.get("pb") is None and price and bvps and bvps > 0:
                peer["pb"] = round(price / bvps, 2)
                changed = True
            if changed:
                source = str(peer.get("source") or "eastmoney_constituents")
                if "ths_financial" not in source:
                    peer["source"] = f"{source}+ths_financial"
                supplemented += 1
        if supplemented:
            logger.info(f"A股行业 peer 财务补强: {supplemented}/{min(len(peers), _A_SHARE_PEER_FINANCIAL_LIMIT)}")

    def _fetch_a_share_peer_financial_snapshot(self, code: str) -> dict:
        """获取 peer 最新 ROE/BVPS；失败时缓存空结果，避免批量重复慢请求。"""
        code = str(code).zfill(6)
        if code in self._a_share_peer_financial_cache:
            return self._a_share_peer_financial_cache[code]

        snapshot: dict = {}
        try:
            import akshare as ak

            df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                roe = self._parse_pct(latest.get("净资产收益率"))
                bvps = self._safe_float(latest.get("每股净资产"))
                if roe is not None:
                    snapshot["roe"] = roe
                if bvps is not None:
                    snapshot["bvps"] = bvps
        except Exception as e:
            logger.debug(f"A股 peer 财务补强失败 ({code}): {e}")

        self._a_share_peer_financial_cache[code] = snapshot
        return snapshot

    async def _fetch_industry_trend(self, industry_name: str) -> Optional[dict]:
        """获取行业近期行情趋势"""
        try:
            import akshare as ak
            from datetime import datetime, timedelta

            start = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

            df = None
            for board_name in self._a_share_board_candidates(industry_name):
                try:
                    df = ak.stock_board_industry_hist_em(
                        symbol=board_name,
                        start_date=start,
                        period="日k",
                    )
                    if df is not None and not df.empty:
                        break
                except Exception:
                    continue

            if df is None or df.empty:
                return None

            closes = df["收盘"].dropna().tolist()
            if len(closes) < 5:
                return None

            return {
                "change_5d": round((closes[-1] / closes[-6] - 1) * 100, 2) if len(closes) >= 6 else None,
                "change_20d": round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None,
                "change_60d": round((closes[-1] / closes[-61] - 1) * 100, 2) if len(closes) >= 61 else None,
            }

        except Exception as e:
            logger.debug(f"获取行业趋势失败 ({industry_name}): {e}")
            return None

    async def _fetch_stock_data(self, result: IndustryData, symbol: str, market: str):
        """获取标的自身的估值和财务数据"""
        try:
            if market == "A":
                await self._fetch_a_stock(result, symbol)
            elif market == "HK":
                await self._fetch_hk_stock(result, symbol)
            else:
                await self._fetch_yfinance_stock(result, symbol, market)
                if market == "US":
                    self._apply_us_stock_reference(result, symbol)
        except Exception as e:
            logger.warning(f"标的数据获取失败: {e}")

    async def _fetch_a_stock(self, result: IndustryData, symbol: str):
        """A股标的数据"""
        import akshare as ak

        # 公司名 + PE（腾讯 API，亲测可用）
        code = symbol.zfill(6)
        prefix = "sz" if code.startswith(("000","001","002","003","300","301")) else "sh"
        latest_price: Optional[float] = None
        try:
            import requests
            resp = requests.get(
                f"https://qt.gtimg.cn/q={prefix}{code}",
                timeout=10, verify=False,
            )
            if "~" in resp.text:
                fields = resp.text.split("~")
                latest_price = self._apply_a_share_tencent_fields(result, fields)
        except Exception as e:
            logger.debug(f"腾讯行情失败: {e}")

        # 财务数据
        try:
            df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                result.stock_roe = self._parse_pct(latest.get("净资产收益率"))
                result.stock_revenue_growth = self._parse_pct(latest.get("营业总收入同比增长率"))
                result.stock_profit_growth = self._parse_pct(latest.get("净利润同比增长率"))
                result.stock_net_margin = self._parse_pct(latest.get("销售净利率"))
                bvps = self._safe_float(latest.get("每股净资产"))
                if result.stock_pb is None and latest_price and bvps and bvps > 0:
                    result.stock_pb = round(latest_price / bvps, 2)
                result.data_source = "akshare"
        except Exception as e:
            logger.debug(f"财务数据失败: {e}")

    def _apply_a_share_tencent_fields(
        self,
        result: IndustryData,
        fields: list[str],
    ) -> Optional[float]:
        """从腾讯 A 股行情字段补齐行业对比所需的标的估值。"""
        latest_price = self._safe_float(fields[3]) if len(fields) > 3 else None
        if len(fields) > 1 and not result.company_name:
            result.company_name = fields[1]
        if len(fields) > 39 and result.stock_pe is None:
            result.stock_pe = self._safe_float(fields[39])
        if len(fields) > 44 and result.stock_market_cap is None:
            result.stock_market_cap = self._safe_float(fields[44])
        if len(fields) > 46 and result.stock_pb is None:
            result.stock_pb = self._safe_float(fields[46])
        if result.data_source in ("", "none") and (
            result.stock_pe is not None or result.stock_pb is not None or result.stock_market_cap is not None
        ):
            result.data_source = "tencent"
        return latest_price

    async def _fetch_hk_stock(self, result: IndustryData, symbol: str):
        """港股标的数据 (腾讯实时行情)"""
        try:
            import requests
            code = symbol.zfill(5)
            resp = requests.get(f"https://qt.gtimg.cn/q=hk{code}", timeout=10, verify=False)
            if "~" in resp.text:
                fields = resp.text.split("~")
                if len(fields) >= 50:
                    result.company_name = fields[1]
                    result.stock_pe = self._safe_float(fields[39])
                    result.stock_pb = self._safe_float(fields[43]) if fields[43] else None
                    mc = self._safe_float(fields[37])
                    if mc: result.stock_market_cap = mc / 1e8
                    result.data_source = "tencent"
                    hk_info = KNOWN_HK_INDUSTRIES.get(self._normalize_hk_symbol(symbol))
                    if hk_info:
                        result.industry_name = result.industry_name or hk_info["name"]
                    logger.info(f"腾讯港股: {result.company_name}, PE={result.stock_pe}")
        except Exception as e:
            logger.debug(f"腾讯港股行业数据失败: {e}")

    async def _fetch_yfinance_stock(self, result: IndustryData, symbol: str, market: str):
        """港股/美股标的数据"""
        try:
            import yfinance as yf
            sym = symbol
            if market == "HK" and not ".HK" in sym:
                sym = f"{symbol}.HK"

            ticker = yf.Ticker(sym)
            info = ticker.info
            if info:
                result.company_name = info.get("longName", "")
                result.industry_name = info.get("industry", "")
                result.stock_pe = self._safe_float(info.get("trailingPE"))
                result.stock_pb = self._safe_float(info.get("priceToBook"))
                result.stock_roe = self._safe_float(info.get("returnOnEquity"))
                if result.stock_roe: result.stock_roe *= 100
                result.stock_revenue_growth = self._safe_float(info.get("revenueGrowth"))
                if result.stock_revenue_growth: result.stock_revenue_growth *= 100
                result.stock_net_margin = self._safe_float(info.get("profitMargins"))
                if result.stock_net_margin: result.stock_net_margin *= 100
                result.industry_pe = self._safe_float(info.get("industryPE"))
                result.industry_pb = self._safe_float(info.get("industryPB"))
                result.data_source = "yfinance"
        except Exception as e:
            logger.debug(f"yfinance 失败: {e}")

    async def _fetch_industry_data(self, result: IndustryData, symbol: str, market: str):
        """获取行业板块数据"""
        if market == "US":
            industry, peers, ref_data = us_fallbacks.build_us_industry_peers(symbol)
            if industry:
                result.industry_name = industry
            if ref_data:
                result.industry_pe = ref_data.get("pe")
                result.industry_pb = ref_data.get("pb")
                result.industry_roe = ref_data.get("roe")
                result.industry_stock_count = len(peers)
                result.data_source = (
                    f"{result.data_source}+us_reference_cached"
                    if result.data_source not in ("", "none")
                    else "us_reference_cached"
                )
            if peers:
                from src.data.industry_preprocessor import calculate_industry_metrics
                metrics = calculate_industry_metrics(peers)
                result.industry_pe = metrics.avg_pe or result.industry_pe
                result.industry_pb = metrics.avg_pb or result.industry_pb
                result.industry_roe = metrics.avg_roe or result.industry_roe
                result.industry_pe_median = metrics.median_pe
                result.industry_pb_median = metrics.median_pb
                result.industry_stock_count = metrics.sample_size
            return

        if market != "A":
            return

        # 找到行业
        industry = await self._find_industry(symbol)
        if not industry:
            result.missing_fields.append("行业分类")
            return
        result.industry_name = industry

        # 行业估值参考（已知常量，避免慢 API）
        INDUSTRY_REF = {
            "银行": {"pe": 5.5, "pb": 0.6, "roe": 10.0},
            "白酒": {"pe": 25.0, "pb": 6.0, "roe": 25.0},
            "证券": {"pe": 18.0, "pb": 1.3, "roe": 7.0},
            "保险": {"pe": 12.0, "pb": 1.0, "roe": 12.0},
            "医药": {"pe": 30.0, "pb": 4.0, "roe": 15.0},
            "新能源": {"pe": 20.0, "pb": 3.0, "roe": 18.0},
            "家电": {"pe": 15.0, "pb": 2.5, "roe": 20.0},
            "电子": {"pe": 25.0, "pb": 3.5, "roe": 12.0},
            "房地产": {"pe": 8.0, "pb": 0.8, "roe": 5.0},
            "电力": {"pe": 15.0, "pb": 1.5, "roe": 10.0},
            "有色金属": {"pe": 15.0, "pb": 2.0, "roe": 10.0},
            "水泥": {"pe": 10.0, "pb": 1.0, "roe": 8.0},
            "互联网": {"pe": 20.0, "pb": 4.0, "roe": 15.0},
            "科技": {"pe": 25.0, "pb": 5.0, "roe": 18.0},
        }
        ref = INDUSTRY_REF.get(industry, {})
        result.industry_pe = ref.get("pe")
        result.industry_pb = ref.get("pb")
        result.industry_roe = ref.get("roe")

        result.data_source = "reference"
        logger.info(f"行业 {industry}(参考): PE~{result.industry_pe}, PB~{result.industry_pb}")

    async def _find_industry(self, symbol: str) -> Optional[str]:
        """查找 A 股标的所属行业

        策略：已知映射 > akshare API > 代码规则推断
        """
        code = symbol.zfill(6)

        # === 已知行业映射（常用标的，无需 API 调用）===
        KNOWN_INDUSTRIES = {
            "000001": "银行", "002142": "银行", "600000": "银行",
            "600036": "银行", "601398": "银行", "601939": "银行",
            "600519": "白酒", "000858": "白酒", "002304": "白酒",
            "000333": "家电", "000651": "家电",
            "300750": "新能源", "601012": "新能源",
            "600276": "医药", "000538": "医药", "300760": "医药",
            "002475": "电子", "002415": "电子",
            "600030": "证券", "300059": "证券",
            "601318": "保险", "601628": "保险",
            "000002": "房地产", "600048": "房地产",
            "600900": "电力", "601985": "电力",
            "601899": "有色金属", "600585": "水泥",
        }
        if code in KNOWN_INDUSTRIES:
            return KNOWN_INDUSTRIES[code]

        # === akshare API（加短超时，失败则降级）===
        try:
            import akshare as ak

            # 只尝试最快的接口
            try:
                df = ak.stock_board_industry_cons_em(symbol="银行")
                if df is not None and not df.empty and "代码" in df.columns:
                    codes = df["代码"].astype(str).tolist()
                    if code in codes: return "银行"
            except: pass

            try:
                df = ak.stock_board_industry_cons_em(symbol="白酒")
                if df is not None and not df.empty and "代码" in df.columns:
                    if code in df["代码"].astype(str).tolist(): return "白酒"
            except: pass

        except Exception:
            pass

        # === 代码规则推断 ===
        if code.startswith(("600000","601","603")):
            return None  # 无法确定
        elif code.startswith(("000","002")):
            return None

        return None

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None: return None
        try:
            v = float(value)
            return v if v == v else None
        except: return None

    @staticmethod
    def _parse_pct(value) -> Optional[float]:
        """解析百分比值"""
        if value is None: return None
        s = str(value).replace("%", "").strip()
        try:
            v = float(s)
            return v if v == v else None
        except: return None
