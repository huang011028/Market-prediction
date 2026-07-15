"""
技术面 LLM Prompt Replay Harness。

在同一批历史 K 线样本上分别运行 baseline prompt 与 candidate prompt，
记录每条 LLM 预测，并用真实未来窗口比较方向命中率、Brier 和过度自信率。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from src.agents.technical_analyst import TechnicalAnalyst
from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    TechnicalCalibrationBootstrapper,
)
from src.core.llm_client import LLMClient
from src.core.prediction_target import PredictionTargetSpec, resolve_prediction_target
from src.core.result import AnalysisResult, Direction
from src.core.technical_improvement_validation import (
    BUCKET_GROUP_TO_SAMPLE_KEY,
    TechnicalImprovementHoldoutValidator,
)
from src.data.price_fetcher import PriceFetcher
from src.prompts.dynamic_overrides import candidate_override_context
from src.utils.technical_calibrator import TechnicalConfidenceCalibrator


TECHNICAL_AGENT_NAME = TechnicalCalibrationBootstrapper.AGENT_NAME


@dataclass
class CandidateRuntimeRule:
    """沙箱 candidate 在 replay 阶段可执行的临时 guardrail。"""

    artifact_id: str
    area: str
    bucket_group: str
    bucket: str
    sample_size: int
    unique_cases: int
    accuracy: float
    confidence_cap: float
    action: str
    conditions: dict = field(default_factory=dict)

    def matches(self, buckets: dict) -> bool:
        if self.conditions:
            return all(str(buckets.get(key)) == str(value) for key, value in self.conditions.items())
        sample_key = BUCKET_GROUP_TO_SAMPLE_KEY.get(self.bucket_group)
        return bool(sample_key) and str(buckets.get(sample_key)) == str(self.bucket)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidateRuntimeManifest:
    """candidate override 的可观测加载状态。"""

    candidate_root: str = ""
    artifacts: list[dict] = field(default_factory=list)
    rules: list[CandidateRuntimeRule] = field(default_factory=list)

    @classmethod
    def load(cls, candidate_root: Optional[Path]) -> "CandidateRuntimeManifest":
        if not candidate_root:
            return cls()
        root = Path(candidate_root)
        manifest = cls(candidate_root=str(root))
        artifact_dir = root / "artifacts"
        if not artifact_dir.exists():
            return manifest
        for metadata_path in sorted(artifact_dir.glob("*.json")):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if payload.get("agent_name") != TECHNICAL_AGENT_NAME:
                continue
            if payload.get("area") not in {"prompt", "skill"}:
                continue
            content_path_text = str(payload.get("content_path") or "")
            content_path = Path(content_path_text) if content_path_text else None
            artifact_meta = {
                "artifact_id": payload.get("artifact_id") or metadata_path.stem,
                "area": payload.get("area"),
                "title": payload.get("title"),
                "content_path": str(content_path) if content_path else "",
                "content_loaded": content_path.exists() if content_path else False,
                "metadata_path": str(metadata_path),
                "source_signals": len(payload.get("source_signals") or []),
            }
            manifest.artifacts.append(artifact_meta)
            manifest.rules.extend(
                cls._rules_from_artifact(payload, str(artifact_meta["artifact_id"]))
            )
        return manifest

    @staticmethod
    def _rules_from_artifact(payload: dict, artifact_id: str) -> list[CandidateRuntimeRule]:
        rules: list[CandidateRuntimeRule] = []
        seen: set[tuple[str, str, str]] = set()
        area = str(payload.get("area") or "")
        for signal in payload.get("source_signals") or []:
            group = str(signal.get("bucket_group") or "")
            bucket = str(signal.get("bucket") or "")
            if group not in BUCKET_GROUP_TO_SAMPLE_KEY or not bucket:
                continue
            accuracy = float(signal.get("accuracy", 0.0) or 0.0)
            if accuracy > 0.20:
                continue
            key = (area, group, bucket)
            if key in seen:
                continue
            seen.add(key)
            sample_size = int(signal.get("sample_size", 0) or 0)
            unique_cases = int(signal.get("unique_cases", sample_size) or 0)
            action = "neutralize_direction" if accuracy <= 0.05 else "cap_confidence"
            cap = 0.20 if action == "neutralize_direction" else 0.35
            rules.append(
                CandidateRuntimeRule(
                    artifact_id=artifact_id,
                    area=area,
                    bucket_group=group,
                    bucket=bucket,
                    sample_size=sample_size,
                    unique_cases=unique_cases,
                    accuracy=accuracy,
                    confidence_cap=cap,
                    action=action,
                    conditions=TechnicalImprovementHoldoutValidator._conditions_for_rule(group, bucket),
                )
            )
        return rules

    def apply(self, result: AnalysisResult, buckets: dict) -> list[dict]:
        matched = [rule for rule in self.rules if rule.matches(buckets)]
        if not matched:
            return []

        before_direction = str(getattr(result.direction, "value", result.direction))
        before_confidence = float(result.confidence or 0.0)
        applied: list[dict] = []
        neutralize = any(rule.action == "neutralize_direction" for rule in matched)
        confidence_cap = min(rule.confidence_cap for rule in matched)

        if neutralize and before_direction != "neutral":
            result.direction = Direction.NEUTRAL
            result.magnitude = None
            result.confidence = min(before_confidence, confidence_cap)
            result.prediction_target = resolve_prediction_target(
                result.timeframe,
                result.direction,
                result.magnitude,
                result.confidence,
                None,
                target=result.target,
            )
        else:
            result.confidence = min(before_confidence, confidence_cap)

        after_direction = str(getattr(result.direction, "value", result.direction))
        after_confidence = float(result.confidence or 0.0)
        for rule in matched:
            applied.append({
                **rule.to_dict(),
                "before_direction": before_direction,
                "after_direction": after_direction,
                "before_confidence": round(before_confidence, 4),
                "after_confidence": round(after_confidence, 4),
            })

        result.data_summary = result.data_summary or {}
        result.data_summary["candidate_runtime_guardrail"] = {
            "matched_rules": applied,
            "buckets": buckets,
        }
        result.reasoning += (
            "\n\n[候选运行时 guardrail: "
            f"{before_direction}->{after_direction}; "
            f"confidence {before_confidence:.0%}->{after_confidence:.0%}; "
            + "; ".join(f"{rule.bucket_group}/{rule.bucket}" for rule in matched[:5])
            + "]"
        )
        return applied

    def to_dict(self) -> dict:
        return {
            "candidate_root": self.candidate_root,
            "artifacts": self.artifacts,
            "rules": [rule.to_dict() for rule in self.rules],
        }


@dataclass
class TechnicalPromptReplayConfig:
    """技术面 LLM prompt replay 配置。"""

    targets: list[str]
    start_date: str
    end_date: str
    timeframe: str = "短期(1周)"
    interval_days: int = 14
    lookback_days: int = 180
    tolerance_days: int = 10
    candidate_root: Optional[Path] = None
    max_samples: int = 60
    min_samples: int = 30
    min_accuracy_delta: float = 0.01
    min_brier_delta: float = 0.0
    min_changed_predictions: int = 1
    overconfidence_threshold: float = 0.60
    max_overconfidence_delta: float = 0.02


@dataclass
class PromptReplayPrediction:
    """单个版本在单个历史样本上的预测与验证结果。"""

    direction: str
    confidence: float
    actual_direction: str
    actual_change_pct: float
    was_correct: bool
    brier_error: float
    overconfident_wrong: bool
    prediction_target: dict = field(default_factory=dict)
    reasoning_excerpt: str = ""
    applied_overrides: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PromptReplaySample:
    """baseline/candidate 在同一历史样本上的对照。"""

    target: str
    as_of: str
    valid_date: str
    price_start: float
    baseline: PromptReplayPrediction
    candidate: PromptReplayPrediction
    changed_direction: bool
    changed_confidence: bool
    candidate_buckets: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["baseline"] = self.baseline.to_dict()
        payload["candidate"] = self.candidate.to_dict()
        return payload


@dataclass
class PromptReplayDecision:
    """候选 prompt replay 门禁决策。"""

    should_apply: bool
    reason: str
    baseline_accuracy: float
    candidate_accuracy: float
    accuracy_delta: float
    baseline_brier: float
    candidate_brier: float
    brier_delta: float
    baseline_overconfidence_rate: float
    candidate_overconfidence_rate: float
    overconfidence_delta: float
    holdout_samples: int
    changed_predictions: int
    changed_directions: int = 0
    changed_confidences: int = 0
    candidate_guardrail_hits: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TechnicalPromptReplayReport:
    """完整技术面 LLM prompt replay 报告。"""

    generated_at: str
    candidate_root: str
    config: dict
    decision: PromptReplayDecision
    samples: list[PromptReplaySample] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    candidate_manifest: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["decision"] = self.decision.to_dict()
        payload["samples"] = [sample.to_dict() for sample in self.samples]
        return payload

    def to_markdown(self) -> str:
        lines = [
            "# 技术面 LLM Prompt Replay 验证报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 候选目录: `{self.candidate_root}`",
            f"- Holdout 样本: {self.decision.holdout_samples}",
            f"- Baseline 命中率: {self.decision.baseline_accuracy:.1%}",
            f"- Candidate 命中率: {self.decision.candidate_accuracy:.1%}",
            f"- 命中率变化: {self.decision.accuracy_delta:+.1%}",
            f"- Baseline Brier: {self.decision.baseline_brier:.4f}",
            f"- Candidate Brier: {self.decision.candidate_brier:.4f}",
            f"- Brier 改善: {self.decision.brier_delta:+.4f}",
            f"- 过度自信率变化: {self.decision.overconfidence_delta:+.1%}",
            f"- 改变预测数: {self.decision.changed_predictions}",
            f"- 方向变化数: {self.decision.changed_directions}",
            f"- 置信度变化数: {self.decision.changed_confidences}",
            f"- Candidate Guardrail 命中样本: {self.decision.candidate_guardrail_hits}",
            f"- 决策: {'应用' if self.decision.should_apply else '不应用'}",
            f"- 原因: {self.decision.reason}",
            f"- 耗时: {self.elapsed_seconds:.2f}s",
            "",
            "## Candidate 加载状态",
            "",
            f"- 候选 artifact: {len(self.candidate_manifest.get('artifacts') or [])}",
            f"- 运行时 guardrail 规则: {len(self.candidate_manifest.get('rules') or [])}",
            "",
            "## 改变预测样本",
            "",
        ]
        changed = [
            sample for sample in self.samples
            if sample.changed_direction or sample.changed_confidence
        ]
        if not changed:
            lines.append("暂无方向或置信度变化样本。")
        else:
            for sample in changed[:12]:
                lines.append(
                    "- {target} {as_of}: {before}->{after}, conf {b_conf:.0%}->{c_conf:.0%}, baseline={b_ok}, candidate={c_ok}, actual={actual}".format(
                        target=sample.target,
                        as_of=sample.as_of,
                        before=sample.baseline.direction,
                        after=sample.candidate.direction,
                        b_conf=sample.baseline.confidence,
                        c_conf=sample.candidate.confidence,
                        b_ok=sample.baseline.was_correct,
                        c_ok=sample.candidate.was_correct,
                        actual=sample.candidate.actual_direction,
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


class TechnicalPromptReplayHarness:
    """对技术面候选 prompt 做 baseline/candidate LLM 回放。"""

    def __init__(
        self,
        llm: LLMClient,
        price_fetcher: Optional[PriceFetcher] = None,
        analyst_factory: Optional[Callable[[LLMClient], TechnicalAnalyst]] = None,
    ):
        self.llm = llm
        self.price_fetcher = price_fetcher or PriceFetcher()
        self.analyst_factory = analyst_factory

    async def run(
        self,
        config: TechnicalPromptReplayConfig,
        output_dir: Path,
    ) -> TechnicalPromptReplayReport:
        started = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)
        samples: list[PromptReplaySample] = []
        skipped: list[dict] = []
        candidate_manifest = CandidateRuntimeManifest.load(config.candidate_root)
        dates = TechnicalCalibrationBootstrapper._build_dates(
            config.start_date,
            config.end_date,
            config.interval_days,
        )

        for target in config.targets:
            for as_of in dates:
                if len(samples) >= config.max_samples:
                    break
                try:
                    samples.append(await self._run_one(
                        target,
                        as_of,
                        config,
                        candidate_manifest,
                    ))
                except Exception as e:
                    skipped.append({
                        "target": target,
                        "as_of": as_of.strftime("%Y-%m-%d"),
                        "reason": str(e),
                    })
            if len(samples) >= config.max_samples:
                break

        decision = self._decide(samples, config)
        report = TechnicalPromptReplayReport(
            generated_at=datetime.now().isoformat(),
            candidate_root=str(config.candidate_root or ""),
            config={
                **asdict(config),
                "candidate_root": str(config.candidate_root or ""),
            },
            decision=decision,
            samples=samples,
            skipped=skipped,
            elapsed_seconds=time.monotonic() - started,
            candidate_manifest=candidate_manifest.to_dict(),
        )
        (output_dir / "technical_prompt_replay.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "technical_prompt_replay.md").write_text(
            report.to_markdown(),
            encoding="utf-8",
        )
        return report

    async def _run_one(
        self,
        target: str,
        as_of: datetime,
        config: TechnicalPromptReplayConfig,
        candidate_manifest: CandidateRuntimeManifest,
    ) -> PromptReplaySample:
        price_data = await self.price_fetcher.fetch_as_of(
            target,
            as_of,
            lookback_days=config.lookback_days,
        )
        if getattr(price_data, "trading_days", 0) < 20:
            raise ValueError(f"数据不足: {getattr(price_data, 'trading_days', 0)} 个交易日")
        price_start = float(getattr(price_data, "price_current", 0.0) or 0.0)
        if price_start <= 0:
            raise ValueError("起始价格不可用")

        data = price_data.to_agent_dict()
        analyst = self._build_analyst()
        context = analyst.build_context(target, config.timeframe)

        baseline_result = await analyst.analyze(data, context)
        with candidate_override_context(config.candidate_root):
            candidate_result = await analyst.analyze(data, context)
        candidate_buckets = self._technical_buckets(analyst, data, context)
        candidate_overrides = candidate_manifest.apply(candidate_result, candidate_buckets)

        baseline = await self._validate_prediction(
            target=target,
            as_of=as_of,
            price_start=price_start,
            result=baseline_result,
            overconfidence_threshold=config.overconfidence_threshold,
        )
        candidate = await self._validate_prediction(
            target=target,
            as_of=as_of,
            price_start=price_start,
            result=candidate_result,
            overconfidence_threshold=config.overconfidence_threshold,
            applied_overrides=candidate_overrides,
        )
        valid_date = max(
            baseline_result.prediction_target.horizon_calendar_days,
            candidate_result.prediction_target.horizon_calendar_days,
        )
        return PromptReplaySample(
            target=target,
            as_of=as_of.strftime("%Y-%m-%d"),
            valid_date=(as_of + timedelta(days=valid_date)).strftime("%Y-%m-%d"),
            price_start=round(price_start, 4),
            baseline=baseline,
            candidate=candidate,
            changed_direction=baseline.direction != candidate.direction,
            changed_confidence=abs(baseline.confidence - candidate.confidence) >= 0.02,
            candidate_buckets=candidate_buckets,
        )

    async def _validate_prediction(
        self,
        target: str,
        as_of: datetime,
        price_start: float,
        result: AnalysisResult,
        overconfidence_threshold: float,
        applied_overrides: Optional[list[dict]] = None,
    ) -> PromptReplayPrediction:
        spec = PredictionTargetSpec.from_dict(result.prediction_target)
        valid_dt = as_of + timedelta(days=spec.horizon_calendar_days)
        direction = getattr(result.direction, "value", result.direction)
        outcome = await TechnicalCalibrationBootstrapper._horizon_window_outcome(
            self.price_fetcher,
            target,
            price_start,
            as_of,
            valid_dt,
            str(direction),
            spec,
        )
        correct = TechnicalCalibrationBootstrapper._direction_correct(
            str(direction),
            outcome["effective_fixed_return_pct"],
            outcome["window_max_change_pct"],
            outcome["window_min_change_pct"],
            spec,
        )
        confidence = max(0.0, min(1.0, float(result.confidence or 0.0)))
        brier_error = (confidence - (1.0 if correct else 0.0)) ** 2
        return PromptReplayPrediction(
            direction=str(direction),
            confidence=round(confidence, 4),
            actual_direction=outcome["actual_direction"],
            actual_change_pct=round(float(outcome["actual_change_pct"]), 2),
            was_correct=bool(correct),
            brier_error=round(brier_error, 4),
            overconfident_wrong=(
                not correct and confidence >= overconfidence_threshold
            ),
            prediction_target=spec.to_dict(),
            reasoning_excerpt=str(result.reasoning or "")[:240],
            applied_overrides=list(applied_overrides or []),
        )

    def _build_analyst(self) -> TechnicalAnalyst:
        analyst = (
            self.analyst_factory(self.llm)
            if self.analyst_factory
            else TechnicalAnalyst(self.llm)
        )
        analyst.price_fetcher = self.price_fetcher
        return analyst

    @staticmethod
    def _technical_buckets(
        analyst: TechnicalAnalyst,
        data: dict,
        context: dict,
    ) -> dict:
        try:
            evidence_packet = analyst._build_evidence_packet(
                data,
                context.get("timeframe", "短期"),
            )
            return TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
                evidence_packet,
                context.get("timeframe", "短期"),
            )
        except Exception:
            return {}

    @staticmethod
    def _decide(
        samples: list[PromptReplaySample],
        config: TechnicalPromptReplayConfig,
    ) -> PromptReplayDecision:
        total = len(samples)
        if total < config.min_samples:
            baseline_acc = _accuracy([sample.baseline for sample in samples])
            return PromptReplayDecision(
                should_apply=False,
                reason=f"LLM prompt replay 样本不足: {total} < {config.min_samples}",
                baseline_accuracy=baseline_acc,
                candidate_accuracy=_accuracy([sample.candidate for sample in samples]),
                accuracy_delta=0.0,
                baseline_brier=_brier([sample.baseline for sample in samples]),
                candidate_brier=_brier([sample.candidate for sample in samples]),
                brier_delta=0.0,
                baseline_overconfidence_rate=_overconfidence_rate([sample.baseline for sample in samples]),
                candidate_overconfidence_rate=_overconfidence_rate([sample.candidate for sample in samples]),
                overconfidence_delta=0.0,
                holdout_samples=total,
                changed_predictions=0,
                changed_directions=0,
                changed_confidences=0,
                candidate_guardrail_hits=0,
            )

        baseline_predictions = [sample.baseline for sample in samples]
        candidate_predictions = [sample.candidate for sample in samples]
        baseline_acc = _accuracy(baseline_predictions)
        candidate_acc = _accuracy(candidate_predictions)
        baseline_brier = _brier(baseline_predictions)
        candidate_brier = _brier(candidate_predictions)
        baseline_over = _overconfidence_rate(baseline_predictions)
        candidate_over = _overconfidence_rate(candidate_predictions)
        changed_directions = sum(1 for sample in samples if sample.changed_direction)
        changed_confidences = sum(1 for sample in samples if sample.changed_confidence)
        changed = sum(
            1 for sample in samples
            if sample.changed_direction or sample.changed_confidence
        )
        guardrail_hits = sum(
            1 for sample in samples
            if sample.candidate.applied_overrides
        )

        accuracy_delta = candidate_acc - baseline_acc
        brier_delta = baseline_brier - candidate_brier
        over_delta = candidate_over - baseline_over
        should_apply = (
            changed >= config.min_changed_predictions
            and accuracy_delta >= config.min_accuracy_delta
            and brier_delta >= config.min_brier_delta
            and over_delta <= config.max_overconfidence_delta
        )
        if should_apply:
            reason = "candidate prompt 在 LLM holdout replay 上提升命中率且风险指标未变差"
        elif changed < config.min_changed_predictions:
            reason = f"candidate prompt 没有足够改变预测: {changed} < {config.min_changed_predictions}"
        elif accuracy_delta < config.min_accuracy_delta:
            reason = f"命中率提升不足: {accuracy_delta:+.1%} < {config.min_accuracy_delta:.1%}"
        elif brier_delta < config.min_brier_delta:
            reason = f"Brier 改善不足: {brier_delta:+.4f} < {config.min_brier_delta:.4f}"
        else:
            reason = f"过度自信率变差: {over_delta:+.1%} > {config.max_overconfidence_delta:.1%}"

        return PromptReplayDecision(
            should_apply=should_apply,
            reason=reason,
            baseline_accuracy=round(baseline_acc, 4),
            candidate_accuracy=round(candidate_acc, 4),
            accuracy_delta=round(accuracy_delta, 4),
            baseline_brier=round(baseline_brier, 4),
            candidate_brier=round(candidate_brier, 4),
            brier_delta=round(brier_delta, 4),
            baseline_overconfidence_rate=round(baseline_over, 4),
            candidate_overconfidence_rate=round(candidate_over, 4),
            overconfidence_delta=round(over_delta, 4),
            holdout_samples=total,
            changed_predictions=changed,
            changed_directions=changed_directions,
            changed_confidences=changed_confidences,
            candidate_guardrail_hits=guardrail_hits,
        )


def _accuracy(predictions: list[PromptReplayPrediction]) -> float:
    if not predictions:
        return 0.0
    return sum(1 for item in predictions if item.was_correct) / len(predictions)


def _brier(predictions: list[PromptReplayPrediction]) -> float:
    if not predictions:
        return 0.0
    return sum(float(item.brier_error or 0.0) for item in predictions) / len(predictions)


def _overconfidence_rate(predictions: list[PromptReplayPrediction]) -> float:
    if not predictions:
        return 0.0
    return sum(1 for item in predictions if item.overconfident_wrong) / len(predictions)
