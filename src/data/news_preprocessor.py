"""
新闻预处理管线

对原始新闻列表执行：
1. 去重（标题相似度）
2. 相关度评分过滤
3. 情感预标注（规则匹配）
4. 事件分类（关键词匹配）
5. 时间衰减加权
6. 结构化摘要输出
"""

import math
import re
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ================================================================
# 配置常量
# ================================================================

# 情感关键词词典
SENTIMENT_KEYWORDS: dict[str, list[str]] = {
    "positive": [
        # 业绩相关
        "超预期", "大增", "增长", "创新高", "盈利", "扭亏", "营收增长",
        "净利润", "业绩预增", "业绩预告增", "同比增", "环比增",
        # 利好事件
        "利好", "突破", "中标", "订单", "签约", "合作",
        # 资本运作
        "分红", "回购", "增持", "股权激励", "高送转",
        # 评级
        "上调", "买入", "增持", "推荐", "优于大市",
        "outperform", "buy", "overweight", "upgrade",
        # 其他
        "获批", "上市", "发布", "升级", "领先",
    ],
    "negative": [
        # 业绩相关
        "低于预期", "下滑", "暴跌", "亏损", "创新低", "下降", "预亏",
        "净利润下降", "营收下降", "同比降", "环比降",
        # 利空事件
        "利空", "违规", "处罚", "调查", "诉讼", "退市",
        "不合格", "被点名", "曝光", "投诉", "召回",
        # 资本运作
        "减持", "套现", "质押", "爆雷", "违约", "债务危机",
        # 评级
        "下调", "卖出", "减持", "中性", "谨慎",
        "underperform", "sell", "underweight", "downgrade",
        # 其他
        "警示", "问询", "监管", "暂停", "取消", "终止", "失败",
    ],
}

# 事件分类关键词
EVENT_CATEGORIES: dict[str, list[str]] = {
    "earnings": [
        "财报", "业绩", "营收", "利润", "净利润", "EPS", "季报", "年报", "中报",
        "earnings", "revenue", "profit",
    ],
    "policy": [
        "政策", "监管", "发改委", "工信部", "央行", "证监会", "国务院",
        "regulation", "policy", "government",
    ],
    "corp_action": [
        "回购", "分红", "增持", "减持", "并购", "重组", "收购", "融资", "定增",
        "buyback", "dividend", "merger", "acquisition",
    ],
    "rumor": [
        "传闻", "传言", "据传", "知情人士", "市场消息",
        "rumor", "speculation", "sources say",
    ],
    "industry": [
        "行业", "赛道", "竞品", "市场份额", "产业",
        "industry", "sector", "market share",
    ],
    "rating": [
        "评级", "目标价", "研报", "分析师", "上调", "下调",
        "target price", "rating", "analyst",
    ],
    "product": [
        "发布", "新品", "产品", "技术", "研发", "版本",
        "product", "launch", "release", "tech",
    ],
}

