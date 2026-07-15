"""
动态 Prompt / 声明式 Skill 调优附录。

Agent 改进工程师只允许自动写入 config/agent_improvement 下的声明式文件。
预测 Agent 在运行时读取这些文件，把历史验证后的低风险规则注入 system prompt。
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CANDIDATE_OVERRIDE_ROOT: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "agent_improvement_candidate_root",
    default=None,
)

AGENT_SLUGS = {
    "近期股价分析师": "technical",
    "最新新闻分析师": "news",
    "公司前景分析师": "fundamental",
    "行业对比分析师": "industry",
    "国际形势分析师": "macro",
    "汇总分析师": "aggregator",
}


def agent_slug(agent_name: str) -> str:
    if agent_name in AGENT_SLUGS:
        return AGENT_SLUGS[agent_name]
    safe = str(agent_name or "agent").strip().lower().replace(" ", "_")
    for ch in '/\\:*?"<>|()[]':
        safe = safe.replace(ch, "_")
    return safe[:80] or "agent"


@contextmanager
def candidate_override_context(candidate_root: str | Path | None) -> Iterator[None]:
    """临时启用候选沙箱覆盖层，用于 baseline/candidate 隔离验证。"""
    root = Path(candidate_root).resolve() if candidate_root else None
    token = _CANDIDATE_OVERRIDE_ROOT.set(root)
    try:
        yield
    finally:
        _CANDIDATE_OVERRIDE_ROOT.reset(token)


def build_prompt_with_overrides(
    base_prompt: str,
    agent_name: str,
    project_root: Path | None = None,
    candidate_root: str | Path | None = None,
) -> str:
    """把受控调优规则追加到 agent system prompt。"""
    appendix = load_agent_override_appendix(
        agent_name,
        project_root=project_root,
        candidate_root=candidate_root,
    )
    if not appendix:
        return base_prompt
    return f"{base_prompt.rstrip()}\n\n{appendix}"


def load_agent_override_appendix(
    agent_name: str,
    project_root: Path | None = None,
    candidate_root: str | Path | None = None,
) -> str:
    root = Path(project_root) if project_root else PROJECT_ROOT
    slug = agent_slug(agent_name)
    candidate = (
        Path(candidate_root).resolve()
        if candidate_root
        else _CANDIDATE_OVERRIDE_ROOT.get()
    )

    sections: list[str] = []
    sections.extend(
        _load_override_sections(
            root / "config" / "agent_improvement",
            slug,
            guardrail_title="历史评估调优规则",
            skill_title="声明式 Skill 规则",
        )
    )
    if candidate:
        sections.extend(
            _load_override_sections(
                candidate,
                slug,
                guardrail_title="候选 Prompt Guardrail",
                skill_title="候选声明式 Skill 规则",
            )
        )

    if not sections:
        return ""
    return "\n\n".join([
        "<!-- AGENT_IMPROVEMENT_OVERRIDES_START -->",
        *sections,
        "<!-- AGENT_IMPROVEMENT_OVERRIDES_END -->",
    ])


def _load_override_sections(
    base_dir: Path,
    slug: str,
    guardrail_title: str,
    skill_title: str,
) -> list[str]:
    guardrail = base_dir / "prompt_guardrails" / f"{slug}.md"
    skill_dir = base_dir / "skills"

    sections: list[str] = []
    if guardrail.exists():
        text = _read_text(guardrail)
        if text:
            sections.append(f"## {guardrail_title}\n" + text)

    skill_texts = [
        text for text in (
            _read_text(path) for path in _skill_paths(skill_dir, slug)
        ) if text
    ]
    if skill_texts:
        sections.append(f"## {skill_title}\n" + "\n\n".join(skill_texts))
    return sections


def _skill_paths(skill_dir: Path, slug: str) -> Iterable[Path]:
    if not skill_dir.exists():
        return []
    paths = sorted(skill_dir.glob(f"{slug}*.md"))
    shared = skill_dir / "shared.md"
    if shared.exists():
        paths.append(shared)
    return paths


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
