"""Constrained stacking for technical and industry-enhanced Quant predictions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from src.core.quant_models import LABELS, QuantPrediction


@dataclass
class IndustryStackArtifact:
    technical_weight: float = 1.0
    industry_weight: float = 0.0
    max_industry_weight: float = 0.35
    validation_samples: int = 0
    technical_brier: float = 0.0
    stacked_brier: float = 0.0
    brier_delta: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class ConstrainedIndustryStacker:
    """Choose a non-negative, capped industry increment on validation data."""

    def __init__(self, *, max_industry_weight: float = 0.35, min_delta: float = 0.0):
        self.max_industry_weight = min(1.0, max(0.0, float(max_industry_weight)))
        self.min_delta = max(0.0, float(min_delta))
        self.artifact = IndustryStackArtifact(max_industry_weight=self.max_industry_weight)

    def fit(
        self,
        technical: list[QuantPrediction],
        industry: list[QuantPrediction],
        actual_labels: list[str],
    ) -> "ConstrainedIndustryStacker":
        if not technical or len(technical) != len(industry) or len(technical) != len(actual_labels):
            raise ValueError("行业 stacking 的验证预测与标签长度不一致")
        actual = _actual_matrix(actual_labels)
        technical_probs = _probability_matrix(technical)
        industry_probs = _probability_matrix(industry)
        technical_brier = _brier(technical_probs, actual)
        best_brier = technical_brier
        best_weight = 0.0
        for weight in np.linspace(0.0, self.max_industry_weight, 29):
            blended = (1.0 - weight) * technical_probs + weight * industry_probs
            loss = _brier(blended, actual) + 1e-5 * float(weight) ** 2
            if loss < best_brier - 1e-9:
                best_brier = _brier(blended, actual)
                best_weight = float(weight)
        if technical_brier - best_brier < self.min_delta:
            best_weight = 0.0
            best_brier = technical_brier
        self.artifact = IndustryStackArtifact(
            technical_weight=round(1.0 - best_weight, 6),
            industry_weight=round(best_weight, 6),
            max_industry_weight=self.max_industry_weight,
            validation_samples=len(actual_labels),
            technical_brier=round(technical_brier, 8),
            stacked_brier=round(best_brier, 8),
            brier_delta=round(technical_brier - best_brier, 8),
        )
        return self

    def transform(
        self,
        technical: list[QuantPrediction],
        industry: list[QuantPrediction],
    ) -> list[QuantPrediction]:
        if len(technical) != len(industry):
            raise ValueError("行业 stacking 的两组预测长度不一致")
        weight = self.artifact.industry_weight
        return [
            _blend(left, right, weight)
            for left, right in zip(technical, industry)
        ]

    def save(self, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)


def _blend(technical: QuantPrediction, industry: QuantPrediction, industry_weight: float) -> QuantPrediction:
    weight = min(1.0, max(0.0, float(industry_weight)))
    probabilities = (
        (1.0 - weight) * np.asarray([
            technical.prob_down, technical.prob_no_edge, technical.prob_up,
        ], dtype=float)
        + weight * np.asarray([
            industry.prob_down, industry.prob_no_edge, industry.prob_up,
        ], dtype=float)
    )
    probabilities /= probabilities.sum() or 1.0
    down = round(float(probabilities[0]), 6)
    no_edge = round(float(probabilities[1]), 6)
    up = round(max(0.0, 1.0 - down - no_edge), 6)
    expected = (
        (1.0 - weight) * technical.expected_return_pct
        + weight * industry.expected_return_pct
    )
    return QuantPrediction(
        expected_return_pct=round(float(expected), 4),
        prob_down=down,
        prob_no_edge=no_edge,
        prob_up=up,
        direction=LABELS[int(np.argmax(probabilities))],
        expected_return_p10=_blend_optional(technical.expected_return_p10, industry.expected_return_p10, weight),
        expected_return_p50=_blend_optional(technical.expected_return_p50, industry.expected_return_p50, weight),
        expected_return_p90=_blend_optional(technical.expected_return_p90, industry.expected_return_p90, weight),
    )


def _blend_optional(left: float | None, right: float | None, weight: float) -> float | None:
    if left is None and right is None:
        return None
    left_value = float(left if left is not None else right)
    right_value = float(right if right is not None else left)
    return round((1.0 - weight) * left_value + weight * right_value, 4)


def _probability_matrix(predictions: list[QuantPrediction]) -> np.ndarray:
    values = np.asarray([
        [item.prob_down, item.prob_no_edge, item.prob_up]
        for item in predictions
    ], dtype=float)
    values = np.clip(values, 0.0, 1.0)
    return values / np.maximum(values.sum(axis=1, keepdims=True), 1e-12)


def _actual_matrix(labels: list[str]) -> np.ndarray:
    values = np.zeros((len(labels), len(LABELS)), dtype=float)
    for index, label in enumerate(labels):
        if label in LABELS:
            values[index, LABELS.index(label)] = 1.0
    return values


def _brier(probabilities: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.sum((probabilities - actual) ** 2, axis=1) / len(LABELS)))
