"""Two-stage, leakage-safe Quant validation for actionable edge and ranking."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.core.experiment_ledger import ExperimentLedger, ExperimentTrial
from src.core.experiment_manifest import detect_experiment_source, write_experiment_manifest
from src.core.quant_models import QuantPrediction, _model_features, dependency_status
from src.core.quant_walk_forward import (
    WalkForwardConfig,
    QuantWalkForwardEvaluator,
    evaluate_predictions,
)
from src.data.quant_feature_store import FEATURE_SCHEMA_VERSION, QuantFeatureStore


SIZE_FEATURE_CANDIDATES = (
    "meta__market_cap",
    "meta__float_market_cap",
    "valuation__market_cap",
    "valuation__float_market_cap",
)


@dataclass
class TwoStageConfig:
    market: str = "A"
    horizon: str = "5d"
    target_version: str = "v3.1"
    feature_version: str = FEATURE_SCHEMA_VERSION
    gate_models: list[str] = field(default_factory=lambda: ["logistic", "lightgbm"])
    rank_models: list[str] = field(
        default_factory=lambda: ["ridge", "lightgbm_regression", "lightgbm_ranker"]
    )
    feature_sets: dict[str, list[str]] = field(default_factory=lambda: {
        "technical": ["technical"],
        "research_v2": [
            "technical", "fundamental", "news", "industry", "valuation", "market",
        ],
    })
    excluded_features: list[str] = field(default_factory=list)
    train_days: int = 730
    validation_days: int = 120
    test_days: int = 120
    purge_days: int = 7
    lockbox_days: int = 180
    min_train_samples: int = 3000
    min_validation_samples: int = 400
    min_test_samples: int = 400
    min_unique_train_dates: int = 80
    min_actionable_train_samples: int = 300
    gate_calibration_methods: list[str] = field(
        default_factory=lambda: ["identity", "sigmoid", "isotonic"]
    )
    gate_threshold_candidates: list[float] = field(
        default_factory=lambda: [0.40, 0.45, 0.50, 0.55, 0.60]
    )
    min_validation_actionable_coverage: float = 0.01
    max_validation_actionable_coverage: float = 0.40
    conformal_alpha: float = 0.20
    cross_sectional_standardize: bool = True
    neutralize_industry: bool = True
    neutralize_risk_exposures: bool = True
    require_size_exposure_for_promotion: bool = True
    require_feature_incremental_for_promotion: bool = True
    min_feature_brier_delta: float = 0.0
    min_feature_rank_ic_delta: float = 0.005
    min_feature_top_k_delta_pct: float = 0.05
    top_k: int = 10
    min_brier_delta: float = 0.002
    min_rank_ic: float = 0.0
    min_actionable_coverage: float = 0.01
    max_top_industry_concentration: float = 0.40
    bootstrap_iterations: int = 500
    bootstrap_block_size: int = 4
    bootstrap_seed: int = 20260716
    save_models: bool = True
    unlock_lockbox: bool = False
    research_family: str = "quant_two_stage_edge"


@dataclass
class BinaryCalibrationArtifact:
    method: str = "identity"
    validation_samples: int = 0
    raw_brier: float = 0.0
    calibrated_brier: float = 0.0
    brier_delta: float = 0.0


class BinaryProbabilityCalibrator:
    """Select identity, sigmoid, or isotonic calibration using validation only."""

    def __init__(self, methods: list[str]):
        self.methods = list(dict.fromkeys(methods or ["identity"]))
        self.model = None
        self.artifact = BinaryCalibrationArtifact()

    def fit(self, raw: np.ndarray, actual: np.ndarray) -> "BinaryProbabilityCalibrator":
        raw = np.clip(np.asarray(raw, dtype=float), 1e-6, 1 - 1e-6)
        actual = np.asarray(actual, dtype=int)
        raw_brier = float(np.mean((raw - actual) ** 2))
        best_loss = raw_brier
        best_method = "identity"
        best_model = None
        if len(raw) >= 30 and len(set(actual.tolist())) == 2:
            if "sigmoid" in self.methods:
                from sklearn.linear_model import LogisticRegression

                model = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
                model.fit(_logit(raw).reshape(-1, 1), actual)
                calibrated = model.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]
                loss = float(np.mean((calibrated - actual) ** 2))
                if loss < best_loss - 1e-6:
                    best_loss, best_method, best_model = loss, "sigmoid", model
            if "isotonic" in self.methods and len(np.unique(raw)) >= 10:
                from sklearn.isotonic import IsotonicRegression

                model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
                model.fit(raw, actual)
                calibrated = model.predict(raw)
                loss = float(np.mean((calibrated - actual) ** 2))
                if loss < best_loss - 1e-6:
                    best_loss, best_method, best_model = loss, "isotonic", model
        self.model = best_model
        self.artifact = BinaryCalibrationArtifact(
            method=best_method,
            validation_samples=len(raw),
            raw_brier=round(raw_brier, 8),
            calibrated_brier=round(best_loss, 8),
            brier_delta=round(raw_brier - best_loss, 8),
        )
        return self

    def transform(self, raw: np.ndarray) -> np.ndarray:
        raw = np.clip(np.asarray(raw, dtype=float), 1e-6, 1 - 1e-6)
        if self.artifact.method == "sigmoid":
            return self.model.predict_proba(_logit(raw).reshape(-1, 1))[:, 1]
        if self.artifact.method == "isotonic":
            return np.asarray(self.model.predict(raw), dtype=float)
        return raw.copy()


@dataclass
class RankBundle:
    kind: str
    estimator: Any
    slope: float = 1.0
    intercept: float = 0.0

    def predict(self, x) -> np.ndarray:
        values = np.asarray(self.estimator.predict(x), dtype=float)
        return values * self.slope + self.intercept


@dataclass
class TwoStageFold:
    fold: int
    train_range: list[str]
    validation_range: list[str]
    test_range: list[str]
    train_samples: int
    actionable_train_samples: int
    validation_samples: int
    test_samples: int
    variants: dict[str, Any]


@dataclass
class TwoStageReport:
    generated_at: str
    config: dict[str, Any]
    data_summary: dict[str, Any]
    folds: list[TwoStageFold]
    aggregate_metrics: dict[str, Any]
    ablation: dict[str, Any]
    risk_controls: dict[str, Any]
    promotion_gate: dict[str, Any]
    lockbox: dict[str, Any]
    trial_id: str
    artifact_paths: dict[str, str]
    skipped: list[dict[str, Any]]
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TwoStageQuantEvaluator:
    def __init__(
        self,
        store: Optional[QuantFeatureStore] = None,
        ledger: Optional[ExperimentLedger] = None,
    ):
        self.store = store or QuantFeatureStore()
        self.ledger = ledger

    def run(
        self,
        config: Optional[TwoStageConfig] = None,
        output_dir: Optional[str | Path] = None,
    ) -> TwoStageReport:
        started = time.monotonic()
        config = config or TwoStageConfig()
        rows = self.store.rows(
            market=config.market,
            horizon=config.horizon,
            target_version=config.target_version,
            feature_version=config.feature_version,
            labeled_only=True,
            limit=1_000_000,
        )
        if not rows:
            raise ValueError(f"{config.market}/{config.horizon} 没有两阶段模型可用样本")
        rows = sorted(rows, key=lambda row: (row["as_of"], row["symbol"]))
        max_date = date.fromisoformat(rows[-1]["as_of"][:10])
        lockbox_start = max_date - timedelta(days=max(0, config.lockbox_days))
        development = [
            row for row in rows
            if date.fromisoformat(row["as_of"][:10]) < lockbox_start
        ]
        lockbox_rows = [
            row for row in rows
            if date.fromisoformat(row["as_of"][:10]) >= lockbox_start
        ]
        fold_config = WalkForwardConfig(
            train_days=config.train_days,
            validation_days=config.validation_days,
            test_days=config.test_days,
            purge_days=config.purge_days,
            min_train_samples=config.min_train_samples,
            min_validation_samples=config.min_validation_samples,
            min_test_samples=config.min_test_samples,
            min_unique_train_dates=config.min_unique_train_dates,
        )
        fold_specs = QuantWalkForwardEvaluator._fold_specs(development, fold_config)
        if not fold_specs:
            raise ValueError("两阶段模型无法形成满足门槛的 Walk-forward fold")

        trial_id = datetime.now().strftime("quant_two_stage_%Y%m%d_%H%M%S_%f")
        root = Path(output_dir) if output_dir else Path("output") / "quant_two_stage" / trial_id
        root.mkdir(parents=True, exist_ok=True)
        source_type = detect_experiment_source()
        ledger = self.ledger
        if ledger is None:
            ledger = (
                ExperimentLedger(root.parent / ".experiment_ledger.db")
                if source_type == "test" or os.getenv("PYTEST_CURRENT_TEST")
                else ExperimentLedger.default()
            )
        prior_trials = ledger.prior_trial_count(
            research_family=config.research_family,
            market=config.market,
            horizon=config.horizon,
            target_version=config.target_version,
        )
        risk_controls = _risk_control_coverage(
            rows,
            cross_sectional_standardization=config.cross_sectional_standardize,
        )
        folds: list[TwoStageFold] = []
        skipped: list[dict[str, Any]] = []
        artifact_paths: dict[str, str] = {}
        aggregate_inputs: dict[str, dict[str, list]] = {}
        prior_rows: list[dict[str, Any]] = []
        prior_predictions: list[QuantPrediction] = []

        for fold_index, (train, validation, test) in enumerate(fold_specs, 1):
            variants: dict[str, Any] = {}
            fold_prior = _empirical_prior_prediction(train)
            prior_rows.extend(test)
            prior_predictions.extend([fold_prior] * len(test))
            for feature_name, families in config.feature_sets.items():
                prepared = _prepare_feature_splits(
                    train, validation, test, families,
                    standardize=config.cross_sectional_standardize,
                    excluded_features=config.excluded_features,
                )
                vectorizer, x_train, x_validation, x_test = prepared
                y_gate_train = _actionable_labels(train)
                y_gate_validation = _actionable_labels(validation)
                gate_prior = float(y_gate_train.mean())
                actionable_indices = np.flatnonzero(y_gate_train == 1)
                if len(actionable_indices) < config.min_actionable_train_samples:
                    skipped.append({
                        "fold": fold_index,
                        "feature_set": feature_name,
                        "reason": f"有边际训练样本不足: {len(actionable_indices)}",
                    })
                    continue
                for gate_kind in config.gate_models:
                    try:
                        gate = _fit_gate(gate_kind, x_train, y_gate_train)
                        raw_gate_validation = _gate_probability(gate, x_validation)
                        calibrator = BinaryProbabilityCalibrator(
                            config.gate_calibration_methods,
                        ).fit(raw_gate_validation, y_gate_validation)
                        gate_validation = calibrator.transform(raw_gate_validation)
                        gate_test = calibrator.transform(_gate_probability(gate, x_test))
                    except Exception as exc:
                        skipped.append({
                            "fold": fold_index, "gate": gate_kind,
                            "feature_set": feature_name, "reason": str(exc),
                        })
                        continue
                    for rank_kind in config.rank_models:
                        key = f"gate_{gate_kind}__rank_{rank_kind}__{feature_name}"
                        try:
                            rank = _fit_rank(
                                rank_kind,
                                x_train[actionable_indices],
                                [train[index] for index in actionable_indices],
                            )
                            one_stage_rank = _fit_rank(rank_kind, x_train, train)
                            validation_raw_return = rank.predict(x_validation)
                            test_raw_return = rank.predict(x_test)
                            validation_one_stage_return = one_stage_rank.predict(x_validation)
                            test_one_stage_return = one_stage_rank.predict(x_test)
                            residual_std = _residual_std(
                                rank.predict(x_train[actionable_indices]),
                                np.asarray([
                                    float(train[index]["label_return_pct"])
                                    for index in actionable_indices
                                ]),
                            )
                            validation_controlled_return = _neutralize_predictions(
                                validation, validation_raw_return,
                                industry=config.neutralize_industry,
                                risk=config.neutralize_risk_exposures,
                            )
                            test_controlled_return = _neutralize_predictions(
                                test, test_raw_return,
                                industry=config.neutralize_industry,
                                risk=config.neutralize_risk_exposures,
                            )
                            threshold = _select_gate_threshold(
                                validation,
                                gate_validation,
                                validation_controlled_return,
                                residual_std,
                                config,
                            )
                            interval_radius = _conformal_radius(
                                validation,
                                validation_controlled_return,
                                alpha=config.conformal_alpha,
                            )
                            validation_predictions = _compose_predictions(
                                validation_controlled_return, gate_validation,
                                gate_threshold=threshold, residual_std=residual_std,
                                interval_radius=interval_radius,
                            )
                            test_predictions = _compose_predictions(
                                test_controlled_return, gate_test,
                                gate_threshold=threshold, residual_std=residual_std,
                                interval_radius=interval_radius,
                            )
                            one_stage_test = _one_stage_predictions(
                                test_one_stage_return, residual_std,
                            )
                            raw_test_predictions = _compose_predictions(
                                test_raw_return, gate_test,
                                gate_threshold=threshold, residual_std=residual_std,
                                interval_radius=interval_radius,
                            )
                            validation_metrics = _two_stage_metrics(
                                validation, validation_predictions, gate_validation,
                                threshold, config.top_k, gate_prior=gate_prior,
                                bootstrap_iterations=config.bootstrap_iterations,
                                bootstrap_block_size=config.bootstrap_block_size,
                                bootstrap_seed=config.bootstrap_seed,
                            )
                            test_metrics = _two_stage_metrics(
                                test, test_predictions, gate_test,
                                threshold, config.top_k, gate_prior=gate_prior,
                                bootstrap_iterations=config.bootstrap_iterations,
                                bootstrap_block_size=config.bootstrap_block_size,
                                bootstrap_seed=config.bootstrap_seed,
                            )
                            one_stage_metrics = _two_stage_metrics(
                                test, one_stage_test,
                                np.ones(len(test)), 0.0, config.top_k,
                                gate_prior=gate_prior,
                                bootstrap_iterations=config.bootstrap_iterations,
                                bootstrap_block_size=config.bootstrap_block_size,
                                bootstrap_seed=config.bootstrap_seed,
                            )
                            raw_metrics = _two_stage_metrics(
                                test, raw_test_predictions, gate_test,
                                threshold, config.top_k, gate_prior=gate_prior,
                                bootstrap_iterations=config.bootstrap_iterations,
                                bootstrap_block_size=config.bootstrap_block_size,
                                bootstrap_seed=config.bootstrap_seed,
                            )
                            variants[key] = {
                                "feature_set": feature_name,
                                "gate_model": gate_kind,
                                "rank_model": rank_kind,
                                "gate_calibration": asdict(calibrator.artifact),
                                "gate_threshold": threshold,
                                "conformal_radius_pct": round(interval_radius, 6),
                                "validation": validation_metrics,
                                "test": test_metrics,
                                "ablation": {
                                    "one_stage": one_stage_metrics,
                                    "two_stage_uncontrolled": raw_metrics,
                                    "two_stage_controlled": test_metrics,
                                },
                            }
                            bucket = aggregate_inputs.setdefault(key, {
                                "rows": [], "predictions": [], "gate": [],
                                "one_stage": [], "raw": [], "thresholds": [],
                                "gate_prior": [],
                            })
                            bucket["rows"].extend(test)
                            bucket["predictions"].extend(test_predictions)
                            bucket["gate"].extend(gate_test.tolist())
                            bucket["one_stage"].extend(one_stage_test)
                            bucket["raw"].extend(raw_test_predictions)
                            bucket["thresholds"].extend([threshold] * len(test))
                            bucket["gate_prior"].extend([gate_prior] * len(test))
                            oof_path = root / "oof" / f"fold_{fold_index}_{key}.jsonl"
                            _write_oof(oof_path, test, test_predictions, gate_test, key, fold_index)
                            artifact_paths[f"fold_{fold_index}_{key}_oof"] = str(oof_path)
                            if config.save_models:
                                model_path = root / "models" / f"fold_{fold_index}" / key / "bundle.joblib"
                                model_path.parent.mkdir(parents=True, exist_ok=True)
                                import joblib

                                joblib.dump({
                                    "vectorizer": vectorizer,
                                    "gate": gate,
                                    "gate_calibrator": calibrator,
                                    "rank": rank,
                                    "gate_threshold": threshold,
                                    "conformal_radius_pct": interval_radius,
                                    "feature_families": families,
                                    "excluded_features": config.excluded_features,
                                }, model_path)
                                artifact_paths[f"fold_{fold_index}_{key}_model"] = str(model_path)
                        except Exception as exc:
                            skipped.append({
                                "fold": fold_index, "model": key, "reason": str(exc),
                            })
            folds.append(TwoStageFold(
                fold=fold_index,
                train_range=[train[0]["as_of"], train[-1]["as_of"]],
                validation_range=[validation[0]["as_of"], validation[-1]["as_of"]],
                test_range=[test[0]["as_of"], test[-1]["as_of"]],
                train_samples=len(train),
                actionable_train_samples=int(_actionable_labels(train).sum()),
                validation_samples=len(validation),
                test_samples=len(test),
                variants=variants,
            ))

        aggregate = {
            key: _aggregate_variant(bucket, config)
            for key, bucket in aggregate_inputs.items()
        }
        if not aggregate:
            raise ValueError("两阶段模型没有完成任何可用候选")
        prior_metrics = _empirical_prior_metrics(prior_rows, prior_predictions)
        aggregate["empirical_prior"] = prior_metrics
        ablation = _aggregate_ablation(aggregate)
        promotion = _promotion_gate(
            aggregate,
            fold_count=len(folds),
            risk_controls=risk_controls,
            prior_trials=prior_trials,
            config=config,
        )
        dataset_hash = _dataset_hash(rows)
        report = TwoStageReport(
            generated_at=datetime.now().isoformat(),
            config=asdict(config),
            data_summary={
                "data_version": "research_data.v2",
                "feature_version": config.feature_version,
                "total_rows": len(rows),
                "development_rows": len(development),
                "unique_dates": len({row["as_of"] for row in rows}),
                "unique_symbols": len({row["symbol"] for row in rows}),
                "date_range": [rows[0]["as_of"], rows[-1]["as_of"]],
                "dataset_hash": dataset_hash,
            },
            folds=folds,
            aggregate_metrics=aggregate,
            ablation=ablation,
            risk_controls=risk_controls,
            promotion_gate=promotion,
            lockbox={
                "status": "unlocked" if config.unlock_lockbox else "locked",
                "start_date": lockbox_start.isoformat(),
                "samples": len(lockbox_rows),
                "unique_dates": len({row["as_of"] for row in lockbox_rows}),
                "note": "Phase 1 默认不读取 lockbox",
            },
            trial_id=trial_id,
            artifact_paths=artifact_paths,
            skipped=skipped,
            elapsed_seconds=time.monotonic() - started,
        )
        report_path = root / "two_stage_report.json"
        report.artifact_paths["report"] = str(report_path)
        ledger.append(ExperimentTrial(
            trial_id=trial_id,
            research_family=config.research_family,
            market=config.market,
            horizon=config.horizon,
            target_version=config.target_version,
            feature_version=config.feature_version,
            dataset_hash=dataset_hash,
            config_hash=ExperimentLedger.config_hash(asdict(config)),
            source_type=source_type,
            report_path=str(report_path),
            best_model=str(promotion.get("best_model") or ""),
            should_promote=bool(promotion.get("should_promote")),
            candidates=sorted(key for key in aggregate if key != "empirical_prior"),
            thresholds={
                "min_brier_delta": config.min_brier_delta,
                "gate_threshold_candidates": config.gate_threshold_candidates,
            },
            metrics={"promotion_gate": promotion},
        ))
        promotion["global_trial_count_after"] = prior_trials + 1
        report.artifact_paths["manifest"] = write_experiment_manifest(
            root,
            experiment_id=root.name,
            kind="quant_two_stage",
            source_type=source_type,
            config=asdict(config),
            dataset_hash=dataset_hash,
            artifacts=report.artifact_paths,
            metrics={
                "folds": len(folds),
                "best_model": promotion.get("best_model"),
                "should_promote": promotion.get("should_promote", False),
            },
            project_root=Path(__file__).resolve().parents[2],
        )
        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return report


def _prepare_feature_splits(
    train, validation, test, families, *, standardize: bool,
    excluded_features: Optional[list[str]] = None,
):
    from sklearn.feature_extraction import DictVectorizer

    prepared = []
    for rows in (train, validation, test):
        values = _cross_sectional_standardize(rows) if standardize else rows
        prepared.append([
            {
                key: value for key, value in _model_features(row, families).items()
                if key not in set(excluded_features or [])
            }
            for row in values
        ])
    vectorizer = DictVectorizer(sparse=True)
    x_train = vectorizer.fit_transform(prepared[0])
    return vectorizer, x_train, vectorizer.transform(prepared[1]), vectorizer.transform(prepared[2])


def _cross_sectional_standardize(rows: list[dict]) -> list[dict]:
    result: list[dict] = []
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(str(row["as_of"]), []).append(row)
    for group in groups.values():
        numeric_keys = sorted({
            key for row in group for key, value in (row.get("features") or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        })
        statistics = {}
        for key in numeric_keys:
            values = np.asarray([
                float(row["features"][key])
                for row in group if row.get("features", {}).get(key) is not None
            ])
            if len(values) >= 3:
                median = float(np.median(values))
                mad = float(np.median(np.abs(values - median))) * 1.4826
                scale = mad if mad > 1e-9 else max(float(values.std()), 1e-9)
                statistics[key] = (median, scale)
        for row in group:
            copied = dict(row)
            features = dict(row.get("features") or {})
            for key, (mean, std) in statistics.items():
                if features.get(key) is not None:
                    features[key] = float(np.clip(
                        (float(features[key]) - mean) / std, -5.0, 5.0,
                    ))
            copied["features"] = features
            result.append(copied)
    return sorted(result, key=lambda row: (row["as_of"], row["symbol"]))


def _actionable_labels(rows: list[dict]) -> np.ndarray:
    return np.asarray([
        int(str(row.get("label_direction")) in {"bullish", "bearish"})
        for row in rows
    ], dtype=int)


def _fit_gate(kind: str, x, y: np.ndarray):
    if len(set(y.tolist())) < 2:
        raise ValueError("gate 训练至少需要有边际和无边际两个类别")
    if kind == "logistic":
        from sklearn.linear_model import LogisticRegression

        fit_x = x.copy()
        if hasattr(fit_x, "indices"):
            fit_x.indices = fit_x.indices.astype(np.int32, copy=False)
            fit_x.indptr = fit_x.indptr.astype(np.int32, copy=False)
        return LogisticRegression(
            C=0.25, class_weight="balanced", max_iter=5000,
            solver="liblinear", random_state=42,
        ).fit(fit_x, y)
    if kind == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            objective="binary", n_estimators=250, learning_rate=0.03,
            num_leaves=15, max_depth=5, min_child_samples=max(20, len(y) // 100),
            subsample=0.8, colsample_bytree=0.8, reg_alpha=0.5,
            reg_lambda=2.0, class_weight="balanced", random_state=42,
            n_jobs=1, verbosity=-1,
        ).fit(x, y)
    raise ValueError(f"未知 gate 模型: {kind}")


def _gate_probability(model, x) -> np.ndarray:
    return np.asarray(model.predict_proba(x)[:, 1], dtype=float)


def _fit_rank(kind: str, x, rows: list[dict]) -> RankBundle:
    y = np.asarray([float(row["label_return_pct"]) for row in rows])
    if kind == "ridge":
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        estimator = make_pipeline(
            StandardScaler(with_mean=False), Ridge(alpha=10.0),
        ).fit(x, y)
        return RankBundle(kind, estimator)
    if kind == "lightgbm_regression":
        from lightgbm import LGBMRegressor

        estimator = LGBMRegressor(
            objective="huber", n_estimators=250, learning_rate=0.03,
            num_leaves=15, max_depth=5, min_child_samples=max(20, len(y) // 100),
            subsample=0.8, colsample_bytree=0.8, reg_alpha=0.5,
            reg_lambda=2.0, random_state=42, n_jobs=1, verbosity=-1,
        ).fit(x, y)
        return RankBundle(kind, estimator)
    if kind == "lightgbm_ranker":
        from lightgbm import LGBMRanker

        frame = pd.DataFrame({
            "date": [row["as_of"] for row in rows],
            "return": y,
        })
        relevance = _ranking_relevance(frame)
        groups = frame.groupby("date", sort=False).size().tolist()
        estimator = LGBMRanker(
            objective="lambdarank", n_estimators=200, learning_rate=0.03,
            num_leaves=15, max_depth=5, min_child_samples=max(10, len(y) // 150),
            reg_alpha=0.5, reg_lambda=2.0, random_state=42,
            n_jobs=1, verbosity=-1,
        ).fit(x, relevance, group=groups)
        score = np.asarray(estimator.predict(x), dtype=float)
        slope, intercept = _linear_scale(score, y)
        return RankBundle(kind, estimator, slope, intercept)
    raise ValueError(f"未知收益排序模型: {kind}")


def _ranking_relevance(frame: pd.DataFrame, levels: int = 5) -> np.ndarray:
    """Map each date's returns to bounded ordinal labels accepted by LambdaRank."""
    levels = max(2, min(int(levels), 31))
    percentile = frame.groupby("date")["return"].rank(
        pct=True,
        method="average",
    )
    return np.minimum(
        levels - 1,
        np.floor(percentile.to_numpy(dtype=float) * levels).astype(int),
    )


