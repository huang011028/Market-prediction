import json

from scripts.bootstrap_experiment_ledger import bootstrap
from src.core.experiment_ledger import ExperimentLedger


def test_bootstrap_imports_legacy_trials_once(tmp_path):
    source = tmp_path / "output" / "run" / "trial_ledger.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({
        "trial_id": "legacy-1",
        "generated_at": "2026-07-01T10:00:00",
        "market": "A",
        "horizon": "5d",
        "models": ["ridge", "lightgbm"],
        "dataset_hash": "data-1",
        "config_hash": "config-1",
        "best_model": "ridge",
        "should_promote": False,
        "report": "report.json",
    }) + "\n", encoding="utf-8")
    ledger = ExperimentLedger(tmp_path / "ledger.db")

    first = bootstrap(tmp_path / "output", ledger)
    second = bootstrap(tmp_path / "output", ledger)

    assert first["imported"] == 1
    assert second["skipped"] == 1
    assert ledger.status()["total_trials"] == 1
