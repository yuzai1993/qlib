from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from quant_platform import __main__ as cli


def test_cli_defaults_to_original_project_and_research_diagnostics_archive():
    args = cli.build_parser().parse_args([])
    assert args.project_root == Path(__file__).resolve().parents[2]
    assert args.live_config == "alla_v2_top5d1f100_postclose_real_active"
    assert args.data_root is None
    assert args.port == 8090
    assert not hasattr(args, "monitor_url")


def test_cli_accepts_an_explicit_historical_live_configuration():
    args = cli.build_parser().parse_args(
        ["--live-config", "alla_v4_ladder_k1h5_postclose_real"]
    )
    assert args.live_config == "alla_v4_ladder_k1h5_postclose_real"


@pytest.mark.parametrize(
    ("custom_project", "explicit_research"),
    [(False, False), (True, False), (True, True)],
)
@pytest.mark.parametrize("custom_data", [False, True])
def test_main_uses_project_root_for_research_unless_explicitly_overridden(
    tmp_path, monkeypatch, custom_project, explicit_research, custom_data
):
    default_project = tmp_path / "default-project"
    project = tmp_path / "custom-project" if custom_project else default_project
    configs = project / "live_trading/configs"
    configs.mkdir(parents=True)
    (configs / "alla_v2_top5d1f100_postclose_real_active.yaml").write_text(
        "live: {}\n", encoding="utf-8"
    )
    research = tmp_path / "research" if explicit_research else project
    research.mkdir(exist_ok=True)
    arguments = ["quant_platform"]
    if custom_data:
        arguments.extend(["--data-root", str(tmp_path / "archive")])
    if custom_project:
        arguments.extend(["--project-root", str(project)])
    if explicit_research:
        arguments.extend(["--research-root", str(research)])
    monkeypatch.setattr(cli, "__file__", str(default_project / "quant_platform/__main__.py"))
    monkeypatch.setattr("sys.argv", arguments)
    monkeypatch.setattr(
        cli, "socket", SimpleNamespace(socket=lambda: nullcontext(SimpleNamespace(bind=lambda address: None)))
    )

    health_responses = []

    def serve(app, **kwargs):
        with TestClient(app, base_url="http://127.0.0.1:8090") as client:
            health_responses.append(client.get("/api/health").json())

    monkeypatch.setattr(uvicorn, "run", serve)
    cli.main()

    assert len(health_responses) == 1
    assert health_responses[0]["research_root"] == str(research)
    assert health_responses[0]["analysis_storage"] == str(tmp_path / "archive" if custom_data else research / "backtest/diagnostics")