def _linear_scale(score: np.ndarray, actual: np.ndarray) -> tuple[float, float]:
    if len(score) < 3 or float(np.std(score)) < 1e-9:
        return 0.0, float(np.mean(actual))
    slope, intercept = np.polyfit(score, actual, 1)
    return float(slope), float(intercept)


def _residual_std(predicted: np.ndarray, actual: np.ndarray) -> float:
    return max(0.25, float(np.std(np.asarray(actual) - np.asarray(predicted))))


def _select_gate_threshold(rows, probabilities, expected, residual_std, config) -> float:
    candidates = sorted(set(float(value) for value in config.gate_threshold_candidates))
    scored = []
    for threshold in candidates:
        predictions = _compose_predictions(
            expected, probabilities, gate_threshold=threshold,
            residual_std=residual_std, interval_radius=residual_std * 1.2816,
        )
        metrics = _two_stage_metrics(
            rows, predictions, probabilities, threshold, config.top_k,
            bootstrap_iterations=config.bootstrap_iterations,
            bootstrap_block_size=config.bootstrap_block_size,
            bootstrap_seed=config.bootstrap_seed,
        )
        coverage = metrics["actionable_coverage"]
        eligible = (
            config.min_validation_actionable_coverage
            <= coverage
            <= config.max_validation_actionable_coverage
        )
        scored.append((
            0 if eligible else 1,
            -metrics["top_k_mean_return_pct"],
            metrics["gate_brier"],
            abs(coverage - max(config.min_validation_actionable_coverage, 0.05)),
            threshold,
        ))
    return float(min(scored)[-1])


