"""
行业行业数据处理管线

对行业对比数据执行：
1. 行业平均估值计算（从成分股列表）
2. 标的行业排名分位计算
3. 行业周期判断（复苏/繁荣/衰退/萧条）
4. 性价比综合评分
5. 数据质量评估
6. 结构化摘要输出
"""

import json
import logging
import os
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


# ================================================================
# 数据结构
# ================================================================


@dataclass
class IndustryMetrics:
    """行业统计指标"""
    avg_pe: Optional[float] = None
    median_pe: Optional[float] = None
    avg_pb: Optional[float] = None
    median_pb: Optional[float] = None
    avg_roe: Optional[float] = None
    median_roe: Optional[float] = None
    sample_size: int = 0
    pe_std: Optional[float] = None     # PE标准差


@dataclass
class StockRankInIndustry:
    """标的在行业中的排名"""
    pe_rank: str = "N/A"              # "5/42"
    pe_percentile: Optional[float] = None
    roe_rank: str = "N/A"
    roe_percentile: Optional[float] = None
    valuation_label: str = "N/A"
    vs_median_pe: str = "N/A"


@dataclass
class IndustryCycle:
    """行业周期判断"""
    cycle: str = "unknown"            # recovery/boom/slowdown/depression/normal
    phase_name: str = "未知"
    signal: str = ""
    momentum_score: float = 0.0       # -1 ~ 1


@dataclass
class ValueScore:
    """性价比评分"""
    value_ratio: Optional[float] = None
    score: str = "unknown"            # excellent/good/fair/expensive/overpriced
    interpretation: str = ""
    roe_ratio: Optional[float] = None
    pe_ratio: Optional[float] = None


@dataclass
class IndustryDataQuality:
    """行业数据质量"""
    overall: float = 0.0              # 0~1
    has_constituents: bool = False    # 是否有成分股数据
    has_trend: bool = False           # 是否有趋势数据
    confidence_ceiling: float = 0.45
    notes: str = ""


# ================================================================
# 行业平均估值计算
# ================================================================


