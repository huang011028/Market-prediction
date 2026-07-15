from src.data.quant_pit_enrichment import (
    AnnouncementEvent,
    FundamentalEvent,
    IndustryMembership,
    PerformanceEvent,
    QuantPitEnrichmentStore,
)


def test_enrichment_features_respect_effective_dates(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    store.upsert_fundamentals([
        FundamentalEvent(
            market="A",
            symbol="000001",
            effective_date="2025-04-25",
            report_date="2025-03-31",
            features={"revenue_yoy_pct": 8.5, "netprofit_yoy_pct": 6.2},
        ),
    ])
    store.upsert_announcements([
        AnnouncementEvent("A", "000001", "2025-04-20", "一季度报告", "earnings"),
        AnnouncementEvent("A", "000001", "2025-05-28", "监管处罚公告", "risk"),
    ])

    before, before_lineage = store.features_as_of("000001", "2025-04-24")
    after, after_lineage = store.features_as_of("000001", "2025-05-01")

    assert before["fundamental__available"] == 0
    assert before_lineage["fundamental"] is None
    assert after["fundamental__available"] == 1
    assert after["fundamental__revenue_yoy_pct"] == 8.5
    assert after["fundamental__field_completeness"] > 0
    assert 0 <= after["fundamental__quality_score"] <= 1
    assert after["news__earnings_count_30d"] == 1
    assert after["news__risk_count_30d"] == 0
    assert after_lineage["fundamental"]["effective_date"] == "2025-04-25"


def test_industry_membership_uses_effective_interval(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    standard = store.DEFAULT_INDUSTRY_STANDARD
    store.replace_industry_memberships("000001", "A", [
        IndustryMembership(
            "A", "000001", standard, "金融", "银行", "股份制银行",
            "2020-01-01", "2025-01-01",
        ),
        IndustryMembership(
            "A", "000001", standard, "金融", "多元金融", "金融科技",
            "2025-01-01", None,
        ),
    ])

    old, _ = store.features_as_of("000001", "2024-12-31")
    new, lineage = store.features_as_of("000001", "2025-01-01")

    assert old["meta__industry"] == "银行"
    assert new["meta__industry"] == "多元金融"
    assert new["meta__industry_pit_verified"] is True
    assert lineage["industry"]["effective_from"] == "2025-01-01"


def test_enrichment_status_reports_source_coverage(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    store.upsert_announcements([
        AnnouncementEvent("A", "000001", "2025-01-01", "重大合同公告", "operations"),
    ])

    status = store.status()

    assert status["announcement_events"]["records"] == 1
    assert status["announcement_events"]["symbols"] == 1


def test_performance_surprise_and_event_intensity_are_point_in_time(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    store.upsert_performance_events([
        PerformanceEvent(
            "A", "000001", "guidance", "2025-04-10", "2025-03-31",
            {"netprofit_yoy_pct": 10.0, "forecast_type": "预增"},
            "guidance",
        ),
        PerformanceEvent(
            "A", "000001", "flash", "2025-04-20", "2025-03-31",
            {"eps": 0.4, "book_value_per_share": 5.0},
            "flash",
        ),
    ])
    store.upsert_fundamentals([
        FundamentalEvent(
            "A", "000001", "2025-04-25", "2025-03-31",
            {"netprofit_yoy_pct": 16.0, "basic_eps": 0.5},
        ),
    ])
    store.upsert_announcements([
        AnnouncementEvent("A", "000001", "2025-04-26", "重大合同公告", "operations"),
        AnnouncementEvent("A", "000001", "2025-04-27", "监管处罚公告", "risk"),
    ])

    before, _ = store.features_as_of("000001", "2025-04-24")
    after, lineage = store.features_as_of("000001", "2025-04-28")

    assert "fundamental__netprofit_surprise_vs_guidance_pct" not in before
    assert after["fundamental__netprofit_surprise_vs_guidance_pct"] == 6.0
    assert after["fundamental__eps_surprise_vs_flash_pct"] == 25.0
    assert after["fundamental__performance_available"] == 1
    assert after["fundamental__surprise_available"] == 1
    assert after["fundamental__high_quality"] == 1
    assert after["news__event_intensity_30d"] > 0
    assert after["news__risk_intensity_30d"] > 0
    assert after["news__catalyst_intensity_30d"] > 0
    assert lineage["performance"]["report_date"] == "2025-03-31"
    assert lineage["fundamental_quality"]["surprise_available"] is True


def test_flash_can_fill_pit_fundamental_fields_before_formal_statement(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    store.upsert_performance_events([
        PerformanceEvent(
            "A", "000001", "flash", "2025-04-20", "2025-03-31",
            {
                "eps": 0.4,
                "revenue_yoy_pct": 12.0,
                "netprofit_yoy_pct": 18.0,
                "book_value_per_share": 5.0,
            },
            "flash",
        ),
    ])

    features, lineage = store.features_as_of("000001", "2025-04-22")

    assert features["fundamental__available"] == 1
    assert features["fundamental__performance_available"] == 1
    assert features["fundamental__revenue_yoy_pct"] == 12.0
    assert features["fundamental__basic_eps"] == 0.4
    assert features["fundamental__high_quality"] == 1
    assert lineage["fundamental"] is None
    assert lineage["performance"]["event_kind"] == "flash"