def _conformal_radius(rows, expected: np.ndarray, *, alpha: float) -> float:
    actual = np.asarray([float(row["label_return_pct"]) for row in rows])
    residual = np.abs(actual - np.asarray(expected, dtype=float))
    if not len(residual):
        return 1.5
    level = min(0.99, max(0.50, 1.0 - float(alpha)))
    return max(0.25, float(np.quantile(residual, level, method="higher")))


def _neutralize_predictions(rows, predicted, *, industry: bool, risk: bool) -> np.ndarray:
    values = np.asarray(predicted, dtype=float).copy()
    by_date: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_date.setdefault(str(row["as_of"]), []).append(index)
    for indices in by_date.values():
        if len(indices) < 5:
            values[indices] -= float(np.mean(values[indices]))
            continue
        design = [np.ones(len(indices))]
        features = [rows[index].get("features") or {} for index in indices]
        if industry:
            industries = [str(item.get("meta__industry") or "unknown") for item in features]
            for name in sorted(set(industries))[1:]:
                design.append(np.asarray([float(value == name) for value in industries]))
        if risk:
            for key, log_scale in (
                ("meta__avg_traded_value_20d", True),
                ("meta__daily_volatility_pct", False),
                (_available_size_key(features), True),
            ):
                if not key:
                    continue
                column = np.asarray([float(item.get(key) or 0.0) for item in features])
                if log_scale:
                    column = np.log1p(np.maximum(column, 0.0))
                if float(np.std(column)) > 1e-9:
                    column = (column - column.mean()) / column.std()
                    design.append(column)
        matrix = np.column_stack(design)
        if matrix.shape[1] >= len(indices) - 1:
            values[indices] -= float(np.mean(values[indices]))
            continue
        beta, *_ = np.linalg.lstsq(matrix, values[indices], rcond=None)
        values[indices] = values[indices] - matrix @ beta
    return values


