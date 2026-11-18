import hashlib
import json

from backtest.scripts.maintenance.experiment_archive import EXPERIMENT_TYPES
from backtest.scripts.register.experiment_registry import append_registry
from quant_platform.research import ResearchCatalog, SEEDS, WINDOW


def test_style_experts_preserve_horizons_and_decile_metrics(tmp_path):
    eid = "style-experts/monthly"
    archive = tmp_path / "backtest/experiments/models" / eid
    (archive / "metrics").mkdir(parents=True)
    (archive / "reports").mkdir()
    report = archive / "reports/detail.html"
    report.write_text("<p>自然月专家</p>")
    source = archive / "metrics/comparison.json"
    metrics = dict(net_ann=.4, net_ann_vol=.3, net_sharpe=1.3, ann=.42, turnover=.1,
                   rank_ic_mean=.08, rank_ic_grade=.11, cagr=.41, max_drawdown=-.3)
    doc = dict(style_expert_protocol="natural-month-h20-oracle-v1", seeds=SEEDS,
               window=WINDOW, selected="oracle", rows=[
                   dict(id="oracle", name="专家 H20／H7", metrics=metrics),
                   dict(id="v5-h5", name="v5 H5", metrics={**metrics, "rank_ic_grade": .09}),
               ])
    source.write_text(json.dumps(doc))
    row = dict(exp_id=eid, date="2026-11-18", state="completed", phase="M", phase_m_protocol="v1",
               seeds=SEEDS, style_expert_protocol=doc["style_expert_protocol"],
               baseline_ref="training-target/h20-quantile10-p50-v5",
               portable_eval_output=str(source.relative_to(tmp_path)),
               portable_eval_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
               detail_report=str(report.relative_to(tmp_path)))
    append_registry(tmp_path / "backtest/experiments/registry.jsonl", row)
    catalog = ResearchCatalog(tmp_path)
    record = catalog.snapshot()["records"][0]
    assert EXPERIMENT_TYPES["style_experts"] == "风格专家"
    assert record["official"] and not record["comparable"]
    assert not record["is_baseline"] and not record["current"]
    assert record["metrics"]["rank_ic_grade"] == .11
    assert record["core_results"]["records"][1]["metrics"]["rank_ic_grade"] == .09
    assert record["core_results"]["kind"] == "style_experts"
    assert "H20／H7" in record["protocol"]
    assert any(d["key"] == "rank_ic_grade" for d in record["metric_definitions"])
    doc["rows"][0]["metrics"]["rank_ic_grade"] = .99
    source.write_text(json.dumps(doc))
    altered = catalog.snapshot()["records"][0]
    assert not altered["official"]
    assert altered["source_status"] == "invalid"
