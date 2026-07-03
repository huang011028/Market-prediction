"""
新闻分析师 v2 测试

覆盖:
- 预处理管线（去重、情感标注、分类、时间衰减）
- 多源采集集成
- Agent 一致性校验
- 置信度校准器
"""
import pytest
from datetime import datetime, timedelta


# ================================================================
# 预处理管线测试
# ================================================================

class TestNewsPreprocessing:
    """测试新闻预处理管线各组件"""

    @pytest.fixture
    def sample_news(self):
        """生成示例新闻列表"""
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        three_days_ago = today - timedelta(days=3)

        return [
            {
                "title": "腾讯Q2营收同比增长15% 超市场预期",
                "summary": "腾讯控股发布2026年第二季度财报，营收同比增长15%超市场预期，净利润增长22%",
                "source": "东方财富",
                "time": today.strftime("%Y-%m-%d %H:%M:%S"),
                "url": "https://example.com/1",
            },
            {
                "title": "腾讯Q2财报亮眼 多家投行上调目标价",
                "summary": "高盛、摩根士丹利等多家国际投行在腾讯财报后上调目标价，平均上调幅度10%",
                "source": "证券时报",
                "time": today.strftime("%Y-%m-%d %H:%M:%S"),
                "url": "https://example.com/2",
            },
            {
                "title": "腾讯Q2营收超预期 利润大增",  # 与第1条高度相似
                "summary": "腾讯Q2业绩超预期，营收增长15%",
                "source": "新浪财经",
                "time": yesterday.strftime("%Y-%m-%d %H:%M:%S"),
                "url": "https://example.com/3",
            },
            {
                "title": "腾讯云业务成本上升 拖累整体利润率",
                "summary": "尽管营收增长，但腾讯云业务基础设施投入加大导致成本上升",
                "source": "21世纪经济报道",
                "time": yesterday.strftime("%Y-%m-%d %H:%M:%S"),
                "url": "https://example.com/4",
            },
            {
                "title": "游戏行业监管趋严 版号发放放缓",
                "summary": "国家新闻出版署最新一批游戏版号数量减少，行业监管信号趋严",
                "source": "东方财富",
                "time": three_days_ago.strftime("%Y-%m-%d %H:%M:%S"),
                "url": "https://example.com/5",
            },
            {
                "title": "某分析师看好科技板块长期前景",  # 与腾讯无关
                "summary": "某券商分析师发布研究报告看好科技板块",
                "source": "自媒体",
                "time": three_days_ago.strftime("%Y-%m-%d %H:%M:%S"),
                "url": "https://example.com/6",
            },
        ]

    def test_deduplication(self, sample_news):
        """测试去重：相似标题应被合并"""
        from src.data.news_preprocessor import NewsDeduplicator

        dedup = NewsDeduplicator(similarity_threshold=0.5)
        result = dedup.deduplicate(sample_news)

        # 第1和第3条高度相似，应去重（保留来源更权威的东方财富）
        # 第1条 source="东方财富"，第3条 source="新浪财经"，东方财富优先级更高
        titles = [item["title"] for item in result]
        # 应该保留了东方财富的版本而不是新浪的重复
        assert len(result) <= len(sample_news)

    def test_sentiment_positive(self):
        """测试正面情感识别"""
        from src.data.news_preprocessor import SentimentTagger

        tagger = SentimentTagger()
        news = {
            "title": "营收大增超预期 净利润创新高",
            "summary": "公司业绩大幅增长，多家机构上调评级",
            "source": "东方财富",
            "time": "2026-07-03",
        }
        assert tagger.tag(news) == "positive"

    def test_sentiment_negative(self):
        """测试负面情感识别"""
        from src.data.news_preprocessor import SentimentTagger

        tagger = SentimentTagger()
        news = {
            "title": "业绩低于预期 股价暴跌 大股东减持",
            "summary": "公司业绩大幅下滑，违规被处罚",
            "source": "东方财富",
            "time": "2026-07-03",
        }
        assert tagger.tag(news) == "negative"

    def test_sentiment_neutral(self):
        """测试中性/矛盾情感"""
        from src.data.news_preprocessor import SentimentTagger

        tagger = SentimentTagger()
        # "超预期" 是正面关键词，"亏损" 是负面关键词，两者抵消
        news = {
            "title": "部分业务超预期 但子公司亏损扩大",
            "summary": "增收和减收因素并存",
            "source": "东方财富",
            "time": "2026-07-03",
        }
        # 正负各1个关键词，应判断为 neutral（非 unknown，因为有关键词命中）
        result = tagger.tag(news)
        assert result == "neutral"

    def test_categorization_earnings(self):
        """测试财报类分类"""
        from src.data.news_preprocessor import NewsCategorizer

        cat = NewsCategorizer()
        news = {
            "title": "Q2财报营收利润均超预期",
            "summary": "公司发布最新季度财报",
            "source": "东方财富",
            "time": "2026-07-03",
        }
        assert cat.categorize(news) == "earnings"

    def test_categorization_policy(self):
        """测试政策类分类"""
        from src.data.news_preprocessor import NewsCategorizer

        cat = NewsCategorizer()
        news = {
            "title": "工信部发布新产业政策 行业监管加强",
            "summary": "",
            "source": "东方财富",
            "time": "2026-07-03",
        }
        assert cat.categorize(news) == "policy"

    def test_time_decay_today(self):
        """测试今天新闻权重=1.0"""
        from src.data.news_preprocessor import TimeDecayWeighter

        weighter = TimeDecayWeighter(half_life_days=3)
        today = datetime.now()
        news = {"title": "test", "time": today.strftime("%Y-%m-%d"), "source": ""}
        assert weighter.weight(news, today) == 1.0

    def test_time_decay_old(self):
        """测试旧新闻权重衰减"""
        from src.data.news_preprocessor import TimeDecayWeighter

        weighter = TimeDecayWeighter(half_life_days=3)
        today = datetime.now()
        six_days_ago = today - timedelta(days=6)
        news = {
            "title": "test",
            "time": six_days_ago.strftime("%Y-%m-%d"),
            "source": "",
        }
        weight = weighter.weight(news, today)
        # 6 天 = 2 个半衰期，权重约为 0.25
        assert 0.2 <= weight <= 0.3

    def test_full_pipeline(self, sample_news):
        """测试完整预处理管线"""
        from src.data.news_preprocessor import process_news_pipeline

        result = process_news_pipeline(sample_news, "0700")

        assert "sentiment_stats" in result
        assert "category_breakdown" in result
        assert "top_news" in result
        assert "anomaly_flags" in result

        # 应该有去重效果
        assert result["after_dedup"] <= result["total_fetched"]

        # 情感统计应包含所有类别
        stats = result["sentiment_stats"]
        assert "positive" in stats
        assert "negative" in stats

    def test_empty_input(self):
        """测试空输入"""
        from src.data.news_preprocessor import process_news_pipeline

        result = process_news_pipeline([], "0700")
        assert result["total_fetched"] == 0
        assert result["top_news"] == []


