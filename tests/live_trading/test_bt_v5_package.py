"""Integrity and deployment-boundary checks for the prepared BT v5 package."""

import copy
import hashlib
import json
import pickle
from pathlib import Path

import pytest
import yaml

from live_trading.modules.backtest_parity import (
    ParityError,
    validate_configured_backtest,
    validate_backtest_parity,
)
from live_trading.modules.live_config import load_live_config

ROOT = Path(__file__).resolve().parents[2]
LIVE = ROOT / "live_trading/configs/alla_v2_top5d1f100_postclose_real.yaml"


def test_deployment_package_passes_parity_and_is_offline_by_default():
    config = load_live_config(LIVE)
    validate_configured_backtest(config, ROOT)
    assert config["live"]["bridge_root"] == "live_trading/data/bt_v5_offline_bridge"
    assert config["monitor"]["notify"]["channel"] == "none"
    assert (
        config["storage"]["db_path"]
        != "live_trading/data/alla_v4_ladder_k1h5_postclose_real.db"
    )


@pytest.mark.parametrize(
    "field,value",
    [("force_sell_rank", None), ("risk_degree", 1.0), ("initial_buy_count", 1)],
)
def test_execution_critical_drift_rejected(field, value):
    config = load_live_config(LIVE)
    backtest = yaml.safe_load((ROOT / config["parity"]["backtest_config"]).read_text())
    changed = copy.deepcopy(config)
    changed["strategy"][field] = value
    with pytest.raises(ParityError):
        validate_backtest_parity(changed, backtest)


def test_exported_v2_models_match_manifest_and_configuration():
    config = load_live_config(LIVE)
    manifest = json.loads(
        (ROOT / "live_trading/models/v2_t5h5es/manifest.json").read_text()
    )
    assert [m["seed"] for m in manifest["members"]] == [42, 1000, 2000, 3000, 4000]
    assert [m["best_iteration"] for m in manifest["members"]] == [36, 63, 42, 57, 48]
    for member, configured in zip(manifest["members"], config["model"]["members"]):
        payload = (ROOT / member["model_path"]).read_bytes()
        assert (
            hashlib.sha256(payload).hexdigest()
            == member["sha256"]
            == configured["sha256"]
        )
        assert member["model_path"] == configured["model_path"]
        model = pickle.loads(payload)
        assert model.es_metric == "top5_h5_net_ann"
        assert model.model.best_iteration == member["best_iteration"]
        assert model.model.num_feature() == member["num_features"]
        assert model.tradable_mask is None
