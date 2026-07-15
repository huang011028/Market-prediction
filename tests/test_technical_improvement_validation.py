from src.core.calibration_bootstrap import CalibrationSample
from src.core.agent_skill_registry import AgentSkillRegistry
from src.core.technical_improvement_validation import (
    TechnicalConfidencePolicyValidator,
    TechnicalConfidencePolicyValidationReport,
    TechnicalConfidencePolicyDecision,
    TechnicalImprovementValidationReport,
    TechnicalValidationDecision,
    TechnicalImprovementHoldoutValidator,
    TechnicalImprovementRule,
)


def _sample(
    target,
    predicted,
    actual_change,
    buckets,
    was_correct=False,
    window_max_change_pct=None,
    window_min_change_pct=None,
):
    return CalibrationSample(
        agent_name="近期股价分析师",
        target=target,
        as_of="2025-01-01",
        valid_date="2025-01-08",
        predicted_direction=predicted,
        predicted_confidence=0.7,
        actual_direction="neutral",
        actual_change_pct=actual_change,
        was_correct=was_correct,
        price_start=10,
        price_end=10 * (1 + actual_change / 100),
        buckets=buckets,
        window_max_change_pct=window_max_change_pct,
        window_min_change_pct=window_min_change_pct,
    )


def test_build_rules_filters_to_prompt_and_skill_low_accuracy_buckets():
    report = {
        "wrong_strategy_signals": [
            {
                "area": "skill",
                "bucket_group": "trend_buckets",
                "bucket": "up",
                "sample_size": 30,
                "unique_cases": 20,
                "accuracy": 0.3,
                "dominant_actual_direction": "neutral",
                "dominant_actual_rate": 0.7,
            },
            {
                "area": "calibration",
                "bucket_group": "confidence_bins",
                "bucket": "0.6-0.8",
                "sample_size": 30,
                "accuracy": 0.2,
            },
            {
                "area": "prompt",
                "bucket_group": "volume_buckets",
                "bucket": "neutral",
                "sample_size": 4,
                "accuracy": 0.1,
            },
            {
                "area": "skill",
                "bucket_group": "market_regime_buckets",
                "bucket": "bear_trend",
                "sample_size": 30,
                "unique_cases": 20,
                "accuracy": 0.5,
            },
        ]
    }

    rules = TechnicalImprovementHoldoutValidator().build_rules(
        report,
        require_composite=False,
    )

    assert len(rules) == 1
    assert rules[0].bucket_group == "trend_buckets"
    assert rules[0].bucket == "up"
    assert rules[0].action == "neutralize_direction"


def test_regime_bucket_rule_matches_holdout_samples():
    rule = TechnicalImprovementRule(
        bucket_group="market_regime_buckets",
        bucket="bear_trend",
        area="skill",
    )
    sample = _sample(
        "A",
        "bullish",
        -2.0,
        {"market_regime_bucket": "bear_trend"},
    )

    assert rule.matches(sample)


def test_build_rules_uses_dominant_actual_direction_for_action():
    report = {
        "wrong_strategy_signals": [
            {
                "area": "skill",
                "bucket_group": "technical_scenario_buckets",
                "bucket": "bear_rebound|near_resistance|shrinking",
                "sample_size": 20,
                "unique_cases": 20,
                "accuracy": 0.2,
                "dominant_actual_direction": "bearish",
                "dominant_actual_rate": 0.65,
            },
            {
                "area": "skill",
                "bucket_group": "volatility_buckets",
                "bucket": "normal",
                "sample_size": 20,
                "unique_cases": 20,
                "accuracy": 0.2,
                "dominant_actual_direction": "bullish",
                "dominant_actual_rate": 0.45,
            },
        ]
    }

    rules = TechnicalImprovementHoldoutValidator().build_rules(report)

    assert len(rules) == 1
    assert rules[0].action == "force_bearish"
    assert rules[0].conditions == {
        "market_regime_bucket": "bear_rebound",
        "sr_zone_bucket": "near_resistance",
        "volume_bucket": "shrinking",
    }


def test_composite_rule_requires_all_bucket_conditions():
    rule = TechnicalImprovementRule(
        bucket_group="technical_scenario_buckets",
        bucket="bear_rebound|near_resistance|shrinking",
        area="skill",
        conditions={
            "market_regime_bucket": "bear_rebound",
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "shrinking",
        },
    )
    sample = _sample(
        "A",
        "neutral",
        -2.0,
        {
            "market_regime_bucket": "bear_rebound",
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "confirm_up",
            "technical_scenario_bucket": "bear_rebound|near_resistance|confirm_up",
        },
    )

    assert not rule.matches(sample)