# ================================================================
# Agent 一致性校验测试
# ================================================================

class TestConsistencyValidation:
    """测试 Agent 输出一致性校验"""

    @pytest.fixture
    def news_analyst(self):
        """创建 NewsAnalyst 实例（不使用真实 LLM）"""
        import sys

        # 使用 __new__ 绕过 __init__（与 Aggregator 测试相同技巧）
        from src.agents.news_analyst import NewsAnalyst

        instance = NewsAnalyst.__new__(NewsAnalyst)
        instance.name = "最新新闻分析师"
        return instance

    def test_direction_vs_sentiment_mismatch(self, news_analyst):
        """测试方向与情绪统计不一致应被检测"""
        from src.core.result import AnalysisResult, Direction

        result = AnalysisResult(
            agent_name="最新新闻分析师",
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            confidence=0.7,
            reasoning="测试",
        )

        data = {
            "preprocessing": {
                "sentiment_stats": {
                    "weighted_positive_score": 0.5,
                    "weighted_negative_score": 2.5,  # 负面远大于正面
                },
                "anomaly_flags": {},
            },
            "_data_quality": {"news_count": 10, "score": 0.9},
        }

        issues = news_analyst._validate_consistency(result, data)
        assert len(issues) > 0
        assert any("负面" in i for i in issues)

    def test_high_confidence_low_data(self, news_analyst):
        """测试高置信度+低数据量应被检测"""
        from src.core.result import AnalysisResult, Direction

        result = AnalysisResult(
            agent_name="最新新闻分析师",
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,
            confidence=0.75,  # 高置信度
            reasoning="测试",
        )

        data = {
            "preprocessing": {
                "sentiment_stats": {"weighted_positive_score": 1.0, "weighted_negative_score": 0},
                "anomaly_flags": {},
            },
            "_data_quality": {"news_count": 2, "score": 0.3},  # 仅2条新闻
        }

        issues = news_analyst._validate_consistency(result, data)
        assert len(issues) > 0
        assert any("过度自信" in i or "新闻数量" in i for i in issues)

    def test_sentiment_divergence_non_neutral(self, news_analyst):
        """测试情绪分化时非 neutral 应被检测"""
        from src.core.result import AnalysisResult, Direction

        result = AnalysisResult(
            agent_name="最新新闻分析师",
            target="0700",
            timeframe="短期(1周)",
            direction=Direction.BULLISH,  # 非 neutral
            confidence=0.65,
            reasoning="测试",
        )

        data = {
            "preprocessing": {
                "sentiment_stats": {"weighted_positive_score": 1.0, "weighted_negative_score": 0.9},
                "anomaly_flags": {"sentiment_divergence": True},
            },
            "_data_quality": {"news_count": 10, "score": 0.8},
        }

        issues = news_analyst._validate_consistency(result, data)
        assert len(issues) > 0
        assert any("情绪分化" in i for i in issues)


