"""
Agent 改进补丁草案生成器

把回放/校准得到的 improvement_signals 转成可审阅的 prompt、MCP、
数据源策略和 skill 特征工程修改草案。它只写 draft 文档，不直接修改运行时代码。
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from src.core.agent_improvement import AgentImprovementAdvisor, AgentImprovementSignal


@dataclass
class ImprovementPatchDraft:
    """一个可审阅的改进草案。"""

    area: str
    title: str
    target_files: list[str]
    rationale: str
    proposed_patch: str
    source_signals: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImprovementPatchPlan:
    """一组改进草案。"""

    agent_name: str
    drafts: list[ImprovementPatchDraft] = field(default_factory=list)
    ignored_signals: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "drafts": [draft.to_dict() for draft in self.drafts],
            "ignored_signals": self.ignored_signals,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# {self.agent_name} Agent 工程改进补丁草案",
            "",
            "这些内容是根据历史回放/校准统计生成的草案，不会自动修改源码。",
            "",
        ]
        if not self.drafts:
            lines.append("暂无达到阈值的 prompt/MCP/skill 改进草案。")
            return "\n".join(lines)

        lines.extend([
            "## 草案总览",
            "",
            "| 改进面 | 标题 | 目标文件 | 触发信号数 |",
            "| --- | --- | --- | ---: |",
        ])
        for draft in self.drafts:
            lines.append(
                f"| {draft.area} | {draft.title} | {', '.join(draft.target_files)} | "
                f"{len(draft.source_signals)} |"
            )

        for idx, draft in enumerate(self.drafts, start=1):
            lines.extend([
                "",
                f"## {idx}. {draft.title}",
                "",
                f"- 改进面: `{draft.area}`",
                f"- 目标文件: {', '.join(f'`{path}`' for path in draft.target_files)}",
                f"- 原因: {draft.rationale}",
                "",
                "触发信号:",
                "",
            ])
            for signal in draft.source_signals:
                lines.append(
                    "- `{bucket_group}/{bucket}`: 样本 {sample_size}, 独立案例 {unique_cases}, 命中率 {accuracy:.1%}, "
                    "{issue}".format(
                        bucket_group=signal.get("bucket_group"),
                        bucket=signal.get("bucket"),
                        sample_size=int(signal.get("sample_size", 0) or 0),
                        unique_cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                        accuracy=float(signal.get("accuracy", 0.0) or 0.0),
                        issue=signal.get("issue", ""),
                    )
                )
            lines.extend([
                "",
                "草案补丁:",
                "",
                draft.proposed_patch,
                "",
            ])

        return "\n".join(lines).rstrip() + "\n"


class AgentImprovementPatchPlanner:
    """把改进信号转成 prompt/MCP/skill/data_source 草案。"""

    def build_plan(
        self,
        agent_name: str,
        signals: Iterable[dict | AgentImprovementSignal],
    ) -> ImprovementPatchPlan:
        signal_dicts = [self._signal_to_dict(signal) for signal in signals]
        grouped: dict[str, list[dict]] = {}
        ignored = []
        for signal in signal_dicts:
            area = signal.get("area") or "calibration"
            if area in {"prompt", "mcp", "skill", "data_source", "calibration"}:
                grouped.setdefault(area, []).append(signal)
            else:
                ignored.append(signal)

        drafts: list[ImprovementPatchDraft] = []
        if agent_name == "最新新闻分析师":
            if grouped.get("prompt"):
                drafts.append(self._build_news_prompt_draft(grouped["prompt"]))
            if grouped.get("mcp"):
                drafts.append(self._build_news_mcp_draft(grouped["mcp"]))
            if grouped.get("data_source"):
                drafts.append(self._build_news_data_source_draft(grouped["data_source"]))
            if grouped.get("skill"):
                drafts.append(self._build_news_skill_draft(grouped["skill"]))
            if grouped.get("calibration"):
                drafts.append(self._build_news_calibration_draft(grouped["calibration"]))
        else:
            drafts.extend(self._build_generic_drafts(agent_name, grouped))

        return ImprovementPatchPlan(
            agent_name=agent_name,
            drafts=drafts,
            ignored_signals=ignored,
        )

    def build_plan_from_report(
        self,
        report: dict,
        min_samples: Optional[int] = None,
    ) -> ImprovementPatchPlan:
        """从 replay/bootstrap report 生成草案。"""
        agent_name = report.get("agent_name") or "最新新闻分析师"
        signals = report.get("improvement_signals") or []
        if not signals and report.get("calibration_stats"):
            signals = [
                signal.to_dict()
                for signal in AgentImprovementAdvisor().recommend(
                    agent_name,
                    report.get("calibration_stats") or {},
                    min_samples=min_samples,
                )
            ]
        return self.build_plan(agent_name, signals)

    def write_plan(
        self,
        plan: ImprovementPatchPlan,
        output_dir: str | Path,
    ) -> dict:
        """把草案写入目录，返回写入路径。"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        written = {}
        plan_path = output_dir / "improvement_patch_plan.md"
        plan_path.write_text(plan.to_markdown(), encoding="utf-8")
        written["plan_md"] = str(plan_path)

        json_path = output_dir / "improvement_patch_plan.json"
        json_path.write_text(
            json.dumps(plan.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written["plan_json"] = str(json_path)

        for draft in plan.drafts:
            slug = self._safe_slug(f"{draft.area}_{draft.title}")
            path = output_dir / f"{slug}.md"
            path.write_text(self._draft_to_markdown(draft), encoding="utf-8")
            written.setdefault("drafts", []).append(str(path))

        return written

    @staticmethod
    def _signal_to_dict(signal: dict | AgentImprovementSignal) -> dict:
        if isinstance(signal, AgentImprovementSignal):
            return signal.to_dict()
        return dict(signal)

    @staticmethod
    def _draft_to_markdown(draft: ImprovementPatchDraft) -> str:
        return "\n".join([
            f"# {draft.title}",
            "",
            f"- 改进面: `{draft.area}`",
            f"- 目标文件: {', '.join(f'`{path}`' for path in draft.target_files)}",
            f"- 原因: {draft.rationale}",
            "",
            "## 触发信号",
            "",
            *[
                "- `{bucket_group}/{bucket}`: 样本 {sample_size}, 独立案例 {unique_cases}, 命中率 {accuracy:.1%}".format(
                    bucket_group=signal.get("bucket_group"),
                    bucket=signal.get("bucket"),
                    sample_size=int(signal.get("sample_size", 0) or 0),
                    unique_cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                    accuracy=float(signal.get("accuracy", 0.0) or 0.0),
                )
                for signal in draft.source_signals
            ],
            "",
            "## 草案补丁",
            "",
            draft.proposed_patch,
            "",
        ])

    def _build_news_prompt_draft(self, signals: list[dict]) -> ImprovementPatchDraft:
        buckets = self._bucket_list(signals)
        patch = f"""```diff
diff --git a/src/prompts/news_prompts.py b/src/prompts/news_prompts.py
@@
 CONFIDENCE_ANCHORS = \"\"\"
 ...
 \"\"\"
+
+BACKTESTED_NEWS_FAILURE_GUARDRAILS = \"\"\"
+## 历史回放失败场景自检
+以下场景在近期回放中命中率偏低，输出前必须额外自检:
+- {buckets}
+- 区分事实催化、传闻、情绪噪声和已经被市场消化的信息。
+- 若新闻矩阵与最终方向不一致，必须说明反向证据，并将 confidence 下调到 0.50 以下。
+- 若事件为 rumor_driven 或 mixed，默认 neutral，除非有官方公告或多源交叉验证。
+\"\"\"
@@
-{{CONFIDENCE_ANCHORS}}
+{{CONFIDENCE_ANCHORS}}
+{{BACKTESTED_NEWS_FAILURE_GUARDRAILS}}
@@
-{{CONFIDENCE_ANCHORS}}
+{{CONFIDENCE_ANCHORS}}
+{{BACKTESTED_NEWS_FAILURE_GUARDRAILS}}
```
"""
        return ImprovementPatchDraft(
            area="prompt",
            title="新闻事件归因与失败场景自检 prompt 草案",
            target_files=["src/prompts/news_prompts.py"],
            rationale="新闻情绪/事件桶回放命中率偏低，需要把失败场景显式写入系统提示词和综合判断提示词。",
            proposed_patch=patch,
            source_signals=signals,
        )

    def _build_news_mcp_draft(self, signals: list[dict]) -> ImprovementPatchDraft:
        buckets = self._bucket_list(signals)
        patch = f"""```diff
diff --git a/config/news_mcp_policy.yaml b/config/news_mcp_policy.yaml
new file mode 100644
@@
+tools:
+  announcement_search:
+    role: \"优先确认交易所公告/公司公告\"
+    retry: 2
+  financial_news_search:
+    role: \"补充财经媒体报道\"
+    retry: 1
+failure_buckets:
+  - {buckets}
+policy:
+  missing_tool_confidence_cap: 0.45
+  stale_tool_result_confidence_cap: 0.50
diff --git a/src/data/news_fetcher.py b/src/data/news_fetcher.py
@@
+# TODO(draft): 对 MCP 工具失败、超时、返回字段缺失分别记录 tool_health，
+# 供 NewsAnalyst 和 Aggregator 在历史低命中桶中自动降权。
```
"""
        return ImprovementPatchDraft(
            area="mcp",
            title="新闻 MCP 工具健康度与失败降权草案",
            target_files=["config/news_mcp_policy.yaml", "src/data/news_fetcher.py"],
            rationale="MCP/工具桶回放命中率偏低，需要区分工具不可用、字段缺失和数据本身不支持结论。",
            proposed_patch=patch,
            source_signals=signals,
        )

    def _build_news_data_source_draft(self, signals: list[dict]) -> ImprovementPatchDraft:
        buckets = self._bucket_list(signals)
        patch = f"""```diff
diff --git a/config/news_sources.yaml b/config/news_sources.yaml
new file mode 100644
@@
+sources:
+  official_announcements:
+    priority: 1
+    role: \"公司公告/交易所公告，用于确认传闻和重大事件\"
+    status: draft
+  exchange_filings:
+    priority: 1
+    role: \"交易所披露、监管处罚、停复牌、回购减持公告\"
+    status: draft
+  financial_media:
+    priority: 2
+    role: \"东方财富、证券时报、新浪等新闻源\"
+    status: current
+policy:
+  min_independent_sources_for_high_confidence: 2
+  single_source_confidence_cap: 0.55
+  stale_news_confidence_cap: 0.45
+failure_buckets:
+  - {buckets}
diff --git a/src/data/news_fetcher.py b/src/data/news_fetcher.py
@@
+# TODO(draft): 接入 official_announcements / exchange_filings 后，
+# 在 NewsData.sources_used 中保留来源类型、时效和独立来源数，
+# 供 NewsConfidenceCalibrator 和 NewsAnalyst 的 source/freshness 桶使用。
```
"""
        return ImprovementPatchDraft(
            area="data_source",
            title="新闻数据源策略缺口草案",
            target_files=["config/news_sources.yaml", "src/data/news_fetcher.py"],
            rationale="来源、时效或新闻数量桶回放命中率偏低，优先补官方公告、交易所披露和来源健康度策略。",
            proposed_patch=patch,
            source_signals=signals,
        )

    def _build_news_skill_draft(self, signals: list[dict]) -> ImprovementPatchDraft:
        buckets = self._bucket_list(signals)
        patch = f"""```diff
diff --git a/src/data/news_preprocessor.py b/src/data/news_preprocessor.py
@@
 class SentimentTagger:
@@
+    # TODO(draft): 基于回放失败桶增加反例词典:
+    # - 将\"传闻/网传/市场消息\"标记为 lower_certainty
+    # - 将\"澄清/否认/辟谣\"作为 rumor 反向证据
+    # - 将\"已公告/交易所披露/公司公告\"作为 official_confirmation
@@
 class NewsCategorizer:
@@
+    # TODO(draft): 增加 event_certainty 字段:
+    # official / media_report / rumor / analyst_view / unknown
+    # 当前失败桶: {buckets}
diff --git a/src/agents/news_analyst.py b/src/agents/news_analyst.py
@@
+# TODO(draft): 在 _derive_news_signals 中使用 event_certainty:
+# rumor + single_source => suggested_direction=neutral, max_confidence<=0.35
+# official_confirmation + multi_source => 允许事件桶提升置信上限
```
"""
        return ImprovementPatchDraft(
            area="skill",
            title="新闻预处理与事件确定性 skill 草案",
            target_files=["src/data/news_preprocessor.py", "src/agents/news_analyst.py"],
            rationale="skill 类失败通常来自情绪/事件标签太粗，需要补事件确定性和传闻反例规则。",
            proposed_patch=patch,
            source_signals=signals,
        )

    def _build_news_calibration_draft(self, signals: list[dict]) -> ImprovementPatchDraft:
        buckets = self._bucket_list(signals)
        patch = f"""```diff
diff --git a/src/utils/news_calibrator.py b/src/utils/news_calibrator.py
@@
+# TODO(draft): 对以下低命中场景加入更强的置信度硬上限:
+# {buckets}
+# 建议: 当桶命中率低于 45% 且样本 >= 10 时，
+# calibrated = min(calibrated, historical_accuracy + 0.10)
```
"""
        return ImprovementPatchDraft(
            area="calibration",
            title="新闻校准硬上限草案",
            target_files=["src/utils/news_calibrator.py"],
            rationale="置信度区间或未归类新闻场景命中率偏低，优先强化校准层而不是改 prompt。",
            proposed_patch=patch,
            source_signals=signals,
        )

    def _build_generic_drafts(
        self,
        agent_name: str,
        grouped: dict[str, list[dict]],
    ) -> list[ImprovementPatchDraft]:
        drafts = []
        for area, signals in grouped.items():
            target_files = self._generic_target_files(agent_name, area)
            drafts.append(
                ImprovementPatchDraft(
                    area=area,
                    title=f"{agent_name} {area} 改进草案",
                    target_files=target_files,
                    rationale=f"{agent_name} 的 {area} 场景出现低命中信号，需要人工确认目标文件。",
                    proposed_patch=self._generic_patch_text(agent_name, area, signals),
                    source_signals=signals,
                )
            )
        return drafts

    @staticmethod
    def _generic_target_files(agent_name: str, area: str) -> list[str]:
        agent_targets = {
            "近期股价分析师": {
                "prompt": ["src/prompts/technical_prompts.py"],
                "skill": ["src/agents/technical_analyst.py"],
                "calibration": ["src/utils/technical_calibrator.py"],
            },
            "公司前景分析师": {
                "prompt": ["src/prompts/fundamental_prompts.py"],
                "skill": ["src/agents/fundamental_analyst.py"],
                "data_source": ["src/data/fundamental_fetcher.py"],
                "calibration": ["src/utils/fundamental_calibrator.py"],
            },
            "行业对比分析师": {
                "prompt": ["src/prompts/industry_prompts.py"],
                "skill": ["src/agents/industry_analyst.py"],
                "data_source": ["src/data/industry_fetcher.py", "src/data/industry_preprocessor.py"],
                "calibration": ["src/utils/industry_calibrator.py"],
            },
            "国际形势分析师": {
                "prompt": ["src/prompts/macro_prompts.py"],
                "skill": ["src/agents/macro_analyst.py"],
                "data_source": ["src/data/macro_fetcher.py"],
                "calibration": ["src/utils/macro_calibrator.py"],
            },
            "汇总分析师": {
                "prompt": ["src/prompts/aggregator_prompts.py"],
                "skill": ["src/agents/aggregator.py"],
                "calibration": ["src/agents/aggregator.py"],
            },
        }
        defaults = {
            "prompt": ["src/prompts"],
            "mcp": ["config"],
            "skill": ["src/agents"],
            "data_source": ["src/data"],
            "calibration": ["src/utils"],
        }
        return agent_targets.get(agent_name, {}).get(area, defaults.get(area, []))

    @staticmethod
    def _generic_patch_text(agent_name: str, area: str, signals: list[dict]) -> str:
        buckets = "\n".join(
            "- {group}/{bucket}: n={n}, cases={cases}, acc={acc:.1%}, avg_conf={conf:.1%}".format(
                group=signal.get("bucket_group"),
                bucket=signal.get("bucket"),
                n=int(signal.get("sample_size", 0) or 0),
                cases=int(signal.get("unique_cases", signal.get("sample_size", 0)) or 0),
                acc=float(signal.get("accuracy", 0.0) or 0.0),
                conf=float(signal.get("avg_confidence", 0.0) or 0.0),
            )
            for signal in signals
        )
        if area == "data_source":
            body = (
                "1. 将以下低命中数据源/数据质量场景加入 source policy 降权表。\n"
                "2. 为低命中来源增加替代数据源或官方来源交叉验证。\n"
                "3. 在样本重新验证前，禁止这些场景输出高置信方向。"
            )
        elif area == "mcp":
            body = (
                "1. 为低命中工具场景记录 tool_health。\n"
                "2. 区分 MCP 不可用、字段缺失、请求过期和数据本身无结论。\n"
                "3. 将工具失败场景反馈给 Aggregator 降权。"
            )
        elif area == "prompt":
            body = (
                "1. 把低命中桶写入 prompt 的历史反例自检。\n"
                "2. 要求输出反向证据、已消化信息和降置信理由。\n"
                "3. 高命中桶仅作为保留策略，不盲目提高置信。"
            )
        elif area == "skill":
            body = (
                "1. 检查特征工程、阈值和场景标签。\n"
                "2. 为失败桶增加反例规则。\n"
                "3. 修改后用同一批历史样本回放验证。"
            )
        else:
            body = (
                "1. 将低命中桶加入校准硬上限。\n"
                "2. Aggregator 按历史命中率动态降权。\n"
                "3. 保留高命中桶的当前判断路径。"
            )
        return (
            "```text\n"
            f"{agent_name} / {area} 历史回放信号:\n"
            f"{buckets or '- 暂无具体桶'}\n\n"
            f"建议动作:\n{body}\n"
            "```"
        )

    @staticmethod
    def _bucket_list(signals: list[dict]) -> str:
        if not signals:
            return "暂无具体桶"
        return "\n++- ".join(
            f"{signal.get('bucket_group')}/{signal.get('bucket')} "
            f"(n={signal.get('sample_size')}, "
            f"cases={signal.get('unique_cases', signal.get('sample_size'))}, "
            f"acc={float(signal.get('accuracy', 0.0)):.1%})"
            for signal in signals
        )

    @staticmethod
    def _safe_slug(value: str) -> str:
        safe = value.strip().lower().replace(" ", "_")
        for ch in '/\\:*?"<>|()[]':
            safe = safe.replace(ch, "_")
        return safe[:80] or "draft"