# 标的关键词映射（常用标的）
STOCK_KEYWORDS: dict[str, list[str]] = {
    # A股
    "000001": ["平安银行", "平安银行"],
    "000002": ["万科", "万科A", "万科企业"],
    "000858": ["五粮液", "五粮"],
    "002415": ["海康威视", "海康"],
    "300750": ["宁德时代", "宁德", "CATL"],
    "600519": ["贵州茅台", "茅台"],
    "601318": ["中国平安"],
    "600036": ["招商银行", "招行"],
    "601398": ["工商银行", "工行"],
    "000651": ["格力电器", "格力"],
    "000333": ["美的集团", "美的"],
    "002594": ["比亚迪", "BYD"],
    "601899": ["紫金矿业", "紫金"],
    "600900": ["长江电力", "长电"],
    # 港股
    "0700":  ["腾讯", "腾讯控股", "Tencent", "微信", "WeChat"],
    "9988":  ["阿里巴巴", "阿里", "Alibaba", "淘宝", "天猫"],
    "9618":  ["京东", "JD.com", "JD"],
    "3690":  ["美团", "Meituan", "美团点评", "王兴", "小象超市", "美团优选", "美团外卖"],
    "1810":  ["小米", "Xiaomi", "小米集团", "雷军"],
    "1024":  ["快手", "Kuaishou"],
    "2015":  ["理想汽车", "理想", "Li Auto"],
    "9866":  ["蔚来", "NIO", "蔚来汽车"],
    "2269":  ["药明生物", "药明", "WuXi"],
    "2318":  ["中国平安", "平安"],
    "1299":  ["友邦保险", "友邦", "AIA"],
    "0005":  ["汇丰", "汇丰控股", "HSBC"],
    "0388":  ["港交所", "香港交易所", "HKEX"],
    "0941":  ["中国移动", "中移动", "China Mobile"],
    "0883":  ["中海油", "中国海洋石油", "CNOOC"],
    "1398":  ["工商银行", "工行"],
    "3988":  ["中国银行", "中行"],
    "2628":  ["中国人寿", "国寿"],
    "2331":  ["李宁", "Li Ning"],
    "2020":  ["安踏", "安踏体育", "ANTA"],
    "6186":  ["中国飞鹤", "飞鹤"],
    "6862":  ["海底捞", "Haidilao"],
    "1177":  ["中国生物制药", "中生制药"],
    "1093":  ["石药集团", "石药"],
    "1801":  ["信达生物", "信达"],
    "6618":  ["京东健康", "JD Health"],
    "9633":  ["农夫山泉", "农夫山泉"],
    "9999":  ["网易", "NetEase"],
    "1919":  ["中远海控", "COSCO"],
    "0992":  ["联想集团", "联想", "Lenovo"],
    "0017":  ["新世界发展", "新世界"],
    "0016":  ["新鸿基地产", "新鸿基"],
    "0001":  ["长和", "长实", "长江和记"],
    "0027":  ["银河娱乐", "银娱"],
    "1928":  ["金沙中国", "金沙"],
    "1876":  ["百威亚太", "百威", "Budweiser"],
    "2688":  ["新奥能源", "新奥"],
    "0291":  ["华润啤酒", "雪花啤酒"],
    "0960":  ["龙湖集团", "龙湖"],
    "0175":  ["吉利汽车", "吉利", "Geely"],
    "2333":  ["长城汽车", "长城"],
    "1211":  ["比亚迪股份", "比亚迪", "BYD"],
    "0991":  ["大唐发电", "大唐"],
    "0902":  ["华能国际", "华能"],
    "0762":  ["中国联通", "联通"],
    "0728":  ["中国电信", "中国电信"],
    "0788":  ["中国铁塔", "中国铁塔"],
    "0881":  ["中升控股", "中升"],
    "1109":  ["华润置地", "华润置地"],
    "0688":  ["中国海外发展", "中海"],
    "2007":  ["碧桂园", "碧桂园"],
    "6098":  ["碧桂园服务", "碧桂园服务"],
    "1209":  ["华润万象生活", "万象生活"],
    "0780":  ["同程旅行", "同程"],
    "2018":  ["瑞声科技", "瑞声"],
    "2382":  ["舜宇光学", "舜宇"],
    "0285":  ["比亚迪电子", "比亚迪电子"],
    "1478":  ["丘钛科技", "丘钛"],
    "1833":  ["平安好医生", "平安好医生"],
    "6060":  ["众安在线", "众安保险"],
    "3888":  ["金山软件", "金山"],
    "0772":  ["阅文集团", "阅文"],
    "1357":  ["美图公司", "美图"],
    # 美股
    "AAPL":  ["Apple", "苹果", "iPhone", "Tim Cook"],
    "TSLA":  ["Tesla", "特斯拉", "Model", "Elon Musk", "马斯克"],
    "MSFT":  ["Microsoft", "微软", "Windows", "Azure", "OpenAI"],
    "GOOGL": ["Google", "谷歌", "Alphabet", "Gemini"],
    "AMZN":  ["Amazon", "亚马逊", "AWS"],
    "META":  ["Meta", "Facebook", "Instagram", "扎克伯格"],
    "NVDA":  ["NVIDIA", "英伟达", "黄仁勋", "GPU"],
    "AMD":   ["Advanced Micro Devices", "AMD", "超威半导体", "Ryzen", "EPYC"],
    "BABA":  ["Alibaba", "阿里巴巴", "阿里"],
    "JD":    ["JD.com", "京东"],
    "BIDU":  ["Baidu", "百度", "文心一言", "ERNIE"],
    "NIO":   ["NIO", "蔚来"],
    "XPEV":  ["XPeng", "小鹏"],
    "LI":    ["Li Auto", "理想汽车"],
    "PDD":   ["Pinduoduo", "拼多多", "Temu"],
    "BILI":  ["Bilibili", "哔哩哔哩", "B站"],
}