def _compose_predictions(
    expected: np.ndarray,
    gate_probability: np.ndarray,
    *,
    gate_threshold: float,
    residual_std: float,
    interval_radius: float,
) -> list[QuantPrediction]:
    result = []
    sigma = max(0.25, float(residual_std))
    for value, gate in zip(expected, gate_probability):
        gate = min(1.0, max(0.0, float(gate)))
        up_share = _normal_cdf(float(value) / sigma)
        up = gate * up_share
        down = gate * (1.0 - up_share)
        no_edge = 1.0 - gate
        direction = (
            "neutral" if gate < gate_threshold
            else "bullish" if value >= 0 else "bearish"
        )
        result.append(QuantPrediction(
            expected_return_pct=round(float(value), 4),
            prob_down=round(down, 6),
            prob_no_edge=round(no_edge, 6),
            prob_up=round(max(0.0, 1.0 - down - no_edge), 6),
            direction=direction,
            expected_return_p10=round(float(value) - interval_radius, 4),
            expected_return_p50=round(float(value), 4),
            expected_return_p90=round(float(value) + interval_radius, 4),
        ))
    return result


def _one_stage_predictions(expected: np.ndarray, residual_std: float) -> list[QuantPrediction]:
    gate = np.ones(len(expected), dtype=float)
    return _compose_predictions(
        expected, gate, gate_threshold=0.0, residual_std=residual_std,
        interval_radius=max(0.25, residual_std * 1.2816),
    )


