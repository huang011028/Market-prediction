"""
LLM JSON extraction and repair helpers.

LLM responses often contain nearly valid JSON with small syntax mistakes
such as a missing comma between fields. This module keeps that recovery logic
centralized so every agent fails in the same predictable way.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMJsonParseResult:
    data: Any = None
    json_text: str = ""
    repaired: bool = False
    repairs: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.error == ""


def extract_json_text(content: str) -> str:
    """Extract the most likely JSON payload from an LLM response."""
    text = str(content or "").strip()
    fenced = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1].strip()
    return text


def repair_json_text(json_text: str) -> tuple[str, list[str]]:
    """Repair common JSON mistakes seen in LLM output."""
    text = str(json_text or "").strip().lstrip("\ufeff")
    repairs: list[str] = []

    def apply(pattern: str, repl: str, name: str, flags: int = 0) -> None:
        nonlocal text
        updated = re.sub(pattern, repl, text, flags=flags)
        if updated != text:
            repairs.append(name)
            text = updated

    apply(r"(:|\[|,)\s*\+(\d+(?:\.\d+)?)", r"\1 \2", "removed_plus_sign")
    apply(r",\s*}", "}", "removed_trailing_object_comma")
    apply(r",\s*]", "]", "removed_trailing_array_comma")

    updated = _insert_missing_commas_between_lines(text)
    if updated != text:
        repairs.append("inserted_missing_line_comma")
        text = updated

    # Same-line variant: {"a": "x" "b": 1}
    updated = re.sub(
        r'((?:"(?:\\.|[^"\\])*"|[-+]?\d+(?:\.\d+)?|true|false|null|\]|\}))\s+(?="[^"\\]+"\s*:)',
        r"\1, ",
        text,
    )
    if updated != text:
        repairs.append("inserted_missing_inline_comma")
        text = updated

    return text, repairs


def parse_llm_json(content: str) -> LLMJsonParseResult:
    """Parse LLM JSON with structured repair metadata."""
    extracted = extract_json_text(content)
    repaired, repairs = repair_json_text(extracted)
    candidates = [(extracted, []), (repaired, repairs)]
    seen: set[str] = set()
    last_error = ""

    for candidate, candidate_repairs in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return LLMJsonParseResult(
                data=json.loads(candidate, strict=False),
                json_text=candidate,
                repaired=bool(candidate_repairs),
                repairs=list(candidate_repairs),
            )
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = str(exc)

    for candidate, candidate_repairs in candidates:
        pythonish = _json_to_python_literal(candidate)
        if pythonish in seen:
            continue
        seen.add(pythonish)
        try:
            return LLMJsonParseResult(
                data=ast.literal_eval(pythonish),
                json_text=pythonish,
                repaired=True,
                repairs=list(dict.fromkeys(candidate_repairs + ["parsed_python_literal"])),
            )
        except (SyntaxError, ValueError, TypeError) as exc:
            last_error = str(exc)

    return LLMJsonParseResult(
        json_text=repaired or extracted,
        repaired=bool(repairs),
        repairs=repairs,
        error=last_error or "Unable to parse LLM JSON",
    )


def _insert_missing_commas_between_lines(text: str) -> str:
    lines = text.splitlines()
    if len(lines) <= 1:
        return text

    fixed: list[str] = []
    for idx, line in enumerate(lines):
        current = line.rstrip()
        stripped = current.strip()
        fixed.append(current)
        if idx >= len(lines) - 1:
            continue

        next_stripped = lines[idx + 1].lstrip()
        if _line_needs_comma(stripped, next_stripped):
            fixed[-1] = current + ","
    return "\n".join(fixed)


def _line_needs_comma(current: str, next_line: str) -> bool:
    if not current or not next_line:
        return False
    if current.endswith((",", "{", "[")):
        return False
    if next_line.startswith(("}", "]", ",")):
        return False
    if not re.match(r'"[^"\\]+"\s*:', next_line):
        return False
    return bool(re.search(r'(?:"(?:\\.|[^"\\])*"|[-+]?\d+(?:\.\d+)?|true|false|null|\]|\})$', current))


def _json_to_python_literal(text: str) -> str:
    converted = re.sub(r"\btrue\b", "True", text)
    converted = re.sub(r"\bfalse\b", "False", converted)
    converted = re.sub(r"\bnull\b", "None", converted)
    return converted