# ================================================================
# 动态公司名解析器
# ================================================================

# 缓存：避免重复 API 调用
_company_name_cache: dict[str, list[str]] = {}


def _resolve_company_name_from_tencent(symbol: str, market: str) -> Optional[str]:
    """通过腾讯行情 API 获取公司名称

    腾讯免费 API：http://qt.gtimg.cn/q={prefix}{code}
    A 股响应格式: v_sz000001="51~平安银行~..."
    港股响应格式: v_hk00700="100~腾讯控股~..."
    """
    import requests

    # 构建腾讯 API 的行情代码
    clean = symbol.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "").replace(".SH", "")
    try:
        if market == "A":
            if clean.startswith(("6", "5", "9")):
                qt_code = f"sh{clean}"
            else:
                qt_code = f"sz{clean}"
        elif market == "HK":
            qt_code = f"hk{clean.zfill(5)}"
        else:
            return None

        url = f"http://qt.gtimg.cn/q={qt_code}"
        resp = requests.get(url, timeout=5, verify=False)
        resp.encoding = "gbk"

        # 解析: v_xxx="market_id~公司名~..."
        for line in resp.text.splitlines():
            if "~" in line:
                parts = line.split("~")
                if len(parts) >= 2:
                    name = parts[1].strip().strip('"')
                    # 清理港股后缀（-W, -SW, -S 等）
                    name = re.sub(r"[-]\s*[WS]+\s*$", "", name).strip()
                    if name and len(name) >= 2 and name != clean:
                        logger.debug(f"腾讯API解析公司名: {symbol} → {name}")
                        return name

    except Exception as e:
        logger.debug(f"腾讯API公司名解析失败 ({symbol}): {e}")

    return None


def _resolve_company_name_from_sina(symbol: str, market: str) -> Optional[str]:
    """通过新浪行情 API 获取公司名称

    新浪免费 API：https://hq.sinajs.cn/list={prefix}{code}
    """
    import requests

    clean = symbol.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "").replace(".SH", "")
    try:
        if market == "A":
            if clean.startswith(("6", "5", "9")):
                sina_code = f"sh{clean}"
            else:
                sina_code = f"sz{clean}"
        elif market == "HK":
            sina_code = f"hk{clean.zfill(5)}"
        else:
            return None

        url = f"https://hq.sinajs.cn/list={sina_code}"
        headers = {"Referer": "https://finance.sina.com.cn"}
        resp = requests.get(url, headers=headers, timeout=5, verify=False)
        resp.encoding = "gbk"

        # 解析: var hq_str_xxx="公司名,价格,..."
        for line in resp.text.splitlines():
            if '="' in line:
                content = line.split('="', 1)[1].rstrip('";')
                parts = content.split(",")
                if parts and parts[0].strip():
                    name = parts[0].strip()
                    # 清理港股后缀
                    name = re.sub(r"[-]\s*[WS]+\s*$", "", name).strip()
                    if name and len(name) >= 2 and name != clean:
                        logger.debug(f"新浪API解析公司名: {symbol} → {name}")
                        return name

    except Exception as e:
        logger.debug(f"新浪API公司名解析失败 ({symbol}): {e}")

    return None


def resolve_company_name(symbol: str, market: str = "A") -> Optional[str]:
    """动态解析公司名称（带缓存）

    尝试顺序：
    1. 腾讯行情 API（快，覆盖面广）
    2. 新浪行情 API（备选）

    Args:
        symbol: 股票代码
        market: 市场（A/HK/US）

    Returns:
        公司名称，解析失败返回 None
    """
    clean = symbol.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "").replace(".SH", "")

    # 查缓存
    cache_key = f"{market}:{clean}"
    if cache_key in _company_name_cache:
        return _company_name_cache[cache_key][0] if _company_name_cache[cache_key] else None

    name = None

    # 腾讯 API（A股+港股）
    if market in ("A", "HK"):
        name = _resolve_company_name_from_tencent(clean, market)

    # 新浪 API（备选）
    if not name and market in ("A", "HK"):
        name = _resolve_company_name_from_sina(clean, market)

    # 缓存结果
    if name:
        _company_name_cache[cache_key] = [name]
    else:
        _company_name_cache[cache_key] = []

    return name