def test_holdout_validation_can_force_direction_from_rule():
    validator = TechnicalImprovementHoldoutValidator()
    rules = [
        TechnicalImprovementRule(
            bucket_group="technical_scenario_buckets",
            bucket="bear_rebound|near_resistance|shrinking",
            area="skill",
            action="force_bearish",
            conditions={
                "market_regime_bucket": "bear_rebound",
                "sr_zone_bucket": "near_resistance",
                "volume_bucket": "shrinking",
            },
        )
    ]
    samples = [
        _sample(
            "A",
            "neutral",
            -2.0,
            {
                "market_regime_bucket": "bear_rebound",
                "sr_zone_bucket": "near_resistance",
                "volume_bucket": "shrinking",
            },
            was_correct=False,
        ),
        _sample("B", "neutral", 0.1, {"market_regime_bucket": "bull_trend"}, was_correct=True),
        _sample("C", "bearish", -2.0, {"market_regime_bucket": "bear_trend"}, was_correct=True),
        _sample("D", "neutral", -0.1, {"market_regime_bucket": "sideways_range"}, was_correct=True),
    ]

    decision, examples = validator.validate_samples(
        samples,
        rules,
        min_accuracy_delta=0.01,
        min_holdout_samples=4,
    )

    assert decision.should_apply is True
    assert decision.changed_predictions == 1
    assert examples[0]["candidate_direction"] == "bearish"


def test_holdout_validation_allows_apply_only_when_candidate_improves():
    validator = TechnicalImprovementHoldoutValidator()
    rules = [
        TechnicalImprovementRule(
            bucket_group="trend_buckets",
            bucket="up",
            area="skill",
            sample_size=30,
            unique_cases=30,
            accuracy=0.3,
        )
    ]
    samples = [
        _sample("A", "bullish", 0.2, {"trend_bucket": "up"}, was_correct=False),
        _sample("B", "bullish", -0.2, {"trend_bucket": "up"}, was_correct=False),
        _sample("C", "bearish", -2.0, {"trend_bucket": "down"}, was_correct=True),
        _sample("D", "neutral", 0.1, {"trend_bucket": "sideways"}, was_correct=True),
    ]

    decision, examples = validator.validate_samples(
        samples,
        rules,
        min_accuracy_delta=0.01,
        min_holdout_samples=4,
    )

    assert decision.should_apply is True
    assert decision.baseline_accuracy == 0.5
    assert decision.candidate_accuracy == 1.0
    assert decision.changed_predictions == 2
    assert len(examples) == 2


def test_holdout_validation_rejects_candidate_without_accuracy_gain():
    validator = TechnicalImprovementHoldoutValidator()
    rules = [
        TechnicalImprovementRule(
            bucket_group="trend_buckets",
            bucket="up",
            area="skill",
            sample_size=30,
            unique_cases=30,
            accuracy=0.3,
        )
    ]
    samples = [
        _sample("A", "bullish", 3.0, {"trend_bucket": "up"}, was_correct=True),
        _sample("B", "bearish", -2.0, {"trend_bucket": "down"}, was_correct=True),
        _sample("C", "neutral", 0.1, {"trend_bucket": "sideways"}, was_correct=True),
        _sample("D", "neutral", -0.1, {"trend_bucket": "sideways"}, was_correct=True),
    ]

    decision, _ = validator.validate_samples(
        samples,
        rules,
        min_accuracy_delta=0.01,
        min_holdout_samples=4,
    )

    assert decision.should_apply is False
    assert decision.candidate_accuracy < decision.baseline_accuracy


def test_holdout_candidate_uses_window_validation_when_available():
    validator = TechnicalImprovementHoldoutValidator()
    rules = [
        TechnicalImprovementRule(
            bucket_group="trend_buckets",
            bucket="up",
            area="skill",
            sample_size=30,
            unique_cases=30,
            accuracy=0.3,
        )
    ]
    samples = [
        _sample(
            "A",
            "bullish",
            0.2,
            {"trend_bucket": "up"},
            was_correct=False,
            window_max_change_pct=0.4,
            window_min_change_pct=-0.2,
        ),
        _sample("B", "bearish", -2.0, {"trend_bucket": "down"}, was_correct=True),
        _sample("C", "neutral", 0.1, {"trend_bucket": "sideways"}, was_correct=True),
        _sample("D", "neutral", -0.1, {"trend_bucket": "sideways"}, was_correct=True),
    ]

    decision, examples = validator.validate_samples(
        samples,
        rules,
        min_accuracy_delta=0.01,
        min_holdout_samples=4,
    )

    assert decision.should_apply is True
    assert decision.candidate_accuracy == 1.0
    assert examples[0]["candidate_correct"] is True


