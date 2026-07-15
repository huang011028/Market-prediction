"""
历史样本评估与 Agent 贡献归因。

这个模块把历史校准样本、新闻快照回放结果和已验证预测统一成
HistoricalAgentSample，再按 agent / 证据桶 / 聚合贡献生成可审计报告。
它用于回答两个问题：
- 哪些 agent、哪些证据、哪些场景真的有预测能力；
- 哪些 prompt / MCP / skill / 数据源策略正在系统性拉偏预测。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional


AGGREGATOR_AGENT_NAME = "汇总分析师"


@dataclass
class HistoricalAgentSample:
    """一个 agent 在历史样本上的预测验证结果。"""

    agent_name: str
    target: str
    timeframe: str
    as_of: str
    valid_date: str
    predicted_direction: str
    predicted_confidence: float
    actual_direction: str
    actual_change_pct: float
    was_correct: bool
    buckets: dict = field(default_factory=dict)
    evidence_reason: str = ""
    prediction_id: Optional[str] = None
    final_direction: Optional[str] = None
    final_confidence: Optional[float] = None
    final_was_correct: Optional[bool] = None
    prediction_target: dict = field(default_factory=dict)
    fixed_horizon_return_pct: Optional[float] = None
    effective_fixed_return_pct: Optional[float] = None
    target_type_used: str = "absolute_return"
    benchmark_return_pct: Optional[float] = None
    excess_return_pct: Optional[float] = None
    expected_excess_return_pct: Optional[float] = None
    prob_up: Optional[float] = None
    prob_down: Optional[float] = None
    prob_no_edge: Optional[float] = None
    edge_score: Optional[float] = None
    decision: str = ""
    no_trade_reason: str = ""
    neutral_reason: str = ""
    actual_effective_return_pct: Optional[float] = None
    actual_absolute_return_pct: Optional[float] = None
    actual_benchmark_return_pct: Optional[float] = None
    window_max_effective_return_pct: Optional[float] = None
    window_min_effective_return_pct: Optional[float] = None
    brier_score: Optional[float] = None
    edge_hit: Optional[bool] = None

    @property
    def case_id(self) -> str:
        """同一标的、同一评估窗口视作一个独立历史案例。"""
        if self.prediction_id:
            return str(self.prediction_id)
        return "|".join([
            str(self.target or "unknown"),
            str(self.timeframe or "unknown"),
            str(self.as_of or "unknown"),
            str(self.valid_date or "unknown"),
        ])

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HistoricalEvaluationReport:
    """批量历史样本评估报告。"""

    total_samples: int
    verified_predictions: int
    agents: dict = field(default_factory=dict)
    bucket_stats: dict = field(default_factory=dict)
    agent_contributions: dict = field(default_factory=dict)
    improvement_signals: list[dict] = field(default_factory=list)
    wrong_strategy_signals: list[dict] = field(default_factory=list)
    strength_signals: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_markdown(self) -> str:
        lines = [
            "# 历史样本 Agent 评估报告",
            "",
            f"- Agent 样本数: {self.total_samples}",
            f"- 已验证预测数: {self.verified_predictions}",
            f"- 错误策略信号: {len(self.wrong_strategy_signals)}",
            f"- 可保留优势信号: {len(self.strength_signals)}",
            "",
            "## Agent 总览",
            "",
            "| Agent | 样本 | 独立案例 | 命中率 | 平均置信 | 校准缺口 | Brier | Edge命中 | 平均真实超额 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for agent, stats in sorted(
            self.agents.items(),
            key=lambda item: (-item[1].get("total", 0), item[0]),
        ):
            lines.append(
                "| {agent} | {total} | {cases} | {acc:.1%} | {conf:.1%} | {gap:+.1%} | {brier} | {edge_hit} | {actual_eff} |".format(
                    agent=agent,
                    total=stats.get("total", 0),
                    cases=stats.get("unique_cases", stats.get("total", 0)),
                    acc=stats.get("accuracy", 0.0),
                    conf=stats.get("avg_confidence", 0.0),
                    gap=stats.get("calibration_gap", 0.0),
                    brier=self._format_number(stats.get("avg_brier_score"), digits=3),
                    edge_hit=self._format_percent(stats.get("edge_hit_rate")),
                    actual_eff=self._format_percent(
                        stats.get("avg_actual_effective_return_pct"),
                        signed=True,
                        multiplier=1.0,
                    ),
                )
            )

        if self.agent_contributions:
            lines.extend([
                "",
                "## Aggregator 贡献归因",
                "",
                "| Agent | 可归因样本 | 帮助最终正确 | 强化最终错误 | 被忽略但正确 | 净贡献 |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ])
            for agent, stats in sorted(
                self.agent_contributions.items(),
                key=lambda item: item[1].get("net_help_score", 0.0),
            ):
                lines.append(
                    "| {agent} | {total} | {helped} | {reinforced} | {ignored} | {net:+.1%} |".format(
                        agent=agent,
                        total=stats.get("total_with_final", 0),
                        helped=stats.get("helped_final_correct", 0),
                        reinforced=stats.get("reinforced_final_error", 0),
                        ignored=stats.get("ignored_correct_contrarian", 0),
                        net=stats.get("net_help_score", 0.0),
                    )
                )

        lines.extend([
            "",
            "## 错误策略信号",
            "",
        ])
        if not self.wrong_strategy_signals:
            lines.append("暂无达到阈值的错误策略信号。")
        else:
            lines.extend([
                "| 优先级 | Agent | 改进面 | 场景 | 样本 | 独立案例 | 命中率 | Brier | Edge命中 | 结论 |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
            ])
            for signal in self.wrong_strategy_signals[:30]:
                lines.append(
                    "| {priority} | {agent} | {area} | {group}/{bucket} | {n} | {cases} | {acc:.1%} | {brier} | {edge_hit} | {issue} |".format(
                        priority=signal.get("priority", ""),
                        agent=signal.get("agent_name", ""),
                        area=signal.get("area", ""),
                        group=signal.get("bucket_group", ""),
                        bucket=signal.get("bucket", ""),
                        n=int(signal.get("sample_size", 0) or 0),
                        cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                        acc=float(signal.get("accuracy", 0.0) or 0.0),
                        brier=self._format_number(signal.get("avg_brier_score"), digits=3),
                        edge_hit=self._format_percent(signal.get("edge_hit_rate")),
                        issue=signal.get("issue", ""),
                    )
                )

        if self.strength_signals:
            lines.extend([
                "",
                "## 可保留优势信号",
                "",
                "| Agent | 场景 | 样本 | 独立案例 | 命中率 | Edge命中 | 建议 |",
                "| --- | --- | ---: | ---: | ---: | ---: | --- |",
            ])
            for signal in self.strength_signals[:20]:
                lines.append(
                    "| {agent} | {group}/{bucket} | {n} | {cases} | {acc:.1%} | {edge_hit} | {rec} |".format(
                        agent=signal.get("agent_name", ""),
                        group=signal.get("bucket_group", ""),
                        bucket=signal.get("bucket", ""),
                        n=int(signal.get("sample_size", 0) or 0),
                        cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                        acc=float(signal.get("accuracy", 0.0) or 0.0),
                        edge_hit=self._format_percent(signal.get("edge_hit_rate")),
                        rec=signal.get("recommendation", ""),
                    )
                )

        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _format_number(value, digits: int = 2) -> str:
        if value is None:
            return "N/A"
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return "N/A"

    @staticmethod
    def _format_percent(
        value,
        *,
        signed: bool = False,
        multiplier: float = 100.0,
    ) -> str:
        if value is None:
            return "N/A"
        try:
            parsed = float(value) * multiplier
        except (TypeError, ValueError):
            return "N/A"
        sign = "+" if signed else ""
        return f"{parsed:{sign}.1f}%"


class HistoricalAgentEvaluator:
    """批量历史样本评估器。"""

    DEFAULT_MIN_SAMPLES = 5
    LOW_ACCURACY = 0.45
    HIGH_ACCURACY = 0.70
    OVERCONFIDENCE_GAP = 0.20

    DATA_SOURCE_BUCKET_GROUPS = {
        "source_buckets",
        "freshness_buckets",
        "news_count_buckets",
        "data_quality_buckets",
        "data_quality_levels",
        "source_type_buckets",
    }
    MCP_BUCKET_GROUPS = {"mcp_buckets", "tool_buckets", "api_health_buckets"}
    TECH_SKILL_BUCKET_GROUPS = {
        "trend_buckets",
        "momentum_buckets",
        "volume_buckets",
        "market_regime_buckets",
        "volatility_buckets",
        "technical_scenario_buckets",
        "regime_sr_buckets",
        "regime_volume_buckets",
        "sr_volume_buckets",
        "intraday_buckets",
    }
    PROMPT_BUCKET_GROUPS = {
        "position_buckets",
        "sr_zone_buckets",
        "risk_reward_buckets",
        "sentiment_buckets",
        "event_buckets",
        "scorecard_rating_buckets",
        "pe_percentile_buckets",
        "market_buckets",
        "sector_buckets",
        "industry_buckets",
    }

    def evaluate(
        self,
        samples: Iterable[HistoricalAgentSample | dict],
        min_samples: Optional[int] = None,
    ) -> HistoricalEvaluationReport:
        """生成跨 agent 历史评估报告。"""
        min_samples = min_samples or self.DEFAULT_MIN_SAMPLES
        normalized = [self._coerce_sample(sample) for sample in samples]
        normalized = [sample for sample in normalized if sample is not None]

        agent_stats = defaultdict(self._empty_counter)
        bucket_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(self._empty_counter)))
        contributions = defaultdict(self._empty_contribution)
        prediction_ids = set()

        for sample in normalized:
            if sample.prediction_id:
                prediction_ids.add(sample.prediction_id)
            self._update_counter(agent_stats[sample.agent_name], sample)

            for bucket_group, bucket in self._bucket_pairs(sample):
                self._update_counter(
                    bucket_stats[sample.agent_name][bucket_group][bucket],
                    sample,
                )

            self._update_contribution(contributions[sample.agent_name], sample)

        agent_report = {
            agent: self._finalize_counter(stats)
            for agent, stats in agent_stats.items()
        }
        bucket_report = {
            agent: {
                group: {
                    bucket: self._finalize_counter(stats)
                    for bucket, stats in buckets.items()
                }
                for group, buckets in groups.items()
            }
            for agent, groups in bucket_stats.items()
        }
        contribution_report = {
            agent: self._finalize_contribution(stats)
            for agent, stats in contributions.items()
            if stats.get("total_with_final", 0) > 0
        }
        signals = self._build_signals(
            bucket_report,
            contribution_report,
            min_samples=min_samples,
        )
        wrong_signals = [
            signal for signal in signals
            if signal.get("signal_type") == "wrong_strategy"
        ]
        strength_signals = [
            signal for signal in signals
            if signal.get("signal_type") == "strength"
        ]

        return HistoricalEvaluationReport(
            total_samples=len(normalized),
            verified_predictions=len(prediction_ids),
            agents=agent_report,
            bucket_stats=bucket_report,
            agent_contributions=contribution_report,
            improvement_signals=signals,
            wrong_strategy_signals=wrong_signals,
            strength_signals=strength_signals,
        )

    def samples_from_bootstrap_report(self, report: dict) -> list[HistoricalAgentSample]:
        """从 calibration bootstrap / replay report dict 提取样本。"""
        reports = report.get("reports") if isinstance(report, dict) else None
        if isinstance(reports, list):
            samples = []
            for child in reports:
                samples.extend(self.samples_from_bootstrap_report(child))
            return samples

        agent_name = report.get("agent_name") or "unknown"
        samples = []
        for raw in report.get("samples") or []:
            sample = self._coerce_sample({
                **raw,
                "agent_name": raw.get("agent_name") or agent_name,
            })
            if sample:
                samples.append(sample)
        return samples

    def samples_from_prediction_store(
        self,
        store,
        limit: int = 2000,
        prediction_ids: Optional[Iterable[str]] = None,
        target: Optional[str] = None,
        timeframe: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[HistoricalAgentSample]:
        """从 PredictionStore 中读取已验证预测并拆成 agent 样本。"""
        filters = ["p.verified_at IS NOT NULL"]
        params: list = []
        ids = list(prediction_ids or [])
        if ids:
            placeholders = ",".join("?" for _ in ids)
            filters.append(f"p.id IN ({placeholders})")
            params.extend(ids)
        if target:
            filters.append("p.target=?")
            params.append(target)
        if timeframe:
            filters.append("p.timeframe=?")
            params.append(timeframe)
        if start_date:
            filters.append("p.predicted_at>=?")
            params.append(start_date)
        if end_date:
            filters.append("p.predicted_at<=?")
            params.append(end_date)
        where_clause = " AND ".join(filters)
        effective_limit = max(limit, len(ids) * 10) if ids else limit

        with store._conn() as conn:
            rows = conn.execute(
                f"""SELECT
                       p.id AS prediction_id,
                       p.target,
                       p.timeframe,
                       p.predicted_at,
                       p.valid_until,
                       p.direction AS final_direction,
                       p.confidence AS final_confidence,
                       p.actual_direction,
                       p.actual_change_pct,
                       p.actual_effective_return_pct,
                       p.actual_absolute_return_pct,
                       p.actual_benchmark_return_pct,
                       p.window_max_effective_return_pct,
                       p.window_min_effective_return_pct,
                       p.target_type_used,
                       p.brier_score,
                       p.edge_hit,
                       p.expected_excess_return_pct,
                       p.prob_up,
                       p.prob_down,
                       p.prob_no_edge,
                       p.edge_score,
                       p.decision,
                       p.no_trade_reason,
                       p.neutral_reason,
                       p.target_type,
                       p.horizon,
                       p.horizon_trading_days,
                       p.horizon_calendar_days,
                       p.benchmark_symbol,
                       p.up_threshold_pct,
                       p.down_threshold_pct,
                       p.neutral_band_pct,
                       p.report_json,
                       p.direction_correct AS final_direction_correct,
                       ar.agent_name,
                       ar.direction AS agent_direction,
                       ar.confidence AS agent_confidence,
                       ar.data_summary
                   FROM agent_results ar
                   JOIN predictions p ON p.id = ar.prediction_id
                   WHERE {where_clause}
                   ORDER BY p.predicted_at DESC
                   LIMIT ?""",
                (*params, effective_limit),
            ).fetchall()

        samples = []
        for row in rows:
            row = dict(row)
            data_summary = self._loads_json(row.get("data_summary"), {})
            prediction_target = (
                data_summary.get("prediction_target")
                or self._prediction_target_from_row(row)
            )
            buckets = self._derive_buckets_from_agent_summary(
                row.get("agent_name") or "",
                data_summary,
                row.get("timeframe") or "",
            )
            actual_direction = row.get("actual_direction") or "neutral"
            agent_direction = row.get("agent_direction") or "neutral"
            samples.append(
                HistoricalAgentSample(
                    agent_name=row.get("agent_name") or "unknown",
                    target=row.get("target") or "",
                    timeframe=row.get("timeframe") or "",
                    as_of=str(row.get("predicted_at") or "")[:10],
                    valid_date=str(row.get("valid_until") or "")[:10],
                    predicted_direction=agent_direction,
                    predicted_confidence=self._safe_float(
                        row.get("agent_confidence"), 0.0,
                    ),
                    actual_direction=actual_direction,
                    actual_change_pct=self._safe_float(
                        row.get("actual_change_pct"), 0.0,
                    ),
                    was_correct=agent_direction == actual_direction,
                    buckets=buckets,
                    prediction_id=row.get("prediction_id"),
                    final_direction=row.get("final_direction"),
                    final_confidence=self._safe_float(
                        row.get("final_confidence"), None,
                    ),
                    final_was_correct=self._safe_bool_or_none(
                        row.get("final_direction_correct"),
                    ),
                    prediction_target=prediction_target,
                    target_type_used=(
                        row.get("target_type_used")
                        or prediction_target.get("target_type")
                        or "absolute_return"
                    ),
                    benchmark_return_pct=self._safe_float(
                        row.get("actual_benchmark_return_pct"), None,
                    ),
                    excess_return_pct=self._safe_float(
                        row.get("actual_effective_return_pct"), None,
                    ),
                    expected_excess_return_pct=self._safe_float(
                        row.get("expected_excess_return_pct"), None,
                    ),
                    prob_up=self._safe_float(row.get("prob_up"), None),
                    prob_down=self._safe_float(row.get("prob_down"), None),
                    prob_no_edge=self._safe_float(row.get("prob_no_edge"), None),
                    edge_score=self._safe_float(row.get("edge_score"), None),
                    decision=row.get("decision") or "",
                    no_trade_reason=row.get("no_trade_reason") or "",
                    neutral_reason=row.get("neutral_reason") or "",
                    actual_effective_return_pct=self._safe_float(
                        row.get("actual_effective_return_pct"), None,
                    ),
                    actual_absolute_return_pct=self._safe_float(
                        row.get("actual_absolute_return_pct"), None,
                    ),
                    actual_benchmark_return_pct=self._safe_float(
                        row.get("actual_benchmark_return_pct"), None,
                    ),
                    window_max_effective_return_pct=self._safe_float(
                        row.get("window_max_effective_return_pct"), None,
                    ),
                    window_min_effective_return_pct=self._safe_float(
                        row.get("window_min_effective_return_pct"), None,
                    ),
                    brier_score=self._safe_float(row.get("brier_score"), None),
                    edge_hit=self._safe_bool_or_none(row.get("edge_hit")),
                )
            )
        return samples

    @staticmethod
    def load_report_file(path: str | Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write_report(
        self,
        report: HistoricalEvaluationReport,
        output_path: str | Path,
    ) -> dict:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.to_json(), encoding="utf-8")

        md_path = output_path.with_suffix(".md")
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        return {"json": str(output_path), "markdown": str(md_path)}

    def _build_signals(
        self,
        bucket_report: dict,
        contribution_report: dict,
        min_samples: int,
    ) -> list[dict]:
        signals = []
        for agent_name, groups in bucket_report.items():
            for bucket_group, buckets in groups.items():
                for bucket, stats in buckets.items():
                    total = int(stats.get("total", 0) or 0)
                    if total < min_samples:
                        continue
                    accuracy = float(stats.get("accuracy", 0.0) or 0.0)
                    avg_confidence = float(stats.get("avg_confidence", 0.0) or 0.0)
                    calibration_gap = avg_confidence - accuracy
                    if (
                        accuracy < self.LOW_ACCURACY
                        or calibration_gap >= self.OVERCONFIDENCE_GAP
                    ):
                        signals.append(
                            self._build_bucket_signal(
                                agent_name,
                                bucket_group,
                                bucket,
                                stats,
                                is_strength=False,
                            )
                        )
                    elif accuracy >= self.HIGH_ACCURACY and total >= min_samples * 2:
                        signals.append(
                            self._build_bucket_signal(
                                agent_name,
                                bucket_group,
                                bucket,
                                stats,
                                is_strength=True,
                            )
                        )

        for agent_name, stats in contribution_report.items():
            total = int(stats.get("total_with_final", 0) or 0)
            if total < min_samples:
                continue
            unique_cases = int(stats.get("unique_cases", total) or 0)
            reinforced_rate = stats.get("reinforced_final_error_rate", 0.0)
            ignored_rate = stats.get("ignored_correct_contrarian_rate", 0.0)
            if reinforced_rate >= 0.35:
                signals.append(self._build_contribution_signal(
                    agent_name,
                    "reinforced_final_errors",
                    total,
                    unique_cases,
                    1.0 - reinforced_rate,
                    (
                        f"{agent_name} 与最终裁决同向时，有 {reinforced_rate:.1%} 的样本是在强化错误结论。"
                    ),
                    "在 Aggregator 中降低该 agent 同向投票的默认权重，要求引用结构化证据再确认。",
                ))
            if ignored_rate >= 0.25:
                signals.append(self._build_contribution_signal(
                    agent_name,
                    "ignored_correct_contrarian",
                    total,
                    unique_cases,
                    1.0 - ignored_rate,
                    (
                        f"{agent_name} 与最终裁决相反但事后正确的比例达到 {ignored_rate:.1%}。"
                    ),
                    "在 Aggregator 反向证据审计中提高该 agent 的异议可见性，避免过早服从多数投票。",
                ))

        return sorted(
            signals,
            key=lambda signal: (
                {"P0": 0, "P1": 1, "P2": 2}.get(signal.get("priority"), 3),
                signal.get("accuracy", 0.0),
                -int(signal.get("sample_size", 0) or 0),
            ),
        )

    def _build_bucket_signal(
        self,
        agent_name: str,
        bucket_group: str,
        bucket: str,
        stats: dict,
        is_strength: bool,
    ) -> dict:
        total = int(stats.get("total", 0) or 0)
        unique_cases = int(stats.get("unique_cases", total) or 0)
        accuracy = float(stats.get("accuracy", 0.0) or 0.0)
        avg_confidence = float(stats.get("avg_confidence", 0.0) or 0.0)
        calibration_gap = avg_confidence - accuracy
        area = self._map_area(agent_name, bucket_group)
        v2_metrics = self._v2_metric_fields(stats)

        if is_strength:
            return {
                "agent_name": agent_name,
                "area": area,
                "priority": "P2",
                "bucket_group": bucket_group,
                "bucket": bucket,
                "sample_size": total,
                "unique_cases": unique_cases,
                "accuracy": accuracy,
                "avg_confidence": avg_confidence,
                "calibration_gap": calibration_gap,
                "actual_direction_counts": stats.get("actual_direction_counts", {}),
                "predicted_direction_counts": stats.get("predicted_direction_counts", {}),
                "dominant_actual_direction": stats.get("dominant_actual_direction", ""),
                "dominant_actual_rate": stats.get("dominant_actual_rate", 0.0),
                **v2_metrics,
                "signal_type": "strength",
                "issue": (
                    f"{bucket_group}/{bucket} 历史命中率 {accuracy:.1%}，"
                    f"样本数 {total}，当前策略有保留价值。"
                ),
                "recommendation": "保留该场景的判断路径；后续 prompt/MCP/skill/数据源策略调整不要削弱它。",
            }

        priority = "P0" if (
            accuracy < 0.35 and total >= self.DEFAULT_MIN_SAMPLES * 2
        ) or calibration_gap >= 0.30 else "P1"
        issue = (
            f"{bucket_group}/{bucket} 历史命中率 {accuracy:.1%}，"
            f"平均置信 {avg_confidence:.1%}，样本数 {total}，"
            f"独立案例 {unique_cases}。"
        )
        if calibration_gap > 0:
            issue += f" 置信度高于命中率 {calibration_gap:.1%}，属于过度自信策略。"
        if stats.get("avg_brier_score") is not None:
            issue += f" 平均 Brier {stats.get('avg_brier_score'):.3f}。"
        if stats.get("edge_hit_rate") is not None:
            issue += f" Edge 命中率 {stats.get('edge_hit_rate'):.1%}。"

        return {
            "agent_name": agent_name,
            "area": area,
            "priority": priority,
            "bucket_group": bucket_group,
            "bucket": bucket,
            "sample_size": total,
            "unique_cases": unique_cases,
            "accuracy": accuracy,
            "avg_confidence": avg_confidence,
            "calibration_gap": calibration_gap,
            "actual_direction_counts": stats.get("actual_direction_counts", {}),
            "predicted_direction_counts": stats.get("predicted_direction_counts", {}),
            "dominant_actual_direction": stats.get("dominant_actual_direction", ""),
            "dominant_actual_rate": stats.get("dominant_actual_rate", 0.0),
            **v2_metrics,
            "signal_type": "wrong_strategy",
            "issue": issue,
            "recommendation": self._recommendation_for_area(area, bucket_group, bucket),
        }

    @staticmethod
    def _v2_metric_fields(stats: dict) -> dict:
        return {
            "avg_brier_score": stats.get("avg_brier_score"),
            "brier_samples": stats.get("brier_samples", 0),
            "edge_hit_rate": stats.get("edge_hit_rate"),
            "edge_hit_samples": stats.get("edge_hit_samples", 0),
            "avg_edge_score": stats.get("avg_edge_score"),
            "actionable_count": stats.get("actionable_count", 0),
            "actionable_coverage": stats.get("actionable_coverage", 0.0),
            "avg_actual_effective_return_pct": stats.get("avg_actual_effective_return_pct"),
            "avg_expected_excess_return_pct": stats.get("avg_expected_excess_return_pct"),
        }

    @staticmethod
    def _build_contribution_signal(
        agent_name: str,
        bucket: str,
        total: int,
        unique_cases: int,
        accuracy: float,
        issue: str,
        recommendation: str,
    ) -> dict:
        return {
            "agent_name": AGGREGATOR_AGENT_NAME,
            "area": "calibration",
            "priority": "P1",
            "bucket_group": f"agent_contribution/{agent_name}",
            "bucket": bucket,
            "sample_size": total,
            "unique_cases": unique_cases,
            "accuracy": accuracy,
            "avg_confidence": 0.0,
            "calibration_gap": 0.0,
            "signal_type": "wrong_strategy",
            "issue": issue,
            "recommendation": recommendation,
        }

    def _map_area(self, agent_name: str, bucket_group: str) -> str:
        if bucket_group in self.DATA_SOURCE_BUCKET_GROUPS:
            return "data_source"
        if bucket_group in self.MCP_BUCKET_GROUPS:
            return "mcp"
        if bucket_group == "confidence_bins":
            return "calibration"
        if bucket_group in self.TECH_SKILL_BUCKET_GROUPS:
            return "skill"
        if bucket_group in self.PROMPT_BUCKET_GROUPS:
            return "prompt"
        if agent_name in {"行业对比分析师", "国际形势分析师"} and "quality" in bucket_group:
            return "data_source"
        return "calibration"

    @staticmethod
    def _recommendation_for_area(area: str, bucket_group: str, bucket: str) -> str:
        if area == "data_source":
            return (
                "把该数据源/数据质量场景设为低可信或补充替代数据源；"
                "样本再次证明前不得给高置信方向。"
            )
        if area == "mcp":
            return "检查 MCP/工具调用覆盖率、失败重试和字段完整性，避免单工具失效直接变成强方向。"
        if area == "prompt":
            return "把该历史失败场景写入 prompt 反例自检，要求输出反向证据和降置信理由。"
        if area == "skill":
            return "检查特征工程、阈值和场景标签，增加反例规则并回放验证。"
        return "下调该场景的置信上限，并在 Aggregator 中按历史命中率动态降权。"

    def _bucket_pairs(self, sample: HistoricalAgentSample) -> list[tuple[str, str]]:
        buckets = dict(sample.buckets or {})
        buckets.setdefault("confidence_bin", self._confidence_bin(sample.predicted_confidence))
        if sample.decision:
            buckets.setdefault("decision_bucket", sample.decision)
        if sample.edge_score is not None:
            buckets.setdefault("edge_bucket", self._edge_bucket(sample.edge_score))
        resolved_brier = sample.brier_score
        if resolved_brier is None:
            resolved_brier = self._brier_score_for_sample(sample)
        if resolved_brier is not None:
            buckets.setdefault("brier_bucket", self._brier_bucket(resolved_brier))
        target_spec = sample.prediction_target or {}
        if target_spec.get("horizon"):
            buckets.setdefault("horizon_bucket", target_spec.get("horizon"))
        if sample.target_type_used:
            buckets.setdefault("target_type_bucket", sample.target_type_used)

        pairs = []
        for raw_group, raw_bucket in buckets.items():
            if raw_bucket in (None, "", "N/A"):
                continue
            group = self._normalize_bucket_group(raw_group)
            bucket = str(raw_bucket)
            pairs.append((group, bucket))
        return pairs

    @staticmethod
    def _normalize_bucket_group(key: str) -> str:
        key = str(key or "unknown").strip()
        if key == "confidence_bin":
            return "confidence_bins"
        if key.endswith("_bucket"):
            return f"{key[:-7]}_buckets"
        if key.endswith("_level"):
            return f"{key}s"
        if key in {"industry", "market", "sector"}:
            return f"{key}_buckets"
        return key

    @staticmethod
    def _confidence_bin(confidence: float) -> str:
        confidence = HistoricalAgentEvaluator._safe_float(confidence, 0.0)
        if confidence < 0.2:
            return "0.0-0.2"
        if confidence < 0.4:
            return "0.2-0.4"
        if confidence < 0.6:
            return "0.4-0.6"
        if confidence < 0.8:
            return "0.6-0.8"
        return "0.8-1.0"

    @staticmethod
    def _edge_bucket(edge_score: float) -> str:
        edge_score = HistoricalAgentEvaluator._safe_float(edge_score, 0.0) or 0.0
        if edge_score < 0.25:
            return "no_edge"
        if edge_score < 0.5:
            return "weak_edge"
        if edge_score < 0.75:
            return "moderate_edge"
        return "strong_edge"

    @staticmethod
    def _brier_bucket(brier_score: float) -> str:
        brier_score = HistoricalAgentEvaluator._safe_float(brier_score, 1.0) or 1.0
        if brier_score <= 0.08:
            return "excellent_calibration"
        if brier_score <= 0.16:
            return "good_calibration"
        if brier_score <= 0.28:
            return "weak_calibration"
        return "poor_calibration"

    @staticmethod
    def _empty_counter() -> dict:
        return {
            "total": 0,
            "correct": 0,
            "confidence_sum": 0.0,
            "abs_return_sum": 0.0,
            "high_conf_wrong": 0,
            "brier_sum": 0.0,
            "brier_count": 0,
            "edge_hit_count": 0,
            "edge_hit_total": 0,
            "edge_score_sum": 0.0,
            "edge_score_count": 0,
            "actionable_count": 0,
            "expected_excess_return_sum": 0.0,
            "expected_excess_return_count": 0,
            "effective_return_sum": 0.0,
            "effective_return_count": 0,
            "case_ids": set(),
            "actual_direction_counts": defaultdict(int),
            "predicted_direction_counts": defaultdict(int),
        }

    @staticmethod
    def _update_counter(counter: dict, sample: HistoricalAgentSample) -> None:
        counter["total"] += 1
        if sample.was_correct:
            counter["correct"] += 1
        counter["confidence_sum"] += sample.predicted_confidence
        counter["abs_return_sum"] += abs(sample.actual_change_pct)
        if sample.predicted_confidence >= 0.65 and not sample.was_correct:
            counter["high_conf_wrong"] += 1
        counter.setdefault("case_ids", set()).add(sample.case_id)
        counter.setdefault("actual_direction_counts", defaultdict(int))[
            sample.actual_direction or "neutral"
        ] += 1
        counter.setdefault("predicted_direction_counts", defaultdict(int))[
            sample.predicted_direction or "neutral"
        ] += 1

        brier_score = sample.brier_score
        if brier_score is None:
            brier_score = HistoricalAgentEvaluator._brier_score_for_sample(sample)
        if brier_score is not None:
            counter["brier_sum"] += brier_score
            counter["brier_count"] += 1

        if sample.edge_hit is not None:
            counter["edge_hit_total"] += 1
            if bool(sample.edge_hit):
                counter["edge_hit_count"] += 1

        if sample.edge_score is not None:
            counter["edge_score_sum"] += sample.edge_score
            counter["edge_score_count"] += 1

        if sample.decision in {"long_bias", "short_bias"}:
            counter["actionable_count"] += 1

        if sample.expected_excess_return_pct is not None:
            counter["expected_excess_return_sum"] += sample.expected_excess_return_pct
            counter["expected_excess_return_count"] += 1

        actual_effective = sample.actual_effective_return_pct
        if actual_effective is None:
            actual_effective = sample.effective_fixed_return_pct
        if actual_effective is None:
            actual_effective = sample.excess_return_pct
        if actual_effective is not None:
            counter["effective_return_sum"] += actual_effective
            counter["effective_return_count"] += 1

    @staticmethod
    def _finalize_counter(counter: dict) -> dict:
        total = int(counter.get("total", 0) or 0)
        if total <= 0:
            return {
                "total": 0,
                "accuracy": 0.0,
                "avg_confidence": 0.0,
                "calibration_gap": 0.0,
                "avg_abs_return_pct": 0.0,
                "high_conf_wrong": 0,
                "unique_cases": 0,
                "avg_brier_score": None,
                "brier_samples": 0,
                "edge_hit_rate": None,
                "edge_hit_samples": 0,
                "avg_edge_score": None,
                "actionable_count": 0,
                "actionable_coverage": 0.0,
                "avg_actual_effective_return_pct": None,
                "avg_expected_excess_return_pct": None,
            }
        accuracy = counter.get("correct", 0) / total
        avg_conf = counter.get("confidence_sum", 0.0) / total
        unique_cases = len(counter.get("case_ids") or [])
        actual_counts = dict(counter.get("actual_direction_counts") or {})
        predicted_counts = dict(counter.get("predicted_direction_counts") or {})
        dominant_actual, dominant_actual_count = HistoricalAgentEvaluator._dominant_count(
            actual_counts,
        )
        return {
            "total": total,
            "unique_cases": unique_cases,
            "correct": int(counter.get("correct", 0) or 0),
            "accuracy": round(accuracy, 3),
            "avg_confidence": round(avg_conf, 3),
            "calibration_gap": round(avg_conf - accuracy, 3),
            "avg_abs_return_pct": round(counter.get("abs_return_sum", 0.0) / total, 2),
            "high_conf_wrong": int(counter.get("high_conf_wrong", 0) or 0),
            "actual_direction_counts": actual_counts,
            "predicted_direction_counts": predicted_counts,
            "dominant_actual_direction": dominant_actual,
            "dominant_actual_rate": round(dominant_actual_count / total, 3),
            "avg_brier_score": HistoricalAgentEvaluator._safe_average(
                counter.get("brier_sum", 0.0),
                counter.get("brier_count", 0),
                digits=4,
            ),
            "brier_samples": int(counter.get("brier_count", 0) or 0),
            "edge_hit_rate": HistoricalAgentEvaluator._safe_average(
                counter.get("edge_hit_count", 0),
                counter.get("edge_hit_total", 0),
                digits=3,
            ),
            "edge_hit_samples": int(counter.get("edge_hit_total", 0) or 0),
            "avg_edge_score": HistoricalAgentEvaluator._safe_average(
                counter.get("edge_score_sum", 0.0),
                counter.get("edge_score_count", 0),
                digits=3,
            ),
            "actionable_count": int(counter.get("actionable_count", 0) or 0),
            "actionable_coverage": round(
                int(counter.get("actionable_count", 0) or 0) / total,
                3,
            ),
            "avg_actual_effective_return_pct": HistoricalAgentEvaluator._safe_average(
                counter.get("effective_return_sum", 0.0),
                counter.get("effective_return_count", 0),
                digits=2,
            ),
            "effective_return_samples": int(counter.get("effective_return_count", 0) or 0),
            "avg_expected_excess_return_pct": HistoricalAgentEvaluator._safe_average(
                counter.get("expected_excess_return_sum", 0.0),
                counter.get("expected_excess_return_count", 0),
                digits=2,
            ),
            "expected_excess_return_samples": int(
                counter.get("expected_excess_return_count", 0) or 0,
            ),
        }

    @staticmethod
    def _dominant_count(counts: dict) -> tuple[str, int]:
        if not counts:
            return "", 0
        direction, count = max(
            counts.items(),
            key=lambda item: (int(item[1] or 0), str(item[0])),
        )
        return str(direction), int(count or 0)

    @staticmethod
    def _safe_average(total: float, count: int, digits: int = 3) -> Optional[float]:
        count = int(count or 0)
        if count <= 0:
            return None
        return round(float(total or 0.0) / count, digits)

    @staticmethod
    def _brier_score_for_sample(sample: HistoricalAgentSample) -> Optional[float]:
        probs = {
            "bullish": sample.prob_up,
            "bearish": sample.prob_down,
            "neutral": sample.prob_no_edge,
        }
        if any(value is None for value in probs.values()):
            return None
        total = sum(float(value or 0.0) for value in probs.values())
        if total <= 0:
            return None
        normalized = {
            label: float(value or 0.0) / total
            for label, value in probs.items()
        }
        actual = sample.actual_direction or "neutral"
        score = sum(
            (normalized[label] - (1.0 if actual == label else 0.0)) ** 2
            for label in ("bullish", "bearish", "neutral")
        ) / 3.0
        return round(score, 4)

    @staticmethod
    def _empty_contribution() -> dict:
        return {
            "total_with_final": 0,
            "agreed_with_final": 0,
            "helped_final_correct": 0,
            "reinforced_final_error": 0,
            "ignored_correct_contrarian": 0,
            "correctly_discounted_wrong_agent": 0,
            "case_ids": set(),
        }

    @staticmethod
    def _update_contribution(contribution: dict, sample: HistoricalAgentSample) -> None:
        if not sample.final_direction:
            return

        final_correct = sample.final_was_correct
        if final_correct is None:
            final_correct = sample.final_direction == sample.actual_direction

        contribution["total_with_final"] += 1
        contribution.setdefault("case_ids", set()).add(sample.case_id)
        agrees = sample.predicted_direction == sample.final_direction
        if agrees:
            contribution["agreed_with_final"] += 1
            if final_correct:
                contribution["helped_final_correct"] += 1
            else:
                contribution["reinforced_final_error"] += 1
        else:
            if sample.was_correct and not final_correct:
                contribution["ignored_correct_contrarian"] += 1
            elif (not sample.was_correct) and final_correct:
                contribution["correctly_discounted_wrong_agent"] += 1

    @staticmethod
    def _finalize_contribution(stats: dict) -> dict:
        total = int(stats.get("total_with_final", 0) or 0)
        if total <= 0:
            return {}
        helped = int(stats.get("helped_final_correct", 0) or 0)
        discounted = int(stats.get("correctly_discounted_wrong_agent", 0) or 0)
        reinforced = int(stats.get("reinforced_final_error", 0) or 0)
        ignored = int(stats.get("ignored_correct_contrarian", 0) or 0)
        net = (helped + discounted - reinforced - ignored) / total
        result = {key: value for key, value in stats.items() if key != "case_ids"}
        unique_cases = len(stats.get("case_ids") or [])
        result.update({
            "unique_cases": unique_cases,
            "agree_rate": round(stats.get("agreed_with_final", 0) / total, 3),
            "reinforced_final_error_rate": round(reinforced / total, 3),
            "ignored_correct_contrarian_rate": round(ignored / total, 3),
            "net_help_score": round(net, 3),
        })
        return result

    def _derive_buckets_from_agent_summary(
        self,
        agent_name: str,
        data_summary: dict,
        timeframe: str,
    ) -> dict:
        evidence = data_summary.get("evidence") or {}
        try:
            if agent_name == "近期股价分析师":
                from src.utils.technical_calibrator import TechnicalConfidenceCalibrator

                return TechnicalConfidenceCalibrator.extract_buckets_from_evidence(
                    evidence,
                    timeframe,
                )
            if agent_name == "最新新闻分析师":
                from src.utils.news_calibrator import NewsConfidenceCalibrator

                return NewsConfidenceCalibrator.extract_buckets_from_evidence(evidence)
        except Exception:
            return {}

        if agent_name == "公司前景分析师":
            return self._fundamental_buckets(data_summary)
        if agent_name == "行业对比分析师":
            return self._industry_buckets(data_summary)
        if agent_name == "国际形势分析师":
            return self._macro_buckets(data_summary)
        return {}

    def _fundamental_buckets(self, data_summary: dict) -> dict:
        quality = data_summary.get("data_quality") or {}
        quality_value = self._safe_float(
            quality.get("overall_quality"),
            self._safe_float(data_summary.get("quality"), None),
        )
        if quality_value is None:
            data_quality_bucket = "unknown"
        elif quality_value >= 0.70:
            data_quality_bucket = "high"
        elif quality_value >= 0.40:
            data_quality_bucket = "medium"
        else:
            data_quality_bucket = "low"

        pe_percentile = self._safe_float(
            (data_summary.get("valuation_analysis") or {}).get("pe_percentile_3yr"),
            None,
        )
        return {
            "data_quality_bucket": data_quality_bucket,
            "scorecard_rating_bucket": (
                (data_summary.get("quality_scorecard") or {}).get("rating")
                or "unknown"
            ),
            "pe_percentile_bucket": self._percentile_bucket(pe_percentile),
        }

    def _industry_buckets(self, data_summary: dict) -> dict:
        quality = data_summary.get("data_quality") or {}
        has_constituents = bool(quality.get("has_constituents"))
        has_trend = bool(quality.get("has_trend"))
        overall = self._safe_float(quality.get("overall"), 0.0)
        if has_constituents and has_trend:
            data_quality_level = "constituents+trend"
        elif has_constituents:
            data_quality_level = "constituents_only"
        elif overall and overall > 0.1:
            data_quality_level = "reference_only"
        else:
            data_quality_level = "none"
        return {
            "industry": data_summary.get("industry") or "unknown",
            "data_quality_level": data_quality_level,
        }

    def _macro_buckets(self, data_summary: dict) -> dict:
        quality = data_summary.get("data_quality") or {}
        freshness = self._parse_percent(quality.get("overall_freshness"), 0.5)
        ref_count = int(self._safe_float(quality.get("reference_count"), 0) or 0)
        realtime_count = int(self._safe_float(quality.get("realtime_count"), 0) or 0)
        if freshness >= 0.70 and ref_count <= 1:
            data_quality_level = "fresh"
        elif freshness >= 0.45 and ref_count <= 2:
            data_quality_level = "mixed"
        elif ref_count >= 3:
            data_quality_level = "reference_heavy"
        elif realtime_count <= 2:
            data_quality_level = "sparse"
        else:
            data_quality_level = "stale"
        return {
            "market": data_summary.get("market") or "unknown",
            "sector": data_summary.get("sector") or "unknown",
            "data_quality_level": data_quality_level,
        }

    @staticmethod
    def _percentile_bucket(value: Optional[float]) -> str:
        if value is None:
            return "unknown"
        if value <= 0.2:
            return "very_low"
        if value <= 0.4:
            return "low"
        if value <= 0.6:
            return "mid"
        if value <= 0.8:
            return "high"
        return "very_high"

    @staticmethod
    def _coerce_sample(
        raw: HistoricalAgentSample | dict,
    ) -> Optional[HistoricalAgentSample]:
        if isinstance(raw, HistoricalAgentSample):
            return raw
        if not isinstance(raw, dict):
            return None

        predicted_direction = (
            raw.get("predicted_direction")
            or raw.get("direction")
            or raw.get("agent_direction")
            or "neutral"
        )
        actual_direction = raw.get("actual_direction") or "neutral"
        was_correct = raw.get("was_correct")
        if was_correct is None:
            was_correct = predicted_direction == actual_direction

        return HistoricalAgentSample(
            agent_name=raw.get("agent_name") or "unknown",
            target=raw.get("target") or raw.get("symbol") or "",
            timeframe=raw.get("timeframe") or "",
            as_of=raw.get("as_of") or raw.get("date") or "",
            valid_date=raw.get("valid_date") or raw.get("valid_until") or "",
            predicted_direction=predicted_direction,
            predicted_confidence=HistoricalAgentEvaluator._safe_float(
                raw.get("predicted_confidence") or raw.get("confidence"),
                0.0,
            ),
            actual_direction=actual_direction,
            actual_change_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("actual_change_pct"),
                    raw.get("actual_return_pct"),
                ),
                0.0,
            ),
            was_correct=bool(was_correct),
            buckets=raw.get("buckets") or {},
            evidence_reason=raw.get("evidence_reason") or "",
            prediction_id=raw.get("prediction_id"),
            final_direction=raw.get("final_direction"),
            final_confidence=HistoricalAgentEvaluator._safe_float(
                raw.get("final_confidence"), None,
            ),
            final_was_correct=raw.get("final_was_correct"),
            prediction_target=raw.get("prediction_target") or {},
            fixed_horizon_return_pct=HistoricalAgentEvaluator._safe_float(
                raw.get("fixed_horizon_return_pct"), None,
            ),
            effective_fixed_return_pct=HistoricalAgentEvaluator._safe_float(
                raw.get("effective_fixed_return_pct"), None,
            ),
            target_type_used=raw.get("target_type_used") or "absolute_return",
            benchmark_return_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("benchmark_return_pct"),
                    raw.get("actual_benchmark_return_pct"),
                ),
                None,
            ),
            excess_return_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("excess_return_pct"),
                    raw.get("actual_effective_return_pct"),
                ),
                None,
            ),
            expected_excess_return_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("expected_excess_return_pct"),
                    raw.get("expected_return_pct"),
                ),
                None,
            ),
            prob_up=HistoricalAgentEvaluator._safe_float(raw.get("prob_up"), None),
            prob_down=HistoricalAgentEvaluator._safe_float(raw.get("prob_down"), None),
            prob_no_edge=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("prob_no_edge"),
                    raw.get("prob_neutral"),
                ),
                None,
            ),
            edge_score=HistoricalAgentEvaluator._safe_float(
                raw.get("edge_score"), None,
            ),
            decision=raw.get("decision") or "",
            no_trade_reason=raw.get("no_trade_reason") or "",
            neutral_reason=raw.get("neutral_reason") or "",
            actual_effective_return_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("actual_effective_return_pct"),
                    raw.get("effective_fixed_return_pct"),
                    raw.get("excess_return_pct"),
                ),
                None,
            ),
            actual_absolute_return_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("actual_absolute_return_pct"),
                    raw.get("fixed_horizon_return_pct"),
                    raw.get("actual_change_pct"),
                ),
                None,
            ),
            actual_benchmark_return_pct=HistoricalAgentEvaluator._safe_float(
                HistoricalAgentEvaluator._first_present(
                    raw.get("actual_benchmark_return_pct"),
                    raw.get("benchmark_return_pct"),
                ),
                None,
            ),
            window_max_effective_return_pct=HistoricalAgentEvaluator._safe_float(
                raw.get("window_max_effective_return_pct"), None,
            ),
            window_min_effective_return_pct=HistoricalAgentEvaluator._safe_float(
                raw.get("window_min_effective_return_pct"), None,
            ),
            brier_score=HistoricalAgentEvaluator._safe_float(
                raw.get("brier_score"), None,
            ),
            edge_hit=HistoricalAgentEvaluator._safe_bool_or_none(
                raw.get("edge_hit"),
            ),
        )

    @staticmethod
    def _prediction_target_from_row(row: dict) -> dict:
        report = HistoricalAgentEvaluator._loads_json(row.get("report_json"), {})
        target = report.get("prediction_target") if isinstance(report, dict) else None
        if isinstance(target, dict) and target:
            return target
        target = {
            "target_type": row.get("target_type"),
            "horizon": row.get("horizon"),
            "horizon_trading_days": row.get("horizon_trading_days"),
            "horizon_calendar_days": row.get("horizon_calendar_days"),
            "benchmark_symbol": row.get("benchmark_symbol"),
            "up_threshold_pct": row.get("up_threshold_pct"),
            "down_threshold_pct": row.get("down_threshold_pct"),
            "neutral_band_pct": row.get("neutral_band_pct"),
            "expected_return_pct": row.get("expected_excess_return_pct"),
            "prob_up": row.get("prob_up"),
            "prob_down": row.get("prob_down"),
            "prob_neutral": row.get("prob_no_edge"),
        }
        return {key: value for key, value in target.items() if value not in (None, "")}

    @staticmethod
    def _loads_json(value, default):
        if not value:
            return default
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _safe_float(value, default: Optional[float] = 0.0) -> Optional[float]:
        try:
            if value in (None, "", "N/A"):
                return default
            if isinstance(value, str):
                stripped = value.strip()
                if stripped.endswith("%"):
                    return float(stripped[:-1]) / 100
                value = stripped
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _first_present(*values):
        for value in values:
            if value not in (None, "", "N/A"):
                return value
        return None

    @staticmethod
    def _safe_bool_or_none(value) -> Optional[bool]:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "y", "1"}:
                return True
            if lowered in {"false", "no", "n", "0"}:
                return False
        return bool(value)

    @staticmethod
    def _parse_percent(value, default: float = 0.5) -> float:
        parsed = HistoricalAgentEvaluator._safe_float(value, default)
        if parsed is None:
            return default
        return parsed / 100 if parsed > 1 else parsed
