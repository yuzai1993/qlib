import textwrap
from pathlib import Path

from live_trading.scripts.cutover_preflight import cutover_preflight

CONFIG_REL = Path("live_trading/configs/alla_v4_ladder_k3h5_postclose_real.yaml")
DB_REL = Path("live_trading/data/alla_v4_ladder_k3h5_postclose_real.db")


def _write_yaml(root: Path, opening_cash: float) -> None:
    path = root / CONFIG_REL
    path.parent.mkdir(parents=True)
    path.write_text(textwrap.dedent(f"""\
        account:
          opening_cash: {opening_cash}
          opening_value_adjustment: 0.0
        storage:
          db_path: "{DB_REL.as_posix()}"
        model:
          members: []
        live:
          strategy_id: alla_v4_ladder_k3h5_postclose_real
    """), encoding="utf-8")


def test_preflight_does_not_create_the_new_ledger(tmp_path):
    _write_yaml(tmp_path, 1_000_000.0)
    result = cutover_preflight(tmp_path, skip_parity=True, skip_sha=True)
    assert result["new_ledger_exists"] is False
    assert not (tmp_path / DB_REL).exists()


def test_preflight_flags_placeholder_opening_cash(tmp_path):
    _write_yaml(tmp_path, 1_000_000.0)
    result = cutover_preflight(tmp_path, skip_parity=True, skip_sha=True)
    assert result["opening_cash_is_placeholder"] is True


def test_preflight_clears_placeholder_after_cash_is_written(tmp_path):
    _write_yaml(tmp_path, 123_456.78)
    result = cutover_preflight(tmp_path, skip_parity=True, skip_sha=True)
    assert result["opening_cash_is_placeholder"] is False


def test_preflight_source_never_imports_the_live_recorder():
    text = Path(
        "live_trading/scripts/cutover_preflight.py"
    ).read_text(encoding="utf-8")
    assert "import LiveRecorder" not in text
    assert "fill_importer" not in text