def test_holdout_passed_rules_write_agent_skill_registry(tmp_path):
    rule = TechnicalImprovementRule(
        bucket_group="regime_volume_buckets",
        bucket="bull_trend|neutral",
        area="skill",
        action="force_bullish",
        sample_size=16,
        unique_cases=16,
        accuracy=0.438,
        dominant_actual_direction="bullish",
        dominant_actual_rate=0.688,
        conditions={
            "market_regime_bucket": "bull_trend",
            "volume_bucket": "neutral",
        },
    )
    report = TechnicalImprovementValidationReport(
        generated_at="2026-07-07T16:00:00",
        training_report_path="output/train.json",
        holdout_config={},
        rules=[rule],
        decision=TechnicalValidationDecision(
            should_apply=True,
            reason="candidate 在 holdout 上达到更高命中率",
            baseline_accuracy=0.27,
            candidate_accuracy=0.33,
            accuracy_delta=0.06,
            holdout_samples=66,
            changed_predictions=6,
        ),
    )

    registry_path = tmp_path / "agent_skill_registry.json"
    skill_ids = TechnicalImprovementHoldoutValidator.write_registry_skills(
        report,
        registry_path=registry_path,
        holdout_report_path="output/holdout.json",
    )
    policy = AgentSkillRegistry(registry_path).direction_policy_for_agent("近期股价分析师")

    assert len(skill_ids) == 1
    assert policy["rules"][0]["action"] == "force_bullish"
    assert policy["rules"][0]["conditions"] == {
        "market_regime_bucket": "bull_trend",
        "volume_bucket": "neutral",
    }


def test_confidence_policy_validation_passes_when_brier_improves():
    validator = TechnicalConfidencePolicyValidator()
    rule = TechnicalImprovementRule(
        bucket_group="sr_volume_buckets",
        bucket="near_resistance|shrinking",
        area="skill",
        action="cap_confidence",
        conditions={
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "shrinking",
        },
    )
    samples = [
        _sample(
            "A",
            "bullish",
            -2.0,
            {"sr_zone_bucket": "near_resistance", "volume_bucket": "shrinking"},
            was_correct=False,
        ),
        _sample(
            "B",
            "bullish",
            -2.0,
            {"sr_zone_bucket": "near_resistance", "volume_bucket": "shrinking"},
            was_correct=False,
        ),
        _sample(
            "C",
            "bullish",
            -2.0,
            {"sr_zone_bucket": "near_resistance", "volume_bucket": "shrinking"},
            was_correct=False,
        ),
        _sample("D", "bearish", -2.0, {"sr_zone_bucket": "middle_range"}, was_correct=True),
    ]

    decision, examples = validator.validate_confidence_samples(
        samples,
        [rule],
        confidence_cap=0.35,
        min_brier_delta=0.001,
        min_holdout_samples=4,
        min_changed_predictions=3,
        min_matched_samples=3,
    )

    assert decision.should_apply is True
    assert decision.candidate_brier < decision.baseline_brier
    assert len(examples) == 3


def test_confidence_policy_registry_write(tmp_path):
    rule = TechnicalImprovementRule(
        bucket_group="sr_volume_buckets",
        bucket="near_resistance|shrinking",
        area="skill",
        action="cap_confidence",
        sample_size=13,
        unique_cases=13,
        accuracy=0.154,
        avg_confidence=0.412,
        conditions={
            "sr_zone_bucket": "near_resistance",
            "volume_bucket": "shrinking",
        },
    )
    report = TechnicalConfidencePolicyValidationReport(
        generated_at="2026-07-07T16:30:00",
        training_report_path="output/train.json",
        holdout_config={},
        rules=[rule],
        decision=TechnicalConfidencePolicyDecision(
            should_apply=True,
            reason="candidate 在 holdout 上降低 Brier score",
            baseline_brier=0.32,
            candidate_brier=0.28,
            brier_delta=0.04,
            holdout_samples=20,
            changed_predictions=5,
            matched_samples=5,
            confidence_cap=0.35,
        ),
    )

    registry_path = tmp_path / "agent_skill_registry.json"
    skill_ids = TechnicalConfidencePolicyValidator.write_confidence_registry_skills(
        report,
        registry_path=registry_path,
        holdout_report_path="output/holdout.json",
    )
    policy = AgentSkillRegistry(registry_path).confidence_policy_for_agent("近期股价分析师")

    assert len(skill_ids) == 1
    assert policy["rules"][0]["action"] == "cap_confidence"
    assert policy["rules"][0]["confidence_cap"] == 0.35
