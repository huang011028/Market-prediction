"""Purged walk-forward validation for statistical stock baselines."""

from __future__ import annotations

import json
import hashlib
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.core.quant_models import (
    LABELS,
    QuantModelUnavailable,
    QuantPrediction,
    create_quant_model,
    dependency_status,
)
from src.core.quant_calibration import MulticlassProbabilityCalibrator
from src.core.experiment_manifest import detect_experiment_source, write_experiment_manifest
from src.core.experiment_ledger import ExperimentLedger, trial_from_report
from src.core.quant_stacking import ConstrainedIndustryStacker
from src.data.quant_feature_store import FEATURE_SCHEMA_VERSION, QuantFeatureStore


@dataclass
class WalkForwardConfig:
    market: str = "A"
    horizon: str = "5d"
    target_version: str = "v3.1"
    feature_version: str = FEATURE_SCHEMA_VERSION
    model_names: list[str] = field(default_factory=lambda: ["ridge", "logistic", "lightgbm"])
    train_days: int = 365
    validation_days: int = 90
    test_days: int = 90
    purge_days: int = 7
    lockbox_days: int = 90
    min_train_samples: int = 200
    min_validation_samples: int = 30
    min_test_samples: int = 30
    min_unique_train_dates: int = 60
    unlock_lockbox: bool = False
    save_models: bool = True
    min_brier_delta: float = 0.002
    bootstrap_iterations: int = 500
    feature_set_names: list[str] = field(default_factory=lambda: ["all"])
    calibrate_probabilities: bool = True
    calibration_method: str = "temperature"
    calibration_min_samples: int = 100
    enable_industry_stacking: bool = True
    max_industry_stack_weight: float = 0.35
    min_industry_stack_brier_delta: float = 0.0
    min_actionable_coverage: float = 0.01
    research_family: str = "quant_directional_edge"


@dataclass
class WalkForwardFold:
    fold: int
    train_range: list[str]
    validation_range: list[str]
    test_range: list[str]
    train_samples: int
    validation_samples: int
    test_samples: int
    models: dict[str, Any]


@dataclass
class WalkForwardReport:
    generated_at: str
    config: dict
    data_summary: dict
    folds: list[WalkForwardFold]
    aggregate_metrics: dict[str, Any]
    promotion_gate: dict[str, Any]
    lockbox: dict[str, Any]
    dependencies: dict[str, Any]
    trial_id: str
    feature_ablation: dict[str, Any] = field(default_factory=dict)
    artifact_paths: dict[str, Any] = field(default_factory=dict)
    skipped: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["folds"] = [asdict(fold) for fold in self.folds]
        return payload


