import json

from src.core.experiment_manifest import (
    resolve_experiment_location,
    write_experiment_manifest,
)


def test_pytest_experiments_are_isolated_and_manifested(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test-isolation")
    location = resolve_experiment_location(
        "quant_walk_forward",
        project_root=tmp_path,
        output_root=tmp_path / "output",
        stamp="run001",
    )

    assert location.source_type == "test"
    assert location.root == tmp_path / ".pytest-tmp" / "experiments" / "quant_walk_forward" / "run001"

    path = write_experiment_manifest(
        location.root,
        experiment_id=location.experiment_id,
        kind=location.kind,
        source_type=location.source_type,
        config={"market": "A", "horizon": "5d"},
        dataset_hash="abc123",
        project_root=tmp_path,
    )
    payload = json.loads(open(path, encoding="utf-8").read())
    assert payload["source_type"] == "test"
    assert payload["dataset_hash"] == "abc123"
    assert payload["config_hash"]