def _two_stage_metrics(
    rows,
    predictions,
    gate,
    threshold,
    top_k,
    *,
    gate_prior: Any = None,
    bootstrap_iterations: int = 500,
    bootstrap_block_size: int = 4,
    bootstrap_seed: int = 42,
) -> dict[str, Any]:
    metrics = evaluate_predictions(rows, predictions)
    actual_gate = _actionable_labels(rows)
    gate = np.asarray(gate, dtype=float)
    gate_brier_values = (gate - actual_gate) ** 2
    if gate_prior is None:
        gate_prior = float(actual_gate.mean()) if len(actual_gate) else 0.0
    gate_prior_values = (np.asarray(gate_prior, dtype=float) - actual_gate) ** 2
    top = _top_k_metrics(
        rows,
        predictions,
        gate,
        threshold,
        top_k,
        bootstrap_iterations=bootstrap_iterations,
        bootstrap_block_size=bootstrap_block_size,
        bootstrap_seed=bootstrap_seed,
    )
    metrics.update({
        "gate_brier": round(float(gate_brier_values.mean()), 6),
        "gate_prior_brier": round(float(gate_prior_values.mean()), 6),
        "gate_brier_delta": round(float(gate_prior_values.mean() - gate_brier_values.mean()), 6),
        "gate_ece": round(_binary_ece(gate, actual_gate), 6),
        **top,
    })
    return metrics