def derive_keywords_from_company_name(name: str) -> list[str]:
    """从公司名派生搜索关键词

    例如: "美团-W" → ["美团", "Meituan"]
          "腾讯控股" → ["腾讯", "腾讯控股", "Tencent"]
          "京东集团-SW" → ["京东", "JD.com"]
    """
    keywords = [name]

    # 去掉常见后缀
    clean = re.sub(r"[-]\s*[WS]+\s*$", "", name).strip()
    clean = re.sub(r"(股份|控股|集团|有限|公司|企业|技术|实业|证券|银行|保险|医药|生物|科技|汽车|能源|地产|发展|国际|中国)$", "", clean)
    clean = clean.strip()
    if clean and clean != name:
        keywords.append(clean)

    # 取前两个字作为简称（中文）
    if len(clean) >= 2 and re.search(r"[\u4e00-\u9fa5]", clean):
        short = clean[:2]
        if short not in keywords:
            keywords.append(short)

    return [kw for kw in keywords if len(kw) >= 2]


# 关键词黑名单：纯数字代码容易误匹配的上下文
KEYWORD_FALSE_POSITIVE_PATTERNS = [
    r"(?:价格|涨|跌|收|报|触及|突破|升至|跌至|报价)[^\d]*{code}",
    r"{code}\s*(?:美元|元/吨|点|美元/吨)",
    r"(?:代码|编号|型号|批次|货号)[^\d]*{code}",
]


def get_stock_keywords(symbol: str, market: str = "A") -> list[str]:
    """获取标的关键词列表（公司名 + 代码）

    优先级：
    1. 硬编码映射表（最快，最精确）
    2. 动态 API 解析（覆盖未在映射表中的标的）
    3. 仅代码（兜底）

    Args:
        symbol: 股票代码
        market: 市场（A/HK/US）

    Returns:
        关键词列表
    """
    clean = symbol.strip().upper().replace(".HK", "").replace(".SZ", "").replace(".SS", "").replace(".SH", "")

    # 1. 查硬编码映射表
    if clean in STOCK_KEYWORDS:
        return STOCK_KEYWORDS[clean]

    # 2. 动态解析公司名
    name = resolve_company_name(symbol, market)
    if name:
        keywords = derive_keywords_from_company_name(name)
        keywords.insert(0, name)  # 完整名称优先
        # 同时加入原始代码
        if symbol not in keywords:
            keywords.append(symbol)
        if clean not in keywords:
            keywords.append(clean)
        logger.info(f"动态解析公司名: {symbol} → {name}, 关键词={keywords[:5]}")
        return keywords

    # 3. 都失败了，仅用代码
    logger.warning(f"无法解析公司名: {symbol}，仅用代码匹配（可能不准）")
    return [symbol, clean]


def is_numeric_only_keywords(keywords: list[str]) -> bool:
    """检查关键词是否只有数字代码（无公司名）"""
    return all(kw.replace(".", "").isdigit() for kw in keywords)


# ================================================================
# 1. 去重器
# ================================================================

class NewsDeduplicator:
    """基于标题关键词 Jaccard 相似度的新闻去重器"""

    def __init__(self, similarity_threshold: float = 0.6):
        self.threshold = similarity_threshold

    def deduplicate(self, items: list[dict]) -> list[dict]:
        """去重，保留来源最权威或发布时间最早的"""
        if len(items) <= 1:
            return items

        kept = []
        for item in items:
            is_dup = False
            for existing in kept:
                if self._similarity(item.get("title", ""), existing.get("title", "")) >= self.threshold:
                    is_dup = True
                    # 保留来源更权威的
                    if self._source_rank(item) > self._source_rank(existing):
                        kept.remove(existing)
                        kept.append(item)
                    break
            if not is_dup:
                kept.append(item)

        return kept

    def _similarity(self, title1: str, title2: str) -> float:
        """计算两个标题的 Jaccard 相似度（基于 2-gram 字符集）"""
        if not title1 or not title2:
            return 0.0

        def char_bigrams(s: str) -> set:
            s = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", s)
            return {s[i : i + 2] for i in range(len(s) - 1)}

        set1 = char_bigrams(title1)
        set2 = char_bigrams(title2)

        if not set1 or not set2:
            return 0.0

        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union)

    @staticmethod
    def _source_rank(item: dict) -> int:
        """来源权威度排名（越高越权威）"""
        source = item.get("source", "").lower()
        if any(k in source for k in ["证监会", "交易所", "深交所", "上交所"]):
            return 5
        if any(k in source for k in ["证券时报", "上海证券报", "中国证券报", "财新"]):
            return 4
        if any(k in source for k in ["东方财富", "新浪", "网易", "腾讯", "凤凰"]):
            return 3
        if any(k in source for k in ["券商", "证券", "研报"]):
            return 3
        return 1


