"""
标的宏观上下文解析

根据标的代码/公司名推断行业分类，返回该行业的宏观敏感因子，
帮助 LLM 将宏观环境精确翻译为对该标的的影响。

核心思路：
  不同行业对同一宏观因子（利率、汇率、PMI等）的敏感度完全不同。
  银行利好利率上行（息差扩大），互联网利空利率上行（DCF估值压缩）。
"""

import re
import logging

logger = logging.getLogger(__name__)

# ================================================================
# 行业 → 宏观敏感因子映射
# ================================================================

SECTOR_MACRO_SENSITIVITY: dict[str, dict] = {
    "银行": {
        "rate_sensitive": 0.9,
        "rate_direction": "positive",  # 利率上行 → 利好息差
        "fx_sensitive": 0.7,
        "fx_note": "美元走强利好港股银行（挂钩美元资产）",
        "cycle_sensitive": 0.6,
        "geopolitical_sensitive": 0.3,
        "liquidity_sensitive": 0.8,
        "notes": "利率上行利好息差；大行是流动性风向标；关注LPR和社融",
        "transmission_hints": [
            "利率上行 → 净息差扩大 → 银行利润增厚",
            "降准降息 → 信贷成本降低 → 贷款需求回升",
            "社融超预期 → 经济活跃度提升 → 银行业务量增长",
            "人民币升值 → 外资流入 → 港股银行估值修复",
        ],
    },
    "保险": {
        "rate_sensitive": 0.8,
        "rate_direction": "positive",
        "fx_sensitive": 0.5,
        "cycle_sensitive": 0.5,
        "geopolitical_sensitive": 0.3,
        "liquidity_sensitive": 0.6,
        "notes": "利率上行利好保险资金投资收益率；关注长端利率",
        "transmission_hints": [
            "长端利率上行 → 保险配置盘收益率提升 → 利润改善",
            "降息 → 保险产品吸引力上升（相对存款） → 保费增长",
        ],
    },
    "互联网平台": {
        "rate_sensitive": 0.8,
        "rate_direction": "negative",  # 利率上行 → DCF估值压缩
        "fx_sensitive": 0.4,
        "cycle_sensitive": 0.5,
        "geopolitical_sensitive": 0.7,
        "liquidity_sensitive": 0.7,
        "notes": "高估值成长股，DCF对利率极度敏感；中美科技摩擦是核心风险",
        "transmission_hints": [
            "利率下行 → DCF估值模型分母减小 → 远期现金流现值提升 → 估值扩张",
            "人民币升值 → 外资回流港股 → 互联网权重股率先受益",
            "PMI上行 → 广告主投放意愿增强 → 互联网广告收入提升",
            "中美科技摩擦升级 → 海外业务受限 → 估值折价",
        ],
    },
    "半导体/硬件": {
        "rate_sensitive": 0.6,
        "rate_direction": "negative",
        "fx_sensitive": 0.5,
        "cycle_sensitive": 0.8,  # 半导体周期性强
        "geopolitical_sensitive": 0.9,  # 芯片制裁
        "liquidity_sensitive": 0.5,
        "notes": "周期性行业，跟随全球半导体周期；地缘政治（芯片制裁）是最大变量",
        "transmission_hints": [
            "全球PMI上行 → 芯片需求回暖 → 半导体出货量增加",
            "中美科技制裁 → 国产替代加速 → 利好国内半导体",
            "利率下行 → 科技股估值提升 → 半导体板块获益",
        ],
    },
    "消费": {
        "rate_sensitive": 0.3,
        "rate_direction": "neutral",
        "fx_sensitive": 0.3,
        "cycle_sensitive": 0.8,
        "geopolitical_sensitive": 0.4,
        "liquidity_sensitive": 0.5,
        "notes": "消费随经济周期波动；CPI和居民收入是关键；关注消费刺激政策",
        "transmission_hints": [
            "PMI上行 + 就业改善 → 居民收入预期回升 → 消费意愿增强",
            "CPI温和上行 → 消费品提价空间 → 利好品牌消费",
            "降息 → 房贷压力减轻 → 可支配收入增加 → 可选消费受益",
        ],
    },
    "新能源汽车": {
        "rate_sensitive": 0.5,
        "rate_direction": "negative",
        "fx_sensitive": 0.6,
        "cycle_sensitive": 0.6,
        "geopolitical_sensitive": 0.7,
        "liquidity_sensitive": 0.5,
        "notes": "高资本开支行业，对融资成本和补贴政策敏感；出口受汇率和关税影响",
        "transmission_hints": [
            "利率下行 → 融资成本降低 → 扩产压力减轻",
            "人民币贬值 → 出口竞争力提升 → 海外收入折算增加",
            "欧美关税政策 → 出口受阻 → 海外扩张计划承压",
        ],
    },
    "能源/资源": {
        "rate_sensitive": 0.2,
        "rate_direction": "neutral",
        "fx_sensitive": 0.7,
        "cycle_sensitive": 0.7,
        "geopolitical_sensitive": 0.8,  # 地缘影响油价
        "liquidity_sensitive": 0.3,
        "notes": "商品价格驱动；DXY走势直接影响大宗商品定价；地缘冲突影响供给",
        "transmission_hints": [
            "DXY走强 → 美元计价大宗商品承压 → 能源股利空",
            "地缘冲突升级 → 供给中断担忧 → 油价上涨 → 能源股利好",
            "全球经济复苏 → 能源需求增加 → 量价齐升",
        ],
    },
    "医药": {
        "rate_sensitive": 0.4,
        "rate_direction": "negative",
        "fx_sensitive": 0.3,
        "cycle_sensitive": 0.2,  # 防御性行业
        "geopolitical_sensitive": 0.4,
        "liquidity_sensitive": 0.5,
        "notes": "防御性行业，经济下行期有避险属性；CXO受地缘政治影响",
        "transmission_hints": [
            "经济衰退 → 资金涌入防御板块 → 医药相对收益",
            "利率下行 → 创新药DCF估值提升 → 利好研发型药企",
            "中美脱钩 → CXO订单转移 → 需关注具体公司海外业务占比",
        ],
    },
    "地产/物业": {
        "rate_sensitive": 0.9,  # 极高
        "rate_direction": "positive",
        "fx_sensitive": 0.3,
        "cycle_sensitive": 0.7,
        "geopolitical_sensitive": 0.2,
        "liquidity_sensitive": 0.9,
        "notes": "对利率和流动性极度敏感；关注LPR、按揭利率、房企融资政策",
        "transmission_hints": [
            "LPR下调 → 按揭利率降低 → 购房需求释放 → 销售回暖",
            "降准降息 → 房企融资改善 → 现金流压力缓解",
            "社融超预期 → 信用扩张 → 地产行业流动性改善",
        ],
    },
    "电信/公用事业": {
        "rate_sensitive": 0.5,
        "rate_direction": "negative",  # 高股息 vs 债券的替代效应
        "fx_sensitive": 0.2,
        "cycle_sensitive": 0.1,  # 防御性极强
        "geopolitical_sensitive": 0.3,
        "liquidity_sensitive": 0.3,
        "notes": "防御性行业，高股息；在利率下行环境中因股息率优势而受追捧",
        "transmission_hints": [
            "利率下行 → 债券收益率下降 → 高股息股吸引力上升",
            "经济不确定性上升 → 资金避险 → 公用事业防御价值凸显",
        ],
    },
    "科技/软件": {
        "rate_sensitive": 0.7,
        "rate_direction": "negative",
        "fx_sensitive": 0.4,
        "cycle_sensitive": 0.5,
        "geopolitical_sensitive": 0.5,
        "liquidity_sensitive": 0.6,
        "notes": "成长型行业，DCF估值对利率敏感；AI主题提供额外催化剂",
        "transmission_hints": [
            "利率下行 → 科技股估值扩张 → SaaS/云服务等高PS标的受益最大",
            "PMI上行 → 企业IT支出增加 → 软件/SaaS收入增长",
        ],
    },
}

