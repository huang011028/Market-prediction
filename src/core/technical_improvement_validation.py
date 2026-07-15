"""Holdout validation for technical prompt/skill improvements."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    CalibrationSample,
    TechnicalCalibrationBootstrapper,
)
from src.core.agent_skill_registry import (
    AgentSkillRegistry,
    rules_to_confidence_policy_skills,
    rules_to_direction_policy_skills,
)


BUCKET_GROUP_TO_SAMPLE_KEY = {
    "trend_buckets": "trend_bucket",
    "momentum_buckets": "momentum_bucket",
    "volume_buckets": "volume_bucket",
    "position_buckets": "position_bucket",
    "market_regime_buckets": "market_regime_bucket",
    "volatility_buckets": "volatility_bucket",
    "sr_zone_buckets": "sr_zone_bucket",
    "risk_reward_buckets": "risk_reward_bucket",
    "technical_scenario_buckets": "technical_scenario_bucket",
    "regime_sr_buckets": "regime_sr_bucket",
    "regime_volume_buckets": "regime_volume_bucket",
    "sr_volume_buckets": "sr_volume_bucket",
    "intraday_buckets": "intraday_bucket",
    "timeframe_buckets": "timeframe_bucket",
}

COMPOSITE_BUCKET_GROUPS = {
    "technical_scenario_buckets",
    "regime_sr_buckets",
    "regime_volume_buckets",
    "sr_volume_buckets",
}


@dataclass
class TechnicalImprovementRule:
    """One candidate prompt/skill rule derived from historical failures."""

    bucket_group: str
    bucket: str
    area: str
    priority: str = "P1"
    sample_size: int = 0
    unique_cases: int = 0
    accuracy: float = 0.0
    avg_confidence: float = 0.0
    action: str = "neutralize_direction"
    dominant_actual_direction: str = ""
    dominant_actual_rate: float = 0.0
    conditions: dict = field(default_factory=dict)

    @property
    def sample_key(self) -> Optional[str]:
        return BUCKET_GROUP_TO_SAMPLE_KEY.get(self.bucket_group)

    def matches(self, sample: CalibrationSample) -> bool:
        if self.conditions:
            buckets = sample.buckets or {}
            return all(str(buckets.get(key)) == str(value) for key, value in self.conditions.items())
        key = self.sample_key
        if not key:
            return False
        return str((sample.buckets or {}).get(key)) == str(self.bucket)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TechnicalValidationDecision:
    """Validation result for a candidate technical improvement."""

    should_apply: bool
    reason: str
    baseline_accuracy: float
    candidate_accuracy: float
    accuracy_delta: float
    holdout_samples: int
    changed_predictions: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TechnicalImprovementValidationReport:
    """Full holdout validation report."""

    generated_at: str
    training_report_path: str
    holdout_config: dict
    rules: list[TechnicalImprovementRule]
    decision: TechnicalValidationDecision
    skipped: list[dict] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["rules"] = [rule.to_dict() for rule in self.rules]
        payload["decision"] = self.decision.to_dict()
        return payload

    def to_markdown(self) -> str:
        lines = [
            "# 技术面候选 Prompt/Skill Holdout 验证报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 训练报告: `{self.training_report_path}`",
            f"- Holdout 样本: {self.decision.holdout_samples}",
            f"- Baseline 命中率: {self.decision.baseline_accuracy:.1%}",
            f"- Candidate 命中率: {self.decision.candidate_accuracy:.1%}",
            f"- 命中率变化: {self.decision.accuracy_delta:+.1%}",
            f"- 改变预测数: {self.decision.changed_predictions}",
            f"- 决策: {'应用' if self.decision.should_apply else '不应用'}",
            f"- 原因: {self.decision.reason}",
            f"- 耗时: {self.elapsed_seconds:.2f}s",
            "",
            "## 候选规则",
            "",
        ]
        if not self.rules:
            lines.append("暂无达到阈值的候选规则。")
        else:
            lines.extend([
                "| Area | Bucket | 样本 | 独立案例 | 训练命中率 | 动作 |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ])
            for rule in self.rules:
                lines.append(
                    "| {area} | {group}/{bucket} | {samples} | {cases} | {acc:.1%} | {action} |".format(
                        area=rule.area,
                        group=rule.bucket_group,
                        bucket=rule.bucket,
                        samples=rule.sample_size,
                        cases=rule.unique_cases,
                        acc=rule.accuracy,
                        action=rule.action,
                    )
                )

        if self.examples:
            lines.extend(["", "## 改变预测示例", ""])
            for item in self.examples[:10]:
                lines.append(
                    "- {target} {as_of}: {before}->{after}, actual={actual}, "
                    "baseline={b_ok}, candidate={c_ok}, rules={rules}".format(
                        target=item.get("target"),
                        as_of=item.get("as_of"),
                        before=item.get("baseline_direction"),
                        after=item.get("candidate_direction"),
                        actual=item.get("actual_direction"),
                        b_ok=item.get("baseline_correct"),
                        c_ok=item.get("candidate_correct"),
                        rules=",".join(item.get("matched_rules") or []),
                    )
                )

        if self.skipped:
            lines.extend(["", "## 跳过样本", ""])
            for item in self.skipped[:20]:
                lines.append(
                    "- {target} {as_of}: {reason}".format(
                        target=item.get("target"),
                        as_of=item.get("as_of"),
                        reason=item.get("reason"),
                    )
                )
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class TechnicalConfidencePolicyDecision:
    """Validation result for candidate confidence cap skills."""

    should_apply: bool
    reason: str
    baseline_brier: float
    candidate_brier: float
    brier_delta: float
    holdout_samples: int
    changed_predictions: int
    matched_samples: int
    confidence_cap: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TechnicalConfidencePolicyValidationReport:
    """Full holdout validation report for confidence cap skills."""

    generated_at: str
    training_report_path: str
    holdout_config: dict
    rules: list[TechnicalImprovementRule]
    decision: TechnicalConfidencePolicyDecision
    skipped: list[dict] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["rules"] = [rule.to_dict() for rule in self.rules]
        payload["decision"] = self.decision.to_dict()
        return payload

    def to_markdown(self) -> str:
        lines = [
            "# 技术面候选 Confidence Skill Holdout 验证报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 训练报告: `{self.training_report_path}`",
            f"- Holdout 样本: {self.decision.holdout_samples}",
            f"- 命中规则样本: {self.decision.matched_samples}",
            f"- Baseline Brier: {self.decision.baseline_brier:.4f}",
            f"- Candidate Brier: {self.decision.candidate_brier:.4f}",
            f"- Brier 改善: {self.decision.brier_delta:+.4f}",
            f"- 置信度封顶: {self.decision.confidence_cap:.0%}",
            f"- 改变 confidence 样本: {self.decision.changed_predictions}",
            f"- 决策: {'应用' if self.decision.should_apply else '不应用'}",
            f"- 原因: {self.decision.reason}",
            f"- 耗时: {self.elapsed_seconds:.2f}s",
            "",
            "## 候选规则",
            "",
        ]
        if not self.rules:
            lines.append("暂无达到阈值的候选置信度规则。")
        else:
            lines.extend([
                "| Area | Bucket | 样本 | 独立案例 | 训练命中率 | 平均置信 |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ])
            for rule in self.rules:
                lines.append(
                    "| {area} | {group}/{bucket} | {samples} | {cases} | {acc:.1%} | {conf:.1%} |".format(
                        area=rule.area,
                        group=rule.bucket_group,
                        bucket=rule.bucket,
                        samples=rule.sample_size,
                        cases=rule.unique_cases,
                        acc=rule.accuracy,
                        conf=rule.avg_confidence,
                    )
                )

        if self.examples:
            lines.extend(["", "## 改变 confidence 示例", ""])
            for item in self.examples[:10]:
                lines.append(
                    "- {target} {as_of}: conf {before:.0%}->{after:.0%}, "
                    "correct={correct}, rules={rules}".format(
                        target=item.get("target"),
                        as_of=item.get("as_of"),
                        before=float(item.get("baseline_confidence", 0.0) or 0.0),
                        after=float(item.get("candidate_confidence", 0.0) or 0.0),
                        correct=item.get("was_correct"),
                        rules=",".join(item.get("matched_rules") or []),
                    )
                )
        return "\n".join(lines).rstrip() + "\n"


class TechnicalImprovementHoldoutValidator:
    """Validate candidate technical prompt/skill rules on holdout samples."""

    def __init__(self, bootstrapper: Optional[TechnicalCalibrationBootstrapper] = None):
        self.bootstrapper = bootstrapper or TechnicalCalibrationBootstrapper()

    def build_rules(
        self,
        evaluation_report: dict,
        min_samples: int = 10,
        min_unique_cases: int = 5,
        max_training_accuracy: float = 0.45,
        areas: Iterable[str] = ("prompt", "skill"),
        require_composite: bool = True,
    ) -> list[TechnicalImprovementRule]:
        allowed_areas = set(areas)
        raw_signals = (
            evaluation_report.get("wrong_strategy_signals")
            or evaluation_report.get("improvement_signals")
            or []
        )
        rules: list[TechnicalImprovementRule] = []
        seen: set[tuple[str, str, str]] = set()
        for signal in raw_signals:
            area = str(signal.get("area") or "")
            if area not in allowed_areas:
                continue
            group = str(signal.get("bucket_group") or "")
            bucket = str(signal.get("bucket") or "")
            if group not in BUCKET_GROUP_TO_SAMPLE_KEY or not bucket:
                continue
            if require_composite and group not in COMPOSITE_BUCKET_GROUPS:
                continue
            sample_size = int(signal.get("sample_size", 0) or 0)
            unique_cases = int(signal.get("unique_cases", sample_size) or 0)
            accuracy = float(signal.get("accuracy", 0.0) or 0.0)
            if sample_size < min_samples or unique_cases < min_unique_cases:
                continue
            if accuracy > max_training_accuracy:
                continue
            action = self._derive_action(signal)
            if action == "calibration_only":
                continue
            key = (area, group, bucket)
            if key in seen:
                continue
            seen.add(key)
            rules.append(
                TechnicalImprovementRule(
                    bucket_group=group,
                    bucket=bucket,
                    area=area,
                    priority=str(signal.get("priority") or "P1"),
                    sample_size=sample_size,
                    unique_cases=unique_cases,
                    accuracy=accuracy,
                    avg_confidence=float(signal.get("avg_confidence", 0.0) or 0.0),
                    action=action,
                    dominant_actual_direction=str(
                        signal.get("dominant_actual_direction") or "",
                    ),
                    dominant_actual_rate=float(
                        signal.get("dominant_actual_rate", 0.0) or 0.0,
                    ),
                    conditions=self._conditions_for_rule(group, bucket),
                )
            )
        return rules

    @staticmethod
    def _conditions_for_rule(bucket_group: str, bucket: str) -> dict:
        parts = str(bucket or "").split("|")
        if bucket_group == "technical_scenario_buckets" and len(parts) == 3:
            return {
                "market_regime_bucket": parts[0],
                "sr_zone_bucket": parts[1],
                "volume_bucket": parts[2],
            }
        if bucket_group == "regime_sr_buckets" and len(parts) == 2:
            return {
                "market_regime_bucket": parts[0],
                "sr_zone_bucket": parts[1],
            }
        if bucket_group == "regime_volume_buckets" and len(parts) == 2:
            return {
                "market_regime_bucket": parts[0],
                "volume_bucket": parts[1],
            }
        if bucket_group == "sr_volume_buckets" and len(parts) == 2:
            return {
                "sr_zone_bucket": parts[0],
                "volume_bucket": parts[1],
            }
        sample_key = BUCKET_GROUP_TO_SAMPLE_KEY.get(bucket_group)
        return {sample_key: bucket} if sample_key else {}

    @staticmethod
    def _derive_action(signal: dict) -> str:
        dominant = str(signal.get("dominant_actual_direction") or "")
        dominant_rate = float(signal.get("dominant_actual_rate", 0.0) or 0.0)
        if not dominant or dominant_rate <= 0:
            counts = signal.get("actual_direction_counts") or {}
            total = sum(int(v or 0) for v in counts.values())
            if total:
                dominant, count = max(
                    counts.items(),
                    key=lambda item: (int(item[1] or 0), str(item[0])),
                )
                dominant = str(dominant)
                dominant_rate = int(count or 0) / total

        if dominant in {"bullish", "bearish"} and dominant_rate >= 0.55:
            return f"force_{dominant}"
        if dominant == "neutral" and dominant_rate >= 0.45:
            return "neutralize_direction"
        return "calibration_only"

    async def run(
        self,
        evaluation_report: dict,
        training_report_path: str,
        holdout_config: CalibrationBootstrapConfig,
        output_dir: Path,
        min_accuracy_delta: float = 0.01,
        min_holdout_samples: int = 20,
        min_changed_predictions: int = 1,
        rule_min_samples: int = 10,
        rule_min_unique_cases: int = 5,
    ) -> TechnicalImprovementValidationReport:
        started = time.monotonic()
        rules = self.build_rules(
            evaluation_report,
            min_samples=rule_min_samples,
            min_unique_cases=rule_min_unique_cases,
        )
        samples, skipped = await self._collect_holdout_samples(holdout_config)
        decision, examples = self.validate_samples(
            samples,
            rules,
            min_accuracy_delta=min_accuracy_delta,
            min_holdout_samples=min_holdout_samples,
            min_changed_predictions=min_changed_predictions,
        )
        report = TechnicalImprovementValidationReport(
            generated_at=datetime.now().isoformat(),
            training_report_path=training_report_path,
            holdout_config=asdict(holdout_config),
            rules=rules,
            decision=decision,
            skipped=skipped,
            examples=examples,
            elapsed_seconds=time.monotonic() - started,
        )
        self.write_report(report, output_dir)
        return report

    def validate_samples(
        self,
        samples: list[CalibrationSample],
        rules: list[TechnicalImprovementRule],
        min_accuracy_delta: float = 0.01,
        min_holdout_samples: int = 20,
        min_changed_predictions: int = 1,
    ) -> tuple[TechnicalValidationDecision, list[dict]]:
        if len(samples) < min_holdout_samples:
            return (
                TechnicalValidationDecision(
                    should_apply=False,
                    reason=f"holdout 样本不足: {len(samples)} < {min_holdout_samples}",
                    baseline_accuracy=self._accuracy(samples),
                    candidate_accuracy=self._accuracy(samples),
                    accuracy_delta=0.0,
                    holdout_samples=len(samples),
                    changed_predictions=0,
                ),
                [],
            )
        if not rules:
            baseline = self._accuracy(samples)
            return (
                TechnicalValidationDecision(
                    should_apply=False,
                    reason="没有达到阈值的候选 prompt/skill 规则",
                    baseline_accuracy=baseline,
                    candidate_accuracy=baseline,
                    accuracy_delta=0.0,
                    holdout_samples=len(samples),
                    changed_predictions=0,
                ),
                [],
            )

        baseline_correct = sum(1 for sample in samples if sample.was_correct)
        candidate_correct = 0
        changed = 0
        examples: list[dict] = []
        for sample in samples:
            candidate_direction, matched = self.apply_rules(sample, rules)
            candidate_ok = TechnicalCalibrationBootstrapper._direction_correct(
                candidate_direction,
                (
                    sample.effective_fixed_return_pct
                    if sample.effective_fixed_return_pct is not None
                    else sample.actual_change_pct
                ),
                sample.window_max_change_pct,
                sample.window_min_change_pct,
                sample.prediction_target,
            )
            if candidate_ok:
                candidate_correct += 1
            if candidate_direction != sample.predicted_direction:
                changed += 1
                examples.append({
                    "target": sample.target,
                    "as_of": sample.as_of,
                    "baseline_direction": sample.predicted_direction,
                    "candidate_direction": candidate_direction,
                    "actual_direction": sample.actual_direction,
                    "actual_change_pct": sample.actual_change_pct,
                    "baseline_correct": sample.was_correct,
                    "candidate_correct": candidate_ok,
                    "matched_rules": [
                        f"{rule.bucket_group}/{rule.bucket}" for rule in matched
                    ],
                })

        total = len(samples)
        baseline_acc = baseline_correct / total if total else 0.0
        candidate_acc = candidate_correct / total if total else 0.0
        delta = candidate_acc - baseline_acc
        should_apply = (
            changed >= min_changed_predictions
            and delta >= min_accuracy_delta
        )
        if should_apply:
            reason = "candidate 在 holdout 上达到更高命中率"
        elif changed < min_changed_predictions:
            reason = f"candidate 没有足够改变预测: {changed} < {min_changed_predictions}"
        else:
            reason = f"candidate 未超过最低提升阈值: {delta:+.1%} < {min_accuracy_delta:.1%}"
        return (
            TechnicalValidationDecision(
                should_apply=should_apply,
                reason=reason,
                baseline_accuracy=round(baseline_acc, 4),
                candidate_accuracy=round(candidate_acc, 4),
                accuracy_delta=round(delta, 4),
                holdout_samples=total,
                changed_predictions=changed,
            ),
            examples,
        )

    @staticmethod
    def apply_rules(
        sample: CalibrationSample,
        rules: list[TechnicalImprovementRule],
    ) -> tuple[str, list[TechnicalImprovementRule]]:
        matched = [rule for rule in rules if rule.matches(sample)]
        if not matched:
            return sample.predicted_direction, []
        for rule in matched:
            if rule.action == "force_bullish":
                return "bullish", matched
            if rule.action == "force_bearish":
                return "bearish", matched
        if any(rule.action == "neutralize_direction" for rule in matched):
            if sample.predicted_direction == "neutral":
                return sample.predicted_direction, matched
            return "neutral", matched
        return sample.predicted_direction, matched

    async def _collect_holdout_samples(
        self,
        config: CalibrationBootstrapConfig,
    ) -> tuple[list[CalibrationSample], list[dict]]:
        dates = self.bootstrapper._build_dates(
            config.start_date,
            config.end_date,
            config.interval_days,
        )
        samples: list[CalibrationSample] = []
        skipped: list[dict] = []
        for target in config.targets:
            for as_of in dates:
                try:
                    samples.append(
                        await self.bootstrapper._generate_sample(target, as_of, config)
                    )
                except Exception as e:
                    skipped.append({
                        "target": target,
                        "as_of": as_of.strftime("%Y-%m-%d"),
                        "reason": str(e),
                    })
        return samples, skipped

    @staticmethod
    def _accuracy(samples: list[CalibrationSample]) -> float:
        if not samples:
            return 0.0
        return sum(1 for sample in samples if sample.was_correct) / len(samples)

    @staticmethod
    def write_report(
        report: TechnicalImprovementValidationReport,
        output_dir: Path,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "technical_improvement_validation.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "technical_improvement_validation.md").write_text(
            report.to_markdown(),
            encoding="utf-8",
        )

    @staticmethod
    def write_registry_skills(
        report: TechnicalImprovementValidationReport,
        registry_path: Optional[str | Path] = None,
        holdout_report_path: Optional[str | Path] = None,
    ) -> list[str]:
        """Write holdout-passed direction rules to Agent Skill Registry."""
        if not report.decision.should_apply:
            return []

        registry = AgentSkillRegistry(registry_path)
        source = {
            "generated_by": "Agent 改进工程师",
            "data_source": "historical_kline_backtest",
            "training_report_path": report.training_report_path,
            "holdout_report_path": str(holdout_report_path or ""),
            "created_at": datetime.now().isoformat(),
        }
        skills = rules_to_direction_policy_skills(
            report.rules,
            agent_name=TechnicalCalibrationBootstrapper.AGENT_NAME,
            source=source,
            holdout_decision=report.decision.to_dict(),
        )
        written = []
        for skill in skills:
            registry.upsert_skill(skill)
            written.append(skill.skill_id)
        if written:
            registry.save()
        return written


class TechnicalConfidencePolicyValidator(TechnicalImprovementHoldoutValidator):
    """Validate confidence cap skills on holdout samples."""

    def build_confidence_rules(
        self,
        evaluation_report: dict,
        min_samples: int = 8,
        min_unique_cases: int = 5,
        max_training_accuracy: float = 0.35,
        min_confidence_gap: float = 0.10,
        areas: Iterable[str] = ("prompt", "skill"),
        require_composite: bool = True,
    ) -> list[TechnicalImprovementRule]:
        allowed_areas = set(areas)
        raw_signals = (
            evaluation_report.get("wrong_strategy_signals")
            or evaluation_report.get("improvement_signals")
            or []
        )
        rules: list[TechnicalImprovementRule] = []
        seen: set[tuple[str, str, str]] = set()
        for signal in raw_signals:
            area = str(signal.get("area") or "")
            if area not in allowed_areas:
                continue
            group = str(signal.get("bucket_group") or "")
            bucket = str(signal.get("bucket") or "")
            if group not in BUCKET_GROUP_TO_SAMPLE_KEY or not bucket:
                continue
            if require_composite and group not in COMPOSITE_BUCKET_GROUPS:
                continue
            sample_size = int(signal.get("sample_size", 0) or 0)
            unique_cases = int(signal.get("unique_cases", sample_size) or 0)
            accuracy = float(signal.get("accuracy", 0.0) or 0.0)
            avg_confidence = float(signal.get("avg_confidence", 0.0) or 0.0)
            if sample_size < min_samples or unique_cases < min_unique_cases:
                continue
            if accuracy > max_training_accuracy:
                continue
            if avg_confidence - accuracy < min_confidence_gap:
                continue
            key = (area, group, bucket)
            if key in seen:
                continue
            seen.add(key)
            rules.append(
                TechnicalImprovementRule(
                    bucket_group=group,
                    bucket=bucket,
                    area=area,
                    priority=str(signal.get("priority") or "P1"),
                    sample_size=sample_size,
                    unique_cases=unique_cases,
                    accuracy=accuracy,
                    avg_confidence=avg_confidence,
                    action="cap_confidence",
                    dominant_actual_direction=str(
                        signal.get("dominant_actual_direction") or "",
                    ),
                    dominant_actual_rate=float(
                        signal.get("dominant_actual_rate", 0.0) or 0.0,
                    ),
                    conditions=self._conditions_for_rule(group, bucket),
                )
            )
        return rules

    async def run_confidence_validation(
        self,
        evaluation_report: dict,
        training_report_path: str,
        holdout_config: CalibrationBootstrapConfig,
        output_dir: Path,
        confidence_cap: float = 0.35,
        min_brier_delta: float = 0.005,
        min_holdout_samples: int = 20,
        min_changed_predictions: int = 3,
        min_matched_samples: int = 3,
        rule_min_samples: int = 8,
        rule_min_unique_cases: int = 5,
    ) -> TechnicalConfidencePolicyValidationReport:
        started = time.monotonic()
        rules = self.build_confidence_rules(
            evaluation_report,
            min_samples=rule_min_samples,
            min_unique_cases=rule_min_unique_cases,
        )
        samples, skipped = await self._collect_holdout_samples(holdout_config)
        decision, examples = self.validate_confidence_samples(
            samples,
            rules,
            confidence_cap=confidence_cap,
            min_brier_delta=min_brier_delta,
            min_holdout_samples=min_holdout_samples,
            min_changed_predictions=min_changed_predictions,
            min_matched_samples=min_matched_samples,
        )
        report = TechnicalConfidencePolicyValidationReport(
            generated_at=datetime.now().isoformat(),
            training_report_path=training_report_path,
            holdout_config=asdict(holdout_config),
            rules=rules,
            decision=decision,
            skipped=skipped,
            examples=examples,
            elapsed_seconds=time.monotonic() - started,
        )
        self.write_confidence_report(report, output_dir)
        return report

    def validate_confidence_samples(
        self,
        samples: list[CalibrationSample],
        rules: list[TechnicalImprovementRule],
        confidence_cap: float = 0.35,
        min_brier_delta: float = 0.005,
        min_holdout_samples: int = 20,
        min_changed_predictions: int = 3,
        min_matched_samples: int = 3,
    ) -> tuple[TechnicalConfidencePolicyDecision, list[dict]]:
        if len(samples) < min_holdout_samples:
            baseline = self._brier_score(samples)
            return (
                TechnicalConfidencePolicyDecision(
                    should_apply=False,
                    reason=f"holdout 样本不足: {len(samples)} < {min_holdout_samples}",
                    baseline_brier=baseline,
                    candidate_brier=baseline,
                    brier_delta=0.0,
                    holdout_samples=len(samples),
                    changed_predictions=0,
                    matched_samples=0,
                    confidence_cap=confidence_cap,
                ),
                [],
            )
        if not rules:
            baseline = self._brier_score(samples)
            return (
                TechnicalConfidencePolicyDecision(
                    should_apply=False,
                    reason="没有达到阈值的候选 confidence skill 规则",
                    baseline_brier=baseline,
                    candidate_brier=baseline,
                    brier_delta=0.0,
                    holdout_samples=len(samples),
                    changed_predictions=0,
                    matched_samples=0,
                    confidence_cap=confidence_cap,
                ),
                [],
            )

        baseline_errors = []
        candidate_errors = []
        changed = 0
        matched_count = 0
        examples: list[dict] = []
        cap = max(0.05, min(0.95, float(confidence_cap)))
        for sample in samples:
            correct = 1.0 if sample.was_correct else 0.0
            baseline_conf = max(0.05, min(0.95, float(sample.predicted_confidence or 0.0)))
            matched = [rule for rule in rules if rule.matches(sample)]
            candidate_conf = baseline_conf
            if matched:
                matched_count += 1
                candidate_conf = min(candidate_conf, cap)
            baseline_errors.append((baseline_conf - correct) ** 2)
            candidate_errors.append((candidate_conf - correct) ** 2)
            if candidate_conf != baseline_conf:
                changed += 1
                examples.append({
                    "target": sample.target,
                    "as_of": sample.as_of,
                    "baseline_confidence": baseline_conf,
                    "candidate_confidence": candidate_conf,
                    "was_correct": sample.was_correct,
                    "matched_rules": [
                        f"{rule.bucket_group}/{rule.bucket}" for rule in matched
                    ],
                })

        baseline_brier = sum(baseline_errors) / len(baseline_errors)
        candidate_brier = sum(candidate_errors) / len(candidate_errors)
        delta = baseline_brier - candidate_brier
        should_apply = (
            matched_count >= min_matched_samples
            and changed >= min_changed_predictions
            and delta >= min_brier_delta
        )
        if should_apply:
            reason = "candidate 在 holdout 上降低 Brier score"
        elif matched_count < min_matched_samples:
            reason = f"命中规则样本不足: {matched_count} < {min_matched_samples}"
        elif changed < min_changed_predictions:
            reason = f"candidate 没有足够改变 confidence: {changed} < {min_changed_predictions}"
        else:
            reason = f"Brier 改善不足: {delta:+.4f} < {min_brier_delta:.4f}"
        return (
            TechnicalConfidencePolicyDecision(
                should_apply=should_apply,
                reason=reason,
                baseline_brier=round(baseline_brier, 4),
                candidate_brier=round(candidate_brier, 4),
                brier_delta=round(delta, 4),
                holdout_samples=len(samples),
                changed_predictions=changed,
                matched_samples=matched_count,
                confidence_cap=round(cap, 3),
            ),
            examples,
        )

    @staticmethod
    def _brier_score(samples: list[CalibrationSample]) -> float:
        if not samples:
            return 0.0
        errors = []
        for sample in samples:
            correct = 1.0 if sample.was_correct else 0.0
            confidence = max(0.05, min(0.95, float(sample.predicted_confidence or 0.0)))
            errors.append((confidence - correct) ** 2)
        return round(sum(errors) / len(errors), 4)

    @staticmethod
    def write_confidence_report(
        report: TechnicalConfidencePolicyValidationReport,
        output_dir: Path,
    ) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "technical_confidence_policy_validation.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "technical_confidence_policy_validation.md").write_text(
            report.to_markdown(),
            encoding="utf-8",
        )

    @staticmethod
    def write_confidence_registry_skills(
        report: TechnicalConfidencePolicyValidationReport,
        registry_path: Optional[str | Path] = None,
        holdout_report_path: Optional[str | Path] = None,
    ) -> list[str]:
        if not report.decision.should_apply:
            return []

        registry = AgentSkillRegistry(registry_path)
        source = {
            "generated_by": "Agent 改进工程师",
            "data_source": "historical_kline_backtest",
            "training_report_path": report.training_report_path,
            "holdout_report_path": str(holdout_report_path or ""),
            "created_at": datetime.now().isoformat(),
        }
        skills = rules_to_confidence_policy_skills(
            report.rules,
            agent_name=TechnicalCalibrationBootstrapper.AGENT_NAME,
            source=source,
            holdout_decision=report.decision.to_dict(),
            confidence_cap=report.decision.confidence_cap,
        )
        written = []
        for skill in skills:
            registry.upsert_skill(skill)
            written.append(skill.skill_id)
        if written:
            registry.save()
        return written