# ================================================================
# 2. 相关度评分器
# ================================================================

class RelevanceScorer:
    """基于关键词匹配的新闻相关度评分

    v2 改进：
    - 区分"有公司名关键词"和"仅数字代码"两种情况
    - 仅数字代码时降低阈值，避免误杀
    - 来源加分（东方财富/akshare 已做预过滤）
    - 排除明显误匹配（如"铝价触及3690美元"虽含代码但非公司新闻）
    """

    def __init__(self, min_score: float = 0.25):
        self.min_score = min_score
        # 当关键词只有数字时，使用更低阈值
        self.min_score_numeric_only = 0.15

    def score(self, news: dict, keywords: list[str]) -> float:
        """返回 0.0 ~ 1.0 的相关度分数"""
        score = 0.0
        title = news.get("title", "")
        summary = news.get("summary", "")
        source = news.get("source", "")

        # 1. 来源基础分：东方财富/akshare 已按标的预过滤，给基础信任分
        if any(s in source for s in ["东方财富", "eastmoney", "证券时报", "上海证券报"]):
            score += 0.15

        # 2. 标题匹配（权重最高）
        title_lower = title.lower()
        title_hits = sum(1 for kw in keywords if kw.lower() in title_lower)
        if title_hits > 0:
            score += 0.45 + min(0.2, title_hits * 0.1)

        # 3. 正文匹配
        if summary:
            summary_lower = summary.lower()
            summary_hits = sum(1 for kw in keywords if kw.lower() in summary_lower)
            if summary_hits > 0:
                score += min(0.35, summary_hits * 0.1)

        # 4. 排除误匹配：标题含数字代码但上下文是价格/型号等
        numeric_keywords = [kw for kw in keywords if kw.replace(".", "").isdigit()]
        if numeric_keywords and title_hits > 0:
            for nk in numeric_keywords:
                if self._is_false_positive(title, nk):
                    score -= 0.4  # 大幅降分，因为这是误匹配
                    break

        # 5. 来源权威加分
        if any(k in source for k in ["公告", "交易所", "证监会", "港交所"]):
            score += 0.1

        return max(0.0, min(1.0, score))

    @staticmethod
    def _is_false_positive(title: str, code: str) -> bool:
        """检查标题中的数字代码是否是误匹配（如价格、型号等）"""
        import re
        patterns = [
            rf"(?:价格|涨|跌|收|报|触及|突破|升至|跌至|报价|达到)[^\d]*{code}",
            rf"{code}\s*(?:美元|元/吨|元/桶|点\b|美金)",
            rf"(?:代码|编号|型号|批次|货号|订单号)[^\d]*{code}",
            rf"LME.*{code}",  # LME铝价等
            rf"{code}\s*(?:美元/吨|元/吨)",
        ]
        for pat in patterns:
            if re.search(pat, title):
                return True
        return False

    def filter(self, items: list[dict], keywords: list[str]) -> list[dict]:
        """过滤低相关度新闻"""
        numeric_only = is_numeric_only_keywords(keywords)
        effective_min = self.min_score_numeric_only if numeric_only else self.min_score

        scored = [(self.score(item, keywords), item) for item in items]
        # 不过滤：如果没有公司名关键词，保留所有新闻（让 LLM 来判断）
        # 至少保留 min(5, len(items)) 条
        if numeric_only:
            kept = [item for s, item in scored if s >= effective_min]
            if len(kept) < min(5, len(items)):
                # 数字关键词且过滤后太少 → 保留原始数据，不强行过滤
                logger.debug(
                    f"仅数字关键词({keywords})，过滤后仅{len(kept)}条，"
                    f"保留全部{len(items)}条让 LLM 判断"
                )
                return items
            return kept
        else:
            return [item for s, item in scored if s >= effective_min]


# ================================================================
# 3. 情感预标注器
# ================================================================

