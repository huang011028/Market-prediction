import json

from scripts.run_quant_two_stage import run


def test_two_stage_config_is_frozen_and_lockbox_stays_closed():
    config = json.loads(open("config/quant/two_stage_v1.json", encoding="utf-8").read())

    assert config["version"] == "quant_two_stage.v1"
    assert config["two_stage"]["unlock_lockbox"] is False
    assert config["two_stage"]["require_size_exposure_for_promotion"] is True
    assert config["portfolio"]["production_policy"]["edge_threshold"] == 0.05
    assert config["portfolio"]["production_policy"]["selection_source"].startswith("pre_registered")
