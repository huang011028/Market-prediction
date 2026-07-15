"""Constrained, out-of-sample learned weights for the five analyst agents."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.data.quant_feature_store import QuantFeatureStore


AGENT_SLUGS = ("technical", "news", "fundamental", "macro", "industry")


@dataclass
class LearnedAggregatorArtifact:
    market: str
    horizon: str
    weights: dict[str, float]
    trained_at: str
    training_samples: int
    validation_samples: int
    training_dates: int
    validation: dict[str, Any]
    enabled: bool = False
    shadow_only: bool = True
    target_version: str = "v3.1"
    model_version: str = "constrained_probability_pool.v2"
    source: str = "live_or_out_of_fold_predictions"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConstrainedAgentWeightModel:
    """Fit non-negative agent weights that sum to one using multiclass Brier."""

    def __init__(self, prior_weights: Optional[dict[str, float]] = None, l2: float = 0.05):
        self.prior_weights = prior_weights or {slug: 1 / len(AGENT_SLUGS) for slug in AGENT_SLUGS}
        self.l2 = float(l2)
        self.weights = dict(self.prior_weights)

    def fit(self, rows: list[dict[str, Any]]) -> "ConstrainedAgentWeightModel":
        try:
            from scipy.optimize import minimize
        except ImportError:
            minimize = None
        x, y, available = _agent_probability_tensor(rows)
        if len(rows) < 2 or not available:
            raise ValueError("没有足够的 Agent 概率样本")
        slugs = list(available)
        prior = np.asarray([max(0.0, self.prior_weights.get(slug, 0.0)) for slug in slugs])
        prior = prior / (prior.sum() or 1.0)

        def objective(weights):
            pooled = _pool_probabilities(x, weights)
            brier = float(np.mean(np.sum((pooled - y) ** 2, axis=1) / 3.0))
            penalty = self.l2 * float(np.sum((weights - prior) ** 2))
            return brier + penalty

        if minimize is not None:
            result = minimize(
                objective,
                x0=prior,
                method="SLSQP",
                bounds=[(0.0, 1.0)] * len(slugs),
                constraints=[{"type": "eq", "fun": lambda weights: float(np.sum(weights) - 1.0)}],
                options={"maxiter": 500, "ftol": 1e-10},
            )
            if not result.success:
                raise RuntimeError(f"Aggregator 权重优化失败: {result.message}")
            optimized = result.x
        else:
            optimized = _projected_gradient_weights(x, y, prior, self.l2)
        self.weights = {slug: round(float(weight), 6) for slug, weight in zip(slugs, optimized)}
        return self

    def predict(self, rows: list[dict[str, Any]]) -> np.ndarray:
        x, _, slugs = _agent_probability_tensor(rows, require_labels=False)
        weights = np.asarray([self.weights.get(slug, 0.0) for slug in slugs])
        weights = weights / (weights.sum() or 1.0)
        return _pool_probabilities(x, weights)


class LearnedAggregatorTrainer:
    def __init__(self, store: Optional[QuantFeatureStore] = None, artifact_root: Optional[str | Path] = None):
        self.store = store or QuantFeatureStore()
        if artifact_root is None:
            artifact_root = Path(__file__).resolve().parents[2] / "config" / "quant_models"
        self.artifact_root = Path(artifact_root)

    def run(
        self,
        *,
        market: str,
        horizon: str,
        prior_weights: Optional[dict[str, float]] = None,
        min_samples: int = 200,
        min_unique_dates: int = 60,
        purge_days: int = 7,
        lockbox_days: int = 90,
        min_brier_delta: float = 0.005,
        min_folds: int = 3,
        activate_if_passed: bool = False,
    ) -> LearnedAggregatorArtifact:
        rows = self.store.rows(
            market=market,
            horizon=horizon,
            target_version="v3.1",
            labeled_only=True,
            limit=1_000_000,
        )
        rows = [row for row in rows if _eligible_origin(row)]
        if len(rows) < min_samples:
            raise ValueError(f"Aggregator 样本不足: {len(rows)} < {min_samples}")
        unique_dates = sorted({row["as_of"] for row in rows})
        if len(unique_dates) < min_unique_dates:
            raise ValueError(f"Aggregator 独立日期不足: {len(unique_dates)} < {min_unique_dates}")

        max_date = date.fromisoformat(unique_dates[-1])
        lockbox_start = max_date - timedelta(days=lockbox_days)
        development = [row for row in rows if date.fromisoformat(row["as_of"]) < lockbox_start]
        fold_specs = _aggregator_fold_specs(
            development,
            purge_days=purge_days,
            min_folds=min_folds,
            min_train_samples=max(30, min_samples // 2),
            min_validation_samples=max(20, min_samples // 10),
            min_train_dates=max(10, min_unique_dates // 2),
        )
        if len(fold_specs) < min_folds:
            raise ValueError(f"Aggregator walk-forward 折数不足: {len(fold_specs)} < {min_folds}")

        candidate_parts = []
        baseline_parts = []
        actual_parts = []
        validation_rows: list[dict] = []
        fold_metrics = []
        for fold_index, (train, validation) in enumerate(fold_specs, start=1):
            fold_model = ConstrainedAgentWeightModel(prior_weights=prior_weights).fit(train)
            candidate = fold_model.predict(validation)
            baseline_model = ConstrainedAgentWeightModel(prior_weights=prior_weights)
            baseline_model.weights = dict(baseline_model.prior_weights)
            baseline = baseline_model.predict(validation)
            actual = _actual_matrix(validation)
            candidate_brier_fold = _brier(candidate, actual)
            baseline_brier_fold = _brier(baseline, actual)
            fold_metrics.append({
                "fold": fold_index,
                "train_samples": len(train),
                "validation_samples": len(validation),
                "train_range": [train[0]["as_of"], train[-1]["as_of"]],
                "validation_range": [validation[0]["as_of"], validation[-1]["as_of"]],
                "baseline_brier": round(baseline_brier_fold, 6),
                "candidate_brier": round(candidate_brier_fold, 6),
                "brier_delta": round(baseline_brier_fold - candidate_brier_fold, 6),
            })
            candidate_parts.append(candidate)
            baseline_parts.append(baseline)
            actual_parts.append(actual)
            validation_rows.extend(validation)

        candidate_all = np.concatenate(candidate_parts, axis=0)
        baseline_all = np.concatenate(baseline_parts, axis=0)
        actual_all = np.concatenate(actual_parts, axis=0)
        candidate_brier = _brier(candidate_all, actual_all)
        baseline_brier = _brier(baseline_all, actual_all)
        delta = baseline_brier - candidate_brier
        delta_ci = _paired_brier_delta_ci(
            baseline_all,
            candidate_all,
            actual_all,
            validation_rows,
        )
        positive_folds = sum(item["brier_delta"] > 0 for item in fold_metrics)
        passed = (
            delta >= min_brier_delta
            and delta_ci[0] > 0
            and positive_folds >= math.ceil(len(fold_metrics) * 2 / 3)
        )
        model = ConstrainedAgentWeightModel(prior_weights=prior_weights).fit(development)
        artifact = LearnedAggregatorArtifact(
            market=market.upper(),
            horizon=horizon,
            weights=model.weights,
            trained_at=datetime.now().isoformat(),
            training_samples=len(development),
            validation_samples=len(validation_rows),
            training_dates=len({row["as_of"] for row in development}),
            validation={
                "baseline_brier": round(baseline_brier, 6),
                "candidate_brier": round(candidate_brier, 6),
                "brier_delta": round(delta, 6),
                "min_brier_delta": min_brier_delta,
                "brier_delta_ci_low": round(delta_ci[0], 6),
                "brier_delta_ci_high": round(delta_ci[1], 6),
                "folds": fold_metrics,
                "positive_folds": positive_folds,
                "min_folds": min_folds,
                "agent_error_correlation": _agent_error_correlation(validation_rows),
                "passed": passed,
                "lockbox_start": lockbox_start.isoformat(),
                "lockbox_samples": sum(date.fromisoformat(row["as_of"]) >= lockbox_start for row in rows),
                "lockbox_status": "locked",
            },
            enabled=bool(passed and activate_if_passed),
            shadow_only=not bool(passed and activate_if_passed),
        )
        self.save(artifact)
        return artifact

    def save(self, artifact: LearnedAggregatorArtifact) -> Path:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        path = self.artifact_root / f"learned_aggregator_{artifact.market}_{artifact.horizon}.json"
        path.write_text(json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class LearnedAggregatorPolicy:
    """Runtime loader. Disabled/shadow artifacts never alter live predictions."""

    def __init__(self, artifact_root: Optional[str | Path] = None):
        if artifact_root is None:
            artifact_root = Path(__file__).resolve().parents[2] / "config" / "quant_models"
        self.artifact_root = Path(artifact_root)

    def load(self, market: str, horizon: str) -> Optional[dict[str, Any]]:
        path = self.artifact_root / f"learned_aggregator_{market.upper()}_{horizon}.json"
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not payload.get("enabled") or payload.get("shadow_only", True):
            return None
        return payload

    def aggregate(self, agent_results: list[Any], market: str, horizon: str) -> Optional[dict[str, float]]:
        artifact = self.load(market, horizon)
        if not artifact:
            return None
        weights = artifact.get("weights") or {}
        pooled = np.zeros(3, dtype=float)
        expected = 0.0
        used_weight = 0.0
        for result in agent_results:
            slug = _slug_from_name(getattr(result, "agent_name", ""))
            weight = float(weights.get(slug, 0.0) or 0.0)
            target = getattr(result, "prediction_target", None)
            if weight <= 0 or target is None:
                continue
            probs = np.asarray([
                float(getattr(target, "prob_down", 0.0) or 0.0),
                float(getattr(target, "prob_neutral", 0.0) or 0.0),
                float(getattr(target, "prob_up", 0.0) or 0.0),
            ])
            total = probs.sum()
            if total <= 0:
                continue
            pooled += weight * probs / total
            expected += weight * float(getattr(target, "expected_return_pct", 0.0) or 0.0)
            used_weight += weight
        if used_weight <= 0:
            return None
        pooled /= pooled.sum() or 1.0
        return {
            "prob_down": float(pooled[0]),
            "prob_no_edge": float(pooled[1]),
            "prob_up": float(pooled[2]),
            "expected_excess_return_pct": expected / used_weight,
            "model_version": artifact.get("model_version", ""),
        }

    def status(self) -> list[dict[str, Any]]:
        if not self.artifact_root.exists():
            return []
        values = []
        for path in sorted(self.artifact_root.glob("learned_aggregator_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["path"] = str(path)
                values.append(payload)
            except Exception:
                continue
        return values


def _agent_probability_tensor(rows: list[dict], require_labels: bool = True):
    available = [
        slug for slug in AGENT_SLUGS
        if any(f"agent__{slug}__prob_up" in (row.get("features") or {}) for row in rows)
    ]
    if not available:
        return np.zeros((len(rows), 0, 3)), _actual_matrix(rows) if require_labels else None, []
    tensor = np.zeros((len(rows), len(available), 3), dtype=float)
    for row_index, row in enumerate(rows):
        features = row.get("features") or {}
        for agent_index, slug in enumerate(available):
            values = np.asarray([
                float(features.get(f"agent__{slug}__prob_down", 0.0) or 0.0),
                float(features.get(f"agent__{slug}__prob_no_edge", 0.0) or 0.0),
                float(features.get(f"agent__{slug}__prob_up", 0.0) or 0.0),
            ])
            if values.sum() <= 0:
                direction = int(features.get(f"agent__{slug}__direction", 0) or 0)
                confidence = float(features.get(f"agent__{slug}__confidence", 0.0) or 0.0)
                residual = max(0.0, 1.0 - confidence)
                values = (
                    np.asarray([confidence, residual * 0.65, residual * 0.35]) if direction < 0
                    else np.asarray([residual * 0.35, residual * 0.65, confidence]) if direction > 0
                    else np.asarray([residual * 0.5, confidence, residual * 0.5])
                )
            tensor[row_index, agent_index] = values / (values.sum() or 1.0)
    return tensor, _actual_matrix(rows) if require_labels else None, available


def _pool_probabilities(tensor: np.ndarray, weights: np.ndarray) -> np.ndarray:
    pooled = np.tensordot(tensor, weights, axes=([1], [0]))
    totals = pooled.sum(axis=1, keepdims=True)
    totals[totals <= 0] = 1.0
    return pooled / totals


def _actual_matrix(rows: list[dict]) -> np.ndarray:
    matrix = np.zeros((len(rows), 3), dtype=float)
    index = {"bearish": 0, "neutral": 1, "bullish": 2}
    for row_index, row in enumerate(rows):
        matrix[row_index, index.get(str(row.get("label_direction")), 1)] = 1.0
    return matrix


def _brier(predicted: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean(np.sum((predicted - actual) ** 2, axis=1) / 3.0))


def _aggregator_fold_specs(
    rows: list[dict],
    *,
    purge_days: int,
    min_folds: int,
    min_train_samples: int,
    min_validation_samples: int,
    min_train_dates: int,
) -> list[tuple[list[dict], list[dict]]]:
    dates = sorted({str(row["as_of"])[:10] for row in rows})
    if len(dates) < min_train_dates + min_folds:
        return []
    first_validation_index = len(dates)
    for index, value in enumerate(dates):
        cutoff = date.fromisoformat(value) - timedelta(days=max(1, purge_days))
        available_train_dates = sum(date.fromisoformat(item) <= cutoff for item in dates[:index])
        if available_train_dates >= min_train_dates:
            first_validation_index = index
            break
    validation_dates = dates[first_validation_index:]
    chunks = [list(chunk) for chunk in np.array_split(validation_dates, min_folds) if len(chunk)]
    folds = []
    for chunk in chunks:
        validation_start = date.fromisoformat(chunk[0])
        train_cutoff = validation_start - timedelta(days=max(1, purge_days))
        train = [row for row in rows if date.fromisoformat(str(row["as_of"])[:10]) <= train_cutoff]
        chunk_set = set(chunk)
        validation = [row for row in rows if str(row["as_of"])[:10] in chunk_set]
        if (
            len(train) >= min_train_samples
            and len({row["as_of"] for row in train}) >= min_train_dates
            and len(validation) >= min_validation_samples
        ):
            folds.append((train, validation))
    return folds


def _paired_brier_delta_ci(
    baseline: np.ndarray,
    candidate: np.ndarray,
    actual: np.ndarray,
    rows: list[dict],
    iterations: int = 1000,
) -> tuple[float, float]:
    baseline_loss = np.sum((baseline - actual) ** 2, axis=1) / 3.0
    candidate_loss = np.sum((candidate - actual) ** 2, axis=1) / 3.0
    frame: dict[str, list[float]] = {}
    for row, value in zip(rows, baseline_loss - candidate_loss):
        frame.setdefault(str(row["as_of"])[:10], []).append(float(value))
    daily = np.asarray([np.mean(values) for _, values in sorted(frame.items())], dtype=float)
    if len(daily) < 2:
        value = float(daily.mean()) if len(daily) else 0.0
        return value, value
    rng = np.random.default_rng(42)
    means = [float(np.mean(rng.choice(daily, size=len(daily), replace=True))) for _ in range(iterations)]
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _agent_error_correlation(rows: list[dict]) -> dict[str, dict[str, float]]:
    tensor, actual, slugs = _agent_probability_tensor(rows)
    if len(slugs) < 2 or len(rows) < 3:
        return {}
    losses = np.mean((tensor - actual[:, None, :]) ** 2, axis=2)
    result: dict[str, dict[str, float]] = {}
    for i, left in enumerate(slugs):
        result[left] = {}
        for j, right in enumerate(slugs):
            corr = float(np.corrcoef(losses[:, i], losses[:, j])[0, 1])
            result[left][right] = round(corr if math.isfinite(corr) else 0.0, 6)
    return result


def _projected_gradient_weights(
    tensor: np.ndarray,
    actual: np.ndarray,
    prior: np.ndarray,
    l2: float,
) -> np.ndarray:
    """Pure NumPy fallback for the convex constrained weight problem."""
    weights = prior.copy()
    sample_count = max(1, tensor.shape[0])
    for iteration in range(2500):
        pooled = _pool_probabilities(tensor, weights)
        error = pooled - actual
        gradient = (
            2.0
            * np.einsum("nc,nac->a", error, tensor)
            / (sample_count * 3.0)
            + 2.0 * l2 * (weights - prior)
        )
        step = 0.25 / math.sqrt(iteration + 1.0)
        candidate = _project_simplex(weights - step * gradient)
        if float(np.max(np.abs(candidate - weights))) < 1e-10:
            weights = candidate
            break
        weights = candidate
    return weights


def _project_simplex(values: np.ndarray) -> np.ndarray:
    """Euclidean projection onto non-negative weights summing to one."""
    ordered = np.sort(values)[::-1]
    cumulative = np.cumsum(ordered) - 1.0
    indices = np.arange(1, len(values) + 1)
    positive = ordered - cumulative / indices > 0
    rho = np.nonzero(positive)[0][-1]
    theta = cumulative[rho] / float(rho + 1)
    return np.maximum(values - theta, 0.0)


def _eligible_origin(row: dict) -> bool:
    lineage = row.get("lineage") or {}
    return lineage.get("prediction_origin") in {"live", "out_of_fold"}


def _slug_from_name(name: str) -> str:
    mapping = {
        "近期股价分析师": "technical",
        "最新新闻分析师": "news",
        "公司前景分析师": "fundamental",
        "国际形势分析师": "macro",
        "行业对比分析师": "industry",
    }
    return mapping.get(str(name), "")