def _top_k_metrics(
    rows,
    predictions,
    gate,
    threshold,
    top_k,
    *,
    bootstrap_iterations: int,
    bootstrap_block_size: int,
    bootstrap_seed: int,
) -> dict[str, Any]:
    by_date: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_date.setdefault(str(row["as_of"]), []).append(index)
    returns = []
    selections = 0
    industry_counts: dict[str, int] = {}
    for indices in by_date.values():
        eligible = [
            index for index in indices
            if gate[index] >= threshold and predictions[index].expected_return_pct > 0
        ]
        chosen = sorted(
            eligible,
            key=lambda index: predictions[index].expected_return_pct,
            reverse=True,
        )[:max(1, int(top_k))]
        if not chosen:
            continue
        selections += len(chosen)
        returns.append(float(np.mean([
            float(rows[index]["label_return_pct"]) for index in chosen
        ])))
        for index in chosen:
            industry_name = str((rows[index].get("features") or {}).get("meta__industry") or "unknown")
            industry_counts[industry_name] = industry_counts.get(industry_name, 0) + 1
    concentration = (
        max(industry_counts.values()) / selections if selections and industry_counts else 0.0
    )
    ci = _moving_block_bootstrap_mean_ci(
        returns,
        iterations=bootstrap_iterations,
        block_size=bootstrap_block_size,
        seed=bootstrap_seed,
    )
    return {
        "top_k_dates": len(returns),
        "top_k_selections": selections,
        "top_k_mean_return_pct": round(float(np.mean(returns)) if returns else 0.0, 6),
        "top_k_return_ci_low_pct": round(ci[0], 6),
        "top_k_return_ci_high_pct": round(ci[1], 6),
        "top_industry_concentration": round(float(concentration), 6),
    }


