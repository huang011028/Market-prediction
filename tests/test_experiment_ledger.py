import sqlite3

import pytest

from src.core.experiment_ledger import ExperimentLedger, ExperimentTrial


def _trial(trial_id: str, *, config_hash: str = "cfg") -> ExperimentTrial:
    return ExperimentTrial(
        trial_id=trial_id,
        research_family="quant_directional_edge",
        market="A",
        horizon="5d",
        target_version="v3.1",
        feature_version="quant_features.v3",
        dataset_hash="dataset-a",
        config_hash=config_hash,
        source_type="test",
        report_path=f"/tmp/{trial_id}.json",
        candidates=["ridge", "lightgbm"],
    )


def test_global_ledger_counts_trials_across_output_directories(tmp_path):
    ledger = ExperimentLedger(tmp_path / "experiment_ledger.db")

    ledger.append(_trial("trial-1"))
    ledger.append(_trial("trial-2", config_hash="cfg-2"))

    assert ledger.prior_trial_count(
        research_family="quant_directional_edge",
        market="A",
        horizon="5d",
        target_version="v3.1",
    ) == 2
    assert ledger.status()["total_trials"] == 2


def test_global_ledger_is_append_only(tmp_path):
    ledger = ExperimentLedger(tmp_path / "experiment_ledger.db")
    ledger.append(_trial("trial-1"))

    with pytest.raises(ValueError, match="拒绝覆盖"):
        ledger.append(_trial("trial-1"))

    with sqlite3.connect(ledger.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE experiment_trials SET status='changed' WHERE trial_id='trial-1'"
            )
