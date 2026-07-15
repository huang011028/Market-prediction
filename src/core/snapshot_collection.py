"""Batch collection for point-in-time agent snapshots.

This module intentionally separates raw evidence collection from LLM-backed
formal news analysis. The latter is useful, but it can spend API quota, so the
caller must opt in explicitly.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from config.settings import get_settings
from src.agents.news_analyst import NewsAnalyst
from src.core.llm_client import create_llm_client
from src.core.result import AnalysisResult, Direction
from src.data.fundamental_fetcher import FundamentalFetcher
from src.data.industry_fetcher import IndustryFetcher
from src.data.macro_fetcher import MacroFetcherV2
from src.data.news_fetcher import NewsFetcher
from src.data.news_snapshot_archive import NewsSnapshotArchive
from src.data.point_in_time_snapshot_archive import PointInTimeSnapshotArchive
from src.data.stock_context import get_stock_macro_context
from src.data.symbol_resolver import resolve_symbol


PIT_AGENT_ALIASES = {
    "fundamental": "公司前景分析师",
    "industry": "行业对比分析师",
    "macro": "国际形势分析师",
}

NEWS_AGENT = "最新新闻分析师"
NEWS_MODES = {"none", "raw", "evidence", "formal"}


@dataclass
class SnapshotCollectionConfig:
    """Configuration for collecting current point-in-time snapshots."""

    targets: list[str]
    timeframe: str = "短期(1周)"
    agents: list[str] = field(
        default_factory=lambda: ["fundamental", "industry", "macro", "news"]
    )
    news_mode: str = "evidence"
    as_of: Optional[str] = None
    output_dir: Optional[Path] = None
    write_default_archives: bool = False
    max_snapshots: int = 0
    news_max_items: int = 20


@dataclass
class SnapshotCollectionReport:
    """Result of one snapshot collection run."""

    generated_at: str
    root_dir: str
    point_in_time_root: str
    news_root: str
    targets: list[str]
    timeframe: str
    news_mode: str
    saved: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def saved_count(self) -> int:
        return len(self.saved)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["saved_count"] = self.saved_count
        return payload

    def to_markdown(self) -> str:
        lines = [
            "# 当前时点快照采集报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 输出根目录: `{self.root_dir}`",
            f"- point-in-time 目录: `{self.point_in_time_root}`",
            f"- 新闻目录: `{self.news_root}`",
            f"- 标的数: {len(self.targets)}",
            f"- 快照数: {self.saved_count}",
            f"- 错误数: {len(self.errors)}",
            f"- 新闻模式: `{self.news_mode}`",
            f"- 耗时: {self.elapsed_seconds:.2f}s",
            "",
            "## 已保存",
            "",
        ]
        if not self.saved:
            lines.append("暂无。")
        else:
            lines.extend([
                "| Agent | Target | Symbol | as_of | valid_date | path |",
                "| --- | --- | --- | --- | --- | --- |",
            ])
            for item in self.saved:
                lines.append(
                    "| {agent} | {target} | {symbol} | {as_of} | {valid} | `{path}` |".format(
                        agent=item.get("agent_name", ""),
                        target=item.get("target", ""),
                        symbol=item.get("symbol", ""),
                        as_of=item.get("as_of", ""),
                        valid=item.get("valid_date", ""),
                        path=item.get("path", ""),
                    )
                )

        if self.errors:
            lines.extend(["", "## 错误", ""])
            for item in self.errors:
                lines.append(
                    "- {target}/{agent}: {reason}".format(
                        target=item.get("target", ""),
                        agent=item.get("agent_name", ""),
                        reason=item.get("reason", ""),
                    )
                )
        return "\n".join(lines).rstrip() + "\n"


class CurrentSnapshotCollector:
    """Collect PIT snapshots for non-technical agents."""

    def __init__(
        self,
        fundamental_fetcher: Optional[FundamentalFetcher] = None,
        industry_fetcher: Optional[IndustryFetcher] = None,
        macro_fetcher: Optional[MacroFetcherV2] = None,
        news_fetcher: Optional[NewsFetcher] = None,
    ):
        self.fundamental_fetcher = fundamental_fetcher or FundamentalFetcher()
        self.industry_fetcher = industry_fetcher or IndustryFetcher()
        self.macro_fetcher = macro_fetcher or MacroFetcherV2()
        self.news_fetcher = news_fetcher

    async def collect(
        self,
        config: SnapshotCollectionConfig,
    ) -> SnapshotCollectionReport:
        started = time.monotonic()
        if config.as_of and str(config.as_of)[:10] != datetime.now().date().isoformat():
            raise ValueError("当前快照采集只能使用今天的 as_of；历史日期必须走经过验证的回放器")
        normalized_agents = self._normalize_agents(config.agents)
        if config.news_mode not in NEWS_MODES:
            raise ValueError(f"news_mode 必须是 {sorted(NEWS_MODES)}")

        root_dir, pit_root, news_root = self._resolve_roots(config)
        pit_archive = PointInTimeSnapshotArchive(root_dir=pit_root)
        news_archive = NewsSnapshotArchive(root_dir=news_root)
        generated_at = datetime.now().replace(microsecond=0).isoformat()

        saved: list[dict] = []
        errors: list[dict] = []

        for target in config.targets:
            for agent in normalized_agents:
                if config.max_snapshots and len(saved) >= config.max_snapshots:
                    break
                try:
                    if agent == "fundamental":
                        meta = await self._archive_fundamental(
                            pit_archive, target, config.timeframe, config.as_of,
                        )
                    elif agent == "industry":
                        meta = await self._archive_industry(
                            pit_archive, target, config.timeframe, config.as_of,
                        )
                    elif agent == "macro":
                        meta = await self._archive_macro(
                            pit_archive, target, config.timeframe, config.as_of,
                        )
                    elif agent == "news":
                        meta = await self._archive_news(
                            news_archive, target, config,
                        )
                    else:
                        continue
                    if meta:
                        saved.append(self._saved_record(meta, target, agent))
                except Exception as e:
                    errors.append({
                        "target": target,
                        "agent_name": agent,
                        "reason": str(e),
                    })
            if config.max_snapshots and len(saved) >= config.max_snapshots:
                break

        report = SnapshotCollectionReport(
            generated_at=generated_at,
            root_dir=str(root_dir),
            point_in_time_root=str(pit_root),
            news_root=str(news_root),
            targets=config.targets,
            timeframe=config.timeframe,
            news_mode=config.news_mode,
            saved=saved,
            errors=errors,
            elapsed_seconds=time.monotonic() - started,
        )
        self._write_report(report, root_dir)
        return report

    async def _archive_fundamental(
        self,
        archive: PointInTimeSnapshotArchive,
        target: str,
        timeframe: str,
        as_of: Optional[str],
    ) -> dict:
        info = resolve_symbol(target)
        data = await self.fundamental_fetcher.fetch_enhanced(info.symbol, info.market)
        data["_market"] = info.market
        data["_resolved_symbol"] = info.symbol
        data["_resolved_name"] = info.name
        return archive.save_snapshot(
            agent_name="公司前景分析师",
            target=target,
            symbol=info.symbol,
            name=info.name,
            market=info.market,
            timeframe=timeframe,
            data=data,
            as_of=as_of,
        )

    async def _archive_industry(
        self,
        archive: PointInTimeSnapshotArchive,
        target: str,
        timeframe: str,
        as_of: Optional[str],
    ) -> dict:
        info = resolve_symbol(target)
        data = await self.industry_fetcher.fetch_enhanced(info.symbol, info.market)
        data["_market"] = info.market
        data["_resolved_symbol"] = info.symbol
        data["_resolved_name"] = info.name
        return archive.save_snapshot(
            agent_name="行业对比分析师",
            target=target,
            symbol=info.symbol,
            name=info.name,
            market=info.market,
            timeframe=timeframe,
            data=data,
            as_of=as_of,
        )

    async def _archive_macro(
        self,
        archive: PointInTimeSnapshotArchive,
        target: str,
        timeframe: str,
        as_of: Optional[str],
    ) -> dict:
        info = resolve_symbol(target)
        macro_data = await self.macro_fetcher.fetch(info.symbol, info.market)
        data = macro_data.to_agent_dict()
        stock_ctx = get_stock_macro_context(info.symbol, info.market, info.name)
        data["_stock_context"] = stock_ctx
        data["_market"] = info.market
        data["_resolved_symbol"] = info.symbol
        data["_resolved_name"] = info.name
        return archive.save_snapshot(
            agent_name="国际形势分析师",
            target=target,
            symbol=info.symbol,
            name=info.name,
            market=info.market,
            timeframe=timeframe,
            data=data,
            stock_context=stock_ctx,
            as_of=as_of,
        )

    async def _archive_news(
        self,
        archive: NewsSnapshotArchive,
        target: str,
        config: SnapshotCollectionConfig,
    ) -> dict:
        if config.news_mode == "none":
            return {}
        if config.news_mode == "formal":
            llm = create_llm_client()
            result = await NewsAnalyst(llm, snapshot_archive=archive).run(
                target,
                config.timeframe,
            )
            return (result.data_summary or {}).get("news_snapshot") or {}

        info = resolve_symbol(target)
        fetcher = self.news_fetcher or NewsFetcher(max_items=config.news_max_items)
        days = NewsAnalyst.__new__(NewsAnalyst)._timeframe_to_days(config.timeframe)
        news_data = await fetcher.fetch(info.symbol, market=info.market, days=days)
        payload = news_data.to_agent_dict()
        payload["_market"] = info.market
        payload["_resolved_symbol"] = info.symbol
        payload["_resolved_name"] = info.name
        payload["_data_quality"] = self._assess_news_quality(news_data)

        result = None
        step_signals = {}
        if config.news_mode == "evidence":
            result = self._build_evidence_news_result(
                target=target,
                timeframe=config.timeframe,
                news_data=payload,
            )
            step_signals = {"mode": "evidence", "llm_used": False}

        return archive.save_analysis_snapshot(
            target=target,
            timeframe=config.timeframe,
            news_data=payload,
            result=result,
            step_signals=step_signals,
            as_of=config.as_of,
        )

    @staticmethod
    def _build_evidence_news_result(
        target: str,
        timeframe: str,
        news_data: dict,
    ) -> AnalysisResult:
        analyst = NewsAnalyst.__new__(NewsAnalyst)
        evidence = analyst._build_evidence_packet(news_data, timeframe)
        matrix = evidence.get("decision_matrix") or {}
        constraints = evidence.get("confidence_constraints") or {}
        direction_raw = matrix.get("suggested_direction") or "neutral"
        try:
            direction = Direction(direction_raw)
        except ValueError:
            direction = Direction.NEUTRAL
        confidence = constraints.get("max_confidence") or 0.35
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.35
        return AnalysisResult(
            agent_name=NEWS_AGENT,
            target=target,
            timeframe=timeframe,
            direction=direction,
            confidence=max(0.0, min(confidence, 1.0)),
            reasoning=matrix.get("reason", "基于新闻证据包生成的非 LLM 快照预测。"),
            key_factors=(evidence.get("evidence") or {}).get(direction.value, [])[:3],
            risks=(evidence.get("evidence") or {}).get("neutral", [])[:3],
            data_summary={
                "evidence": evidence,
                "collection_mode": "evidence_non_llm",
            },
            data_quality_score=float((news_data.get("_data_quality") or {}).get("score", 0.0) or 0.0),
        )

    @staticmethod
    def _assess_news_quality(news_data) -> dict:
        n = int(getattr(news_data, "news_count", 0) or 0)
        sources = list(getattr(news_data, "sources_used", []) or [])
        quality = 1.0
        if n == 0:
            quality = 0.1
        elif n <= 2:
            quality = 0.3
        elif n <= 5:
            quality = 0.6
        elif n <= 10:
            quality = 0.85
        if len(sources) >= 2:
            quality = min(1.0, quality + 0.1)
        if getattr(news_data, "news_source", "") == "unavailable":
            quality = 0.1
        return {
            "score": round(quality, 2),
            "news_count": n,
            "sources": sources,
            "is_available": getattr(news_data, "news_source", "") != "unavailable",
        }

    @staticmethod
    def _normalize_agents(agents: Iterable[str]) -> list[str]:
        normalized = []
        for agent in agents:
            raw = str(agent).strip()
            if not raw:
                continue
            key = raw.lower()
            if key in {"news", "最新新闻分析师"}:
                normalized.append("news")
            elif key in PIT_AGENT_ALIASES:
                normalized.append(key)
            elif raw in PIT_AGENT_ALIASES.values():
                reverse = {v: k for k, v in PIT_AGENT_ALIASES.items()}
                normalized.append(reverse[raw])
        return normalized

    @staticmethod
    def _resolve_roots(config: SnapshotCollectionConfig) -> tuple[Path, Path, Path]:
        settings = get_settings()
        if config.write_default_archives:
            root = settings.data_dir
            return (
                root,
                settings.data_dir / "point_in_time_snapshots",
                settings.data_dir / "news_snapshots",
            )

        if config.output_dir:
            root = Path(config.output_dir)
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            root = settings.output_dir / "snapshot_collection" / stamp
        return root, root / "point_in_time_snapshots", root / "news_snapshots"

    @staticmethod
    def _saved_record(meta: dict, target: str, agent: str) -> dict:
        payload = dict(meta)
        payload.setdefault("target", target)
        payload.setdefault("agent_name", PIT_AGENT_ALIASES.get(agent, NEWS_AGENT))
        if "symbol" not in payload:
            try:
                payload["symbol"] = resolve_symbol(target).symbol
            except Exception:
                payload["symbol"] = target
        return payload

    @staticmethod
    def _write_report(report: SnapshotCollectionReport, root_dir: Path) -> None:
        root_dir.mkdir(parents=True, exist_ok=True)
        (root_dir / "snapshot_collection_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root_dir / "snapshot_collection_report.md").write_text(
            report.to_markdown(),
            encoding="utf-8",
        )