# 公司名 → 行业映射（动态解析 + 模糊匹配）
COMPANY_SECTOR_HINTS: dict[str, str] = {
    # 银行
    "汇丰": "银行", "HSBC": "银行", "中银香港": "银行", "渣打": "银行",
    "恒生银行": "银行", "东亚银行": "银行", "招商银行": "银行",
    "工行": "银行", "工商银行": "银行", "建行": "银行", "建设银行": "银行",
    "农行": "银行", "农业银行": "银行", "中国银行": "银行",
    # 保险
    "友邦": "保险", "AIA": "保险", "平安": "保险", "国寿": "保险", "中国人寿": "保险",
    "太保": "保险", "中国太保": "保险",
    # 互联网
    "腾讯": "互联网平台", "Tencent": "互联网平台",
    "阿里": "互联网平台", "Alibaba": "互联网平台",
    "美团": "互联网平台", "Meituan": "互联网平台",
    "京东": "互联网平台", "JD": "互联网平台",
    "快手": "互联网平台", "Kuaishou": "互联网平台",
    "网易": "互联网平台", "NetEase": "互联网平台",
    "百度": "互联网平台", "Baidu": "互联网平台",
    "哔哩哔哩": "互联网平台", "Bilibili": "互联网平台",
    # 半导体/硬件
    "中芯国际": "半导体/硬件", "华虹": "半导体/硬件",
    "舜宇": "半导体/硬件", "瑞声": "半导体/硬件",
    "小米": "科技/软件", "Xiaomi": "科技/软件",
    # 消费
    "茅台": "消费", "五粮液": "消费",
    "李宁": "消费", "安踏": "消费", "ANTA": "消费",
    "海底捞": "消费", "农夫山泉": "消费",
    "蒙牛": "消费", "伊利": "消费",
    "百威": "消费", "华润啤酒": "消费",
    # 新能源汽车
    "比亚迪": "新能源汽车", "BYD": "新能源汽车",
    "理想": "新能源汽车", "Li Auto": "新能源汽车",
    "蔚来": "新能源汽车", "NIO": "新能源汽车",
    "小鹏": "新能源汽车", "XPeng": "新能源汽车",
    "吉利": "新能源汽车", "Geely": "新能源汽车",
    # 能源
    "中海油": "能源/资源", "CNOOC": "能源/资源",
    "中石油": "能源/资源", "中石化": "能源/资源",
    "紫金": "能源/资源", "神华": "能源/资源",
    # 医药
    "药明": "医药", "WuXi": "医药",
    "百济": "医药", "信达": "医药",
    "中生制药": "医药", "石药": "医药",
    "翰森": "医药", "康希诺": "医药",
    # 地产
    "新鸿基": "地产/物业", "长实": "地产/物业",
    "恒基": "地产/物业", "新世界": "地产/物业",
    "华润置地": "地产/物业", "龙湖": "地产/物业",
    "碧桂园": "地产/物业", "万科": "地产/物业",
    # 电信
    "中移动": "电信/公用事业", "中国移动": "电信/公用事业",
    "联通": "电信/公用事业", "中国联通": "电信/公用事业",
    "中国电信": "电信/公用事业",
}


