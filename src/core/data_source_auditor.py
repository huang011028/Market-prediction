"""Data source coverage audit for the agent team.

The auditor calls each agent's ``gather_data`` method directly. It does not run
LLM reasoning, so the report isolates data-source coverage from prompt/model
quality.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from src.data.symbol_resolver import resolve_symbol


AGENT_TECH = "近期股价分析师"
AGENT_NEWS = "最新新闻分析师"
AGENT_FUND = "公司前景分析师"
AGENT_MACRO = "国际形势分析师"
AGENT_INDUSTRY = "行业对比分析师"


REQUIRED_FIELDS: dict[str, list[str]] = {
    AGENT_TECH: [
        "price_summary.latest_close",
        "price_summary.change_5d_pct",
        "indicators.MA5",
        "indicators.MACD_signal",
        "indicators.RSI",
        "technical_snapshot",
        "recent_trend",
        "data_quality.score",
    ],
    AGENT_NEWS: [
        "news_count",
        "news_source",
        "sources_used",
        "_data_quality.score",
        "_data_quality.is_available",
    ],
    AGENT_FUND: [
        "financials.latest_revenue_100m",
        "financials.latest_net_profit_100m",
        "financials.revenue_yoy_pct",
        "financials.profit_yoy_pct",
        "financials.roe_pct",
        "valuation.pe",
        "valuation.pb",
        "valuation.market_cap_100m",
        "data_quality.overall_quality",
    ],
    AGENT_MACRO: [
        "china.pmi_manufacturing",
        "china.lpr_1y_pct",
        "china.m2_yoy_pct",
        "forex.dxy",
        "us.10y_yield_pct",
        "us.vix",
        "data_quality.overall_freshness",
    ],
    AGENT_INDUSTRY: [
        "industry_name",
        "stock_metrics.pe",
        "stock_metrics.pb",
        "stock_metrics.roe_pct",
        "industry_average.pe",
        "industry_average.pb",
        "industry_average.roe_pct",
        "rank_in_industry.pe_rank",
        "value_score.score",
        "data_quality.overall",
    ],
}


CRITICAL_FIELDS: dict[str, list[str]] = {
    AGENT_TECH: ["price_summary.latest_close", "data_quality.score"],
    AGENT_NEWS: ["news_count", "_data_quality.is_available"],
    AGENT_FUND: ["data_source", "data_quality.overall_quality"],
    AGENT_MACRO: ["data_quality.overall_freshness"],
    AGENT_INDUSTRY: ["industry_name", "data_quality.overall"],
}


@dataclass
class AgentDataSourceCheck:
    target: str
    resolved_symbol: str
    target_name: str
    market: str
    timeframe: str
    agent_name: str
    status: str
    quality_label: str
    quality_score: float
    data_source: str = ""
    sources: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    critical_missing: list[str] = field(default_factory=list)
    field_coverage: float = 0.0
    elapsed_seconds: float = 0.0
    summary: str = ""
    error: str = ""


@dataclass
class TargetDataSourceAudit:
    target: str
    resolved_symbol: str
    target_name: str
    market: str
    timeframe: str
    checks: list[AgentDataSourceCheck]
    status_counts: dict[str, int]
    overall_status: str
    elapsed_seconds: float


@dataclass
class DataSourceAuditReport:
    generated_at: str
    timeframe: str
    agent_names: list[str]
    targets: list[TargetDataSourceAudit]
    summary: dict[str, Any]
    recurring_issues: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_markdown(self) -> str:
        lines = [
            "# 数据源覆盖率巡检报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 周期: {self.timeframe}",
            f"- 标的数: {self.summary.get('target_count', 0)}",
            f"- Agent检查数: {self.summary.get('check_count', 0)}",
            "",
            "## 总览",
            "",
            "| 状态 | 数量 |",
            "|---|---:|",
        ]
        for status in ("ok", "partial", "poor", "failed"):
            lines.append(f"| {status} | {self.summary.get('status_counts', {}).get(status, 0)} |")

        lines.extend([
            "",
            "## 标的明细",
            "",
            "| 标的 | 市场 | Agent | 状态 | 质量 | 数据源 | 缺失字段 |",
            "|---|---|---|---|---:|---|---|",
        ])
        for target in self.targets:
            label = target.target_name or target.resolved_symbol
            for check in target.checks:
                missing = ", ".join(check.missing_fields[:5])
                if len(check.missing_fields) > 5:
                    missing += f" (+{len(check.missing_fields) - 5})"
                lines.append(
                    "| {label} | {market} | {agent} | {status} | {quality:.0%} | {source} | {missing} |".format(
                        label=label,
                        market=target.market,
                        agent=check.agent_name,
                        status=check.status,
                        quality=check.quality_score,
                        source=check.data_source or "-",
                        missing=missing or "-",
                    )
                )

        lines.extend(["", "## 高频问题", ""])
        if not self.recurring_issues:
            lines.append("- 未发现跨标的重复缺口。")
        else:
            for issue in self.recurring_issues[:20]:
                targets = ", ".join(issue.get("targets", [])[:8])
                lines.append(
                    "- {agent}: `{field}` 缺失 {count} 次，涉及 {targets}".format(
                        agent=issue.get("agent"),
                        field=issue.get("field"),
                        count=issue.get("count"),
                        targets=targets,
                    )
                )

        lines.extend([
            "",
            "## 状态定义",
            "",
            "- ok: 数据质量较高且关键字段完整。",
            "- partial: 可分析，但存在非关键缺口或质量偏低。",
            "- poor: 关键字段缺失、质量很低或主要数据源不可用。",
            "- failed: 数据采集抛异常或超时。",
        ])
        return "\n".join(lines)


class DataSourceCoverageAuditor:
    """Batch audit data coverage across agents and targets."""

    def __init__(
        self,
        agents: Optional[Iterable[Any]] = None,
        timeout_seconds: int = 60,
    ):
        self.agents = list(agents) if agents is not None else self.default_agents()
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def default_agents() -> list[Any]:
        from src.agents.fundamental_analyst import FundamentalAnalyst
        from src.agents.industry_analyst import IndustryAnalyst
        from src.agents.macro_analyst import MacroAnalyst
        from src.agents.news_analyst import NewsAnalyst
        from src.agents.technical_analyst import TechnicalAnalyst

        llm = None
        return [
            TechnicalAnalyst(llm),
            NewsAnalyst(llm, archive_snapshots=False),
            FundamentalAnalyst(llm),
            MacroAnalyst(llm),
            IndustryAnalyst(llm),
        ]

    async def audit_targets(
        self,
        targets: list[str],
        timeframe: str = "短期(1周)",
        agent_names: Optional[list[str]] = None,
        concurrency: int = 1,
    ) -> DataSourceAuditReport:
        selected_agents = self._select_agents(agent_names)
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run_one(target: str) -> TargetDataSourceAudit:
            async with semaphore:
                return await self.audit_target(target, timeframe, selected_agents)

        audits = await asyncio.gather(*(run_one(target) for target in targets))
        summary = self._build_summary(audits)
        recurring = self._find_recurring_issues(audits)
        return DataSourceAuditReport(
            generated_at=datetime.now().isoformat(timespec="seconds"),
            timeframe=timeframe,
            agent_names=[agent.name for agent in selected_agents],
            targets=audits,
            summary=summary,
            recurring_issues=recurring,
        )

    async def audit_target(
        self,
        target: str,
        timeframe: str,
        agents: Optional[list[Any]] = None,
    ) -> TargetDataSourceAudit:
        agents = agents if agents is not None else self.agents
        info = resolve_symbol(target)
        resolved = info.symbol or target
        start = time.monotonic()
        checks = await asyncio.gather(
            *(self._audit_agent(agent, target, resolved, info, timeframe) for agent in agents)
        )
        elapsed = time.monotonic() - start
        status_counts = self._count_statuses(checks)
        overall = self._overall_status(status_counts)
        return TargetDataSourceAudit(
            target=target,
            resolved_symbol=resolved,
            target_name=info.name,
            market=info.market,
            timeframe=timeframe,
            checks=checks,
            status_counts=status_counts,
            overall_status=overall,
            elapsed_seconds=round(elapsed, 2),
        )

    async def _audit_agent(
        self,
        agent: Any,
        target: str,
        resolved_symbol: str,
        info: Any,
        timeframe: str,
    ) -> AgentDataSourceCheck:
        start = time.monotonic()
        agent_name = getattr(agent, "name", agent.__class__.__name__)
        try:
            data = await asyncio.wait_for(
                agent.gather_data(resolved_symbol, timeframe),
                timeout=self.timeout_seconds,
            )
            elapsed = time.monotonic() - start
            return self._build_check(
                target=target,
                resolved_symbol=resolved_symbol,
                target_name=info.name,
                market=info.market,
                timeframe=timeframe,
                agent_name=agent_name,
                data=data or {},
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            return AgentDataSourceCheck(
                target=target,
                resolved_symbol=resolved_symbol,
                target_name=info.name,
                market=info.market,
                timeframe=timeframe,
                agent_name=agent_name,
                status="failed",
                quality_label="failed",
                quality_score=0.0,
                missing_fields=REQUIRED_FIELDS.get(agent_name, []),
                critical_missing=CRITICAL_FIELDS.get(agent_name, []),
                field_coverage=0.0,
                elapsed_seconds=round(elapsed, 2),
                error=str(exc),
                summary=f"数据采集失败: {exc}",
            )

    def _build_check(
        self,
        *,
        target: str,
        resolved_symbol: str,
        target_name: str,
        market: str,
        timeframe: str,
        agent_name: str,
        data: dict[str, Any],
        elapsed_seconds: float,
    ) -> AgentDataSourceCheck:
        required = REQUIRED_FIELDS.get(agent_name, [])
        critical = CRITICAL_FIELDS.get(agent_name, [])
        missing = [field for field in required if self._is_missing_path(data, field)]
        critical_missing = [field for field in critical if self._is_missing_path(data, field)]
        quality_score = self._extract_quality_score(agent_name, data)
        if required:
            field_coverage = (len(required) - len(missing)) / len(required)
        else:
            field_coverage = 1.0

        data_source, sources = self._extract_sources(agent_name, data)
        status = self._status_from_quality(quality_score, missing, critical_missing, data)
        quality_label = self._quality_label(quality_score)
        summary = self._build_agent_summary(agent_name, data, missing, critical_missing)
        return AgentDataSourceCheck(
            target=target,
            resolved_symbol=resolved_symbol,
            target_name=target_name,
            market=market,
            timeframe=timeframe,
            agent_name=agent_name,
            status=status,
            quality_label=quality_label,
            quality_score=round(quality_score, 3),
            data_source=data_source,
            sources=sources,
            missing_fields=missing,
            critical_missing=critical_missing,
            field_coverage=round(field_coverage, 3),
            elapsed_seconds=round(elapsed_seconds, 2),
            summary=summary,
        )

    def _select_agents(self, agent_names: Optional[list[str]]) -> list[Any]:
        if not agent_names:
            return self.agents
        requested = set(agent_names)
        return [agent for agent in self.agents if getattr(agent, "name", "") in requested]

    @staticmethod
    def _get_path(data: Any, path: str) -> Any:
        current = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @classmethod
    def _is_missing_path(cls, data: dict[str, Any], path: str) -> bool:
        return cls._is_missing_value(cls._get_path(data, path))

    @staticmethod
    def _is_missing_value(value: Any) -> bool:
        if value is None:
            return True
        if value in ("", "N/A", "None", "null"):
            return True
        if isinstance(value, (list, tuple, set, dict)) and len(value) == 0:
            return True
        return False

    @classmethod
    def _extract_quality_score(cls, agent_name: str, data: dict[str, Any]) -> float:
        if agent_name == AGENT_TECH:
            return cls._as_float(cls._get_path(data, "data_quality.score"), 0.0)
        if agent_name == AGENT_NEWS:
            return cls._as_float(cls._get_path(data, "_data_quality.score"), 0.0)
        if agent_name == AGENT_FUND:
            return cls._as_float(cls._get_path(data, "data_quality.overall_quality"), 0.0)
        if agent_name == AGENT_MACRO:
            return cls._parse_ratio(cls._get_path(data, "data_quality.overall_freshness"), 0.0)
        if agent_name == AGENT_INDUSTRY:
            return cls._as_float(cls._get_path(data, "data_quality.overall"), 0.0)
        return 0.0

    @classmethod
    def _extract_sources(cls, agent_name: str, data: dict[str, Any]) -> tuple[str, list[str]]:
        if agent_name == AGENT_NEWS:
            sources = [str(v) for v in data.get("sources_used", []) if v]
            return str(data.get("news_source") or ""), sources
        if agent_name == AGENT_MACRO:
            dq = data.get("data_quality", {}) or {}
            source = f"realtime={dq.get('realtime_count', 0)}, reference={dq.get('reference_count', 0)}"
            return source, []
        if agent_name == AGENT_TECH:
            freshness = data.get("freshness", {}) or {}
            source = freshness.get("source") or data.get("market") or ""
            return str(source), []
        return str(data.get("data_source") or ""), []

    @staticmethod
    def _status_from_quality(
        quality_score: float,
        missing: list[str],
        critical_missing: list[str],
        data: dict[str, Any],
    ) -> str:
        if critical_missing:
            return "poor"
        if quality_score < 0.3:
            return "poor"
        if quality_score < 0.5 or missing:
            return "partial"
        return "ok"

    @staticmethod
    def _quality_label(score: float) -> str:
        if score >= 0.75:
            return "good"
        if score >= 0.5:
            return "normal"
        if score >= 0.3:
            return "partial"
        return "poor"

    @staticmethod
    def _build_agent_summary(
        agent_name: str,
        data: dict[str, Any],
        missing: list[str],
        critical_missing: list[str],
    ) -> str:
        if agent_name == AGENT_NEWS:
            return f"news_count={data.get('news_count', 0)}, sources={data.get('sources_used', [])}"
        if agent_name == AGENT_MACRO:
            dq = data.get("data_quality", {}) or {}
            return (
                f"freshness={dq.get('overall_freshness', '?')}, "
                f"realtime={dq.get('realtime_count', 0)}, reference={dq.get('reference_count', 0)}"
            )
        if agent_name == AGENT_FUND:
            dq = data.get("data_quality", {}) or {}
            return (
                f"source={data.get('data_source', '?')}, "
                f"quality={dq.get('overall_quality', '?')}, gaps={len(dq.get('data_gaps', []) or [])}"
            )
        if agent_name == AGENT_INDUSTRY:
            dq = data.get("data_quality", {}) or {}
            return (
                f"industry={data.get('industry_name', '?')}, "
                f"source={data.get('data_source', '?')}, quality={dq.get('overall', '?')}"
            )
        if agent_name == AGENT_TECH:
            dq = data.get("data_quality", {}) or {}
            return (
                f"days={data.get('trading_days', '?')}, latest={data.get('latest_date', '?')}, "
                f"quality={dq.get('score', '?')}"
            )
        if critical_missing:
            return "关键字段缺失: " + ", ".join(critical_missing)
        if missing:
            return "字段缺失: " + ", ".join(missing[:5])
        return "数据可用"

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            if value in (None, "", "N/A"):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _parse_ratio(cls, value: Any, default: float = 0.0) -> float:
        if isinstance(value, str) and value.strip().endswith("%"):
            return cls._as_float(value.strip()[:-1], default * 100) / 100
        number = cls._as_float(value, default)
        return number / 100 if number > 1 else number

    @staticmethod
    def _count_statuses(checks: list[AgentDataSourceCheck]) -> dict[str, int]:
        counts = {"ok": 0, "partial": 0, "poor": 0, "failed": 0}
        for check in checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return counts

    @staticmethod
    def _overall_status(status_counts: dict[str, int]) -> str:
        if status_counts.get("failed", 0) or status_counts.get("poor", 0):
            return "poor"
        if status_counts.get("partial", 0):
            return "partial"
        return "ok"

    @classmethod
    def _build_summary(cls, audits: list[TargetDataSourceAudit]) -> dict[str, Any]:
        all_checks = [check for audit in audits for check in audit.checks]
        status_counts = cls._count_statuses(all_checks)
        by_agent: dict[str, dict[str, int]] = {}
        for check in all_checks:
            by_agent.setdefault(check.agent_name, {"ok": 0, "partial": 0, "poor": 0, "failed": 0})
            by_agent[check.agent_name][check.status] += 1
        avg_quality = (
            sum(check.quality_score for check in all_checks) / len(all_checks)
            if all_checks else 0.0
        )
        return {
            "target_count": len(audits),
            "check_count": len(all_checks),
            "status_counts": status_counts,
            "status_by_agent": by_agent,
            "average_quality": round(avg_quality, 3),
        }

    @staticmethod
    def _find_recurring_issues(audits: list[TargetDataSourceAudit]) -> list[dict[str, Any]]:
        issues: dict[tuple[str, str], dict[str, Any]] = {}
        for audit in audits:
            target_label = audit.target_name or audit.resolved_symbol
            for check in audit.checks:
                for field_name in check.missing_fields:
                    key = (check.agent_name, field_name)
                    item = issues.setdefault(
                        key,
                        {"agent": check.agent_name, "field": field_name, "count": 0, "targets": []},
                    )
                    item["count"] += 1
                    if target_label not in item["targets"]:
                        item["targets"].append(target_label)
        return sorted(issues.values(), key=lambda item: (-item["count"], item["agent"], item["field"]))

    @staticmethod
    def write_report(report: DataSourceAuditReport, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = output_dir / f"data_source_audit_{stamp}.json"
        md_path = output_dir / f"data_source_audit_{stamp}.md"
        json_path.write_text(report.to_json(), encoding="utf-8")
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        return json_path, md_path
