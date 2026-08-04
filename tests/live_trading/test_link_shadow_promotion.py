import json

from live_trading.modules.fill_importer import LiveRecorder
from live_trading.scripts.link_shadow_promotion import main


def test_cli_links_real_ledger_batches(tmp_path, capsys):
    db_path = tmp_path / "live.db"
    recorder = LiveRecorder(str(db_path))
    old = "20260805_same_001"
    new = "20260805_same_002"
    recorder.record_batch(old, "2026-08-05", "SIMULATE", 1)
    recorder.record_batch(new, "2026-08-05", "LIVE", 1)

    assert main([
        "--db-path", str(db_path),
        "--source-batch", old,
        "--replacement-batch", new,
    ]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "changed": True,
        "replacement_batch_id": new,
        "source_batch_id": old,
    }
    assert recorder.get_batch(old)["superseded_by"] == new
