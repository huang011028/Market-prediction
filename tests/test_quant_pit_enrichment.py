from src.data.quant_pit_enrichment import (
    AnnouncementEvent,
    FundamentalEvent,
    FactorEvent,
    IndustryMembership,
    PerformanceEvent,
    QuantPitEnrichmentStore,
    QuantPitRefreshConfig,
    _fetch_consensus_factor_events,
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


def test_financial_quality_and_consensus_are_strictly_point_in_time(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    store.upsert_factor_events([
        FactorEvent(
            "A", "000001", "balance_sheet", "2025-04-25", "2025-03-31",
            {"total_assets": 1000, "total_liabilities": 600, "total_equity": 400,
             "share_capital": 100},
        ),
        FactorEvent(
            "A", "000001", "cash_flow", "2025-04-25", "2025-03-31",
            {"operating_cash_flow": 80, "capex_cash_paid": 20, "net_profit": 50},
        ),
        FactorEvent(
            "A", "000001", "consensus_report", "2025-03-01", "2025-12-31",
            {"eps_by_year": {"2025": 1.0, "2026": 1.2}, "organization": "机构甲",
             "rating_score": 1.0}, source_ref="report-1",
        ),
        FactorEvent(
            "A", "000001", "consensus_report", "2025-04-01", "2025-12-31",
            {"eps_by_year": {"2025": 1.1, "2026": 1.3}, "organization": "机构乙",
             "rating_score": 0.75}, source_ref="report-2",
        ),
    ])

    before, _ = store.features_as_of("000001", "2025-04-24")
    after, lineage = store.features_as_of("000001", "2025-04-26")

    assert before["balance__available"] == 0
    assert before["consensus__available"] == 1
    assert after["balance__leverage_ratio"] == 0.6
    assert after["cashflow__free_cash_flow"] == 60.0
    assert after["cashflow__cash_conversion_ratio"] == 1.6
    assert after["cashflow__accrual_ratio"] == -0.03
    assert after["consensus__eps_current_year_median"] == 1.05
    assert after["consensus__eps_next_year_median"] == 1.25
    assert lineage["cash_flow"]["effective_date"] == "2025-04-25"
    assert lineage["consensus"]["forecast_year_mapping"] == "publication_year_relative"


def test_consensus_annual_surprise_uses_only_reports_before_result(tmp_path):
    store = QuantPitEnrichmentStore(tmp_path / "pit.db")
    store.upsert_factor_events([
        FactorEvent(
            "A", "000001", "consensus_report", "2025-02-01", "2024-12-31",
            {"eps_by_year": {"2024": 1.0}, "organization": "机构甲"}, source_ref="before",
        ),
        FactorEvent(
            "A", "000001", "consensus_report", "2025-04-26", "2025-12-31",
            {"eps_by_year": {"2024": 1.3}, "organization": "机构乙"}, source_ref="after",
        ),
    ])
    store.upsert_fundamentals([
        FundamentalEvent(
            "A", "000001", "2025-04-25", "2024-12-31", {"basic_eps": 1.2},
        ),
    ])

    features, _ = store.features_as_of("000001", "2025-04-28")

    assert features["consensus__annual_eps_expected"] == 1.0
    assert features["consensus__annual_eps_surprise_pct"] == 20.0
    assert features["consensus__annual_surprise_reports"] == 1


def test_raw_consensus_maps_forecasts_relative_to_publication_year(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "TotalPage": 1,
                "data": [{
                    "publishDate": "2024-07-05", "infoCode": "r1", "orgSName": "机构甲",
                    "emRatingName": "买入", "predictThisYearEps": 2.6,
                    "predictNextYearEps": 3.0, "predictNextTwoYearEps": 3.4,
                }],
            }

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response())
    events = _fetch_consensus_factor_events(
        "000001",
        QuantPitRefreshConfig(
            ["000001"], "2024-01-01", "2024-12-31",
            include_fundamental=False, include_performance=False,
            include_announcements=False, include_industry=False,
        ),
    )

    assert events[0].features["eps_by_year"] == {"2024": 2.6, "2025": 3.0, "2026": 3.4}
