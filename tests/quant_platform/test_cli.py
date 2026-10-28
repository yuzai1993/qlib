from pathlib import Path

from quant_platform import __main__ as cli


def test_cli_defaults_to_original_project_and_external_persistent_archive():
    args = cli.build_parser().parse_args([])
    assert args.project_root == Path(__file__).resolve().parents[2]
    assert args.live_config == "alla_v4_ladder_k1h5_postclose_real"
    assert args.data_root == Path.home() / ".qlib/research_platform/analyses"
    assert args.port == 8090
    assert not hasattr(args, "monitor_url")
