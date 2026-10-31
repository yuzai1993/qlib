from __future__ import annotations

import copy
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backtest.scripts import build_strategy_group_reports as groups
from backtest.scripts import build_phase_m_v1_bt_report as hub


def _fixture(tmp_path):
    records = []
    for identity, ann, label in (("baseline/bt-v2", .23, "原对照"), ("trial/f100", .35, "F100")):
        directory = f"backtest/experiments/strategies/variants/{identity.split('/')[-1]}"
        source = f"{directory}/metrics/result.json"
        report = tmp_path / directory / "reports/original.html"
        report.parent.mkdir(parents=True)
        report.write_text("frozen report bytes")
        (tmp_path / source).parent.mkdir(parents=True)
        (tmp_path / source).write_text("{}")
        records.append({"id": identity, "kind": "strategy", "track": "all_a", "name": label,
                        "directory": directory, "source": source, "seeds": [42, 1000, 2000, 3000, 4000],
                        "metrics": {"annualized_return": ann, "sharpe_ratio": ann * 3,
                                    "max_drawdown": -.3},
                        "window": {"start": "2020-08-03", "end": "2026-07-31"},
                        "resources": [{"id": identity, "kind": "report", "available": True}]})
    group = {"id": "strategy-groups/test", "kind": "strategy", "track": "all_a",
             "name": "F100 <研究>", "directory": "backtest/experiments/strategies/topk/test",
             "model_summary": {"version": "v2", "name": "Top5早停"}, "metrics": {},
             "analysis": {"conclusion": "年化提高 <仍需验证>", "limitations": "历史选型窗"},
             "group_summary": {"control_ref": records[0]["id"], "control_label": "原对照",
                               "member_refs": [r["id"] for r in records], "variant_count": 2,
                               "axes": ["force_sell_rank"], "fixed_summary": "100万 & 历史费率"},
             "core_results": {"kind": "strategy_parameter_comparison", "status": "available",
                              "records": [{**r, "label": r["name"]} for r in records]}}
    refs = {r["id"]: tmp_path / r["directory"] / "reports/original.html" for r in records}
    return records, group, refs


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.sources = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "a":
            self.links.append(attrs["href"])
        if tag == "tr" and "data-source-id" in attrs:
            self.sources.append(attrs["data-source-id"])


def test_group_report_keeps_baseline_and_each_frozen_variant_without_aggregation(tmp_path):
    records, group, refs = _fixture(tmp_path)
    frozen = copy.deepcopy(records)
    # The declared control remains authoritative even if caller order changes.
    group["core_results"]["records"].reverse()
    output = tmp_path / group["directory"] / "reports/summary.html"
    html = groups.render_group(group, records, repo_root=tmp_path, output_path=output,
                               resource_paths=refs)
    parsed = _Tags()
    parsed.feed(html)
    assert parsed.sources == ["baseline/bt-v2", "trial/f100"]
    assert "+23.0%" in html and "+35.0%" in html
    assert "+29.0%" not in html
    assert "Baseline · 原对照" in html
    assert "2020-08-03～2026-07-31" in html
    assert "42, 1000, 2000, 3000, 4000" in html
    assert "F100 &lt;研究&gt;" in html and "100万 &amp; 历史费率" in html
    assert "<仍需验证>" not in html
    assert records == frozen
    for link in parsed.links:
        assert (output.parent / unquote(link)).resolve().is_file(), link


def test_group_report_rejects_forged_metric_or_unknown_source(tmp_path):
    records, group, refs = _fixture(tmp_path)
    group["core_results"]["records"][1]["metrics"] = {"annualized_return": .29}
    with pytest.raises(ValueError, match="frozen|authoritative|来源|指标"):
        groups.render_group(group, records, repo_root=tmp_path, resource_paths=refs)


def test_hub_folds_parameter_groups_and_retains_standalone_benchmark(tmp_path):
    records, group, _ = _fixture(tmp_path)
    raw = [{"exp_id": r["id"], "display_name": r["name"], "doc": {"ensemble": {"full_period": r["metrics"]}}}
           for r in records]
    snapshot = {"records": records, "experiment_records": [group, records[0]]}
    folded = hub.fold_strategy_experiments(raw, snapshot)
    assert len(folded) == 2
    html = hub.render_hub([], folded, output_path=tmp_path / "hub.html")
    assert "summary.html" in html
    assert "2个变体" in html
    assert "F100 &lt;研究&gt;" in html
    assert "+29.0%" not in html
    assert "原对照" in html