class QuantWalkForwardEvaluator:
    def __init__(
        self,
        store: Optional[QuantFeatureStore] = None,
        ledger: Optional[ExperimentLedger] = None,
    ):
        self.store = store or QuantFeatureStore()
        self.ledger = ledger

    def run(
        self,
        config: Optional[WalkForwardConfig] = None,
        output_dir: Optional[str | Path] = None,
    ) -> WalkForwardReport:
        started = time.monotonic()
        config = config or WalkForwardConfig()
        rows = self.store.rows(
            market=config.market,
            horizon=config.horizon,
            target_version=config.target_version,
            feature_version=config.feature_version,
            labeled_only=True,
            limit=1_000_000,
        )
        if not rows:
            raise ValueError(f"{config.market}/{config.horizon} 没有已标注 PIT 特征样本")

        rows = sorted(rows, key=lambda row: (row["as_of"], row["symbol"]))
        max_date = date.fromisoformat(rows[-1]["as_of"][:10])
        lockbox_start = max_date - timedelta(days=max(0, config.lockbox_days))
        development = [row for row in rows if date.fromisoformat(row["as_of"][:10]) < lockbox_start]
        lockbox_rows = [row for row in rows if date.fromisoformat(row["as_of"][:10]) >= lockbox_start]
        if len(development) < config.min_train_samples + config.min_validation_samples:
            raise ValueError(
                f"开发样本不足: {len(development)}，至少需要 "
                f"{config.min_train_samples + config.min_validation_samples}"
            )

        fold_specs = self._fold_specs(development, config)
        folds: list[WalkForwardFold] = []
        skipped: list[dict] = []
        all_test_metrics: dict[str, list[dict]] = {}
        artifact_paths: dict[str, Any] = {}
        trial_id = datetime.now().strftime("quant_wf_%Y%m%d_%H%M%S_%f")
        root = Path(output_dir) if output_dir else Path("output") / "quant_walk_forward" / trial_id
        root.mkdir(parents=True, exist_ok=True)
        local_ledger_path = root.parent / "trial_ledger.jsonl"
        source_type = detect_experiment_source()
        global_ledger = self.ledger
        if global_ledger is None:
            if source_type == "test" or os.getenv("PYTEST_CURRENT_TEST"):
                global_ledger = ExperimentLedger(root.parent / ".experiment_ledger.db")
            else:
                global_ledger = ExperimentLedger.default()
        prior_trials = global_ledger.prior_trial_count(
            research_family=config.research_family,
            market=config.market,
            horizon=config.horizon,
            target_version=config.target_version,
        )

        for index, (train, validation, test) in enumerate(fold_specs, start=1):
            model_results: dict[str, Any] = {}
            prior_metrics = _evaluate_prior(train, test)
            momentum_metrics = _evaluate_momentum(test)
            model_results["empirical_prior"] = {"test": prior_metrics, "benchmark": True}
            model_results["simple_momentum"] = {"test": momentum_metrics, "benchmark": True}
            all_test_metrics.setdefault("empirical_prior", []).append(prior_metrics)
            all_test_metrics.setdefault("simple_momentum", []).append(momentum_metrics)

            feature_sets = _resolve_feature_sets(config.feature_set_names)
            multiple_feature_sets = len(feature_sets) > 1 or "all" not in feature_sets
            for model_name in config.model_names:
                validation_predictions_by_set: dict[str, list[QuantPrediction]] = {}
                test_predictions_by_set: dict[str, list[QuantPrediction]] = {}
                for feature_set_name, feature_families in feature_sets.items():
                    model_key = (
                        f"{model_name}__{feature_set_name}"
                        if multiple_feature_sets else model_name
                    )
                    try:
                        fitted = create_quant_model(
                            model_name, feature_families=feature_families,
                        ).fit(
                            train,
                            market=config.market,
                            horizon=config.horizon,
                            target_version=config.target_version,
                        )
                        raw_validation_predictions = fitted.predict(validation)
                        calibrator = MulticlassProbabilityCalibrator(
                            min_samples=config.calibration_min_samples,
                            method=(
                                config.calibration_method
                                if config.calibrate_probabilities else "none"
                            ),
                        ).fit(
                            raw_validation_predictions,
                            [str(row["label_direction"]) for row in validation],
                        )
                        validation_predictions = calibrator.transform(raw_validation_predictions)
                        raw_validation_metrics = evaluate_predictions(
                            validation, raw_validation_predictions,
                        )
                        validation_metrics = evaluate_predictions(
                            validation,
                            validation_predictions,
                        )
                        _attach_calibration_metrics(
                            validation_metrics, raw_validation_metrics,
                        )
                        raw_test_predictions = fitted.predict(test)
                        test_predictions = calibrator.transform(raw_test_predictions)
                        raw_test_metrics = evaluate_predictions(test, raw_test_predictions)
                        test_metrics = evaluate_predictions(test, test_predictions)
                        _attach_calibration_metrics(test_metrics, raw_test_metrics)
                        model_results[model_key] = {
                            "raw_validation": raw_validation_metrics,
                            "validation": validation_metrics,
                            "raw_test": raw_test_metrics,
                            "test": test_metrics,
                            "feature_set": feature_set_name,
                            "metadata": fitted.metadata.to_dict() if fitted.metadata else {},
                            "calibration": calibrator.artifact.to_dict(),
                        }
                        all_test_metrics.setdefault(model_key, []).append(test_metrics)
                        validation_predictions_by_set[feature_set_name] = validation_predictions
                        test_predictions_by_set[feature_set_name] = test_predictions
                        if config.save_models:
                            artifact_paths[f"fold_{index}_{model_key}"] = fitted.save(
                                root / "models" / f"fold_{index}" / model_key
                            )
                            artifact_paths[f"fold_{index}_{model_key}_calibration"] = calibrator.save(
                                root / "models" / f"fold_{index}" / model_key / "calibration.json"
                            )
                        oof_path = root / "oof" / f"fold_{index}_{model_key}.jsonl"
                        self._write_oof(
                            oof_path,
                            test,
                            test_predictions,
                            model_key,
                            index,
                        )
                        artifact_paths[f"fold_{index}_{model_key}_oof"] = str(oof_path)
                    except (QuantModelUnavailable, ValueError, RuntimeError) as exc:
                        skipped.append({
                            "fold": index,
                            "model": model_name,
                            "feature_set": feature_set_name,
                            "reason": str(exc),
                        })

                if (
                    config.enable_industry_stacking
                    and "technical" in validation_predictions_by_set
                    and "technical_industry" in validation_predictions_by_set
                ):
                    stack_key = f"{model_name}__technical_industry_stack"
                    try:
                        stacker = ConstrainedIndustryStacker(
                            max_industry_weight=config.max_industry_stack_weight,
                            min_delta=config.min_industry_stack_brier_delta,
                        ).fit(
                            validation_predictions_by_set["technical"],
                            validation_predictions_by_set["technical_industry"],
                            [str(row["label_direction"]) for row in validation],
                        )
                        validation_predictions = stacker.transform(
                            validation_predictions_by_set["technical"],
                            validation_predictions_by_set["technical_industry"],
                        )
                        test_predictions = stacker.transform(
                            test_predictions_by_set["technical"],
                            test_predictions_by_set["technical_industry"],
                        )
                        validation_metrics = evaluate_predictions(validation, validation_predictions)
                        test_metrics = evaluate_predictions(test, test_predictions)
                        validation_metrics["industry_stack_weight"] = stacker.artifact.industry_weight
                        test_metrics["industry_stack_weight"] = stacker.artifact.industry_weight
                        model_results[stack_key] = {
                            "validation": validation_metrics,
                            "test": test_metrics,
                            "feature_set": "technical_industry_stack",
                            "stacking": stacker.artifact.to_dict(),
                        }
                        all_test_metrics.setdefault(stack_key, []).append(test_metrics)
                        stack_path = root / "models" / f"fold_{index}" / stack_key / "stacking.json"
                        artifact_paths[f"fold_{index}_{stack_key}_stacking"] = stacker.save(stack_path)
                        oof_path = root / "oof" / f"fold_{index}_{stack_key}.jsonl"
                        self._write_oof(oof_path, test, test_predictions, stack_key, index)
                        artifact_paths[f"fold_{index}_{stack_key}_oof"] = str(oof_path)
                    except (ValueError, RuntimeError) as exc:
                        skipped.append({
                            "fold": index,
                            "model": model_name,
                            "feature_set": "technical_industry_stack",
                            "reason": str(exc),
                        })

            folds.append(WalkForwardFold(
                fold=index,
                train_range=[train[0]["as_of"], train[-1]["as_of"]],
                validation_range=[validation[0]["as_of"], validation[-1]["as_of"]],
                test_range=[test[0]["as_of"], test[-1]["as_of"]],
                train_samples=len(train),
                validation_samples=len(validation),
                test_samples=len(test),
                models=model_results,
            ))

        aggregate = {
            name: _aggregate_metrics(values)
            for name, values in all_test_metrics.items()
            if values
        }
        feature_ablation = _feature_ablation(aggregate)
        promotion = _promotion_gate(
            aggregate,
            len(folds),
            min_brier_delta=config.min_brier_delta,
            min_actionable_coverage=config.min_actionable_coverage,
            prior_trials=prior_trials,
        )
        lockbox = {
            "status": "unlocked" if config.unlock_lockbox else "locked",
            "start_date": lockbox_start.isoformat(),
            "samples": len(lockbox_rows),
            "unique_dates": len({row["as_of"] for row in lockbox_rows}),
            "note": (
                "lockbox 已授权评估" if config.unlock_lockbox
                else "默认不读取结果；仅在最终模型冻结后显式解锁"
            ),
        }

        if config.unlock_lockbox and promotion.get("best_model") and lockbox_rows:
            best_name = promotion["best_model"]
            try:
                final_train, final_calibration = _final_calibration_split(development, config)
                actual = [str(row["label_direction"]) for row in final_calibration]
                if best_name.endswith("__technical_industry_stack"):
                    base_name = best_name.split("__", 1)[0]
                    final_predictions = {}
                    final_models = {}
                    final_calibrators = {}
                    for feature_set in ("technical", "technical_industry"):
                        model = create_quant_model(
                            base_name, feature_families=FEATURE_SETS[feature_set],
                        ).fit(
                            final_train,
                            market=config.market,
                            horizon=config.horizon,
                            target_version=config.target_version,
                        )
                        calibrator = MulticlassProbabilityCalibrator(
                            min_samples=config.calibration_min_samples,
                            method=config.calibration_method,
                        ).fit(model.predict(final_calibration), actual)
                        final_predictions[feature_set] = calibrator.transform(
                            model.predict(lockbox_rows)
                        )
                        final_models[feature_set] = model
                        final_calibrators[feature_set] = calibrator
                    stacker = ConstrainedIndustryStacker(
                        max_industry_weight=config.max_industry_stack_weight,
                        min_delta=config.min_industry_stack_brier_delta,
                    ).fit(
                        final_calibrators["technical"].transform(
                            final_models["technical"].predict(final_calibration)
                        ),
                        final_calibrators["technical_industry"].transform(
                            final_models["technical_industry"].predict(final_calibration)
                        ),
                        actual,
                    )
                    lockbox_predictions = stacker.transform(
                        final_predictions["technical"],
                        final_predictions["technical_industry"],
                    )
                    if config.save_models:
                        for feature_set, model in final_models.items():
                            key = f"lockbox_{feature_set}_model"
                            artifact_paths[key] = model.save(
                                root / "models" / "final" / best_name / feature_set
                            )
                            artifact_paths[f"lockbox_{feature_set}_calibration"] = (
                                final_calibrators[feature_set].save(
                                    root / "models" / "final" / best_name
                                    / feature_set / "calibration.json"
                                )
                            )
                        artifact_paths["lockbox_stacking"] = stacker.save(
                            root / "models" / "final" / best_name / "stacking.json"
                        )
                else:
                    base_name, final_families = _model_variant(best_name)
                    final_model = create_quant_model(
                        base_name, feature_families=final_families,
                    ).fit(
                        final_train,
                        market=config.market,
                        horizon=config.horizon,
                        target_version=config.target_version,
                    )
                    final_calibrator = MulticlassProbabilityCalibrator(
                        min_samples=config.calibration_min_samples,
                        method=config.calibration_method,
                    ).fit(final_model.predict(final_calibration), actual)
                    lockbox_predictions = final_calibrator.transform(
                        final_model.predict(lockbox_rows)
                    )
                    if config.save_models:
                        artifact_paths["lockbox_final_model"] = final_model.save(
                            root / "models" / "final" / best_name
                        )
                        artifact_paths["lockbox_final_calibration"] = final_calibrator.save(
                            root / "models" / "final" / best_name / "calibration.json"
                        )
                lockbox["calibration_samples"] = len(final_calibration)
                lockbox["metrics"] = evaluate_predictions(lockbox_rows, lockbox_predictions)
            except Exception as exc:
                lockbox["error"] = str(exc)

        dataset_hash = _dataset_hash(rows)
        promotion["research_family"] = config.research_family
        promotion["research_key"] = ExperimentLedger.research_key(
            config.research_family,
            config.market,
            config.horizon,
            config.target_version,
        )
        promotion["global_ledger_path"] = str(global_ledger.db_path)
        promotion["global_trial_count_before"] = prior_trials
        report = WalkForwardReport(
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
            promotion_gate=promotion,
            lockbox=lockbox,
            dependencies=dependency_status(),
            trial_id=trial_id,
            feature_ablation=feature_ablation,
            artifact_paths=artifact_paths,
            skipped=skipped,
            elapsed_seconds=time.monotonic() - started,
        )
        report_path = root / "walk_forward_report.json"
        report.artifact_paths["report"] = str(report_path)
        global_ledger.append(trial_from_report(
            trial_id=trial_id,
            research_family=config.research_family,
            config=asdict(config),
            dataset_hash=dataset_hash,
            report_path=str(report_path),
            source_type=source_type,
            promotion=promotion,
            aggregate_metrics=aggregate,
        ))
        promotion["global_trial_count_after"] = prior_trials + 1
        manifest_path = write_experiment_manifest(
            root,
            experiment_id=root.name,
            kind="quant_walk_forward",
            source_type=source_type,
            config=asdict(config),
            dataset_hash=report.data_summary["dataset_hash"],
            artifacts=report.artifact_paths,
            metrics={
                "folds": len(folds),
                "best_model": promotion.get("best_model"),
                "should_promote": promotion.get("should_promote", False),
            },
            project_root=Path(__file__).resolve().parents[2],
        )
        report.artifact_paths["manifest"] = manifest_path
        report_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._append_trial(local_ledger_path, report, report_path)
        return report

    @staticmethod
    def _fold_specs(rows: list[dict], config: WalkForwardConfig) -> list[tuple[list, list, list]]:
        dates = sorted({date.fromisoformat(row["as_of"][:10]) for row in rows})
        first = dates[0]
        last = dates[-1]
        cursor = first + timedelta(days=config.train_days)
        folds: list[tuple[list, list, list]] = []
        while cursor < last:
            train_start = cursor - timedelta(days=config.train_days)
            train_end = cursor
            validation_start = train_end + timedelta(days=config.purge_days)
            validation_end = validation_start + timedelta(days=config.validation_days)
            test_start = validation_end + timedelta(days=config.purge_days)
            test_end = test_start + timedelta(days=config.test_days)
            train = _rows_between(rows, train_start, train_end, end_inclusive=True)
            validation = _rows_between(rows, validation_start, validation_end)
            test = _rows_between(rows, test_start, test_end)
            if (
                len(train) >= config.min_train_samples
                and len({row["as_of"] for row in train}) >= config.min_unique_train_dates
                and len(validation) >= config.min_validation_samples
                and len(test) >= config.min_test_samples
            ):
                folds.append((train, validation, test))
            cursor += timedelta(days=config.test_days)
        if not folds:
            folds = _fallback_single_fold(rows, config)
        return folds

    @staticmethod
    def _write_oof(path: Path, rows: list[dict], predictions: list[QuantPrediction], model: str, fold: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row, prediction in zip(rows, predictions):
                payload = {
                    "feature_id": row["feature_id"],
                    "market": row["market"],
                    "symbol": row["symbol"],
                    "as_of": row["as_of"],
                    "model": model,
                    "fold": fold,
                    "actual_direction": row["label_direction"],
                    "actual_return_pct": row["label_return_pct"],
                    "actual_absolute_return_pct": row.get("label_absolute_return_pct"),
                    "actual_benchmark_return_pct": row.get("label_benchmark_return_pct"),
                    "daily_volatility_pct": (row.get("features") or {}).get(
                        "technical__daily_volatility_20d_pct"
                    ),
                    "avg_traded_value_20d": (row.get("features") or {}).get(
                        "meta__avg_traded_value_20d"
                    ),
                    "industry": (row.get("features") or {}).get("meta__industry"),
                    **prediction.to_dict(),
                }
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _append_trial(path: Path, report: WalkForwardReport, report_path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "trial_id": report.trial_id,
            "generated_at": report.generated_at,
            "market": report.config["market"],
            "horizon": report.config["horizon"],
            "feature_version": report.config["feature_version"],
            "models": report.config["model_names"],
            "best_model": report.promotion_gate.get("best_model"),
            "should_promote": report.promotion_gate.get("should_promote", False),
            "dataset_hash": report.data_summary.get("dataset_hash"),
            "config_hash": hashlib.sha256(
                json.dumps(report.config, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16],
            "report": str(report_path),
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    @staticmethod
    def _prior_trial_count(
        path: Path,
        market: str,
        horizon: str,
        feature_version: str,
    ) -> int:
        if not path.exists():
            return 0
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                item.get("market") == market
                and item.get("horizon") == horizon
                and item.get("feature_version") == feature_version
            ):
                count += 1
        return count


FEATURE_SETS: dict[str, list[str]] = {
    "technical": ["technical"],
    "technical_fundamental": ["technical", "fundamental"],
    "technical_news": ["technical", "news"],
    "technical_industry": ["technical", "industry"],
    "technical_valuation": ["technical", "valuation"],
    "enriched": ["technical", "fundamental", "news", "industry", "valuation", "market"],
    "research_v2": ["technical", "fundamental", "news", "industry", "valuation", "market"],
    "technical_size": ["technical", "valuation", "style", "market"],
    "technical_cashflow": ["technical", "cashflow", "balance", "valuation", "style", "market"],
    "technical_consensus": ["technical", "consensus", "valuation", "style", "market"],
    "technical_events": ["technical", "news", "style", "market"],
    "technical_industry_v2": ["technical", "industry", "style", "market", "valuation"],
    "phase2_full": [
        "technical", "fundamental", "balance", "cashflow", "consensus", "news",
        "industry", "valuation", "style", "market",
    ],
    "all": ["all"],
}


def _resolve_feature_sets(names: list[str]) -> dict[str, list[str]]:
    resolved: dict[str, list[str]] = {}
    for name in names or ["all"]:
        if name not in FEATURE_SETS:
            raise ValueError(f"未知特征集: {name}")
        resolved[name] = FEATURE_SETS[name]
    return resolved


def _model_variant(model_key: str) -> tuple[str, list[str]]:
    if "__" not in model_key:
        return model_key, ["all"]
    model_name, feature_set = model_key.split("__", 1)
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"未知模型特征集: {feature_set}")
    return model_name, FEATURE_SETS[feature_set]


def _feature_ablation(aggregate: dict[str, dict]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    model_names = sorted({key.split("__", 1)[0] for key in aggregate if "__" in key})
    for model_name in model_names:
        baseline = aggregate.get(f"{model_name}__technical")
        if not baseline:
            continue
        comparisons = {}
        for key, metrics in aggregate.items():
            if not key.startswith(f"{model_name}__") or key.endswith("__technical"):
                continue
            feature_set = key.split("__", 1)[1]
            comparisons[feature_set] = {
                "brier_delta_vs_technical": round(
                    float(baseline.get("brier_score", 0.0))
                    - float(metrics.get("brier_score", 0.0)),
                    6,
                ),
                "rank_ic_delta_vs_technical": round(
                    float(metrics.get("rank_ic", 0.0))
                    - float(baseline.get("rank_ic", 0.0)),
                    6,
                ),
                "directional_return_delta_pct": round(
                    float(metrics.get("avg_directional_return_pct", 0.0))
                    - float(baseline.get("avg_directional_return_pct", 0.0)),
                    6,
                ),
            }
        result[model_name] = {
            "baseline": "technical",
            "comparisons": comparisons,
        }
    return result


def evaluate_predictions(rows: list[dict], predictions: list[QuantPrediction]) -> dict[str, float]:
    if not rows:
        return _empty_metrics()
    actual_return = np.asarray([float(row["label_return_pct"]) for row in rows])
    predicted_return = np.asarray([prediction.expected_return_pct for prediction in predictions])
    actual_direction = [str(row["label_direction"]) for row in rows]
    predicted_direction = [prediction.direction for prediction in predictions]
    brier_values = []
    log_losses = []
    confidences = []
    correctness = []
    for actual, prediction in zip(actual_direction, predictions):
        probs = {
            "bearish": prediction.prob_down,
            "neutral": prediction.prob_no_edge,
            "bullish": prediction.prob_up,
        }
        brier_values.append(sum(
            (probs[label] - (1.0 if label == actual else 0.0)) ** 2
            for label in LABELS
        ) / 3.0)
        actual_probability = max(float(probs.get(actual, 0.0)), 1e-12)
        log_losses.append(-math.log(actual_probability))
        predicted_label = max(probs, key=probs.get)
        confidences.append(float(probs[predicted_label]))
        correctness.append(1.0 if predicted_label == actual else 0.0)
    daily_ics = []
    frame = pd.DataFrame({
        "date": [row["as_of"] for row in rows],
        "actual": actual_return,
        "predicted": predicted_return,
    })
    for _, group in frame.groupby("date"):
        if len(group) >= 3 and group["actual"].nunique() > 1 and group["predicted"].nunique() > 1:
            daily_ics.append(float(group["actual"].corr(group["predicted"], method="spearman")))
    overall_ic = 0.0
    if len(rows) >= 3 and len(set(actual_return)) > 1 and len(set(predicted_return)) > 1:
        overall_ic = float(pd.Series(actual_return).corr(pd.Series(predicted_return), method="spearman"))
    directional_returns = [
        actual if predicted == "bullish" else -actual
        for actual, predicted in zip(actual_return, predicted_direction)
        if predicted in {"bullish", "bearish"}
    ]
    p10 = np.asarray([
        prediction.expected_return_p10
        if prediction.expected_return_p10 is not None else prediction.expected_return_pct
        for prediction in predictions
    ], dtype=float)
    p90 = np.asarray([
        prediction.expected_return_p90
        if prediction.expected_return_p90 is not None else prediction.expected_return_pct
        for prediction in predictions
    ], dtype=float)
    rank_ci = _block_bootstrap_mean_ci(daily_ics)
    return_ci = _block_bootstrap_mean_ci(directional_returns)
    return {
        "samples": len(rows),
        "direction_accuracy": round(sum(a == p for a, p in zip(actual_direction, predicted_direction)) / len(rows), 6),
        "brier_score": round(float(np.mean(brier_values)), 6),
        "log_loss": round(float(np.mean(log_losses)), 6),
        "expected_calibration_error": round(_expected_calibration_error(confidences, correctness), 6),
        "mae_return_pct": round(float(np.mean(np.abs(actual_return - predicted_return))), 6),
        "rank_ic": round(overall_ic if math.isfinite(overall_ic) else 0.0, 6),
        "rank_ic_ci_low": round(rank_ci[0], 6),
        "rank_ic_ci_high": round(rank_ci[1], 6),
        "mean_daily_rank_ic": round(float(np.mean(daily_ics)) if daily_ics else 0.0, 6),
        "positive_daily_ic_rate": round(sum(value > 0 for value in daily_ics) / len(daily_ics), 6) if daily_ics else 0.0,
        "actionable_coverage": round(sum(p in {"bullish", "bearish"} for p in predicted_direction) / len(rows), 6),
        "avg_directional_return_pct": round(float(np.mean(directional_returns)) if directional_returns else 0.0, 6),
        "directional_return_ci_low_pct": round(return_ci[0], 6),
        "directional_return_ci_high_pct": round(return_ci[1], 6),
        "interval_80_coverage": round(float(np.mean((actual_return >= p10) & (actual_return <= p90))), 6),
        "interval_80_avg_width_pct": round(float(np.mean(p90 - p10)), 6),
        "pinball_loss_p10": round(_pinball_loss(actual_return, p10, 0.1), 6),
        "pinball_loss_p90": round(_pinball_loss(actual_return, p90, 0.9), 6),
    }


def _evaluate_prior(train: list[dict], test: list[dict]) -> dict[str, float]:
    counts = {label: 1.0 for label in LABELS}
    returns = {label: [] for label in LABELS}
    for row in train:
        label = str(row["label_direction"])
        counts[label] = counts.get(label, 1.0) + 1.0
        returns[label].append(float(row["label_return_pct"]))
    total = sum(counts.values())
    probs = {key: value / total for key, value in counts.items()}
    means = {key: float(np.mean(value)) if value else 0.0 for key, value in returns.items()}
    prediction = QuantPrediction(
        expected_return_pct=sum(probs[label] * means[label] for label in LABELS),
        prob_down=probs["bearish"],
        prob_no_edge=probs["neutral"],
        prob_up=probs["bullish"],
        direction=max(probs, key=probs.get),
    )
    return evaluate_predictions(test, [prediction] * len(test))


def _evaluate_momentum(test: list[dict]) -> dict[str, float]:
    predictions = []
    for row in test:
        momentum = float((row.get("features") or {}).get("technical__return_5d_pct") or 0.0)
        threshold = max(0.5, float(row.get("label_threshold_pct") or 1.5) * 0.5)
        if momentum >= threshold:
            predictions.append(QuantPrediction(momentum * 0.25, 0.15, 0.25, 0.60, "bullish"))
        elif momentum <= -threshold:
            predictions.append(QuantPrediction(momentum * 0.25, 0.60, 0.25, 0.15, "bearish"))
        else:
            predictions.append(QuantPrediction(0.0, 0.25, 0.50, 0.25, "neutral"))
    return evaluate_predictions(test, predictions)


def _attach_calibration_metrics(calibrated: dict[str, float], raw: dict[str, float]) -> None:
    calibrated["raw_brier_score"] = float(raw.get("brier_score", 0.0))
    calibrated["raw_expected_calibration_error"] = float(
        raw.get("expected_calibration_error", 0.0)
    )
    calibrated["calibration_brier_delta"] = round(
        calibrated["raw_brier_score"] - float(calibrated.get("brier_score", 0.0)), 6
    )
    calibrated["calibration_ece_delta"] = round(
        calibrated["raw_expected_calibration_error"]
        - float(calibrated.get("expected_calibration_error", 0.0)),
        6,
    )


def _aggregate_metrics(metrics: list[dict]) -> dict[str, float]:
    keys = [key for key in _empty_metrics() if key != "samples"]
    return {
        "folds": len(metrics),
        "samples": int(sum(item.get("samples", 0) for item in metrics)),
        **{
            key: round(float(np.mean([item.get(key, 0.0) for item in metrics])), 6)
            for key in keys
        },
    }


def _promotion_gate(
    aggregate: dict[str, dict],
    fold_count: int,
    *,
    min_brier_delta: float = 0.002,
    min_actionable_coverage: float = 0.01,
    prior_trials: int = 0,
) -> dict[str, Any]:
    prior = aggregate.get("empirical_prior") or {}
    candidates = {
        name: metrics
        for name, metrics in aggregate.items()
        if name not in {"empirical_prior", "simple_momentum"}
    }
    if not candidates:
        return {"should_promote": False, "best_model": None, "reason": "没有完成可用模型验证"}
    best_name, best = min(
        candidates.items(),
        key=lambda item: (
            item[1].get("brier_score", 1.0),
            -item[1].get("rank_ic", 0.0),
        ),
    )
    trial_penalty = math.sqrt(max(1.0, math.log2(prior_trials + 2)))
    required_brier_delta = float(min_brier_delta) * trial_penalty
    brier_delta = prior.get("brier_score", 0.0) - best.get("brier_score", 1.0)
    checks = {
        "multiple_folds": fold_count >= 3,
        "brier_better_than_prior": brier_delta >= required_brier_delta,
        "positive_rank_ic_ci": best.get("rank_ic_ci_low", 0.0) > 0,
        "positive_directional_return_ci": best.get("directional_return_ci_low_pct", 0.0) > 0,
        "probability_calibration": best.get("expected_calibration_error", 1.0) <= 0.15,
        "actionable_coverage": best.get("actionable_coverage", 0.0) >= min_actionable_coverage,
        "interval_coverage": 0.60 <= best.get("interval_80_coverage", 0.0) <= 0.95,
        "sufficient_samples": best.get("samples", 0) >= 100,
    }
    passed = all(checks.values())
    return {
        "should_promote": passed,
        "best_model": best_name,
        "shadow_only": not passed,
        "checks": checks,
        "prior_trials": prior_trials,
        "trial_penalty": round(trial_penalty, 6),
        "brier_delta": round(brier_delta, 6),
        "required_brier_delta": round(required_brier_delta, 6),
        "required_actionable_coverage": round(float(min_actionable_coverage), 6),
        "reason": (
            "模型通过多折样本外门禁；仍需最终 lockbox 和组合成本验证"
            if passed else "模型保留为影子基线，尚未同时通过多折、概率、IC 和收益门禁"
        ),
    }


def _rows_between(rows: list[dict], start: date, end: date, end_inclusive: bool = False) -> list[dict]:
    result = []
    for row in rows:
        value = date.fromisoformat(row["as_of"][:10])
        if value < start:
            continue
        if value < end or (end_inclusive and value == end):
            result.append(row)
    return result


def _fallback_single_fold(rows: list[dict], config: WalkForwardConfig) -> list[tuple[list, list, list]]:
    unique_dates = sorted({row["as_of"] for row in rows})
    if len(unique_dates) < 6:
        return []
    train_cut = unique_dates[max(1, int(len(unique_dates) * 0.60)) - 1]
    validation_cut = unique_dates[max(2, int(len(unique_dates) * 0.80)) - 1]
    train = [row for row in rows if row["as_of"] <= train_cut]
    validation = [row for row in rows if train_cut < row["as_of"] <= validation_cut]
    test = [row for row in rows if row["as_of"] > validation_cut]
    if (
        len(train) < config.min_train_samples
        or len(validation) < config.min_validation_samples
        or len(test) < config.min_test_samples
    ):
        return []
    return [(train, validation, test)]


def _final_calibration_split(
    rows: list[dict], config: WalkForwardConfig,
) -> tuple[list[dict], list[dict]]:
    dates = sorted({date.fromisoformat(row["as_of"][:10]) for row in rows})
    if len(dates) < 3:
        raise ValueError("最终模型没有足够日期划分独立校准段")
    calibration_start = dates[-1] - timedelta(days=config.validation_days)
    train_end = calibration_start - timedelta(days=config.purge_days)
    train = [row for row in rows if date.fromisoformat(row["as_of"][:10]) <= train_end]
    calibration = [
        row for row in rows
        if date.fromisoformat(row["as_of"][:10]) >= calibration_start
    ]
    if len(train) < config.min_train_samples or len(calibration) < config.min_validation_samples:
        split_index = max(1, int(len(dates) * 0.8))
        cutoff = dates[min(split_index, len(dates) - 1)]
        train = [row for row in rows if date.fromisoformat(row["as_of"][:10]) < cutoff]
        calibration = [row for row in rows if date.fromisoformat(row["as_of"][:10]) >= cutoff]
    if not train or not calibration:
        raise ValueError("最终模型训练段或校准段为空")
    return train, calibration


def _empty_metrics() -> dict[str, float]:
    return {
        "samples": 0,
        "direction_accuracy": 0.0,
        "brier_score": 0.0,
        "log_loss": 0.0,
        "expected_calibration_error": 0.0,
        "mae_return_pct": 0.0,
        "rank_ic": 0.0,
        "rank_ic_ci_low": 0.0,
        "rank_ic_ci_high": 0.0,
        "mean_daily_rank_ic": 0.0,
        "positive_daily_ic_rate": 0.0,
        "actionable_coverage": 0.0,
        "avg_directional_return_pct": 0.0,
        "directional_return_ci_low_pct": 0.0,
        "directional_return_ci_high_pct": 0.0,
        "interval_80_coverage": 0.0,
        "interval_80_avg_width_pct": 0.0,
        "pinball_loss_p10": 0.0,
        "pinball_loss_p90": 0.0,
        "raw_brier_score": 0.0,
        "raw_expected_calibration_error": 0.0,
        "calibration_brier_delta": 0.0,
        "calibration_ece_delta": 0.0,
        "industry_stack_weight": 0.0,
    }


def _expected_calibration_error(confidences, correctness, bins: int = 10) -> float:
    if not confidences:
        return 0.0
    conf = np.asarray(confidences, dtype=float)
    corr = np.asarray(correctness, dtype=float)
    total = len(conf)
    value = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        mask = (conf >= lower) & (conf <= upper if index == bins - 1 else conf < upper)
        if mask.any():
            value += float(mask.mean()) * abs(float(conf[mask].mean()) - float(corr[mask].mean()))
    return value


def _pinball_loss(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> float:
    error = actual - predicted
    return float(np.mean(np.maximum(quantile * error, (quantile - 1.0) * error)))


def _block_bootstrap_mean_ci(values, iterations: int = 500) -> tuple[float, float]:
    array = np.asarray([value for value in values if math.isfinite(float(value))], dtype=float)
    if len(array) < 2:
        mean = float(array.mean()) if len(array) else 0.0
        return mean, mean
    block = max(1, int(round(len(array) ** 0.5)))
    rng = np.random.default_rng(42)
    means = []
    for _ in range(max(100, int(iterations))):
        sampled = []
        while len(sampled) < len(array):
            start = int(rng.integers(0, len(array)))
            sampled.extend(array[(start + offset) % len(array)] for offset in range(block))
        means.append(float(np.mean(sampled[:len(array)])))
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def _dataset_hash(rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(str(row.get("feature_id") or "").encode("utf-8"))
        digest.update(str(row.get("label_return_pct") or "").encode("utf-8"))
    return digest.hexdigest()[:20]