def _aggregate_variant(bucket: dict[str, list], config: TwoStageConfig) -> dict[str, Any]:
    rows = bucket["rows"]
    predictions = bucket["predictions"]
    gate = np.asarray(bucket["gate"], dtype=float)
    threshold = float(np.median(bucket["thresholds"]))
    gate_prior = np.asarray(bucket["gate_prior"], dtype=float)
    full = _two_stage_metrics(
        rows, predictions, gate, threshold, config.top_k, gate_prior=gate_prior,
        bootstrap_iterations=config.bootstrap_iterations,
        bootstrap_block_size=config.bootstrap_block_size,
        bootstrap_seed=config.bootstrap_seed,
    )
    one_stage = _two_stage_metrics(
        rows, bucket["one_stage"], np.ones(len(rows)), 0.0, config.top_k,
        gate_prior=gate_prior,
        bootstrap_iterations=config.bootstrap_iterations,
        bootstrap_block_size=config.bootstrap_block_size,
        bootstrap_seed=config.bootstrap_seed,
    )
    raw = _two_stage_metrics(
        rows, bucket["raw"], gate, threshold, config.top_k,
        gate_prior=gate_prior,
        bootstrap_iterations=config.bootstrap_iterations,
        bootstrap_block_size=config.bootstrap_block_size,
        bootstrap_seed=config.bootstrap_seed,
    )
    actual_gate = _actionable_labels(rows)
    deltas = (gate_prior - actual_gate) ** 2 - (gate - actual_gate) ** 2
    gate_ci = _moving_block_bootstrap_mean_ci(
        deltas.tolist(), iterations=config.bootstrap_iterations,
        block_size=config.bootstrap_block_size, seed=config.bootstrap_seed,
    )
    full.update({
        "gate_threshold_median": round(threshold, 6),
        "gate_brier_delta_ci_low": round(gate_ci[0], 6),
        "gate_brier_delta_ci_high": round(gate_ci[1], 6),
        "ablation": {
            "one_stage": one_stage,
            "two_stage_uncontrolled": raw,
            "two_stage_controlled": full.copy(),
            "brier_delta_two_stage_vs_one_stage": round(
                one_stage["brier_score"] - full["brier_score"], 6,
            ),
            "rank_ic_delta_controlled_vs_raw": round(
                full["rank_ic"] - raw["rank_ic"], 6,
            ),
        },
    })
    return full


def _empirical_prior_prediction(train: list[dict[str, Any]]) -> QuantPrediction:
    counts = {label: 1 for label in ("bearish", "neutral", "bullish")}
    for row in train:
        label = str(row["label_direction"])
        counts[label] = counts.get(label, 0) + 1
    total = sum(counts.values())
    return QuantPrediction(
        expected_return_pct=float(np.mean([row["label_return_pct"] for row in train])),
        prob_down=counts["bearish"] / total,
        prob_no_edge=counts["neutral"] / total,
        prob_up=counts["bullish"] / total,
        direction=max(counts, key=counts.get),
    )


def _empirical_prior_metrics(
    rows: list[dict[str, Any]],
    predictions: list[QuantPrediction],
) -> dict[str, Any]:
    if not rows or len(rows) != len(predictions):
        raise ValueError("折内经验先验与 OOF 测试样本不一致")
    return evaluate_predictions(rows, predictions)


def _aggregate_ablation(aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get("ablation", {})
        for key, value in aggregate.items()
        if key != "empirical_prior"
    }


def _promotion_gate(aggregate, fold_count, risk_controls, prior_trials, config):
    prior = aggregate["empirical_prior"]
    candidates = {key: value for key, value in aggregate.items() if key != "empirical_prior"}
    best_name, best = min(
        candidates.items(),
        key=lambda item: (item[1]["brier_score"], -item[1]["rank_ic"]),
    )
    penalty = math.sqrt(max(1.0, math.log2(prior_trials + 2)))
    required_delta = config.min_brier_delta * penalty
    brier_delta = prior["brier_score"] - best["brier_score"]
    incremental = _feature_incremental_gate(best_name, best, candidates, config)
    checks = {
        "multiple_folds": fold_count >= 3,
        "multiclass_brier_better_than_prior": brier_delta >= required_delta,
        "gate_brier_bootstrap_positive": best["gate_brier_delta_ci_low"] > 0,
        "rank_ic_ci_positive": best["rank_ic_ci_low"] > config.min_rank_ic,
        "actionable_coverage": best["actionable_coverage"] >= config.min_actionable_coverage,
        "top_k_return_ci_positive": best["top_k_return_ci_low_pct"] > 0,
        "industry_concentration": (
            best["top_industry_concentration"] <= config.max_top_industry_concentration
        ),
        "conformal_coverage": 0.60 <= best["interval_80_coverage"] <= 0.95,
        "size_exposure_available": (
            risk_controls["size_exposure_coverage"] > 0
            or not config.require_size_exposure_for_promotion
        ),
        "feature_incremental_oof": (
            incremental["passed"] or not config.require_feature_incremental_for_promotion
        ),
    }
    passed = all(checks.values())
    return {
        "should_promote": passed,
        "shadow_only": not passed,
        "best_model": best_name,
        "checks": checks,
        "prior_trials": prior_trials,
        "trial_penalty": round(penalty, 6),
        "brier_delta": round(brier_delta, 6),
        "required_brier_delta": round(required_delta, 6),
        "feature_incremental": incremental,
        "reason": (
            "两阶段模型通过 development 门禁；仍需组合和一次性 lockbox"
            if passed else "两阶段模型保持 shadow，未同时通过概率、排序、收益和风险暴露门禁"
        ),
    }