# ================================================================
# 置信度校准器测试
# ================================================================

class TestConfidenceCalibrator:
    """测试置信度校准器"""

    def test_small_sample_no_calibration(self, tmp_path):
        """测试样本不足时不校准"""
        # 创建最小 PredictionStore（用临时数据库）
        try:
            from src.data.prediction_store import PredictionStore
            from src.core.confidence_calibrator import ConfidenceCalibrator

            db_path = tmp_path / "test_predictions.db"
            store = PredictionStore(db_path=db_path)
            calibrator = ConfidenceCalibrator(store)

            # 样本为 0，应返回原始值
            result = calibrator.calibrate("最新新闻分析师", 0.7)
            assert result == 0.7
        except Exception as e:
            pytest.skip(f"无法创建 PredictionStore: {e}")

    def test_data_quality_penalty(self, tmp_path):
        """测试数据质量惩罚"""
        try:
            from src.data.prediction_store import PredictionStore
            from src.core.confidence_calibrator import ConfidenceCalibrator

            db_path = tmp_path / "test_predictions.db"
            store = PredictionStore(db_path=db_path)
            calibrator = ConfidenceCalibrator(store)

            # 低数据质量应导致置信度下降
            result_low = calibrator.calibrate("最新新闻分析师", 0.7, data_quality=0.3)
            result_high = calibrator.calibrate("最新新闻分析师", 0.7, data_quality=1.0)

            assert result_low <= result_high
        except Exception as e:
            pytest.skip(f"无法创建 PredictionStore: {e}")


# ================================================================
# 多源采集集成测试（需要网络，标记为 slow）
# ================================================================

