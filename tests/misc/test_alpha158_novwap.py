"""Alpha158NoVWAP 特征配置测试（纯配置层，不依赖 qlib 数据）。"""


def test_novwap_feature_config_has_157_features_without_vwap0():
    from qlib.contrib.data.handler import Alpha158NoVWAP

    fields, names = Alpha158NoVWAP.get_feature_config(Alpha158NoVWAP)
    assert len(names) == len(fields) == 157
    assert "VWAP0" not in names
    # 其余价格相对值特征仍在
    for kept in ("OPEN0", "HIGH0", "LOW0", "KMID", "ROC5", "VSUMD60"):
        assert kept in names