def _feature_incremental_gate(best_name, best, candidates, config) -> dict[str, Any]:
    parts = best_name.split("__")
    feature_set = parts[-1] if parts else ""
    if feature_set == "technical":
        return {
            "passed": False,
            "baseline": best_name,
            "candidate": best_name,
            "reason": "最佳候选仍是纯技术面，Phase 2 特征没有形成增量。",
        }
    baseline_name = "__".join([*parts[:-1], "technical"])
    baseline = candidates.get(baseline_name)
    if not baseline:
        return {
            "passed": False, "baseline": baseline_name, "candidate": best_name,
            "reason": "缺少同一 gate/rank 模型的 technical OOF 基线。",
        }
    brier_delta = float(baseline["brier_score"]) - float(best["brier_score"])
    rank_delta = float(best["rank_ic"]) - float(baseline["rank_ic"])
    top_k_delta = float(best["top_k_mean_return_pct"]) - float(baseline["top_k_mean_return_pct"])
    checks = {
        "brier_non_degrading": brier_delta >= config.min_feature_brier_delta,
        "rank_or_return_incremental": (
            rank_delta >= config.min_feature_rank_ic_delta
            or top_k_delta >= config.min_feature_top_k_delta_pct
        ),
    }
    return {
        "passed": all(checks.values()),
        "baseline": baseline_name,
        "candidate": best_name,
        "checks": checks,
        "brier_delta": round(brier_delta, 6),
        "rank_ic_delta": round(rank_delta, 6),
        "top_k_return_delta_pct": round(top_k_delta, 6),
        "reason": "新增特征必须在同模型 OOF 上不损害概率，并改善排序或 Top-K 收益。",
    }


def _risk_control_coverage(
    rows: list[dict],
    *,
    cross_sectional_standardization: bool,
) -> dict[str, Any]:
    total = max(1, len(rows))
    features = [row.get("features") or {} for row in rows]
    size_key = _available_size_key(features)
    return {
        "cross_sectional_standardization": bool(cross_sectional_standardization),
        "industry_coverage": round(sum(bool(item.get("meta__industry")) for item in features) / total, 6),
        "volatility_coverage": round(sum(item.get("meta__daily_volatility_pct") is not None for item in features) / total, 6),
        "liquidity_coverage": round(sum(item.get("meta__avg_traded_value_20d") is not None for item in features) / total, 6),
        "size_feature": size_key or None,
        "size_exposure_coverage": round(
            (sum(item.get(size_key) is not None for item in features) / total)
            if size_key else 0.0,
            6,
        ),
        "size_note": (
            "使用严格 PIT 市值暴露" if size_key
            else "当前特征表没有严格 PIT 市值，未使用成交额冒充规模；晋升门禁保持关闭"
        ),
    }


def _available_size_key(features: list[dict]) -> Optional[str]:
    for key in SIZE_FEATURE_CANDIDATES:
        if any(item.get(key) is not None for item in features):
            return key
    return None


def _write_oof(path, rows, predictions, gate, model, fold):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row, prediction, probability in zip(rows, predictions, gate):
            payload = {
                "feature_id": row["feature_id"],
                "market": row["market"],
                "symbol": row["symbol"],
                "as_of": row["as_of"],
                "model": model,
                "fold": fold,
                "gate_probability": round(float(probability), 6),
                "actual_direction": row["label_direction"],
                "actual_return_pct": row["label_return_pct"],
                "actual_absolute_return_pct": row.get("label_absolute_return_pct"),
                "actual_benchmark_return_pct": row.get("label_benchmark_return_pct"),
                "daily_volatility_pct": (row.get("features") or {}).get("meta__daily_volatility_pct"),
                "avg_traded_value_20d": (row.get("features") or {}).get("meta__avg_traded_value_20d"),
                "industry": (row.get("features") or {}).get("meta__industry"),
                **prediction.to_dict(),
            }
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _moving_block_bootstrap_mean_ci(values, *, iterations, block_size, seed):
    values = np.asarray(values, dtype=float)
    if not len(values):
        return 0.0, 0.0
    if len(values) < 3:
        mean = float(values.mean())
        return mean, mean
    rng = np.random.default_rng(seed)
    block = max(1, min(int(block_size), len(values)))
    starts = np.arange(0, len(values) - block + 1)
    estimates = []
    for _ in range(max(100, int(iterations))):
        sample = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[start:start + block])
        estimates.append(float(np.mean(sample[:len(values)])))
    return float(np.quantile(estimates, 0.025)), float(np.quantile(estimates, 0.975))


def _binary_ece(probabilities, actual, bins=10):
    probabilities = np.asarray(probabilities, dtype=float)
    actual = np.asarray(actual, dtype=float)
    value = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        mask = (probabilities >= lower) & (
            probabilities <= upper if index == bins - 1 else probabilities < upper
        )
        if mask.any():
            value += float(mask.mean()) * abs(
                float(probabilities[mask].mean()) - float(actual[mask].mean())
            )
    return value


def _logit(values):
    values = np.clip(values, 1e-6, 1 - 1e-6)
    return np.log(values / (1.0 - values))


def _normal_cdf(value):
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _dataset_hash(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row["feature_id"]).encode("utf-8"))
        digest.update(str(row.get("updated_at") or "").encode("utf-8"))
    return digest.hexdigest()[:20]


def two_stage_dependency_status() -> dict[str, Any]:
    return dependency_status()