def test_write_all_preserves_existing_historical_report_bytes(tmp_path, monkeypatch):
    source = tmp_path / "original.html"
    source.write_text("historical report must stay byte exact")
    item = {"exp_id": "baseline/test", "bt_version": "v1", "display_name": "基线",
            "doc": {"ensemble": {"full_period": {}, "portable_report": "original.html",
                                    "session_dir": "unused"}}}
    experiment = {"exp_id": "trial/test", "display_name": "实验", "baseline_ref": "baseline/test",
                  "detail_report": "original.html", "doc": item["doc"]}
    monkeypatch.setattr(hub, "EXP_ROOT", tmp_path)
    monkeypatch.setattr(hub, "HUB_OUT", tmp_path / "hub.html")
    monkeypatch.setattr(hub, "load_registry", lambda: [])
    monkeypatch.setattr(hub, "catalog_from_registry", lambda _: ([item], [experiment]))
    hub.write_all()
    assert source.read_text() == "historical report must stay byte exact"


def test_standalone_initial_benchmark_links_its_own_original_report():
    item = {"exp_id": "baseline/first", "bt_version": "v1", "display_name": "最初基准",
            "detail_report": "backtest/experiments/reports/phase_m_v1_bt_report.html",
            "doc": {"ensemble": {"portable_report": "backtest/experiments/strategies/first/reports/original.html"}}}
    projection = {"id": item["exp_id"], "kind": "strategy", "comparison_baseline": {"initial": True}}
    folded = hub.fold_strategy_experiments([item], {"experiment_records": [projection]})
    assert folded[0]["detail_report"].endswith("first/reports/original.html")
    assert "无前版" in folded[0]["baseline_name"]


def test_strategy_benchmark_separates_strategy_number_model_and_rule():
    baselines = [{"exp_id": "baseline/bt-v5", "bt_version": "v5", "display_name": "旧名称",
                  "current": True, "model_summary": {"version": "v2", "name": "Top5早停"},
                  "strategy_summary": {"name": "Top5换1·持1·F100"},
                  "doc": {"ensemble": {"full_period": {}, "session_dir": "unused"}}}]
    frozen = copy.deepcopy(baselines)
    html = hub.render_hub(baselines, [])
    assert "<th class='l'>策略版本</th><th class='l'>模型</th><th class='l'>策略</th>" in html
    assert "s5 · 当前" in html
    assert "v2 · Top5早停" in html and "Top5换1·持1·F100" in html
    assert "当前对照锚点是 <b>s5 · v2 · Top5早停 · Top5换1·持1·F100</b>" in html
    assert "s5 · v2 · Top5早停 · Top5换1·持1·F100</td>" in html
    assert "BT v5" not in html
    assert baselines == frozen


def test_group_summary_labels_strategy_benchmark_but_keeps_model_version(tmp_path):
    records, group, refs = _fixture(tmp_path)
    records[0].update(version="v2", display_version="s2")
    records[1].update(version="v5", display_version="s5")
    html = groups.render_group(group, records, repo_root=tmp_path, resource_paths=refs)
    assert "<th>策略版本</th>" in html
    assert ">s2</td>" in html and ">s5</td>" in html
    assert "Baseline · s2 · 原对照" in html
    assert "实验组 · s5 · F100" in html
    assert "v2 · Top5早停" in html
    assert records[0]["version"] == "v2" and records[1]["version"] == "v5"


def test_group_editorial_strategy_references_change_only_explicit_bt_versions(tmp_path):
    records, group, refs = _fixture(tmp_path)
    group["analysis"]["conclusion"] = "BT v5优于BT v4；模型v2仍需验证。"
    html = groups.render_group(group, records, repo_root=tmp_path, resource_paths=refs)
    assert "s5优于s4；模型v2仍需验证。" in html
    assert group["analysis"]["conclusion"] == "BT v5优于BT v4；模型v2仍需验证。"