def get_stock_macro_context(symbol: str, market: str, company_name: str = "") -> dict:
    """获取标的的宏观分析上下文

    Args:
        symbol: 标的代码
        market: 市场（A/HK/US）
        company_name: 公司名（如果已知，如从腾讯API解析）

    Returns:
        宏观上下文 dict，包含行业分类、敏感因子、传导链提示
    """
    sector = _infer_sector(company_name, symbol)
    sensitivity = SECTOR_MACRO_SENSITIVITY.get(sector, _default_sensitivity())

    context = {
        "symbol": symbol,
        "market": market,
        "company_name": company_name or symbol,
        "inferred_sector": sector,
        "macro_sensitivity": {
            "rate_sensitive": sensitivity.get("rate_sensitive", 0.5),
            "rate_direction": sensitivity.get("rate_direction", "neutral"),
            "fx_sensitive": sensitivity.get("fx_sensitive", 0.4),
            "cycle_sensitive": sensitivity.get("cycle_sensitive", 0.5),
            "geopolitical_sensitive": sensitivity.get("geopolitical_sensitive", 0.5),
            "liquidity_sensitive": sensitivity.get("liquidity_sensitive", 0.5),
        },
        "sector_notes": sensitivity.get("notes", ""),
        "transmission_hints": sensitivity.get("transmission_hints", [
            "通用传导链：利率下行→估值提升；PMI上行→盈利改善；人民币升值→外资流入",
        ]),
    }

    # 市场级叠加
    if market == "HK":
        context["market_note"] = (
            "港股=中国资产+美元定价。美联储政策对港股的影响甚至大于中国央行。"
            "关注港汇(HKD)是否触及弱方兑换保证(7.85)和南向资金流向。"
        )
        context["transmission_hints"].append(
            "港汇触及7.85→金管局干预→短期流动性收紧→港股承压"
        )
    elif market == "A":
        context["market_note"] = (
            "A股政策驱动特征明显。关注中央经济工作会议/国常会政策定调。"
            "社融数据是领先指标，北向资金是短期情绪风向标。"
        )
        context["transmission_hints"].append(
            "政策利好→北向资金流入→权重股领涨→指数上行"
        )
    elif market == "US":
        context["market_note"] = (
            "美股=Fed第一驱动力。就业数据(非农)的即时市场反应往往比CPI更剧烈。"
            "企业回购是重要买方力量。"
        )

    return context