class SentimentTagger:
    """规则匹配情感预标注

    两级方案：
    - Level 1: 规则匹配（快速、确定性强）
    - 无法判断时返回 "unknown"，交由 LLM 判断
    """

    def tag(self, news: dict) -> str:
        """返回 'positive' / 'negative' / 'neutral' / 'unknown'"""
        title = news.get("title", "")
        summary = news.get("summary", "")
        text = f"{title} {summary}"

        pos_count = sum(1 for kw in SENTIMENT_KEYWORDS["positive"] if kw in text)
        neg_count = sum(1 for kw in SENTIMENT_KEYWORDS["negative"] if kw in text)

        # 需要有足够的关键词命中才判断
        if pos_count >= 2 and pos_count > neg_count * 2:
            return "positive"
        elif neg_count >= 2 and neg_count > pos_count * 2:
            return "negative"
        elif pos_count >= 1 and neg_count == 0:
            return "positive"
        elif neg_count >= 1 and pos_count == 0:
            return "negative"
        elif pos_count > 0 or neg_count > 0:
            return "neutral"  # 正负都有，标记为中性
        else:
            return "unknown"

    def tag_batch(self, items: list[dict]) -> list[dict]:
        """批量标注，修改原地并返回"""
        for item in items:
            item["_sentiment"] = self.tag(item)
        return items


# ================================================================
# 4. 事件分类器
# ================================================================

class NewsCategorizer:
    """基于关键词的新闻事件分类"""

    def categorize(self, news: dict) -> str:
        """返回分类标签"""
        title = news.get("title", "")
        summary = news.get("summary", "")
        text = f"{title} {summary}".lower()

        scores = {}
        for category, keywords in EVENT_CATEGORIES.items():
            scores[category] = sum(1 for kw in keywords if kw.lower() in text)

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
        return "other"

    def categorize_batch(self, items: list[dict]) -> dict[str, int]:
        """批量分类，返回分类统计"""
        breakdown: dict[str, int] = {}
        for item in items:
            cat = self.categorize(item)
            item["_category"] = cat
            breakdown[cat] = breakdown.get(cat, 0) + 1
        return breakdown


# ================================================================
# 5. 时间衰减加权器
# ================================================================