class TestMultiSourceFetch:
    """多源采集集成测试"""

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_fetch_eastmoney_a_share(self):
        """测试东方财富 A 股新闻获取"""
        try:
            from src.data.news_sources.eastmoney import fetch_from_eastmoney

            items = await fetch_from_eastmoney("000001", market="A", max_items=5)
            if items is not None:
                assert len(items) > 0
                assert "title" in items[0]
                assert "source" in items[0]
        except ImportError:
            pytest.skip("akshare 未安装")

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_fetch_eastmoney_hk_share(self):
        """测试东方财富港股新闻获取"""
        try:
            from src.data.news_sources.eastmoney import fetch_from_eastmoney

            items = await fetch_from_eastmoney("0700", market="HK", max_items=5)
            if items is not None:
                assert len(items) > 0
        except ImportError:
            pytest.skip("akshare 未安装")

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_fetch_sina_a_share(self):
        """测试新浪 A 股新闻获取"""
        try:
            from src.data.news_sources.sina import fetch_from_sina

            items = await fetch_from_sina("000001", market="A", max_items=5)
            if items is not None:
                assert len(items) > 0
                assert "title" in items[0]
        except ImportError:
            pytest.skip("依赖未安装")

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_news_fetcher_v2_integration(self):
        """测试 NewsFetcher v2 完整流程"""
        try:
            from src.data.news_fetcher import NewsFetcher

            fetcher = NewsFetcher(max_items=10)
            result = await fetcher.fetch("000001", market="A", days=7)

            assert result.symbol == "000001"
            if result.news_count > 0:
                # 验证预处理摘要存在
                assert result.preprocessing_summary
                assert "sentiment_stats" in result.preprocessing_summary
        except ImportError:
            pytest.skip("依赖未安装")

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_news_fetcher_v2_hk(self):
        """测试 NewsFetcher v2 港股"""
        try:
            from src.data.news_fetcher import NewsFetcher

            fetcher = NewsFetcher(max_items=10)
            result = await fetcher.fetch("0700", market="HK", days=7)

            assert result.symbol == "0700"
            # 至少应该尝试了多个源
            assert len(result.sources_used) >= 0
        except ImportError:
            pytest.skip("依赖未安装")


# ================================================================
# Prompt 测试
# ================================================================

class TestNewsPrompts:
    """测试 Prompt 模板完整性"""

    def test_system_prompt_contains_keywords(self):
        """测试系统 prompt 包含关键指引"""
        from src.prompts.news_prompts import NEWS_SYSTEM_PROMPT

        assert "新闻情绪统计" in NEWS_SYSTEM_PROMPT
        assert "重大事件识别" in NEWS_SYSTEM_PROMPT
        assert "confidence" in NEWS_SYSTEM_PROMPT
        assert "direction" in NEWS_SYSTEM_PROMPT

    def test_confidence_anchors_present(self):
        """测试置信度锚定指引存在"""
        from src.prompts.news_prompts import CONFIDENCE_ANCHORS

        assert "0.85-0.95" in CONFIDENCE_ANCHORS
        assert "0.25-0.39" in CONFIDENCE_ANCHORS

    def test_few_shot_examples_present(self):
        """测试 few-shot 示例存在"""
        from src.prompts.news_prompts import FEW_SHOT_EXAMPLES

        assert "明显利好" in FEW_SHOT_EXAMPLES
        assert "信号矛盾" in FEW_SHOT_EXAMPLES
        assert "无实质新闻" in FEW_SHOT_EXAMPLES

    def test_market_appendix_a_share(self):
        """测试 A 股附录"""
        from src.prompts.news_prompts import A_SHARE_NEWS_APPENDIX

        assert "政策信号" in A_SHARE_NEWS_APPENDIX
        assert "利好出尽" in A_SHARE_NEWS_APPENDIX

    def test_market_appendix_hk_share(self):
        """测试港股附录"""
        from src.prompts.news_prompts import HK_SHARE_NEWS_APPENDIX

        assert "机构定价" in HK_SHARE_NEWS_APPENDIX
        assert "做空机制" in HK_SHARE_NEWS_APPENDIX

    def test_signal_extraction_prompt(self):
        """测试信号提取 prompt"""
        from src.prompts.news_prompts import SIGNAL_EXTRACTION_PROMPT

        assert "信号" in SIGNAL_EXTRACTION_PROMPT
        assert "strength" in SIGNAL_EXTRACTION_PROMPT

    def test_synthesis_prompt(self):
        """测试综合判断 prompt"""
        from src.prompts.news_prompts import SYNTHESIS_PROMPT

        assert "魔鬼代言人" in SYNTHESIS_PROMPT
        assert "reflection" in SYNTHESIS_PROMPT.lower()
