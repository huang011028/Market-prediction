"""
Agent 自我改进历史样本实验室。

这个模块让 Agent 改进工程师优先主动构造真实历史样本，而不是只等待
data/predictions.db 里积累线上预测。当前可直接回放技术面历史 K 线；
新闻面需要已归档的新闻快照；基本面、行业、宏观需要后续补齐
point-in-time 历史快照源后才能进入同一闭环。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.agents.improvement_engineer import (
    AgentImprovementEngineer,
    ImprovementEngineerConfig,
)
from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    CalibrationBootstrapReport,
    FundamentalSnapshotCalibrationBootstrapper,
    IndustrySnapshotCalibrationBootstrapper,
    MacroSnapshotCalibrationBootstrapper,
    NewsSnapshotCalibrationBootstrapper,
    TechnicalCalibrationBootstrapper,
)
from src.core.historical_evaluator import HistoricalAgentEvaluator
from src.data.news_snapshot_archive import NewsSnapshotArchive
from src.data.point_in_time_snapshot_archive import PointInTimeSnapshotArchive


@dataclass
class SelfImprovementLabConfig:
    """主动历史样本池配置。"""

    targets: list[str]
    start_date: str
    end_date: str
    timeframe: str = "短期(1周)"
    interval_days: int = 14
    lookback_days: int = 180
    tolerance_days: int = 10
    evaluation_min_samples: int = 5
    run_engineer: bool = False
    engineer_min_samples: int = 20
    engineer_min_unique_cases: int = 5
    dry_run: bool = True
    allow_prompt_apply: bool = True
    allow_skill_apply: bool = True
    output_dir: Optional[Path] = None
    news_snapshots_path: Optional[Path] = None
    point_in_time_snapshots_path: Optional[Path] = None
    use_default_archives: bool = True


@dataclass
class SelfImprovementLabReport:
    """主动历史样本池执行报告。"""

    generated_at: str
    output_dir: str
    targets: list[str]
    timeframe: str
    date_range: dict
    supported_agents: list[str]
    deferred_agents: list[dict]
    bootstrap_paths: dict[str, str] = field(default_factory=dict)
    evaluation_paths: dict[str, str] = field(default_factory=dict)
    engineer_paths: dict[str, str] = field(default_factory=dict)
    total_samples: int = 0
    evaluation_summary: dict = field(default_factory=dict)
    engineer_summary: dict = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "output_dir": self.output_dir,
            "targets": self.targets,
            "timeframe": self.timeframe,
            "date_range": self.date_range,
            "supported_agents": self.supported_agents,
            "deferred_agents": self.deferred_agents,
            "bootstrap_paths": self.bootstrap_paths,
            "evaluation_paths": self.evaluation_paths,
            "engineer_paths": self.engineer_paths,
            "total_samples": self.total_samples,
            "evaluation_summary": self.evaluation_summary,
            "engineer_summary": self.engineer_summary,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }

    def to_markdown(self) -> str:
        lines = [
            "# Agent 自我改进历史样本实验室报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 输出目录: {self.output_dir}",
            f"- 标的: {', '.join(self.targets)}",
            f"- 区间: {self.date_range.get('start')} 到 {self.date_range.get('end')}",
            f"- 周期: {self.timeframe}",
            f"- Agent 样本数: {self.total_samples}",
            f"- 覆盖 Agent: {', '.join(self.supported_agents) or '无'}",
            "",
            "## 输出文件",
            "",
        ]
        for label, path in {
            **self.bootstrap_paths,
            **self.evaluation_paths,
            **self.engineer_paths,
        }.items():
            lines.append(f"- {label}: `{path}`")

        lines.extend(["", "## 评估摘要", ""])
        if self.evaluation_summary:
            lines.extend([
                f"- 错误策略信号: {self.evaluation_summary.get('wrong_strategy_signals', 0)}",
                f"- 可保留优势信号: {self.evaluation_summary.get('strength_signals', 0)}",
            ])
        else:
            lines.append("暂无评估摘要。")

        if self.engineer_summary:
            lines.extend([
                "",
                "## 改进工程师摘要",
                "",
                f"- 动作数: {self.engineer_summary.get('actions', 0)}",
                f"- 已应用/演练: {self.engineer_summary.get('applied', 0)}",
                f"- 需人工确认: {self.engineer_summary.get('protected', 0)}",
            ])

        if self.deferred_agents:
            lines.extend(["", "## 尚未纳入主动回测的 Agent", ""])
            for item in self.deferred_agents:
                lines.append(
                    "- {agent}: {reason}".format(
                        agent=item.get("agent_name"),
                        reason=item.get("reason"),
                    )
                )

        return "\n".join(lines).rstrip() + "\n"


class SelfImprovementLab:
    """主动构造真实历史样本并驱动 Agent 改进工程师。"""

    def __init__(
        self,
        technical_bootstrapper: Optional[TechnicalCalibrationBootstrapper] = None,
        news_bootstrapper: Optional[NewsSnapshotCalibrationBootstrapper] = None,
        fundamental_bootstrapper: Optional[FundamentalSnapshotCalibrationBootstrapper] = None,
        industry_bootstrapper: Optional[IndustrySnapshotCalibrationBootstrapper] = None,
        macro_bootstrapper: Optional[MacroSnapshotCalibrationBootstrapper] = None,
        evaluator: Optional[HistoricalAgentEvaluator] = None,
        engineer: Optional[AgentImprovementEngineer] = None,
    ):
        self.technical_bootstrapper = technical_bootstrapper or TechnicalCalibrationBootstrapper()
        self.news_bootstrapper = news_bootstrapper or NewsSnapshotCalibrationBootstrapper()
        self.fundamental_bootstrapper = (
            fundamental_bootstrapper or FundamentalSnapshotCalibrationBootstrapper()
        )
        self.industry_bootstrapper = industry_bootstrapper or IndustrySnapshotCalibrationBootstrapper()
        self.macro_bootstrapper = macro_bootstrapper or MacroSnapshotCalibrationBootstrapper()
        self.evaluator = evaluator or HistoricalAgentEvaluator()
        self.engineer = engineer or AgentImprovementEngineer()

    async def run(self, config: SelfImprovementLabConfig) -> SelfImprovementLabReport:
        started = time.monotonic()
        output_dir = self._resolve_output_dir(config)
        output_dir.mkdir(parents=True, exist_ok=True)

        bootstrap_reports: list[dict] = []
        bootstrap_paths: dict[str, str] = {}
        supported_agents = ["近期股价分析师"]

        technical_report = await self.technical_bootstrapper.run(
            CalibrationBootstrapConfig(
                targets=config.targets,
                start_date=config.start_date,
                end_date=config.end_date,
                timeframe=config.timeframe,
                interval_days=config.interval_days,
                lookback_days=config.lookback_days,
                tolerance_days=config.tolerance_days,
            )
        )
        technical_path = output_dir / "technical_bootstrap_report.json"
        self._write_bootstrap_report(technical_report, technical_path)
        bootstrap_reports.append(technical_report.to_dict())
        bootstrap_paths["technical"] = str(technical_path)

        news_snapshots = self._load_news_snapshots(config)
        if news_snapshots:
            news_report = await self.news_bootstrapper.run_from_snapshots(
                news_snapshots,
                timeframe=config.timeframe,
                tolerance_days=config.tolerance_days,
            )
            news_path = output_dir / "news_snapshot_bootstrap_report.json"
            self._write_bootstrap_report(news_report, news_path)
            bootstrap_reports.append(news_report.to_dict())
            bootstrap_paths["news"] = str(news_path)
            if news_report.success_samples > 0:
                supported_agents.append("最新新闻分析师")

        pit_reports = await self._run_point_in_time_bootstrappers(config, output_dir)
        for label, path, report in pit_reports:
            bootstrap_reports.append(report.to_dict())
            bootstrap_paths[label] = str(path)
            if report.success_samples > 0:
                supported_agents.append(report.agent_name)

        combined_path = output_dir / "bootstrap_reports.json"
        combined_payload = {"reports": bootstrap_reports}
        combined_path.write_text(
            json.dumps(combined_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        bootstrap_paths["combined"] = str(combined_path)

        samples = self.evaluator.samples_from_bootstrap_report(combined_payload)
        evaluation = self.evaluator.evaluate(
            samples,
            min_samples=config.evaluation_min_samples,
        )
        evaluation_paths = self.evaluator.write_report(
            evaluation,
            output_dir / "historical_agent_evaluation.json",
        )
        evaluation_dict = evaluation.to_dict()

        engineer_paths: dict[str, str] = {}
        engineer_summary: dict = {}
        if config.run_engineer:
            engineer_output_dir = output_dir / "agent_improvement_engineer"
            engineer_report = await self.engineer.run(
                evaluation_dict,
                config=ImprovementEngineerConfig(
                    project_root=Path(__file__).resolve().parents[2],
                    output_dir=engineer_output_dir,
                    min_samples_for_auto_apply=config.engineer_min_samples,
                    min_unique_cases_for_auto_apply=config.engineer_min_unique_cases,
                    dry_run=config.dry_run,
                    allow_prompt_apply=config.allow_prompt_apply,
                    allow_declarative_skill_apply=config.allow_skill_apply,
                ),
                source_report_path=evaluation_paths["json"],
            )
            engineer_paths = {
                "json": str(engineer_output_dir / "agent_improvement_engineer_report.json"),
                "markdown": str(engineer_output_dir / "agent_improvement_engineer_report.md"),
            }
            engineer_summary = {
                "actions": len(engineer_report.actions),
                "applied": len(engineer_report.applied_paths),
                "protected": len(engineer_report.protected_recommendations),
            }

        report = SelfImprovementLabReport(
            generated_at=datetime.now().isoformat(),
            output_dir=str(output_dir),
            targets=config.targets,
            timeframe=config.timeframe,
            date_range={"start": config.start_date, "end": config.end_date},
            supported_agents=supported_agents,
            deferred_agents=self._deferred_agents(config, supported_agents),
            bootstrap_paths=bootstrap_paths,
            evaluation_paths=evaluation_paths,
            engineer_paths=engineer_paths,
            total_samples=len(samples),
            evaluation_summary={
                "wrong_strategy_signals": len(evaluation.wrong_strategy_signals),
                "strength_signals": len(evaluation.strength_signals),
                "agents": list(evaluation.agents.keys()),
            },
            engineer_summary=engineer_summary,
            elapsed_seconds=time.monotonic() - started,
        )
        report_json = output_dir / "self_improvement_lab_report.json"
        report_md = output_dir / "self_improvement_lab_report.md"
        report_json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report_md.write_text(report.to_markdown(), encoding="utf-8")
        return report

    @staticmethod
    def _resolve_output_dir(config: SelfImprovementLabConfig) -> Path:
        if config.output_dir:
            return Path(config.output_dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        project_root = Path(__file__).resolve().parents[2]
        return project_root / "output" / "self_improvement_lab" / stamp

    @staticmethod
    def _write_bootstrap_report(report: CalibrationBootstrapReport, path: Path) -> None:
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _load_news_snapshots(config: SelfImprovementLabConfig) -> list[dict]:
        if config.news_snapshots_path:
            return NewsSnapshotArchive().load_snapshots(
                config.news_snapshots_path,
                start_date=config.start_date,
                end_date=config.end_date,
            )
        if config.use_default_archives:
            return NewsSnapshotArchive().load_snapshots(
                start_date=config.start_date,
                end_date=config.end_date,
            )
        return []

    async def _run_point_in_time_bootstrappers(
        self,
        config: SelfImprovementLabConfig,
        output_dir: Path,
    ) -> list[tuple[str, Path, CalibrationBootstrapReport]]:
        source = config.point_in_time_snapshots_path
        archive = PointInTimeSnapshotArchive(source) if source else PointInTimeSnapshotArchive()
        if source and not Path(source).exists():
            return []
        if not source and not config.use_default_archives:
            return []

        specs = [
            ("fundamental", "公司前景分析师", self.fundamental_bootstrapper),
            ("industry", "行业对比分析师", self.industry_bootstrapper),
            ("macro", "国际形势分析师", self.macro_bootstrapper),
        ]
        reports: list[tuple[str, Path, CalibrationBootstrapReport]] = []
        target_set = {target.upper() for target in config.targets}
        for label, agent_name, bootstrapper in specs:
            snapshots = archive.load_snapshots(
                source,
                agent_name=agent_name,
                start_date=config.start_date,
                end_date=config.end_date,
            )
            snapshots = [
                snapshot for snapshot in snapshots
                if (
                    str(snapshot.get("target") or "").upper() in target_set
                    or str(snapshot.get("symbol") or "").upper() in target_set
                    or str((snapshot.get("data") or {}).get("symbol") or "").upper() in target_set
                    or str((snapshot.get("data") or {}).get("_resolved_symbol") or "").upper()
                    in target_set
                )
            ]
            if not snapshots:
                continue
            report = await bootstrapper.run_from_snapshots(
                snapshots,
                timeframe=config.timeframe,
                tolerance_days=config.tolerance_days,
            )
            path = output_dir / f"{label}_pit_bootstrap_report.json"
            self._write_bootstrap_report(report, path)
            reports.append((label, path, report))
        return reports

    @staticmethod
    def _load_json_snapshots(path: Path) -> list[dict]:
        path = Path(path)
        if path.suffix == ".jsonl":
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ("snapshots", "items", "data"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            return [payload]
        raise ValueError("新闻快照文件必须是 JSON 数组、JSONL，或包含 snapshots/items/data 的 JSON 对象")

    @staticmethod
    def _deferred_agents(config: SelfImprovementLabConfig, supported_agents: list[str]) -> list[dict]:
        deferred = []
        supported = set(supported_agents)
        if "最新新闻分析师" not in supported:
            deferred.append({
                "agent_name": "最新新闻分析师",
                "reason": "若默认新闻快照库在该区间没有样本，需要先提供新闻快照归档，才能回放新闻面。",
            })
        missing_pit = [
            (
                "公司前景分析师",
                "若默认 point-in-time 快照库在该区间没有样本，需要先归档财报、估值和经营指标快照。",
            ),
            (
                "行业对比分析师",
                "若默认 point-in-time 快照库在该区间没有样本，需要先归档行业成分股、估值排名和行业均值快照。",
            ),
            (
                "国际形势分析师",
                "若默认 point-in-time 快照库在该区间没有样本，需要先归档宏观指标、预期值、实际值和市场环境快照。",
            ),
        ]
        for agent_name, reason in missing_pit:
            if agent_name not in supported:
                deferred.append({"agent_name": agent_name, "reason": reason})
        return deferred