def _infer_sector(company_name: str, symbol: str) -> str:
    """推断标的所属行业

    策略：
    1. 公司名关键词匹配（COMPANY_SECTOR_HINTS）
    2. 代码匹配（部分知名标的）
    3. 默认为"综合"
    """
    if not company_name:
        return "综合"

    # 精确匹配
    for hint, sector in COMPANY_SECTOR_HINTS.items():
        if hint.lower() in company_name.lower():
            logger.debug(f"行业推断: {company_name} → {sector} (匹配'{hint}')")
            return sector

    # 模糊推断（基于公司名中的行业关键词）
    sector_keywords = [
        (["银行"], "银行"),
        (["保险"], "保险"),
        (["科技", "软件", "云", "AI", "智能"], "科技/软件"),
        (["半导体", "芯片", "电子", "光电", "光学"], "半导体/硬件"),
        (["汽车", "新能源", "锂电", "动力电池"], "新能源汽车"),
        (["药", "生物", "医疗", "健康", "基因"], "医药"),
        (["地产", "置业", "物业", "房产"], "地产/物业"),
        (["能源", "石油", "石化", "煤炭", "矿业", "黄金", "铜", "铝"], "能源/资源"),
        (["消费", "食品", "饮料", "酒", "奶", "啤酒"], "消费"),
        (["通信", "电信", "联通", "移动"], "电信/公用事业"),
        (["电力", "水务", "燃气", "公用"], "电信/公用事业"),
        (["航空", "机场", "港口", "铁路", "高速", "物流"], "综合"),
        (["互联网", "游戏", "视频", "电商", "外卖", "平台"], "互联网平台"),
    ]

    for keywords, sector in sector_keywords:
        for kw in keywords:
            if kw in company_name:
                logger.debug(f"行业推断: {company_name} → {sector} (关键词'{kw}')")
                return sector

    logger.debug(f"行业推断: {company_name} → 综合 (无匹配)")
    return "综合"


def _default_sensitivity() -> dict:
    """默认宏观敏感度（综合性标的）"""
    return {
        "rate_sensitive": 0.5,
        "rate_direction": "neutral",
        "fx_sensitive": 0.4,
        "cycle_sensitive": 0.5,
        "geopolitical_sensitive": 0.5,
        "liquidity_sensitive": 0.5,
        "notes": "综合性标的，各宏观因子影响较均衡",
        "transmission_hints": [
            "利率下行→估值提升；PMI上行→盈利改善；人民币升值→外资流入",
            "由于行业不明确，宏观分析置信度应适当降低",
        ],
    }
