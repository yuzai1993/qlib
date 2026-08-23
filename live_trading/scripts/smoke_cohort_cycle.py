"""三天阶梯循环自检：层数、到期、pending 重试。不碰 Qlib 数据、不碰真实账本。"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from live_trading.modules.cohort_store import (  # noqa: E402
    CohortState,
    advanced_state,
    state_to_ledger,
)
from live_trading.modules.fill_importer import LiveRecorder  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    recorder = LiveRecorder(str(Path(tmp) / "smoke.db"), opening_cash=1_000_000.0)
    recorder.save_cohort_state(CohortState())

    # 前 5 天各买一只，阶梯逐日长到 5 层
    for day in range(1, 6):
        date = f"2026-08-{day:02d}"
        state = advanced_state(
            recorder.load_cohort_state(), horizon=5, trade_date=date,
            sold={}, filled={f"SH60000{day}": 100},
        )
        recorder.save_cohort_state(state)
        assert len(state.layers) == day, (date, len(state.layers))

    # 第 6 天：最老层 SH600001 到期
    ledger_due = state_to_ledger(recorder.load_cohort_state(), horizon=5).due()
    assert ledger_due == {"SH600001": 100.0}, ledger_due

    # 卖掉一半：剩 50 股必须挂进 pending 次日重试
    state = advanced_state(
        recorder.load_cohort_state(), horizon=5, trade_date="2026-08-06",
        sold={"SH600001": 50}, filled={"SH600006": 100},
    )
    recorder.save_cohort_state(state)
    assert len(state.layers) == 5, len(state.layers)
    assert state.pending == {"SH600001": 50}, state.pending

    # 第 7 天：pending 残量 + 新到期层一起进 due
    due = state_to_ledger(recorder.load_cohort_state(), horizon=5).due()
    assert due["SH600001"] == 50.0, due
    assert due["SH600002"] == 100.0, due

    print("cohort ladder 3-day cycle OK")
