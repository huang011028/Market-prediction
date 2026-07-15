"""Agent Skill Registry.

Stores validated, declarative agent skills in a machine-readable registry.
The registry is intentionally data-only: core code decides how each skill type
is interpreted at runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY_PATH = (
    PROJECT_ROOT / "config" / "agent_improvement" / "agent_skill_registry.json"
)


@dataclass
class AgentSkill:
    """A validated declarative skill available to one agent."""

    skill_id: str
    agent_name: str
    skill_type: str
    enabled: bool
    trigger_conditions: dict
    action: dict
    validation: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    description: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "AgentSkill":
        now = datetime.now().isoformat()
        return cls(
            skill_id=str(payload.get("skill_id") or ""),
            agent_name=str(payload.get("agent_name") or payload.get("agent") or ""),
            skill_type=str(payload.get("skill_type") or ""),
            enabled=bool(payload.get("enabled", True)),
            trigger_conditions=dict(payload.get("trigger_conditions") or {}),
            action=dict(payload.get("action") or {}),
            validation=dict(payload.get("validation") or {}),
            source=dict(payload.get("source") or {}),
            description=str(payload.get("description") or ""),
            tags=list(payload.get("tags") or []),
            created_at=str(payload.get("created_at") or now),
            updated_at=str(payload.get("updated_at") or now),
        )


class AgentSkillRegistry:
    """Read and update validated declarative skills."""

    VERSION = 1

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else DEFAULT_REGISTRY_PATH
        self.skills: list[AgentSkill] = []
        self.metadata: dict = {}
        self._load()

    def enabled_skills(
        self,
        agent_name: Optional[str] = None,
        skill_type: Optional[str] = None,
    ) -> list[AgentSkill]:
        skills = [skill for skill in self.skills if skill.enabled]
        if agent_name:
            skills = [skill for skill in skills if skill.agent_name == agent_name]
        if skill_type:
            skills = [skill for skill in skills if skill.skill_type == skill_type]
        return skills

    def direction_policy_for_agent(self, agent_name: str) -> dict:
        """Convert enabled direction_policy skills to the legacy policy shape."""
        rules = []
        for skill in self.enabled_skills(agent_name, "direction_policy"):
            action = self._legacy_direction_action(skill.action)
            if not action:
                continue
            rule = {
                "skill_id": skill.skill_id,
                "bucket_group": skill.validation.get("bucket_group", ""),
                "sample_key": skill.validation.get("sample_key", ""),
                "bucket": skill.validation.get("bucket", ""),
                "action": action,
                "conditions": dict(skill.trigger_conditions),
                "validation": dict(skill.validation),
                "source": dict(skill.source),
                "description": skill.description,
            }
            rules.append(rule)
        return {
            "source": "agent_skill_registry",
            "registry_path": str(self.path),
            "rules": rules,
        }

    def confidence_policy_for_agent(self, agent_name: str) -> dict:
        """Return enabled confidence_policy skills in runtime policy shape."""
        rules = []
        for skill in self.enabled_skills(agent_name, "confidence_policy"):
            action = str((skill.action or {}).get("type") or "")
            if action != "cap_confidence":
                continue
            cap = _safe_float(skill.action.get("confidence_cap"), None)
            if cap is None:
                continue
            rules.append({
                "skill_id": skill.skill_id,
                "bucket_group": skill.validation.get("bucket_group", ""),
                "sample_key": skill.validation.get("sample_key", ""),
                "bucket": skill.validation.get("bucket", ""),
                "action": "cap_confidence",
                "confidence_cap": cap,
                "conditions": dict(skill.trigger_conditions),
                "validation": dict(skill.validation),
                "source": dict(skill.source),
                "description": skill.description,
            })
        return {
            "source": "agent_skill_registry",
            "registry_path": str(self.path),
            "rules": rules,
        }

    def upsert_skill(self, skill: AgentSkill | dict) -> AgentSkill:
        """Insert or replace a skill by stable skill_id."""
        item = skill if isinstance(skill, AgentSkill) else AgentSkill.from_dict(skill)
        if not item.skill_id:
            raise ValueError("skill_id is required")
        now = datetime.now().isoformat()
        item.updated_at = now
        existing = None
        for idx, current in enumerate(self.skills):
            if current.skill_id == item.skill_id:
                existing = (idx, current)
                break
        if existing:
            idx, old = existing
            item.created_at = old.created_at or item.created_at or now
            self.skills[idx] = item
        else:
            item.created_at = item.created_at or now
            self.skills.append(item)
        return item

    def set_enabled(self, skill_id: str, enabled: bool) -> AgentSkill:
        """Enable or disable one skill."""
        for skill in self.skills:
            if skill.skill_id == skill_id:
                skill.enabled = bool(enabled)
                skill.updated_at = datetime.now().isoformat()
                return skill
        raise KeyError(skill_id)

    def summary(self) -> dict:
        """Return small aggregate counts for UI display."""
        by_agent: dict[str, dict[str, int]] = {}
        by_type: dict[str, dict[str, int]] = {}
        for skill in self.skills:
            for bucket, key in ((by_agent, skill.agent_name), (by_type, skill.skill_type)):
                current = bucket.setdefault(key, {"total": 0, "enabled": 0, "disabled": 0})
                current["total"] += 1
                if skill.enabled:
                    current["enabled"] += 1
                else:
                    current["disabled"] += 1
        return {
            "total": len(self.skills),
            "enabled": sum(1 for skill in self.skills if skill.enabled),
            "disabled": sum(1 for skill in self.skills if not skill.enabled),
            "by_agent": by_agent,
            "by_type": by_type,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.VERSION,
            "updated_at": datetime.now().isoformat(),
            "metadata": self.metadata,
            "skills": [skill.to_dict() for skill in self.skills],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load(self) -> None:
        if not self.path.exists():
            self.skills = []
            self.metadata = {}
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.metadata = dict(payload.get("metadata") or {})
        self.skills = [
            AgentSkill.from_dict(item)
            for item in payload.get("skills", [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _legacy_direction_action(action: dict) -> str:
        action_type = str((action or {}).get("type") or "")
        direction = str((action or {}).get("direction") or "")
        if action_type == "force_direction" and direction in {"bullish", "bearish"}:
            return f"force_{direction}"
        if action_type == "neutralize_direction":
            return "neutralize_direction"
        return ""


def stable_skill_id(
    agent_name: str,
    skill_type: str,
    action: str,
    conditions: dict,
    bucket_group: str = "",
    bucket: str = "",
) -> str:
    """Build a deterministic id so later validations update the same skill."""
    raw = json.dumps(
        {
            "agent": agent_name,
            "skill_type": skill_type,
            "action": action,
            "conditions": conditions,
            "bucket_group": bucket_group,
            "bucket": bucket,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    return ".".join([
        _slug(agent_name),
        _slug(skill_type),
        _slug(bucket_group or "rule"),
        digest,
    ])


def rules_to_direction_policy_skills(
    rules: Iterable,
    *,
    agent_name: str,
    source: dict,
    holdout_decision: dict,
) -> list[AgentSkill]:
    """Convert validated technical rules into registry skills."""
    skills: list[AgentSkill] = []
    for rule in rules:
        rule_dict = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule)
        action_name = str(rule_dict.get("action") or "")
        direction = ""
        action_type = ""
        if action_name == "force_bullish":
            direction = "bullish"
            action_type = "force_direction"
        elif action_name == "force_bearish":
            direction = "bearish"
            action_type = "force_direction"
        elif action_name == "neutralize_direction":
            direction = "neutral"
            action_type = "neutralize_direction"
        else:
            continue

        conditions = dict(rule_dict.get("conditions") or {})
        skill_id = stable_skill_id(
            agent_name=agent_name,
            skill_type="direction_policy",
            action=action_name,
            conditions=conditions,
            bucket_group=str(rule_dict.get("bucket_group") or ""),
            bucket=str(rule_dict.get("bucket") or ""),
        )
        validation = {
            "bucket_group": rule_dict.get("bucket_group"),
            "sample_key": rule_dict.get("sample_key"),
            "bucket": rule_dict.get("bucket"),
            "training_samples": rule_dict.get("sample_size", 0),
            "training_unique_cases": rule_dict.get("unique_cases", 0),
            "training_accuracy": rule_dict.get("accuracy", 0.0),
            "dominant_actual_direction": rule_dict.get("dominant_actual_direction", ""),
            "dominant_actual_rate": rule_dict.get("dominant_actual_rate", 0.0),
            "holdout": dict(holdout_decision),
        }
        skills.append(
            AgentSkill(
                skill_id=skill_id,
                agent_name=agent_name,
                skill_type="direction_policy",
                enabled=True,
                trigger_conditions=conditions,
                action={
                    "type": action_type,
                    "direction": direction,
                    "confidence_floor": 0.50,
                    "confidence_cap": 0.60,
                },
                validation=validation,
                source=dict(source),
                description=(
                    f"历史 holdout 通过的技术方向规则: "
                    f"{rule_dict.get('bucket_group')}/{rule_dict.get('bucket')}"
                ),
                tags=["technical", "holdout_passed", "runtime_policy"],
            )
        )
    return skills


def rules_to_confidence_policy_skills(
    rules: Iterable,
    *,
    agent_name: str,
    source: dict,
    holdout_decision: dict,
    confidence_cap: float = 0.35,
) -> list[AgentSkill]:
    """Convert validated overconfidence rules into confidence cap skills."""
    skills: list[AgentSkill] = []
    for rule in rules:
        rule_dict = rule.to_dict() if hasattr(rule, "to_dict") else dict(rule)
        conditions = dict(rule_dict.get("conditions") or {})
        if not conditions:
            continue
        skill_id = stable_skill_id(
            agent_name=agent_name,
            skill_type="confidence_policy",
            action=f"cap_confidence_{confidence_cap:.2f}",
            conditions=conditions,
            bucket_group=str(rule_dict.get("bucket_group") or ""),
            bucket=str(rule_dict.get("bucket") or ""),
        )
        validation = {
            "bucket_group": rule_dict.get("bucket_group"),
            "sample_key": rule_dict.get("sample_key"),
            "bucket": rule_dict.get("bucket"),
            "training_samples": rule_dict.get("sample_size", 0),
            "training_unique_cases": rule_dict.get("unique_cases", 0),
            "training_accuracy": rule_dict.get("accuracy", 0.0),
            "training_avg_confidence": rule_dict.get("avg_confidence", 0.0),
            "dominant_actual_direction": rule_dict.get("dominant_actual_direction", ""),
            "dominant_actual_rate": rule_dict.get("dominant_actual_rate", 0.0),
            "holdout": dict(holdout_decision),
        }
        skills.append(
            AgentSkill(
                skill_id=skill_id,
                agent_name=agent_name,
                skill_type="confidence_policy",
                enabled=True,
                trigger_conditions=conditions,
                action={
                    "type": "cap_confidence",
                    "confidence_cap": round(confidence_cap, 3),
                },
                validation=validation,
                source=dict(source),
                description=(
                    f"历史 holdout 通过的技术置信度封顶规则: "
                    f"{rule_dict.get('bucket_group')}/{rule_dict.get('bucket')}"
                ),
                tags=["technical", "holdout_passed", "confidence_cap"],
            )
        )
    return skills


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "近期股价分析师": "technical",
        "最新新闻分析师": "news",
        "公司前景分析师": "fundamental",
        "行业对比分析师": "industry",
        "国际形势分析师": "macro",
        "汇总分析师": "aggregator",
    }
    if text in mapping:
        return mapping[text]
    text = mapping.get(str(value or "").strip(), text)
    text = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "_", text)
    return text.strip("_")[:60] or "skill"


def _safe_float(value, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value in (None, "", "N/A"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