def _safe_float(value) -> Optional[float]:
    """安全转换为 float"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v != v:  # NaN check
            return None
        return v
    if isinstance(value, str):
        if value in ("N/A", "", "None", "null"):
            return None
        try:
            return float(value.replace("%", ""))
        except ValueError:
            return None
    return None


def calculate_industry_metrics(peers: list[dict]) -> IndustryMetrics:
    """
    从成分股列表计算行业统计指标。

    Args:
        peers: [{"code": "601398", "name": "工商银行", "pe": 5.2, "pb": 0.6, "roe": 11.0}, ...]

    Returns:
        IndustryMetrics
    """
    pe_values = []
    pb_values = []
    roe_values = []

    for peer in peers:
        pe = _safe_float(peer.get("pe"))
        pb = _safe_float(peer.get("pb"))
        roe = _safe_float(peer.get("roe"))
        if pe and pe > 0:
            pe_values.append(pe)
        if pb and pb > 0:
            pb_values.append(pb)
        if roe is not None and roe != 0:
            roe_values.append(roe)

    if not pe_values:
        return IndustryMetrics(sample_size=len(peers))

    pe_values_sorted = sorted(pe_values)
    pb_values_sorted = sorted(pb_values)
    roe_values_sorted = sorted(roe_values)

    # 计算PE标准差
    pe_mean = sum(pe_values) / len(pe_values)
    pe_std = (
        sum((x - pe_mean) ** 2 for x in pe_values) / len(pe_values)
    ) ** 0.5 if pe_values else None

    return IndustryMetrics(
        avg_pe=round(sum(pe_values) / len(pe_values), 2),
        median_pe=round(pe_values_sorted[len(pe_values_sorted) // 2], 2),
        avg_pb=round(sum(pb_values) / len(pb_values), 2) if pb_values else None,
        median_pb=round(pb_values_sorted[len(pb_values_sorted) // 2], 2) if pb_values else None,
        avg_roe=round(sum(roe_values) / len(roe_values), 2) if roe_values else None,
        median_roe=round(roe_values_sorted[len(roe_values_sorted) // 2], 2) if roe_values else None,
        sample_size=len(peers),
        pe_std=round(pe_std, 2) if pe_std else None,
    )


# ================================================================
# 标的行业排名
# ================================================================


def calculate_industry_rank(stock_pe, stock_roe, peers: list[dict]) -> StockRankInIndustry:
    """
    计算标的在行业中的排名。

    Args:
        stock_pe: 标的PE
        stock_roe: 标的ROE
        peers: 行业成分股列表

    Returns:
        StockRankInIndustry
    """
    rank = StockRankInIndustry()

    if not peers:
        return rank

    # PE排名 (从低到高 = 从便宜到贵)
    # 只取 PE > 0 的成分股（亏损公司不参与 PE 排名）
    pe_values = sorted([_safe_float(p.get("pe")) for p in peers if _safe_float(p.get("pe")) and _safe_float(p.get("pe")) > 0])
    stock_pe_f = _safe_float(stock_pe)

    # 标的 PE 为负（亏损）时，不参与 PE 排名，单独标注
    if stock_pe_f is not None and stock_pe_f <= 0:
        rank.pe_rank = "N/A(亏损)"
        rank.pe_percentile = None
        rank.valuation_label = "PE为负（公司亏损），不适用PE估值比较"
    elif stock_pe_f and pe_values:
        pe_rank = sum(1 for pe in pe_values if pe <= stock_pe_f)
        rank.pe_rank = f"{pe_rank}/{len(pe_values)}"
        rank.pe_percentile = round(pe_rank / len(pe_values), 2)

    # ROE排名 (从高到低 = 从好到差)
    roe_values = sorted([_safe_float(p.get("roe")) for p in peers if _safe_float(p.get("roe"))], reverse=True)
    stock_roe_f = _safe_float(stock_roe)

    if stock_roe_f and roe_values:
        roe_rank = sum(1 for roe in roe_values if roe >= stock_roe_f) + 1
        rank.roe_rank = f"{roe_rank}/{len(roe_values)}"
        rank.roe_percentile = round(roe_rank / len(roe_values), 2)

    # 估值标签
    if rank.pe_percentile is not None and rank.roe_percentile is not None:
        pe_pct = rank.pe_percentile  # 越高 = 越贵
        roe_pct = rank.roe_percentile  # 越高 = ROE越低(差)

        if pe_pct < 0.3 and roe_pct < 0.5:
            rank.valuation_label = "低PE+高ROE：性价比突出"
        elif pe_pct < 0.5 and roe_pct < 0.5:
            rank.valuation_label = "合理估值+良好盈利"
        elif pe_pct > 0.7 and roe_pct > 0.7:
            rank.valuation_label = "高PE+低ROE：估值偏高，需警惕"
        elif pe_pct < 0.3 and roe_pct > 0.7:
            rank.valuation_label = "低PE+低ROE：疑似价值陷阱"
        elif pe_pct > 0.7 and roe_pct < 0.3:
            rank.valuation_label = "高PE+高ROE：溢价成长股"
        elif pe_pct < 0.5 and roe_pct >= 0.5:
            rank.valuation_label = "合理估值+盈利一般"
        else:
            rank.valuation_label = "估值与盈利基本匹配"

    # vs 行业中位数
    if stock_pe_f and pe_values:
        median_pe = pe_values[len(pe_values) // 2]
        if median_pe > 0:
            diff_pct = (stock_pe_f / median_pe - 1) * 100
            rank.vs_median_pe = f"{'+' if diff_pct > 0 else ''}{diff_pct:.0f}%"

    return rank


# ================================================================
# 行业周期判断
# ================================================================


def classify_industry_cycle(trend: dict, pe_percentile: float = None) -> IndustryCycle:
    """
    基于涨跌幅和估值变化的行业周期分类。

    四阶段模型:
    - recovery: 估值低 + 价格开始上涨 → 最佳买点
    - boom: 估值走高 + 价格持续上涨 → 注意过热
    - slowdown: 估值高 + 价格开始下跌 → 回避
    - depression: 估值低 + 价格持续下跌 → 孕育机会

    Args:
        trend: {"change_5d": 1.2, "change_20d": 5.0, "change_60d": 12.0}
        pe_percentile: 估值分位（可选）

    Returns:
        IndustryCycle
    """
    cycle = IndustryCycle()

    change_20d = _safe_float(trend.get("change_20d")) or 0
    change_60d = _safe_float(trend.get("change_60d")) or 0
    change_5d = _safe_float(trend.get("change_5d")) or 0

    if pe_percentile is None:
        pe_percentile = 0.5

    # 动量分数 (简化: 近期涨跌幅加权)
    momentum = (change_5d * 0.3 + change_20d * 0.4 + change_60d * 0.3) / 10
    cycle.momentum_score = max(-1.0, min(1.0, momentum))

    # 周期判断
    if change_60d > 10 and pe_percentile > 0.6:
        cycle.cycle = "boom"
        cycle.phase_name = "繁荣期"
        cycle.signal = "行业处于景气高位，估值偏贵，追高风险大"
    elif change_60d < -10 and pe_percentile > 0.6:
        cycle.cycle = "slowdown"
        cycle.phase_name = "衰退期"
        cycle.signal = "行业下行+估值仍贵，可能进一步下跌"
    elif change_60d < 0 and pe_percentile < 0.4:
        cycle.cycle = "depression"
        cycle.phase_name = "萧条期"
        cycle.signal = "行业低迷+估值便宜，关注拐点信号"
    elif change_60d > 0 and pe_percentile < 0.4:
        cycle.cycle = "recovery"
        cycle.phase_name = "复苏期"
        cycle.signal = "行业从低估中恢复，基本面改善"
    else:
        cycle.cycle = "normal"
        cycle.phase_name = "常态期"
        cycle.signal = "行业无极端信号"

    return cycle


# ================================================================
# 性价比评分
# ================================================================


def calculate_value_score(stock: dict, industry: dict) -> ValueScore:
    """
    综合性价比评分: 质量 vs 价格的匹配度。

    核心公式: value_ratio = (公司PE/行业PE) / (公司ROE/行业ROE)
    < 1, 好公司相对便宜 → 好机会
    > 1, 好公司相对偏贵 → 需看增长
    """
    score = ValueScore()

    stock_pe = _safe_float(stock.get("pe"))
    industry_pe = _safe_float(industry.get("avg_pe")) or _safe_float(industry.get("median_pe"))
    stock_roe = _safe_float(stock.get("roe"))
    industry_roe = _safe_float(industry.get("avg_roe")) or _safe_float(industry.get("median_roe"))

    if not (stock_pe and industry_pe and stock_roe and industry_roe):
        score.score = "insufficient_data"
        score.interpretation = "数据不足，无法计算性价比评分"
        return score

    # 避免除零和负PE
    if industry_pe <= 0 or industry_roe == 0 or stock_roe == 0:
        score.score = "insufficient_data"
        score.interpretation = "数据异常，无法计算性价比评分"
        return score

    # 标的 PE 为负（亏损）时，不适用 PE 性价比比较
    if stock_pe <= 0:
        score.score = "loss_making"
        score.interpretation = "公司当前亏损（PE为负），不适用PE性价比比较，建议关注盈利拐点"
        score.pe_ratio = None
        score.roe_ratio = round(stock_roe / industry_roe, 2) if industry_roe != 0 else None
        score.value_ratio = None
        return score

    pe_ratio = stock_pe / industry_pe
    roe_ratio = stock_roe / industry_roe
    value_ratio = pe_ratio / roe_ratio if roe_ratio != 0 else pe_ratio

    score.pe_ratio = round(pe_ratio, 2)
    score.roe_ratio = round(roe_ratio, 2)
    score.value_ratio = round(value_ratio, 2)

    if value_ratio < 0.7:
        score.score = "excellent"
        score.interpretation = "公司盈利能力优于行业，但估值偏低——大概率被低估"
    elif value_ratio < 1.0:
        score.score = "good"
        score.interpretation = "性价比良好，公司盈利与估值基本匹配"
    elif value_ratio < 1.3:
        score.score = "fair"
        score.interpretation = "性价比一般，估值与盈利基本匹配"
    elif value_ratio < 1.8:
        score.score = "expensive"
        score.interpretation = "性价比偏弱，为获得单位盈利付出的价格偏高"
    else:
        score.score = "overpriced"
        score.interpretation = "性价比差，估值显著高于盈利能力对应的合理水平"

    return score


# ================================================================
# 行业分类缓存
# ================================================================


class IndustryClassifierCache:
    """
    行业分类缓存器。

    大部分标的不经常变化行业分类，缓存可大幅减少 API 调用。
    缓存文件: config/industry_classifier_cache.json
    """

    CACHE_FILE = "config/industry_classifier_cache.json"

    def __init__(self):
        self.cache: dict[str, str] = {}
        self._dirty = False
        self._load_cache()

    def get(self, symbol: str) -> Optional[str]:
        """从缓存查找"""
        code = symbol.strip().zfill(6)
        return self.cache.get(code)

    def put(self, symbol: str, industry: str):
        """缓存行业分类"""
        code = symbol.strip().zfill(6)
        self.cache[code] = industry
        self._dirty = True

    async def save(self):
        """持久化到 JSON 文件"""
        if self._dirty:
            cache_path = self.CACHE_FILE
            cache_dir = os.path.dirname(cache_path)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({
                    "updated_at": datetime.now().isoformat(),
                    "data": self.cache
                }, f, ensure_ascii=False, indent=2)
            self._dirty = False
            logger.debug(f"行业分类缓存已更新: {len(self.cache)} 条")

    def _load_cache(self):
        """从文件加载缓存"""
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.cache = data.get("data", {})
                logger.debug(f"行业分类缓存已加载: {len(self.cache)} 条")
        except Exception as e:
            logger.debug(f"行业分类缓存加载失败: {e}")
            self.cache = {}


# ================================================================
# 扩展行业映射
# ================================================================


# 扩展的已知行业映射（替代原来的28个，扩展到100+）
EXTENDED_KNOWN_INDUSTRIES = {
    # === 银行 ===
    "000001": "银行", "002142": "银行", "600000": "银行", "600036": "银行",
    "601398": "银行", "601939": "银行", "601328": "银行", "601166": "银行",
    "600015": "银行", "600016": "银行", "601818": "银行", "601009": "银行",
    # === 白酒 ===
    "600519": "白酒", "000858": "白酒", "002304": "白酒", "000568": "白酒",
    "603198": "白酒", "600809": "白酒", "603589": "白酒",
    # === 证券 ===
    "600030": "证券", "300059": "证券", "601066": "证券", "600837": "证券",
    "601211": "证券", "600999": "证券",
    # === 保险 ===
    "601318": "保险", "601628": "保险", "601319": "保险", "601601": "保险",
    # === 新能源 ===
    "300750": "新能源", "601012": "新能源", "002594": "新能源", "300014": "新能源",
    "002074": "新能源", "300274": "新能源", "002459": "新能源", "002709": "新能源",
    # === 医药 ===
    "600276": "医药", "000538": "医药", "300760": "医药", "300347": "医药",
    "603259": "医药", "300363": "医药", "600436": "医药", "000963": "医药",
    "600763": "医药", "300122": "医药", "300529": "医药",
    # === 家电 ===
    "000333": "家电", "000651": "家电", "600690": "家电", "002032": "家电",
    "002035": "家电", "002508": "家电", "002705": "家电",
    # === 电子/半导体 ===
    "002475": "电子", "002415": "电子", "603986": "半导体", "600745": "半导体",
    "002371": "半导体", "603501": "半导体", "300661": "半导体", "688981": "半导体",
    "002049": "半导体", "300474": "半导体",
    # === 房地产 ===
    "000002": "房地产", "600048": "房地产", "001979": "房地产", "600606": "房地产",
    "601155": "房地产", "000069": "房地产", "600383": "房地产",
    # === 电力 ===
    "600900": "电力", "601985": "电力", "600886": "电力", "600795": "电力",
    "003816": "电力", "600023": "电力",
    # === 有色金属 ===
    "601899": "有色金属", "600585": "水泥", "600362": "有色金属", "000630": "有色金属",
    "002460": "有色金属", "600497": "有色金属",
    # === 计算机/科技 ===
    "600588": "计算机", "002230": "计算机", "002410": "计算机", "300033": "计算机",
    "300454": "计算机", "300271": "计算机", "300188": "计算机",
    # === 通信 ===
    "000063": "通信", "600522": "通信", "002281": "通信", "300136": "通信",
    "300628": "通信",
    # === 传媒 ===
    "002027": "传媒", "300418": "传媒", "002602": "传媒", "603444": "传媒",
    "300413": "传媒", "002739": "传媒",
    # === 军工 ===
    "600893": "军工", "002179": "军工", "600760": "军工", "000768": "军工",
    "002414": "军工", "002025": "军工", "300775": "军工",
    # === 汽车 ===
    "601127": "汽车", "600104": "汽车", "000625": "汽车", "002594": "汽车",
    # === 食品饮料 ===
    "600887": "食品饮料", "000895": "食品饮料", "002714": "食品饮料",
    "000568": "食品饮料", "002304": "食品饮料", "603288": "食品饮料",
    # === 化工 ===
    "603260": "化工", "002497": "化工", "600309": "化工", "000408": "化工",
    # === 其他 ===
    "601012": "新能源", "600276": "医药", "002475": "电子",
}


# 名称关键词推断（用于缺失映射时的后备）
INDUSTRY_NAME_KEYWORDS = {
    "银行": ["银行"],
    "证券": ["证券", "券商"],
    "保险": ["保险"],
    "白酒": ["酒", "窖", "醇", "液"],
    "医药": ["医药", "制药", "生物", "药", "堂"],
    "新能源": ["新能", "锂", "电", "光", "伏"],
    "半导体": ["半导体", "芯", "集成电路", "微"],
    "房地产": ["地产", "置业", "城建", "置业"],
    "汽车": ["汽车", "车"],
    "科技": ["科技", "软件", "信息", "网络"],
    "军工": ["军工", "航天", "航空", "防务"],
    "食品饮料": ["食品", "饮料", "乳", "奶", "酱油"],
    "化工": ["化工", "化学", "石化"],
    "家电": ["电器", "家电", "空调", "冰箱"],
    "电子": ["电子", "光电", "显示"],
    "计算机": ["计算机", "软件", "信息"],
    "通信": ["通信", "通讯"],
    "传媒": ["传媒", "影视", "游戏", "出版"],
    "电力": ["电力", "电"],
    "有色金属": ["有色", "铜", "铝", "锌"],
    "水泥": ["水泥"],
}


def infer_industry_from_name(company_name: str) -> Optional[str]:
    """从公司名称推断行业分类"""
    if not company_name:
        return None

    for industry, keywords in INDUSTRY_NAME_KEYWORDS.items():
        if any(kw in company_name for kw in keywords):
            return industry
    return None


# ================================================================
# 港股行业映射
# ================================================================


KNOWN_HK_INDUSTRIES = {
    "0700": {"name": "互联网", "peers": ["9988", "9618", "9888", "1810"]},
    "9988": {"name": "互联网", "peers": ["0700", "9618", "9888"]},
    "9618": {"name": "互联网", "peers": ["0700", "9988", "9888"]},
    "9888": {"name": "互联网", "peers": ["0700", "9988", "9618"]},
    "1810": {"name": "互联网", "peers": ["0700", "9988"]},
    "0941": {"name": "电信", "peers": ["0981", "1883", "00019"]},
    "0005": {"name": "金融", "peers": ["0011", "0388", "1299"]},
    "0011": {"name": "金融", "peers": ["0005", "0388"]},
    "0388": {"name": "金融", "peers": ["0005", "0011", "1299"]},
    "1299": {"name": "保险", "peers": ["2318", "2628", "1336"]},
    "2318": {"name": "保险", "peers": ["1299", "2628"]},
    "2628": {"name": "保险", "peers": ["1299", "2318"]},
    "0012": {"name": "房地产", "peers": ["1109", "0016", "0001", "0004"]},
    "1109": {"name": "房地产", "peers": ["0012", "0016", "0001"]},
    "0016": {"name": "房地产", "peers": ["0012", "1109", "0001"]},
    "0001": {"name": "房地产", "peers": ["0012", "1109", "0016"]},
    "0939": {"name": "银行", "peers": ["1398", "3988", "0939"]},
    "1398": {"name": "银行", "peers": ["0939", "3988"]},
    "3988": {"name": "银行", "peers": ["0939", "1398"]},
    "0388": {"name": "金融", "peers": ["0005", "0011"]},
    "0688": {"name": "房地产", "peers": ["0012", "1109"]},
    "1044": {"name": "消费品", "peers": []},
    "0027": {"name": "消费/博彩", "peers": []},
    "1177": {"name": "医药", "peers": ["2269", "1138"]},
}


# ================================================================
# 行业参考值动态缓存
# ================================================================


class IndustryReferenceCache:
    """
    行业参考值缓存管理。

    缓存从东方财富/同花顺获取的实时行业估值数据，
    避免重复调用 API，同时提供比硬编码常量更新鲜的数据。
    """

    CACHE_FILE = "config/industry_reference_cache.json"
    # 有效期：7天
    MAX_AGE_DAYS = 7

    # 硬编码兜底参考值（比原来增加了注释说明可能是近似值）
    HARDCODED_REFERENCE = {
        "银行": {"pe": 5.5, "pb": 0.6, "roe": 10.0, "note": "近似参考值"},
        "白酒": {"pe": 25.0, "pb": 6.0, "roe": 25.0, "note": "近似参考值"},
        "证券": {"pe": 18.0, "pb": 1.3, "roe": 7.0, "note": "近似参考值"},
        "保险": {"pe": 12.0, "pb": 1.0, "roe": 12.0, "note": "近似参考值"},
        "医药": {"pe": 30.0, "pb": 4.0, "roe": 15.0, "note": "近似参考值"},
        "新能源": {"pe": 20.0, "pb": 3.0, "roe": 18.0, "note": "近似参考值"},
        "家电": {"pe": 15.0, "pb": 2.5, "roe": 20.0, "note": "近似参考值"},
        "电子": {"pe": 25.0, "pb": 3.5, "roe": 12.0, "note": "近似参考值"},
        "房地产": {"pe": 8.0, "pb": 0.8, "roe": 5.0, "note": "近似参考值"},
        "电力": {"pe": 15.0, "pb": 1.5, "roe": 10.0, "note": "近似参考值"},
        "有色金属": {"pe": 15.0, "pb": 2.0, "roe": 10.0, "note": "近似参考值"},
        "水泥": {"pe": 10.0, "pb": 1.0, "roe": 8.0, "note": "近似参考值"},
        "互联网": {"pe": 20.0, "pb": 4.0, "roe": 15.0, "note": "近似参考值"},
        "科技": {"pe": 25.0, "pb": 5.0, "roe": 18.0, "note": "近似参考值"},
        "计算机": {"pe": 35.0, "pb": 4.0, "roe": 12.0, "note": "近似参考值"},
        "食品饮料": {"pe": 25.0, "pb": 5.0, "roe": 18.0, "note": "近似参考值"},
        "汽车": {"pe": 18.0, "pb": 2.0, "roe": 10.0, "note": "近似参考值"},
        "半导体": {"pe": 40.0, "pb": 5.0, "roe": 15.0, "note": "近似参考值"},
        "军工": {"pe": 30.0, "pb": 3.0, "roe": 10.0, "note": "近似参考值"},
        "传媒": {"pe": 20.0, "pb": 2.5, "roe": 8.0, "note": "近似参考值"},
        "通信": {"pe": 22.0, "pb": 3.0, "roe": 12.0, "note": "近似参考值"},
        "化工": {"pe": 15.0, "pb": 2.0, "roe": 12.0, "note": "近似参考值"},
    }

    def __init__(self):
        self.cached_data: dict = {}
        self.updated_at: Optional[str] = None
        self._load()

    def get(self, industry: str) -> Optional[dict]:
        """获取行业参考值，优先使用缓存"""
        if self._is_cache_valid() and industry in self.cached_data:
            return self.cached_data[industry]
        # 降级到硬编码
        return self.HARDCODED_REFERENCE.get(industry)

    def update(self, industry: str, data: dict):
        """更新缓存"""
        self.cached_data[industry] = data
        self.updated_at = datetime.now().isoformat()
        self._save()

    def is_using_cached(self, industry: str) -> bool:
        """判断是否使用了实时缓存数据"""
        return self._is_cache_valid() and industry in self.cached_data

    def _is_cache_valid(self) -> bool:
        """检查缓存是否在有效期内"""
        if not self.updated_at:
            return False
        try:
            updated = datetime.fromisoformat(self.updated_at)
            return (datetime.now() - updated).days < self.MAX_AGE_DAYS
        except Exception:
            return False

    def _load(self):
        try:
            if os.path.exists(self.CACHE_FILE):
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                self.cached_data = cache.get("data", {})
                self.updated_at = cache.get("updated_at")
                logger.debug(f"行业参考值缓存已加载: {len(self.cached_data)} 条")
        except Exception as e:
            logger.debug(f"行业参考值缓存加载失败: {e}")

    def _save(self):
        try:
            cache_dir = os.path.dirname(self.CACHE_FILE)
            if cache_dir and not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "updated_at": self.updated_at,
                    "data": self.cached_data
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"行业参考值缓存保存失败: {e}")


# ================================================================
# 主预处理函数
# ================================================================


def process_industry_data(stock_data: dict, industry_peers: list[dict],
                           industry_trend: dict = None,
                           pe_percentile: float = None) -> dict:
    """
    行业对比数据主预处理函数。

    Args:
        stock_data: 标的自身的估值/财务数据
        industry_peers: 行业成分股列表
        industry_trend: 行业近期涨跌幅 {"change_5d": ..., "change_20d": ..., "change_60d": ...}
        pe_percentile: 标的PE历史分位（可选）

    Returns:
        完整的预处理结果字典
    """
    result = {}

    # 1. 行业平均估值
    if industry_peers:
        metrics = calculate_industry_metrics(industry_peers)
        result["industry_metrics"] = {
            "avg_pe": metrics.avg_pe,
            "median_pe": metrics.median_pe,
            "avg_pb": metrics.avg_pb,
            "median_pb": metrics.median_pb,
            "avg_roe": metrics.avg_roe,
            "median_roe": metrics.median_roe,
            "stock_count": metrics.sample_size,
            "pe_std": metrics.pe_std,
        }
        result["industry_peers_top"] = industry_peers[:10]  # 只保留前10
    else:
        result["industry_metrics"] = {}
        result["industry_peers_top"] = []

    # 2. 标的行业排名
    if industry_peers:
        rank = calculate_industry_rank(
            stock_data.get("pe"),
            stock_data.get("roe"),
            industry_peers,
        )
        result["rank_in_industry"] = {
            "pe_rank": rank.pe_rank,
            "pe_percentile": rank.pe_percentile,
            "roe_rank": rank.roe_rank,
            "roe_percentile": rank.roe_percentile,
            "valuation_label": rank.valuation_label,
            "vs_median_pe": rank.vs_median_pe,
        }
    else:
        result["rank_in_industry"] = {"note": "成分股数据不可用，无法计算排名"}

    # 3. 性价比评分
    if industry_peers:
        vs = calculate_value_score(
            stock_data,
            result["industry_metrics"],
        )
        result["value_score"] = {
            "value_ratio": vs.value_ratio,
            "score": vs.score,
            "interpretation": vs.interpretation,
            "pe_ratio": vs.pe_ratio,
            "roe_ratio": vs.roe_ratio,
        }
    else:
        result["value_score"] = {"note": "成分股数据不可用"}

    # 4. 行业趋势和周期
    if industry_trend:
        cycle = classify_industry_cycle(industry_trend, pe_percentile)
        result["industry_trend"] = {
            "change_5d_pct": industry_trend.get("change_5d"),
            "change_20d_pct": industry_trend.get("change_20d"),
            "change_60d_pct": industry_trend.get("change_60d"),
            "cycle": cycle.cycle,
            "phase": cycle.phase_name,
            "signal": cycle.signal,
            "momentum_score": cycle.momentum_score,
        }
    else:
        result["industry_trend"] = {"note": "趋势数据不可用"}

    # 5. 数据质量评估
    has_constituents = bool(industry_peers) and len(industry_peers) > 5
    has_trend = industry_trend is not None and industry_trend.get("change_20d") is not None

    quality_score = 0.0
    if has_constituents:
        quality_score += 0.5
    if has_trend:
        quality_score += 0.3
    if result.get("rank_in_industry", {}).get("pe_rank", "N/A") != "N/A":
        quality_score += 0.2

    ceiling = 0.85 if quality_score >= 0.8 else 0.65 if quality_score >= 0.5 else 0.45 if quality_score >= 0.3 else 0.25

    notes_parts = []
    if has_constituents:
        notes_parts.append(f"成分股数据可用({len(industry_peers)}家)")
    else:
        notes_parts.append("成分股数据不可用")
    if has_trend:
        notes_parts.append("趋势数据可用")
    else:
        notes_parts.append("趋势数据不可用")

    result["data_quality"] = {
        "overall": round(quality_score, 2),
        "has_constituents": has_constituents,
        "has_trend": has_trend,
        "confidence_ceiling": ceiling,
        "notes": ", ".join(notes_parts),
    }

    # 6. 异常标志
    rank_info = result.get("rank_in_industry", {})
    pe_pct = rank_info.get("pe_percentile") if isinstance(rank_info, dict) else None

    result["anomaly_flags"] = {
        "extreme_valuation": pe_pct is not None and (pe_pct > 0.9 or pe_pct < 0.1),
        "sector_rotation_signal": result.get("industry_trend", {}).get("cycle") in ("recovery", "slowdown"),
        "high_sector_divergence": (
            isinstance(result.get("industry_metrics"), dict) and
            result["industry_metrics"].get("pe_std") is not None and
            result["industry_metrics"]["pe_std"] > 5
        ),
    }

    return result
