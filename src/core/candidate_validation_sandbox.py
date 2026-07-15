"""
候选 Prompt/Skill 自动验证沙箱。

Agent 改进工程师先把 LLM 或统计规则生成的候选改法写入隔离目录，
再用独立 holdout 回放验证。只有通过门禁的候选才允许晋升到正式
prompt guardrail 或 Agent Skill Registry。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.agents.improvement_engineer import AgentImprovementEngineer
from src.core.calibration_bootstrap import (
    CalibrationBootstrapConfig,
    TechnicalCalibrationBootstrapper,
)
from src.core.improvement_patch_planner import (
    AgentImprovementPatchPlanner,
    ImprovementPatchDraft,
)
from src.core.llm_client import LLMClient
from src.core.technical_improvement_validation import (
    TechnicalConfidencePolicyValidator,
    TechnicalImprovementHoldoutValidator,
)
from src.core.technical_prompt_replay import (
    TechnicalPromptReplayConfig,
    TechnicalPromptReplayHarness,
)
from src.prompts.dynamic_overrides import agent_slug


@dataclass
class CandidateSandboxConfig:
    """候选验证沙箱配置。"""

    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    output_dir: Optional[Path] = None
    candidate_root: Optional[Path] = None
    candidate_id: Optional[str] = None
    min_samples: int = 20
    min_unique_cases: int = 5
    use_llm_candidates: bool = False
    apply_if_passed: bool = False
    allow_prompt_promotion: bool = True
    allow_skill_promotion: bool = True
    validate_technical: bool = True
    holdout_targets: list[str] = field(default_factory=lambda: [
        "600276",
        "601012",
        "000858",
        "002594",
        "600030",
        "601888",
    ])
    holdout_start_date: str = "2025-07-01"
    holdout_end_date: str = "2025-12-31"
    holdout_timeframe: str = "短期(1周)"
    holdout_interval_days: int = 14
    holdout_lookback_days: int = 180
    holdout_tolerance_days: int = 10
    min_accuracy_delta: float = 0.01
    min_holdout_samples: int = 20
    min_changed_predictions: int = 1
    confidence_cap: float = 0.35
    min_brier_delta: float = 0.005
    min_confidence_changed: int = 3
    min_confidence_matched: int = 3
    run_technical_prompt_replay: bool = False
    prompt_replay_max_samples: int = 60
    prompt_replay_min_samples: int = 30
    prompt_replay_min_accuracy_delta: float = 0.01
    prompt_replay_min_brier_delta: float = 0.0
    prompt_replay_min_changed_predictions: int = 1
    prompt_replay_overconfidence_threshold: float = 0.60
    prompt_replay_max_overconfidence_delta: float = 0.02
    candidate_batch_count: int = 1


@dataclass
class CandidateArtifact:
    """一条隔离候选 prompt/skill。"""

    candidate_id: str
    artifact_id: str
    agent_name: str
    area: str
    title: str
    status: str
    reason: str
    content_path: str
    metadata_path: str
    sample_size: int = 0
    unique_cases: int = 0
    accuracy: Optional[float] = None
    source_signals: list[dict] = field(default_factory=list)
    validation: dict = field(default_factory=dict)
    promotion_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CandidateSandboxReport:
    """候选验证沙箱执行报告。"""

    generated_at: str
    candidate_id: str
    output_dir: str
    candidate_root: str
    source_report_path: Optional[str]
    summary: dict
    artifacts: list[CandidateArtifact] = field(default_factory=list)
    validation_reports: dict = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
    promoted_paths: list[str] = field(default_factory=list)
    registry_skill_ids: list[str] = field(default_factory=list)
    protected_recommendations: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    status: str = "generated"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        return payload

    def to_markdown(self) -> str:
        lines = [
            "# LLM 候选 Prompt/Skill 自动验证沙箱报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 候选批次: `{self.candidate_id}`",
            f"- 候选目录: `{self.candidate_root}`",
            f"- 来源报告: `{self.source_report_path or '内存输入'}`",
            f"- 状态: `{self.status}`",
            f"- 耗时: {self.elapsed_seconds:.2f}s",
            "",
            "## 摘要",
            "",
            f"- 候选数: {self.summary.get('artifacts', 0)}",
            f"- 验证通过: {self.summary.get('validated_passed', 0)}",
            f"- 验证失败: {self.summary.get('validated_failed', 0)}",
            f"- 未验证草案: {self.summary.get('draft_unvalidated', 0)}",
            f"- 晋升文件: {self.summary.get('promoted_paths', 0)}",
            f"- Registry Skill: {self.summary.get('registry_skills', 0)}",
            "",
            "## 候选改法",
            "",
        ]
        if not self.artifacts:
            lines.append("暂无达到阈值的候选 prompt/skill。")
        else:
            lines.extend([
                "| Agent | 面 | 标题 | 状态 | 样本 | 独立案例 | 路径 |",
                "| --- | --- | --- | --- | ---: | ---: | --- |",
            ])
            for artifact in self.artifacts:
                lines.append(
                    "| {agent} | {area} | {title} | {status} | {samples} | {cases} | `{path}` |".format(
                        agent=artifact.agent_name,
                        area=artifact.area,
                        title=artifact.title,
                        status=artifact.status,
                        samples=artifact.sample_size,
                        cases=artifact.unique_cases,
                        path=artifact.content_path,
                    )
                )

        if self.decisions:
            lines.extend(["", "## 验证门禁", ""])
            for decision in self.decisions:
                lines.append(
                    "- {name}: {result}，{reason}".format(
                        name=decision.get("name"),
                        result="通过" if decision.get("should_apply") else "未通过",
                        reason=decision.get("reason", ""),
                    )
                )

        if self.protected_recommendations:
            lines.extend(["", "## 仍需人工确认", ""])
            for item in self.protected_recommendations:
                lines.append(
                    "- `{agent}/{area}` {title}: {reason}".format(
                        agent=item.get("agent_name"),
                        area=item.get("area"),
                        title=item.get("title"),
                        reason=item.get("reason"),
                    )
                )
        return "\n".join(lines).rstrip() + "\n"


class CandidateValidationSandbox:
    """生成、隔离、验证并可选晋升候选 prompt/skill。"""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        planner: Optional[AgentImprovementPatchPlanner] = None,
        direction_validator: Optional[TechnicalImprovementHoldoutValidator] = None,
        confidence_validator: Optional[TechnicalConfidencePolicyValidator] = None,
        prompt_replay_harness: Optional[TechnicalPromptReplayHarness] = None,
    ):
        self.llm = llm
        self.planner = planner or AgentImprovementPatchPlanner()
        self.direction_validator = direction_validator or TechnicalImprovementHoldoutValidator()
        self.confidence_validator = confidence_validator or TechnicalConfidencePolicyValidator()
        self.prompt_replay_harness = prompt_replay_harness

    async def run(
        self,
        evaluation_report: dict,
        config: Optional[CandidateSandboxConfig] = None,
        source_report_path: Optional[str] = None,
    ) -> CandidateSandboxReport:
        started = time.monotonic()
        config = config or CandidateSandboxConfig()
        candidate_id = config.candidate_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = self._resolve_output_dir(config, candidate_id)
        candidate_root = self._resolve_candidate_root(config, candidate_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        candidate_root.mkdir(parents=True, exist_ok=True)

        artifacts, protected = await self._write_candidate_artifacts(
            evaluation_report=evaluation_report,
            config=config,
            candidate_id=candidate_id,
            candidate_root=candidate_root,
        )

        validation_reports: dict = {}
        decisions: list[dict] = []
        registry_skill_ids: list[str] = []
        promoted_paths: list[str] = []

        if config.validate_technical:
            validation_payload = await self._validate_technical_candidates(
                evaluation_report=evaluation_report,
                config=config,
                candidate_root=candidate_root,
                artifacts=artifacts,
                source_report_path=source_report_path,
            )
            validation_reports.update(validation_payload["validation_reports"])
            decisions.extend(validation_payload["decisions"])
            self._attach_validation_to_artifacts(artifacts, validation_payload["decisions"])

            if config.apply_if_passed and config.allow_skill_promotion:
                registry_skill_ids.extend(
                    self._write_registry_skills(
                        validation_payload["reports"],
                        validation_payload["validation_reports"],
                    )
                )
            if config.apply_if_passed:
                promoted_paths.extend(
                    self._promote_passed_artifacts(
                        artifacts,
                        config=config,
                    )
                )

        status = self._report_status(artifacts, promoted_paths, decisions)
        summary = self._summary(artifacts, promoted_paths, registry_skill_ids)
        report = CandidateSandboxReport(
            generated_at=datetime.now().isoformat(),
            candidate_id=candidate_id,
            output_dir=str(output_dir),
            candidate_root=str(candidate_root),
            source_report_path=source_report_path,
            summary=summary,
            artifacts=artifacts,
            validation_reports=validation_reports,
            decisions=decisions,
            promoted_paths=promoted_paths,
            registry_skill_ids=registry_skill_ids,
            protected_recommendations=protected,
            elapsed_seconds=time.monotonic() - started,
            status=status,
        )
        (output_dir / "candidate_sandbox_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / "candidate_sandbox_report.md").write_text(
            report.to_markdown(),
            encoding="utf-8",
        )
        return report

    async def _write_candidate_artifacts(
        self,
        evaluation_report: dict,
        config: CandidateSandboxConfig,
        candidate_id: str,
        candidate_root: Path,
    ) -> tuple[list[CandidateArtifact], list[dict]]:
        raw_signals = list(
            evaluation_report.get("improvement_signals")
            or evaluation_report.get("wrong_strategy_signals")
            or []
        )
        signals = [
            signal for signal in raw_signals
            if signal.get("signal_type", "wrong_strategy") == "wrong_strategy"
        ]
        signals_by_agent: dict[str, list[dict]] = {}
        for signal in signals:
            agent_name = signal.get("agent_name") or "unknown"
            signals_by_agent.setdefault(agent_name, []).append(signal)
        agent_batches = self._candidate_signal_batches(
            evaluation_report,
            signals_by_agent,
            max(1, int(config.candidate_batch_count or 1)),
        )

        artifacts: list[CandidateArtifact] = []
        protected: list[dict] = []
        for agent_name, batches in sorted(agent_batches.items()):
            multi_batch = len(batches) > 1
            for batch_label, agent_signals in batches:
                plan = self.planner.build_plan(agent_name, agent_signals)
                for draft in plan.drafts:
                    effective_draft = draft
                    if multi_batch:
                        effective_draft = replace(
                            draft,
                            title=f"{draft.title} {batch_label}",
                            rationale=f"{draft.rationale}（来源: {batch_label}）",
                        )
                    artifact = await self._write_one_candidate_artifact(
                        agent_name=agent_name,
                        draft=effective_draft,
                        config=config,
                        candidate_id=candidate_id,
                        candidate_root=candidate_root,
                    )
                    if artifact is None:
                        continue
                    if isinstance(artifact, CandidateArtifact):
                        artifacts.append(artifact)
                    else:
                        protected.append(artifact)
        return artifacts, protected

    async def _write_one_candidate_artifact(
        self,
        agent_name: str,
        draft: ImprovementPatchDraft,
        config: CandidateSandboxConfig,
        candidate_id: str,
        candidate_root: Path,
    ) -> CandidateArtifact | dict | None:
        sample_size, unique_cases, accuracy = self._draft_stats(draft)
        if sample_size < config.min_samples or unique_cases < config.min_unique_cases:
            return None
        if draft.area in AgentImprovementEngineer.PROTECTED_AREAS:
            return {
                "agent_name": agent_name,
                "area": draft.area,
                "title": draft.title,
                "reason": "核心代码、MCP、数据源或校准器修改需要人工确认",
                "sample_size": sample_size,
                "unique_cases": unique_cases,
            }
        if draft.area not in AgentImprovementEngineer.AUTO_AREAS:
            return None

        content = await self._render_candidate_content(agent_name, draft, config)
        content_path = self._candidate_content_path(candidate_root, agent_name, draft)
        metadata_path = (
            candidate_root
            / "artifacts"
            / f"{agent_slug(agent_name)}_{draft.area}_{self._safe_slug(draft.title)}.json"
        )
        content_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if draft.area == "prompt" and content_path.exists():
            existing = content_path.read_text(encoding="utf-8").rstrip()
            content = f"{existing}\n\n---\n\n{content}"
        content_path.write_text(content, encoding="utf-8")

        artifact = CandidateArtifact(
            candidate_id=candidate_id,
            artifact_id=f"{agent_slug(agent_name)}_{draft.area}_{self._safe_slug(draft.title)}",
            agent_name=agent_name,
            area=draft.area,
            title=draft.title,
            status="draft_unvalidated",
            reason="已写入候选沙箱，尚未通过独立 holdout",
            content_path=str(content_path),
            metadata_path=str(metadata_path),
            sample_size=sample_size,
            unique_cases=unique_cases,
            accuracy=accuracy,
            source_signals=draft.source_signals,
        )
        metadata_path.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return artifact

    @staticmethod
    def _candidate_signal_batches(
        evaluation_report: dict,
        signals_by_agent: dict[str, list[dict]],
        batch_count: int,
    ) -> dict[str, list[tuple[str, list[dict]]]]:
        explicit_batches = evaluation_report.get("candidate_batches") or []
        if explicit_batches:
            by_agent: dict[str, list[tuple[str, list[dict]]]] = {}
            for idx, batch in enumerate(explicit_batches, start=1):
                batch_signals = list(
                    batch.get("improvement_signals")
                    or batch.get("wrong_strategy_signals")
                    or []
                )
                label = str(batch.get("batch_id") or f"batch_{idx:02d}")
                grouped: dict[str, list[dict]] = {}
                for signal in batch_signals:
                    if signal.get("signal_type", "wrong_strategy") != "wrong_strategy":
                        continue
                    grouped.setdefault(signal.get("agent_name") or "unknown", []).append(signal)
                for agent_name, signals in grouped.items():
                    if signals:
                        by_agent.setdefault(agent_name, []).append((label, signals))
            if by_agent:
                return by_agent

        if batch_count <= 1:
            return {
                agent_name: [("batch_01", signals)]
                for agent_name, signals in signals_by_agent.items()
                if signals
            }

        by_agent = {}
        for agent_name, signals in signals_by_agent.items():
            buckets = [[] for _ in range(min(batch_count, max(1, len(signals))))]
            for idx, signal in enumerate(signals):
                buckets[idx % len(buckets)].append(signal)
            by_agent[agent_name] = [
                (f"batch_{idx:02d}", batch)
                for idx, batch in enumerate(buckets, start=1)
                if batch
            ]
        return by_agent

    async def _render_candidate_content(
        self,
        agent_name: str,
        draft: ImprovementPatchDraft,
        config: CandidateSandboxConfig,
    ) -> str:
        fallback = (
            AgentImprovementEngineer._render_prompt_guardrail(agent_name, draft)
            if draft.area == "prompt"
            else AgentImprovementEngineer._render_declarative_skill(agent_name, draft)
        )
        if not config.use_llm_candidates or not self.llm:
            return fallback
        try:
            response = await self.llm.achat(
                system_prompt=(
                    "你是 Agent 改进工程师。请基于历史失败统计生成一份候选 prompt guardrail "
                    "或声明式 skill。只返回 Markdown，不要修改源码，不要提出 MCP 或数据源接入代码。"
                ),
                user_prompt=json.dumps({
                    "agent_name": agent_name,
                    "area": draft.area,
                    "title": draft.title,
                    "rationale": draft.rationale,
                    "proposed_patch": draft.proposed_patch,
                    "source_signals": draft.source_signals[:12],
                    "fallback_draft": fallback,
                    "constraints": [
                        "候选必须可由历史样本验证",
                        "必须写明适用场景、反例检查、置信度约束",
                        "不能声称已经通过 holdout，验证结果由系统生成",
                    ],
                }, ensure_ascii=False, indent=2)[:16000],
                temperature=0.2,
            )
            content = (response.content or "").strip()
            return content if len(content) >= 40 else fallback
        except Exception:
            return fallback

    async def _validate_technical_candidates(
        self,
        evaluation_report: dict,
        config: CandidateSandboxConfig,
        candidate_root: Path,
        artifacts: list[CandidateArtifact],
        source_report_path: Optional[str],
    ) -> dict:
        validation_root = candidate_root / "validation"
        reports: dict[str, object] = {}
        validation_reports: dict = {}
        decisions: list[dict] = []
        technical_artifacts = [
            artifact for artifact in artifacts
            if artifact.agent_name == TechnicalCalibrationBootstrapper.AGENT_NAME
        ]

        if config.run_technical_prompt_replay:
            if not technical_artifacts:
                decisions.append({
                    "name": "technical_prompt_replay",
                    "should_apply": False,
                    "reason": "没有技术面候选 prompt/skill，无法运行 LLM prompt replay",
                    "metrics": {"holdout_samples": 0},
                })
            elif not self.llm and not self.prompt_replay_harness:
                decisions.append({
                    "name": "technical_prompt_replay",
                    "should_apply": False,
                    "reason": "LLM 未初始化，无法运行技术面 LLM prompt replay",
                    "metrics": {"holdout_samples": 0},
                })
            else:
                harness = self.prompt_replay_harness or TechnicalPromptReplayHarness(self.llm)
                replay_report = await harness.run(
                    TechnicalPromptReplayConfig(
                        targets=config.holdout_targets,
                        start_date=config.holdout_start_date,
                        end_date=config.holdout_end_date,
                        timeframe=config.holdout_timeframe,
                        interval_days=config.holdout_interval_days,
                        lookback_days=config.holdout_lookback_days,
                        tolerance_days=config.holdout_tolerance_days,
                        candidate_root=candidate_root,
                        max_samples=config.prompt_replay_max_samples,
                        min_samples=config.prompt_replay_min_samples,
                        min_accuracy_delta=config.prompt_replay_min_accuracy_delta,
                        min_brier_delta=config.prompt_replay_min_brier_delta,
                        min_changed_predictions=config.prompt_replay_min_changed_predictions,
                        overconfidence_threshold=config.prompt_replay_overconfidence_threshold,
                        max_overconfidence_delta=config.prompt_replay_max_overconfidence_delta,
                    ),
                    output_dir=validation_root / "technical_prompt_replay",
                )
                reports["technical_prompt_replay"] = replay_report
                validation_reports["technical_prompt_replay"] = {
                    "json": str(validation_root / "technical_prompt_replay" / "technical_prompt_replay.json"),
                    "markdown": str(validation_root / "technical_prompt_replay" / "technical_prompt_replay.md"),
                }
                decisions.append({
                    "name": "technical_prompt_replay",
                    "should_apply": replay_report.decision.should_apply,
                    "reason": replay_report.decision.reason,
                    "metrics": replay_report.decision.to_dict(),
                    "samples": len(replay_report.samples),
                })

        direction_rules = self.direction_validator.build_rules(
            evaluation_report,
            min_samples=config.min_samples,
            min_unique_cases=config.min_unique_cases,
        )
        confidence_rules = self.confidence_validator.build_confidence_rules(
            evaluation_report,
            min_samples=config.min_samples,
            min_unique_cases=config.min_unique_cases,
        )
        if not direction_rules and not confidence_rules:
            decisions.extend([
                {
                    "name": "technical_direction",
                    "should_apply": False,
                    "reason": "没有达到阈值的可执行技术方向候选规则",
                    "metrics": {"rules": 0},
                    "rules": [],
                },
                {
                    "name": "technical_confidence",
                    "should_apply": False,
                    "reason": "没有达到阈值的可执行技术置信度候选规则",
                    "metrics": {"rules": 0},
                    "rules": [],
                },
            ])
            return {
                "reports": reports,
                "validation_reports": validation_reports,
                "decisions": decisions,
            }

        if not config.holdout_targets:
            decisions.append({
                "name": "technical_direction",
                "should_apply": False,
                "reason": "未配置 holdout 标的，无法做独立验证",
            })
            return {
                "reports": reports,
                "validation_reports": validation_reports,
                "decisions": decisions,
            }

        holdout_config = CalibrationBootstrapConfig(
            targets=config.holdout_targets,
            start_date=config.holdout_start_date,
            end_date=config.holdout_end_date,
            timeframe=config.holdout_timeframe,
            interval_days=config.holdout_interval_days,
            lookback_days=config.holdout_lookback_days,
            tolerance_days=config.holdout_tolerance_days,
        )
        training_path = source_report_path or "memory_input"

        if direction_rules:
            direction_report = await self.direction_validator.run(
                evaluation_report=evaluation_report,
                training_report_path=training_path,
                holdout_config=holdout_config,
                output_dir=validation_root / "technical_direction",
                min_accuracy_delta=config.min_accuracy_delta,
                min_holdout_samples=config.min_holdout_samples,
                min_changed_predictions=config.min_changed_predictions,
                rule_min_samples=config.min_samples,
                rule_min_unique_cases=config.min_unique_cases,
            )
            reports["technical_direction"] = direction_report
            validation_reports["technical_direction"] = {
                "json": str(validation_root / "technical_direction" / "technical_improvement_validation.json"),
                "markdown": str(validation_root / "technical_direction" / "technical_improvement_validation.md"),
            }
            decisions.append({
                "name": "technical_direction",
                "should_apply": direction_report.decision.should_apply,
                "reason": direction_report.decision.reason,
                "metrics": direction_report.decision.to_dict(),
                "rules": [rule.to_dict() for rule in direction_report.rules],
            })
        else:
            decisions.append({
                "name": "technical_direction",
                "should_apply": False,
                "reason": "没有达到阈值的可执行技术方向候选规则",
                "metrics": {"rules": 0},
                "rules": [],
            })

        if confidence_rules:
            confidence_report = await self.confidence_validator.run_confidence_validation(
                evaluation_report=evaluation_report,
                training_report_path=training_path,
                holdout_config=holdout_config,
                output_dir=validation_root / "technical_confidence",
                confidence_cap=config.confidence_cap,
                min_brier_delta=config.min_brier_delta,
                min_holdout_samples=config.min_holdout_samples,
                min_changed_predictions=config.min_confidence_changed,
                min_matched_samples=config.min_confidence_matched,
                rule_min_samples=config.min_samples,
                rule_min_unique_cases=config.min_unique_cases,
            )
            reports["technical_confidence"] = confidence_report
            validation_reports["technical_confidence"] = {
                "json": str(validation_root / "technical_confidence" / "technical_confidence_policy_validation.json"),
                "markdown": str(validation_root / "technical_confidence" / "technical_confidence_policy_validation.md"),
            }
            decisions.append({
                "name": "technical_confidence",
                "should_apply": confidence_report.decision.should_apply,
                "reason": confidence_report.decision.reason,
                "metrics": confidence_report.decision.to_dict(),
                "rules": [rule.to_dict() for rule in confidence_report.rules],
            })
        else:
            decisions.append({
                "name": "technical_confidence",
                "should_apply": False,
                "reason": "没有达到阈值的可执行技术置信度候选规则",
                "metrics": {"rules": 0},
                "rules": [],
            })
        return {
            "reports": reports,
            "validation_reports": validation_reports,
            "decisions": decisions,
        }

    def _write_registry_skills(
        self,
        reports: dict[str, object],
        validation_reports: dict,
    ) -> list[str]:
        written: list[str] = []
        direction_report = reports.get("technical_direction")
        if direction_report:
            written.extend(
                self.direction_validator.write_registry_skills(
                    direction_report,
                    holdout_report_path=validation_reports.get("technical_direction", {}).get("json"),
                )
            )
        confidence_report = reports.get("technical_confidence")
        if confidence_report:
            written.extend(
                self.confidence_validator.write_confidence_registry_skills(
                    confidence_report,
                    holdout_report_path=validation_reports.get("technical_confidence", {}).get("json"),
                )
            )
        return written

    def _promote_passed_artifacts(
        self,
        artifacts: list[CandidateArtifact],
        config: CandidateSandboxConfig,
    ) -> list[str]:
        if not any(
            any(
                item.get("should_apply")
                for name, item in artifact.validation.items()
                if str(name).startswith("technical_")
            )
            for artifact in artifacts
        ):
            return []

        promoted: list[str] = []
        for artifact in artifacts:
            if artifact.status != "validated_passed":
                continue
            if artifact.area == "prompt" and not config.allow_prompt_promotion:
                continue
            if artifact.area == "skill" and not config.allow_skill_promotion:
                continue

            source = Path(artifact.content_path)
            if artifact.area == "prompt":
                dest = (
                    config.project_root
                    / "config"
                    / "agent_improvement"
                    / "prompt_guardrails"
                    / f"{agent_slug(artifact.agent_name)}.md"
                )
            else:
                dest = (
                    config.project_root
                    / "config"
                    / "agent_improvement"
                    / "skills"
                    / source.name
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            artifact.status = "applied"
            artifact.reason = "候选通过 holdout，并已晋升到正式规则"
            artifact.promotion_path = str(dest)
            promoted.append(str(dest))
        return promoted

    @staticmethod
    def _attach_validation_to_artifacts(
        artifacts: list[CandidateArtifact],
        decisions: list[dict],
    ) -> None:
        technical_decisions = {
            item.get("name"): item
            for item in decisions
            if str(item.get("name") or "").startswith("technical_")
        }
        if not technical_decisions:
            return
        prompt_replay = technical_decisions.get("technical_prompt_replay")
        rule_decisions = {
            name: item for name, item in technical_decisions.items()
            if name in {"technical_direction", "technical_confidence"}
        }
        any_rule_pass = any(item.get("should_apply") for item in rule_decisions.values())
        any_pass = any(item.get("should_apply") for item in technical_decisions.values())
        reason = "; ".join(str(item.get("reason") or "") for item in technical_decisions.values())
        for artifact in artifacts:
            if artifact.agent_name != TechnicalCalibrationBootstrapper.AGENT_NAME:
                continue
            if artifact.area not in {"prompt", "skill"}:
                continue
            artifact.validation.update(technical_decisions)
            if artifact.area == "prompt" and prompt_replay:
                artifact_passed = bool(prompt_replay.get("should_apply"))
            elif artifact.area == "skill" and rule_decisions:
                artifact_passed = any_rule_pass
            else:
                artifact_passed = any_pass
            artifact.status = "validated_passed" if artifact_passed else "validated_failed"
            artifact.reason = (
                "技术面候选通过独立 holdout/replay"
                if artifact_passed
                else f"技术面候选未通过独立 holdout: {reason}"
            )

    @staticmethod
    def _candidate_content_path(
        candidate_root: Path,
        agent_name: str,
        draft: ImprovementPatchDraft,
    ) -> Path:
        slug = agent_slug(agent_name)
        if draft.area == "prompt":
            return candidate_root / "prompt_guardrails" / f"{slug}.md"
        return (
            candidate_root
            / "skills"
            / f"{slug}_{AgentImprovementEngineer._safe_slug(draft.title)}.md"
        )

    @staticmethod
    def _draft_stats(draft: ImprovementPatchDraft) -> tuple[int, int, Optional[float]]:
        sample_size = sum(
            int(signal.get("sample_size", 0) or 0)
            for signal in draft.source_signals
        )
        unique_cases = max(
            [
                int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0)
                for signal in draft.source_signals
            ]
            or [0]
        )
        accuracies = [
            float(signal.get("accuracy"))
            for signal in draft.source_signals
            if signal.get("accuracy") is not None
        ]
        return sample_size, unique_cases, min(accuracies) if accuracies else None

    @staticmethod
    def _resolve_output_dir(config: CandidateSandboxConfig, candidate_id: str) -> Path:
        if config.output_dir:
            return Path(config.output_dir)
        return (
            Path(config.project_root)
            / "output"
            / "agent_candidate_sandbox"
            / candidate_id
        )

    @staticmethod
    def _resolve_candidate_root(config: CandidateSandboxConfig, candidate_id: str) -> Path:
        if config.candidate_root:
            return Path(config.candidate_root)
        return (
            Path(config.project_root)
            / "config"
            / "agent_improvement"
            / "candidates"
            / candidate_id
        )

    @staticmethod
    def _summary(
        artifacts: list[CandidateArtifact],
        promoted_paths: list[str],
        registry_skill_ids: list[str],
    ) -> dict:
        statuses: dict[str, int] = {}
        for artifact in artifacts:
            statuses[artifact.status] = statuses.get(artifact.status, 0) + 1
        return {
            "artifacts": len(artifacts),
            "validated_passed": statuses.get("validated_passed", 0) + statuses.get("applied", 0),
            "validated_failed": statuses.get("validated_failed", 0),
            "draft_unvalidated": statuses.get("draft_unvalidated", 0),
            "promoted_paths": len(promoted_paths),
            "registry_skills": len(registry_skill_ids),
            "statuses": statuses,
        }

    @staticmethod
    def _report_status(
        artifacts: list[CandidateArtifact],
        promoted_paths: list[str],
        decisions: list[dict],
    ) -> str:
        if promoted_paths:
            return "applied"
        if any(decision.get("should_apply") for decision in decisions):
            return "validated_passed"
        if any(artifact.status == "validated_failed" for artifact in artifacts):
            return "validated_failed"
        if artifacts:
            return "draft_unvalidated"
        return "empty"

    @staticmethod
    def _safe_slug(value: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", value.strip())
        return safe.strip("_")[:80] or "candidate"