class TimeDecayWeighter:
    """指数时间衰减加权

    公式: weight = exp(-ln(2) * days_diff / half_life_days)
    - 今天: 1.0
    - half_life 天后: 0.5
    """

    def __init__(self, half_life_days: int = 3):
        self.half_life = half_life_days
        self.decay_factor = math.log(2) / half_life_days

    def weight(self, news: dict, reference_date: Optional[datetime] = None) -> float:
        """计算单条新闻的时间衰减权重"""
        time_str = news.get("time", "") or news.get("publish_time", "")
        if not time_str:
            return 0.5  # 无时间信息，给中性权重

        try:
            # 尝试多种时间格式
            pub_date = self._parse_time(time_str)
            if pub_date is None:
                return 0.5
        except (ValueError, TypeError):
            return 0.5

        ref = reference_date or datetime.now()
        days_diff = max(0, (ref - pub_date).days)

        if days_diff <= 0:
            return 1.0

        return math.exp(-self.decay_factor * days_diff)

    def weight_batch(self, items: list[dict], reference_date: Optional[datetime] = None) -> list[dict]:
        """批量计算权重"""
        for item in items:
            item["_time_weight"] = round(self.weight(item, reference_date), 2)
        return items

    @staticmethod
    def _parse_time(time_str: str) -> Optional[datetime]:
        """解析多种常见时间格式"""
        time_str = time_str.strip()
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
            "%m-%d %H:%M",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        # 尝试提取日期部分
        date_match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", time_str)
        if date_match:
            try:
                return datetime.strptime(date_match.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        return None


# ================================================================
# 6. 异常检测
# ================================================================

class AnomalyDetector:
    """检测新闻数据中的异常模式"""

    @staticmethod
    def detect(items: list[dict]) -> dict:
        """返回异常标记"""
        flags = {}

        # 检测情绪分化（正面和负面信号都比较强）
        pos_count = sum(1 for item in items if item.get("_sentiment") == "positive")
        neg_count = sum(1 for item in items if item.get("_sentiment") == "negative")
        total = len(items)

        if total > 0 and pos_count > 0 and neg_count > 0:
            ratio = max(pos_count, neg_count) / max(1, min(pos_count, neg_count))
            if ratio < 2.0:
                flags["sentiment_divergence"] = True
                flags["sentiment_divergence_detail"] = (
                    f"正面({pos_count}条)与负面({neg_count}条)新闻数量接近，市场情绪存在分歧"
                )

        # 检测新闻量异常（2 天内大量新闻）
        if total >= 8:
            recent_count = sum(
                1 for item in items if item.get("_time_weight", 0) >= 0.8
            )
            if recent_count >= 5:
                flags["sudden_volume_spike"] = True
                flags["volume_spike_detail"] = (
                    f"近2天新闻量暴增({recent_count}条)，可能有重大事件发生"
                )

        return flags


# ================================================================
# 管线编排
# ================================================================

def process_news_pipeline(
    items: list[dict],
    symbol: str,
    market: str = "A",
    reference_date: Optional[datetime] = None,
    max_output: int = 15,
) -> dict:
    """完整的新闻预处理管线

    Args:
        items: 原始新闻列表 (list[dict])
        symbol: 标的代码
        reference_date: 参考日期（用于时间衰减）
        max_output: 最终保留的新闻数量

    Returns:
        结构化摘要 dict，包含：
        - total_fetched: 原始数量
        - after_dedup: 去重后数量
        - after_relevance_filter: 相关度过滤后数量
        - sentiment_stats: 情感统计
        - category_breakdown: 分类统计
        - top_news: 加权排序后的 top N
        - anomaly_flags: 异常检测标记
    """
    if not items:
        return _empty_summary()

    total_fetched = len(items)

    # Step 1: 去重
    deduplicator = NewsDeduplicator()
    items = deduplicator.deduplicate(items)
    after_dedup = len(items)
    logger.debug(f"去重: {total_fetched} → {after_dedup}")

    # Step 2: 相关度过滤
    keywords = get_stock_keywords(symbol, market)
    scorer = RelevanceScorer()
    items = scorer.filter(items, keywords)
    after_filter = len(items)
    logger.debug(f"相关度过滤: {after_dedup} → {after_filter}")

    # Step 3: 情感预标注
    tagger = SentimentTagger()
    items = tagger.tag_batch(items)

    # Step 4: 事件分类
    categorizer = NewsCategorizer()
    category_breakdown = categorizer.categorize_batch(items)

    # Step 5: 时间衰减加权
    weighter = TimeDecayWeighter()
    items = weighter.weight_batch(items, reference_date)

    # Step 6: 异常检测
    anomaly_flags = AnomalyDetector.detect(items)

    # --- 构建输出 ---
    # 按综合得分排序（相关度 * 时间权重，近似）
    # 优先展示高相关度 + 高时效的新闻
    items.sort(key=lambda x: x.get("_time_weight", 0), reverse=True)
    top_news = items[:max_output]

    # 情感统计
    sentiment_counts = {"positive": 0, "negative": 0, "neutral": 0, "unknown": 0}
    weighted_pos = 0.0
    weighted_neg = 0.0
    for item in items:
        s = item.get("_sentiment", "unknown")
        sentiment_counts[s] = sentiment_counts.get(s, 0) + 1
        w = item.get("_time_weight", 0.5)
        if s == "positive":
            weighted_pos += w
        elif s == "negative":
            weighted_neg += w

    return {
        "total_fetched": total_fetched,
        "after_dedup": after_dedup,
        "after_relevance_filter": after_filter,
        "sentiment_stats": {
            "positive": sentiment_counts.get("positive", 0),
            "negative": sentiment_counts.get("negative", 0),
            "neutral": sentiment_counts.get("neutral", 0),
            "unknown": sentiment_counts.get("unknown", 0),
            "weighted_positive_score": round(weighted_pos, 2),
            "weighted_negative_score": round(weighted_neg, 2),
        },
        "category_breakdown": category_breakdown,
        "top_news": top_news,
        "anomaly_flags": anomaly_flags,
    }


def _empty_summary() -> dict:
    """无新闻时的空摘要"""
    return {
        "total_fetched": 0,
        "after_dedup": 0,
        "after_relevance_filter": 0,
        "sentiment_stats": {
            "positive": 0, "negative": 0, "neutral": 0, "unknown": 0,
            "weighted_positive_score": 0.0, "weighted_negative_score": 0.0,
        },
        "category_breakdown": {},
        "top_news": [],
        "anomaly_flags": {},
    }
