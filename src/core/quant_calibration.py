"""Leakage-safe multiclass probability calibration for Quant predictions."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from src.core.quant_models import LABELS, QuantPrediction


@dataclass
class ProbabilityCalibrationArtifact:
    method: str = "identity"
    temperature: float = 1.0
    prior_weight: float = 0.0
    class_prior: tuple[float, float, float] = (1 / 3, 1 / 3, 1 / 3)
    validation_samples: int = 0
    raw_brier: float = 0.0
    calibrated_brier: float = 0.0
    brier_delta: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class MulticlassProbabilityCalibrator:
    """Fit temperature and conservative prior shrinkage on validation data only."""

    def __init__(self, *, min_samples: int = 100, method: str = "temperature"):
        self.min_samples = max(1, int(min_samples))
        self.method = str(method or "none")
        self.artifact = ProbabilityCalibrationArtifact(method="identity")

    def fit(
        self,
        predictions: list[QuantPrediction],
        actual_labels: Iterable[str],
    ) -> "MulticlassProbabilityCalibrator":
        labels = [str(value) for value in actual_labels]
        if self.method == "none" or len(predictions) < self.min_samples or len(labels) != len(predictions):
            self.artifact.validation_samples = len(predictions)
            return self

        probabilities = _probability_matrix(predictions)
        actual = _actual_matrix(labels)
        class_prior = (actual.sum(axis=0) + 1.0) / (len(actual) + len(LABELS))
        raw_brier = _brier(probabilities, actual)
        best = (raw_brier, 1.0, 0.0, probabilities)
        temperatures = np.geomspace(0.65, 4.0, 24)
        prior_weights = (
            np.asarray([0.0])
            if self.method == "temperature"
            else np.linspace(0.0, 0.35, 15)
        )
        for temperature in temperatures:
            tempered = _temperature_scale(probabilities, float(temperature))
            for prior_weight in prior_weights:
                calibrated = (1.0 - prior_weight) * tempered + prior_weight * class_prior
                loss = _brier(calibrated, actual)
                # Prefer the identity transform when validation losses are effectively tied.
                penalty = 1e-6 * (abs(math.log(float(temperature))) + float(prior_weight))
                if loss + penalty < best[0] - 1e-9:
                    best = (loss, float(temperature), float(prior_weight), calibrated)

        calibrated_brier, temperature, prior_weight, _ = best
        improved = calibrated_brier < raw_brier - 1e-6
        self.artifact = ProbabilityCalibrationArtifact(
            method=self.method if improved else "identity",
            temperature=temperature if improved else 1.0,
            prior_weight=prior_weight if improved else 0.0,
            class_prior=tuple(float(value) for value in class_prior),
            validation_samples=len(predictions),
            raw_brier=round(raw_brier, 8),
            calibrated_brier=round(calibrated_brier if improved else raw_brier, 8),
            brier_delta=round(raw_brier - (calibrated_brier if improved else raw_brier), 8),
        )
        return self

    def transform(self, predictions: list[QuantPrediction]) -> list[QuantPrediction]:
        if not predictions or self.artifact.method == "identity":
            return [_copy_prediction(item) for item in predictions]
        probabilities = _probability_matrix(predictions)
        calibrated = _temperature_scale(probabilities, self.artifact.temperature)
        prior = np.asarray(self.artifact.class_prior, dtype=float)
        calibrated = (
            (1.0 - self.artifact.prior_weight) * calibrated
            + self.artifact.prior_weight * prior
        )
        return [
            _with_probabilities(prediction, row)
            for prediction, row in zip(predictions, calibrated)
        ]

    def save(self, path: str | Path) -> str:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)


def _probability_matrix(predictions: list[QuantPrediction]) -> np.ndarray:
    values = np.asarray([
        [item.prob_down, item.prob_no_edge, item.prob_up]
        for item in predictions
    ], dtype=float)
    values = np.clip(values, 1e-9, 1.0)
    return values / values.sum(axis=1, keepdims=True)


def _actual_matrix(labels: list[str]) -> np.ndarray:
    matrix = np.zeros((len(labels), len(LABELS)), dtype=float)
    for index, label in enumerate(labels):
        if label in LABELS:
            matrix[index, LABELS.index(label)] = 1.0
    return matrix


def _temperature_scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(probabilities, 1e-9, 1.0)) / max(0.05, float(temperature))
    logits -= logits.max(axis=1, keepdims=True)
    scaled = np.exp(logits)
    return scaled / scaled.sum(axis=1, keepdims=True)


def _brier(probabilities: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.sum((probabilities - actual) ** 2, axis=1) / len(LABELS)))


def _with_probabilities(prediction: QuantPrediction, values: np.ndarray) -> QuantPrediction:
    values = np.asarray(values, dtype=float)
    values = values / (values.sum() or 1.0)
    down = round(float(values[0]), 6)
    no_edge = round(float(values[1]), 6)
    up = round(max(0.0, 1.0 - down - no_edge), 6)
    return QuantPrediction(
        expected_return_pct=prediction.expected_return_pct,
        prob_down=down,
        prob_no_edge=no_edge,
        prob_up=up,
        direction=LABELS[int(np.argmax(values))],
        expected_return_p10=prediction.expected_return_p10,
        expected_return_p50=prediction.expected_return_p50,
        expected_return_p90=prediction.expected_return_p90,
    )


def _copy_prediction(prediction: QuantPrediction) -> QuantPrediction:
    return QuantPrediction(**prediction.to_dict())
