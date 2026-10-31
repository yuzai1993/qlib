"""Generated reports link from their actual directory after asset relocation."""

from html.parser import HTMLParser
import importlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backtest/scripts"))


class Links(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.hrefs, self.sources = [], []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and "href" in values:
            self.hrefs.append(values["href"])
        if tag == "iframe" and "src" in values:
            self.sources.append(values["src"])


def test_model_hub_links_to_experiment_directory_and_keeps_metrics(tmp_path, monkeypatch):
    report = importlib.import_module("build_phase_m_v1_report")
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    output = tmp_path / "backtest/experiments/reports/phase_m_v1_report.html"
    detail = "backtest/experiments/group/model/reports/detail.html"
    doc = {"pools": {"all": {"ensemble": {"head": {"3": {"5": {"net_ann": 0.125}}}}}}}
    rendered = report.render_hub(
        [{"version": "v1", "name": "model", "doc": doc, "detail_report": detail}],
        [],
        output_path=output,
    )
    href = "../group/model/reports/detail.html"
    assert href in Links(rendered).hrefs
    assert (output.parent / href).resolve() == (tmp_path / detail).resolve()
    assert "+12.5%" in rendered


def test_model_hub_navigation_resolves_to_shared_reports_from_archive_directory(tmp_path, monkeypatch):
    report = importlib.import_module("build_phase_m_v1_report")
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    output = tmp_path / "backtest/experiments/models/group/arm/reports/hub.html"
    shared = tmp_path / "backtest/experiments/reports"
    shared.mkdir(parents=True)
    expected = {shared / "phase_m_v1_bt_report.html"}
    for target in expected:
        target.write_text("shared report")

    rendered = report.render_hub([], [], output_path=output)

    targets = {(output.parent / href).resolve() for href in Links(rendered).hrefs}
    assert expected <= targets
    assert all(target.is_file() for target in targets)
    assert shared / "report.html" not in targets


def test_backtest_detail_resolves_hub_reports_and_figures_from_its_directory(tmp_path, monkeypatch):
    report = importlib.import_module("build_phase_m_v1_bt_report")
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    hub = tmp_path / "backtest/experiments/reports/phase_m_v1_bt_report.html"
    monkeypatch.setattr(report, "HUB_OUT", hub)
    output = tmp_path / "backtest/experiments/group/experiment/reports/detail.html"

    def item(name):
        return {
            "display_name": name,
            "bt_version": "v1",
            "doc": {"ensemble": {
                "session_dir": f"backtest/result/{name}",
                "full_period": {"annualized_return": 0.125},
                "years": {}, "regimes": {},
                "figures": {"report_graph": ["nav.html"]},
            }},
        }

    rendered = report.render_experiment(item("experiment"), item("baseline"), output_path=output)
    links = Links(rendered)
    targets = {(output.parent / href).resolve() for href in links.hrefs}
    assert hub.resolve() in targets
    for name in ("experiment", "baseline"):
        assert (tmp_path / f"backtest/result/{name}/run_01/report.html").resolve() in targets
        assert (tmp_path / f"backtest/result/{name}/run_01/figures/nav.html").resolve() in {
            (output.parent / source).resolve() for source in links.sources
        }
    assert "+12.5%" in rendered


def test_portable_snapshot_links_to_metrics_in_data_directory(tmp_path, monkeypatch):
    report = importlib.import_module("build_phase_m_v1_bt_report")
    monkeypatch.setattr(report, "EXP_ROOT", tmp_path)
    output = tmp_path / "backtest/experiments/group/arm/reports/nav.html"
    metrics = "backtest/experiments/group/arm/data/metrics.json"
    item = {
        "display_name": "snapshot",
        "portable_eval_output": metrics,
        "doc": {"ensemble": {
            "portable_report": str(output.relative_to(tmp_path)),
            "full_period": {"annualized_return": 0.125},
            "nav_curve": [["2020-01-01", 1.1, 1.0]],
            "years": {}, "regimes": {},
        }},
    }
    rendered = report.render_snapshot(item, output_path=output)
    targets = {(output.parent / href).resolve() for href in Links(rendered).hrefs}
    assert (tmp_path / metrics).resolve() in targets
    assert "+12.5%" in rendered
