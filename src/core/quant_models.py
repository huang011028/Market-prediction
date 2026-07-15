"""Statistical baseline models for V3 excess-return prediction."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.core.return_residualizer import normal_quantiles


LABELS = ("bearish", "neutral", "bullish")


@dataclass
class QuantPrediction:
    expected_return_pct: float
    prob_down: float
    prob_no_edge: float
    prob_up: float
    direction: str
    expected_return_p10: Optional[float] = None
    expected_return_p50: Optional[float] = None
    expected_return_p90: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QuantModelMetadata:
    model_name: str
    model_kind: str
    market: str
    horizon: str
    target_version: str
    feature_version: str
    trained_at: str
    training_samples: int
    training_dates: int
    training_symbols: int
    feature_names: list[str] = field(default_factory=list)
    library_versions: dict[str, str] = field(default_factory=dict)
    shadow_only: bool = True
    validation: dict[str, Any] = field(default_factory=dict)
    feature_families: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class QuantModelUnavailable(RuntimeError):
    pass


class QuantBaselineModel:
    model_name = "baseline"
    model_kind = "base"

    def __init__(self, feature_families: Optional[list[str]] = None):
        self.vectorizer = None
        self.estimator = None
        self.threshold_pct = 1.5
        self.residual_std = 2.0
        self.class_return_means = {"bearish": -1.5, "neutral": 0.0, "bullish": 1.5}
        self.metadata: Optional[QuantModelMetadata] = None
        self.feature_families = list(feature_families or [])

    def fit(
        self,
        rows: list[dict[str, Any]],
        *,
        market: str,
        horizon: str,
        target_version: str = "v3.1",
    ) -> "QuantBaselineModel":
        if len(rows) < 2:
            raise ValueError("模型训练至少需要 2 条样本")
        DictVectorizer = _import_sklearn_vectorizer()
        self.vectorizer = DictVectorizer(sparse=True)
        x = self.vectorizer.fit_transform([
            _model_features(row, self.feature_families or None) for row in rows
        ])
        y_return = np.asarray([float(row["label_return_pct"]) for row in rows])
        y_direction = np.asarray([str(row["label_direction"]) for row in rows])
        thresholds = [float(row.get("label_threshold_pct") or 0.0) for row in rows]
        valid_thresholds = [value for value in thresholds if value > 0]
        if valid_thresholds:
            self.threshold_pct = float(np.median(valid_thresholds))
        for label in LABELS:
            values = y_return[y_direction == label]
            if len(values):
                self.class_return_means[label] = float(np.mean(values))
        self.estimator = self._fit_estimator(x, y_return, y_direction)
        fitted = self._raw_return_prediction(x, y_direction)
        if fitted is not None and len(fitted) == len(y_return):
            residual = y_return - fitted
            self.residual_std = max(0.25, float(np.std(residual)))
        self.metadata = QuantModelMetadata(
            model_name=self.model_name,
            model_kind=self.model_kind,
            market=market,
            horizon=horizon,
            target_version=target_version,
            feature_version=str(rows[0].get("feature_version") or "quant_features.v1"),
            trained_at=datetime.now().isoformat(),
            training_samples=len(rows),
            training_dates=len({row["as_of"] for row in rows}),
            training_symbols=len({row["symbol"] for row in rows}),
            feature_names=list(self.vectorizer.get_feature_names_out()),
            library_versions=_library_versions(),
            feature_families=self.feature_families or ["all"],
        )
        return self

    def predict(self, rows: list[dict[str, Any]]) -> list[QuantPrediction]:
        if self.vectorizer is None or self.estimator is None:
            raise RuntimeError("模型尚未训练")
        x = self.vectorizer.transform([
            _model_features(row, self.feature_families or None) for row in rows
        ])
        return self._predict_estimator(x)

    def _fit_estimator(self, x, y_return: np.ndarray, y_direction: np.ndarray):
        raise NotImplementedError

    def _predict_estimator(self, x) -> list[QuantPrediction]:
        raise NotImplementedError

    def _raw_return_prediction(self, x, y_direction: np.ndarray) -> Optional[np.ndarray]:
        return None

    def _return_distribution(self, expected: float) -> QuantPrediction:
        sigma = max(self.residual_std, 0.25)
        down = _normal_cdf((-self.threshold_pct - expected) / sigma)
        up = 1.0 - _normal_cdf((self.threshold_pct - expected) / sigma)
        no_edge = max(0.0, 1.0 - down - up)
        prediction = _prediction(expected, down, no_edge, up)
        p10, p50, p90 = normal_quantiles(expected, sigma)
        prediction.expected_return_p10 = round(float(p10), 4)
        prediction.expected_return_p50 = round(float(p50), 4)
        prediction.expected_return_p90 = round(float(p90), 4)
        return prediction

    def save(self, root: str | Path) -> dict[str, str]:
        if self.metadata is None:
            raise RuntimeError("模型尚未训练")
        try:
            import joblib
        except ImportError as exc:
            raise QuantModelUnavailable("保存模型需要 joblib") from exc
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        model_path = root / f"{self.model_name}.joblib"
        metadata_path = root / f"{self.model_name}.json"
        joblib.dump(self, model_path)
        metadata_path.write_text(
            json.dumps(self.metadata.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"model": str(model_path), "metadata": str(metadata_path)}


class RidgeReturnModel(QuantBaselineModel):
    model_name = "ridge_return"
    model_kind = "linear_regression"

    def _fit_estimator(self, x, y_return: np.ndarray, y_direction: np.ndarray):
        try:
            from sklearn.linear_model import Ridge
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError as exc:
            raise QuantModelUnavailable("Ridge 基线需要 scikit-learn") from exc
        return make_pipeline(
            StandardScaler(with_mean=False),
            Ridge(alpha=10.0),
        ).fit(x, y_return)

    def _raw_return_prediction(self, x, y_direction: np.ndarray) -> Optional[np.ndarray]:
        return np.asarray(self.estimator.predict(x), dtype=float)

    def _predict_estimator(self, x) -> list[QuantPrediction]:
        return [self._return_distribution(float(value)) for value in self.estimator.predict(x)]


class LogisticDirectionModel(QuantBaselineModel):
    model_name = "logistic_direction"
    model_kind = "multiclass_logistic"

    def _fit_estimator(self, x, y_return: np.ndarray, y_direction: np.ndarray):
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler
        except ImportError as exc:
            raise QuantModelUnavailable("Logistic 基线需要 scikit-learn") from exc
        if len(set(y_direction.tolist())) < 2:
            raise ValueError("Logistic 训练至少需要两个真实方向类别")
        return make_pipeline(
            StandardScaler(with_mean=False),
            LogisticRegression(
                C=0.25,
                class_weight="balanced",
                max_iter=3000,
                solver="lbfgs",
            ),
        ).fit(x, y_direction)

    def _predict_estimator(self, x) -> list[QuantPrediction]:
        raw = self.estimator.predict_proba(x)
        classes = list(self.estimator.classes_)
        result = []
        for row in raw:
            probs = {label: 0.0 for label in LABELS}
            for label, probability in zip(classes, row):
                probs[str(label)] = float(probability)
            expected = sum(probs[label] * self.class_return_means[label] for label in LABELS)
            prediction = _prediction(
                expected,
                probs["bearish"],
                probs["neutral"],
                probs["bullish"],
            )
            variance = sum(
                probs[label] * (self.class_return_means[label] - expected) ** 2
                for label in LABELS
            )
            p10, p50, p90 = normal_quantiles(expected, variance ** 0.5)
            prediction.expected_return_p10 = round(float(p10), 4)
            prediction.expected_return_p50 = round(float(p50), 4)
            prediction.expected_return_p90 = round(float(p90), 4)
            result.append(prediction)
        return result


class LightGBMReturnModel(QuantBaselineModel):
    model_name = "lightgbm_return"
    model_kind = "gradient_boosting_regression"

    def _fit_estimator(self, x, y_return: np.ndarray, y_direction: np.ndarray):
        try:
            from lightgbm import LGBMRegressor
        except ImportError as exc:
            raise QuantModelUnavailable("LightGBM 基线需要 lightgbm") from exc
        return LGBMRegressor(
            objective="huber",
            n_estimators=250,
            learning_rate=0.03,
            num_leaves=15,
            max_depth=5,
            min_child_samples=max(20, min(80, len(y_return) // 20)),
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.5,
            reg_lambda=2.0,
            random_state=42,
            n_jobs=1,
            verbosity=-1,
        ).fit(x, y_return)

    def _raw_return_prediction(self, x, y_direction: np.ndarray) -> Optional[np.ndarray]:
        return np.asarray(self.estimator.booster_.predict(x), dtype=float)

    def _predict_estimator(self, x) -> list[QuantPrediction]:
        return [self._return_distribution(float(value)) for value in self.estimator.booster_.predict(x)]


MODEL_TYPES = {
    "ridge": RidgeReturnModel,
    "logistic": LogisticDirectionModel,
    "lightgbm": LightGBMReturnModel,
}


def create_quant_model(
    name: str,
    feature_families: Optional[list[str]] = None,
) -> QuantBaselineModel:
    cls = MODEL_TYPES.get(str(name).lower())
    if cls is None:
        raise ValueError(f"未知量化模型: {name}")
    return cls(feature_families=feature_families)


def dependency_status() -> dict[str, Any]:
    status = {}
    for module in ("sklearn", "lightgbm", "pyarrow", "joblib"):
        try:
            imported = __import__(module)
            status[module] = {"available": True, "version": getattr(imported, "__version__", "")}
        except Exception as exc:
            status[module] = {"available": False, "reason": str(exc)}
    return status


COMMON_META_FEATURES = {
    "meta__market",
    "meta__horizon_days",
    "meta__daily_volatility_pct",
    "meta__price",
    "meta__avg_traded_value_20d",
    "meta__exchange",
    "meta__board",
    "meta__listing_age_days",
}


def _model_features(
    row: dict[str, Any],
    feature_families: Optional[list[str]] = None,
) -> dict[str, Any]:
    features = dict(row.get("features") or {})
    filtered = {
        key: value
        for key, value in features.items()
        if value is not None and not key.startswith("final__")
    }
    if not feature_families or "all" in feature_families:
        return filtered
    allowed = set(feature_families)
    return {
        key: value
        for key, value in filtered.items()
        if key in COMMON_META_FEATURES
        or any(key.startswith(f"{family}__") for family in allowed)
        or ("industry" in allowed and key.startswith("meta__industry"))
    }


def _prediction(expected: float, down: float, no_edge: float, up: float) -> QuantPrediction:
    values = np.asarray([max(0.0, down), max(0.0, no_edge), max(0.0, up)], dtype=float)
    total = float(values.sum()) or 1.0
    values /= total
    direction = LABELS[int(np.argmax(values))]
    return QuantPrediction(
        expected_return_pct=round(float(expected), 4),
        prob_down=round(float(values[0]), 6),
        prob_no_edge=round(float(values[1]), 6),
        prob_up=round(float(values[2]), 6),
        direction=direction,
    )


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _import_sklearn_vectorizer():
    try:
        from sklearn.feature_extraction import DictVectorizer
    except ImportError as exc:
        raise QuantModelUnavailable("量化基线需要 scikit-learn") from exc
    return DictVectorizer


def _library_versions() -> dict[str, str]:
    versions = {"numpy": np.__version__}
    for name in ("sklearn", "lightgbm"):
        try:
            module = __import__(name)
            versions[name] = getattr(module, "__version__", "")
        except Exception:
            continue
    return versions
