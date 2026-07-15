"""
Agent 改进工程师。

这是项目的调优链路 Agent，不参与股票方向预测。它消费历史评估报告，
按权限边界把低风险改进自动落到 prompt guardrail / 声明式 skill，
对核心代码、MCP、数据源和校准器只生成建议草案。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.core.improvement_patch_planner import (
    AgentImprovementPatchPlanner,
    ImprovementPatchDraft,
)
from src.core.llm_client import LLMClient
from src.prompts.dynamic_overrides import agent_slug


ENGINEER_NAME = "Agent 改进工程师"


@dataclass
class ImprovementEngineerConfig:
    """Agent 改进工程师权限配置。"""

    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    output_dir: Optional[Path] = None
    min_samples_for_auto_apply: int = 20
    min_unique_cases_for_auto_apply: int = 5
    dry_run: bool = False
    allow_prompt_apply: bool = True
    allow_declarative_skill_apply: bool = True
    use_llm_review: bool = False

    @property
    def permissions(self) -> dict:
        return {
            "auto_apply": ["prompt", "skill"],
            "draft_only": ["core_code", "mcp", "data_source", "calibration"],
            "prompt_apply_enabled": self.allow_prompt_apply,
            "declarative_skill_apply_enabled": self.allow_declarative_skill_apply,
            "dry_run": self.dry_run,
            "min_samples_for_auto_apply": self.min_samples_for_auto_apply,
            "min_unique_cases_for_auto_apply": self.min_unique_cases_for_auto_apply,
        }


@dataclass
class ImprovementAction:
    """一次改进行动或建议。"""

    agent_name: str
    area: str
    action_type: str
    title: str
    status: str
    reason: str
    path: Optional[str] = None
    signal_count: int = 0
    sample_size: int = 0
    unique_cases: int = 0
    accuracy: Optional[float] = None
    protected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImprovementEngineerReport:
    """Agent 改进工程师执行报告。"""

    engineer_name: str
    generated_at: str
    source_report_path: Optional[str]
    permissions: dict
    evaluation_summary: dict
    actions: list[ImprovementAction] = field(default_factory=list)
    draft_paths: list[str] = field(default_factory=list)
    applied_paths: list[str] = field(default_factory=list)
    protected_recommendations: list[dict] = field(default_factory=list)
    llm_review: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["actions"] = [action.to_dict() for action in self.actions]
        return payload

    def to_markdown(self) -> str:
        lines = [
            f"# {self.engineer_name} 执行报告",
            "",
            f"- 生成时间: {self.generated_at}",
            f"- 来源报告: {self.source_report_path or '内存输入'}",
            f"- 自动修改: prompt={self.permissions.get('prompt_apply_enabled')}, "
            f"skill={self.permissions.get('declarative_skill_apply_enabled')}, "
            f"dry_run={self.permissions.get('dry_run')}",
            f"- 最小自动应用样本数: {self.permissions.get('min_samples_for_auto_apply')}",
            f"- 最小自动应用独立案例数: {self.permissions.get('min_unique_cases_for_auto_apply')}",
            "",
            "## 评估摘要",
            "",
            f"- Agent 样本数: {self.evaluation_summary.get('total_samples', 0)}",
            f"- 已验证预测数: {self.evaluation_summary.get('verified_predictions', 0)}",
            f"- 错误策略信号: {self.evaluation_summary.get('wrong_strategy_signals', 0)}",
            f"- 可保留优势信号: {self.evaluation_summary.get('strength_signals', 0)}",
            "",
            "## 执行动作",
            "",
        ]
        if not self.actions:
            lines.append("暂无达到阈值的可执行动作。")
        else:
            lines.extend([
                "| Agent | 面 | 动作 | 状态 | 样本 | 独立案例 | 命中率 | 路径/原因 |",
                "| --- | --- | --- | --- | ---: | ---: | ---: | --- |",
            ])
            for action in self.actions:
                accuracy = "" if action.accuracy is None else f"{action.accuracy:.1%}"
                lines.append(
                    "| {agent} | {area} | {atype} | {status} | {samples} | {cases} | {acc} | {path} {reason} |".format(
                        agent=action.agent_name,
                        area=action.area,
                        atype=action.action_type,
                        status=action.status,
                        samples=action.sample_size,
                        cases=action.unique_cases,
                        acc=accuracy,
                        path=action.path or "",
                        reason=action.reason,
                    )
                )

        if self.protected_recommendations:
            lines.extend([
                "",
                "## 需人工确认的高风险建议",
                "",
            ])
            for item in self.protected_recommendations:
                lines.append(
                    "- `{agent}/{area}` {title}: {reason}".format(
                        agent=item.get("agent_name"),
                        area=item.get("area"),
                        title=item.get("title"),
                        reason=item.get("reason"),
                    )
                )

        if self.llm_review:
            lines.extend(["", "## LLM 复核意见", "", self.llm_review])

        return "\n".join(lines).rstrip() + "\n"


class AgentImprovementEngineer:
    """根据历史评估结果进行受控自我改进的工程 agent。"""

    name = ENGINEER_NAME
    description = "基于真实历史样本评估结果，受控改进 prompt 和声明式 skill"
    AUTO_AREAS = {"prompt", "skill"}
    PROTECTED_AREAS = {"mcp", "data_source", "calibration", "core_code"}

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        planner: Optional[AgentImprovementPatchPlanner] = None,
    ):
        self.llm = llm
        self.planner = planner or AgentImprovementPatchPlanner()

    async def run(
        self,
        evaluation_report: dict,
        config: Optional[ImprovementEngineerConfig] = None,
        source_report_path: Optional[str] = None,
    ) -> ImprovementEngineerReport:
        config = config or ImprovementEngineerConfig()
        output_dir = self._resolve_output_dir(config)
        output_dir.mkdir(parents=True, exist_ok=True)

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

        actions: list[ImprovementAction] = []
        draft_paths: list[str] = []
        applied_paths: list[str] = []
        protected: list[dict] = []

        for agent_name, agent_signals in sorted(signals_by_agent.items()):
            plan = self.planner.build_plan(agent_name, agent_signals)
            draft_dir = output_dir / "drafts" / agent_slug(agent_name)
            written = self.planner.write_plan(plan, draft_dir)
            draft_paths.extend([written["plan_md"], written["plan_json"]])
            draft_paths.extend(written.get("drafts", []))

            for draft in plan.drafts:
                action = self._handle_draft(
                    agent_name=agent_name,
                    draft=draft,
                    config=config,
                )
                actions.append(action)
                if action.path and action.status in {"applied", "dry_run"}:
                    applied_paths.append(action.path)
                if action.protected:
                    protected.append({
                        "agent_name": agent_name,
                        "area": draft.area,
                        "title": draft.title,
                        "reason": action.reason,
                        "draft_path": written.get("plan_md"),
                    })

        report = ImprovementEngineerReport(
            engineer_name=self.name,
            generated_at=datetime.now().isoformat(),
            source_report_path=source_report_path,
            permissions=config.permissions,
            evaluation_summary={
                "total_samples": evaluation_report.get("total_samples", 0),
                "verified_predictions": evaluation_report.get("verified_predictions", 0),
                "wrong_strategy_signals": len(evaluation_report.get("wrong_strategy_signals") or []),
                "strength_signals": len(evaluation_report.get("strength_signals") or []),
            },
            actions=actions,
            draft_paths=draft_paths,
            applied_paths=applied_paths,
            protected_recommendations=protected,
        )

        if config.use_llm_review and self.llm:
            report.llm_review = await self._build_llm_review(evaluation_report, report)

        report_path = output_dir / "agent_improvement_engineer_report.json"
        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path = output_dir / "agent_improvement_engineer_report.md"
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        draft_paths.extend([str(report_path), str(md_path)])

        return report

    def _handle_draft(
        self,
        agent_name: str,
        draft: ImprovementPatchDraft,
        config: ImprovementEngineerConfig,
    ) -> ImprovementAction:
        sample_size = sum(
            int(signal.get("sample_size", 0) or 0)
            for signal in draft.source_signals
        )
        min_accuracy = self._min_accuracy(draft.source_signals)
        unique_cases = self._max_unique_cases(draft.source_signals)
        if sample_size < config.min_samples_for_auto_apply:
            return ImprovementAction(
                agent_name=agent_name,
                area=draft.area,
                action_type="observe_only",
                title=draft.title,
                status="skipped",
                reason="样本数未达到自动修改阈值",
                signal_count=len(draft.source_signals),
                sample_size=sample_size,
                unique_cases=unique_cases,
                accuracy=min_accuracy,
            )
        if unique_cases < config.min_unique_cases_for_auto_apply:
            return ImprovementAction(
                agent_name=agent_name,
                area=draft.area,
                action_type="observe_only",
                title=draft.title,
                status="skipped",
                reason="独立历史案例数未达到自动修改阈值",
                signal_count=len(draft.source_signals),
                sample_size=sample_size,
                unique_cases=unique_cases,
                accuracy=min_accuracy,
            )

        if draft.area in self.PROTECTED_AREAS:
            return ImprovementAction(
                agent_name=agent_name,
                area=draft.area,
                action_type="draft_only",
                title=draft.title,
                status="protected",
                reason="核心代码、MCP、数据源或校准器修改需要人工确认",
                signal_count=len(draft.source_signals),
                sample_size=sample_size,
                unique_cases=unique_cases,
                accuracy=min_accuracy,
                protected=True,
            )

        if draft.area == "prompt" and config.allow_prompt_apply:
            return self._apply_prompt_guardrail(
                agent_name,
                draft,
                config,
                sample_size,
                unique_cases,
                min_accuracy,
            )

        if draft.area == "skill" and config.allow_declarative_skill_apply:
            return self._apply_declarative_skill(
                agent_name,
                draft,
                config,
                sample_size,
                unique_cases,
                min_accuracy,
            )

        return ImprovementAction(
            agent_name=agent_name,
            area=draft.area,
            action_type="draft_only",
            title=draft.title,
            status="disabled",
            reason="该自动应用权限未启用",
            signal_count=len(draft.source_signals),
            sample_size=sample_size,
            unique_cases=unique_cases,
            accuracy=min_accuracy,
        )

    def _apply_prompt_guardrail(
        self,
        agent_name: str,
        draft: ImprovementPatchDraft,
        config: ImprovementEngineerConfig,
        sample_size: int,
        unique_cases: int,
        accuracy: Optional[float],
    ) -> ImprovementAction:
        path = (
            config.project_root
            / "config"
            / "agent_improvement"
            / "prompt_guardrails"
            / f"{agent_slug(agent_name)}.md"
        )
        content = self._render_prompt_guardrail(agent_name, draft)
        if not config.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return ImprovementAction(
            agent_name=agent_name,
            area=draft.area,
            action_type="apply_prompt_guardrail",
            title=draft.title,
            status="dry_run" if config.dry_run else "applied",
            reason="已生成历史失败场景 prompt 自检规则",
            path=str(path),
            signal_count=len(draft.source_signals),
            sample_size=sample_size,
            unique_cases=unique_cases,
            accuracy=accuracy,
        )

    def _apply_declarative_skill(
        self,
        agent_name: str,
        draft: ImprovementPatchDraft,
        config: ImprovementEngineerConfig,
        sample_size: int,
        unique_cases: int,
        accuracy: Optional[float],
    ) -> ImprovementAction:
        path = (
            config.project_root
            / "config"
            / "agent_improvement"
            / "skills"
            / f"{agent_slug(agent_name)}_{self._safe_slug(draft.title)}.md"
        )
        content = self._render_declarative_skill(agent_name, draft)
        if not config.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return ImprovementAction(
            agent_name=agent_name,
            area=draft.area,
            action_type="apply_declarative_skill",
            title=draft.title,
            status="dry_run" if config.dry_run else "applied",
            reason="已生成声明式 skill 规则文件",
            path=str(path),
            signal_count=len(draft.source_signals),
            sample_size=sample_size,
            unique_cases=unique_cases,
            accuracy=accuracy,
        )

    @staticmethod
    def _render_prompt_guardrail(agent_name: str, draft: ImprovementPatchDraft) -> str:
        lines = [
            f"# {agent_name} 历史失败场景 Prompt Guardrail",
            "",
            f"- 来源: {ENGINEER_NAME}",
            f"- 改进面: {draft.area}",
            f"- 标题: {draft.title}",
            f"- 更新时间: {datetime.now().isoformat()}",
            "",
            "## 触发场景",
            "",
        ]
        for signal in draft.source_signals:
            lines.append(
                "- {group}/{bucket}: 样本 {n}, 独立案例 {cases}, 命中率 {acc:.1%}, 平均置信 {conf:.1%}".format(
                    group=signal.get("bucket_group"),
                    bucket=signal.get("bucket"),
                    n=int(signal.get("sample_size", 0) or 0),
                    cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                    acc=float(signal.get("accuracy", 0.0) or 0.0),
                    conf=float(signal.get("avg_confidence", 0.0) or 0.0),
                )
            )
        lines.extend([
            "",
            "## 必须执行的自检",
            "",
            "- 输出方向前，先检查当前样本是否落入以上历史低命中场景。",
            "- 若落入低命中场景，必须列出反向证据，并默认降低 confidence。",
            "- 不得因为单一证据或单一来源直接给出高置信强方向。",
            "- 若历史命中率明显低于当前置信度，应把 confidence 上限压到历史命中率附近。",
            "",
            "## 草案来源",
            "",
            draft.proposed_patch,
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _render_declarative_skill(agent_name: str, draft: ImprovementPatchDraft) -> str:
        lines = [
            f"# {agent_name} 声明式 Skill: {draft.title}",
            "",
            f"- 来源: {ENGINEER_NAME}",
            f"- 改进面: {draft.area}",
            f"- 更新时间: {datetime.now().isoformat()}",
            "",
            "## 适用条件",
            "",
        ]
        for signal in draft.source_signals:
            lines.append(
                "- `{group}/{bucket}` 历史样本 {n}, 独立案例 {cases}, 命中率 {acc:.1%}".format(
                    group=signal.get("bucket_group"),
                    bucket=signal.get("bucket"),
                    n=int(signal.get("sample_size", 0) or 0),
                    cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                    acc=float(signal.get("accuracy", 0.0) or 0.0),
                )
            )
        lines.extend([
            "",
            "## 执行规则",
            "",
            "- 这是一份声明式规则，不包含可执行代码。",
            "- 命中适用条件时，优先执行反例检查，再输出方向。",
            "- 若规则与原有自由文本推理冲突，以结构化证据和历史命中率为准。",
            "- 修改后必须使用同一批历史样本和留出样本复测。",
            "",
            "## 原始草案",
            "",
            draft.proposed_patch,
            "",
        ])
        return "\n".join(lines)

    async def _build_llm_review(
        self,
        evaluation_report: dict,
        engineer_report: ImprovementEngineerReport,
    ) -> str:
        if not self.llm:
            return ""
        payload = {
            "evaluation_summary": engineer_report.evaluation_summary,
            "permissions": engineer_report.permissions,
            "actions": [action.to_dict() for action in engineer_report.actions],
        }
        response = await self.llm.achat(
            system_prompt=(
                "你是一个严格的 Agent 工程评审员。请只审查本次自动改进是否符合权限边界，"
                "是否存在过拟合风险，以及下一轮应该优先验证什么。"
            ),
            user_prompt=json.dumps(payload, ensure_ascii=False, indent=2)[:12000],
            temperature=0.1,
        )
        return response.content

    @staticmethod
    def _resolve_output_dir(config: ImprovementEngineerConfig) -> Path:
        if config.output_dir:
            return Path(config.output_dir)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return config.project_root / "output" / "agent_improvement_engineer" / stamp

    @staticmethod
    def _min_accuracy(signals: list[dict]) -> Optional[float]:
        values = [
            float(signal.get("accuracy"))
            for signal in signals
            if signal.get("accuracy") is not None
        ]
        return min(values) if values else None

    @staticmethod
    def _max_unique_cases(signals: list[dict]) -> int:
        values = [
            int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0)
            for signal in signals
        ]
        return max(values) if values else 0

    @staticmethod
    def _safe_slug(value: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]+", "_", value.strip())
        return safe.strip("_")[:80] or "skill"
