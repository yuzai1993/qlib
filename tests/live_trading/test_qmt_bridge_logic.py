"""QMT 内置桥接策略的纯逻辑测试（不依赖 QMT API）。

覆盖设计文档定稿的安全关键路径：
- 过期批次（trade_date != 当日）整批 SKIPPED
- 重复批次 SKIPPED duplicate
- checksum 不符整批拒绝
- 合法批次正确认领并按 priority 排序（先卖后买）
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from live_trading.modules.signal_schema import compute_checksum

BRIDGE_PATH = REPO_ROOT / "live_trading" / "qmt_strategy" / "qmt_signal_bridge.py"
BATCH_ID = "20260714_csi300_topk10_001"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("qmt_signal_bridge", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.BRIDGE_ROOT = str(tmp_path)
    mod._ensure_dirs()
    mod._load_processed()
    return mod


def test_qmt_cash_reservation_fees_match_live_config(bridge):
    config_path = (
        REPO_ROOT / "live_trading" / "configs" /
        "csi1000_b6m_b2s_postclose.yaml"
    )
    fees = yaml.safe_load(config_path.read_text(encoding="utf-8"))["fees"]

    assert bridge.COMMISSION_RATE == pytest.approx(fees["commission_rate"])
    assert bridge.MIN_COMMISSION == pytest.approx(fees["min_commission"])
    assert bridge.TRANSFER_FEE_RATE == pytest.approx(fees["transfer_fee_rate"])


def test_init_registers_post_close_timer_independent_of_market_bars(bridge):
    assert bridge.TRADE_START == "15:05:00"
    assert bridge.SELL_WAIT_TIMEOUT_SEC == 240
    assert bridge.CANCEL_AT == "15:28:00"
    assert bridge.FINALIZE_AT == "15:30:00"
    assert bridge.SNAPSHOT_REFRESH_AT == "15:31:00"
    assert bridge.MAX_ORDER_QUANTITY == 100
    calls = []

    class Context:
        def schedule_run(self, *args):
            calls.append(args)
            return 1

    bridge.init(Context())

    assert len(calls) == 1
    callback, first_at, repeats, interval, name = calls[0]
    assert callback is bridge.timer_callback
    assert first_at.endswith("150455")
    assert repeats == -1
    assert interval.total_seconds() == bridge.POLL_SECONDS
    assert name == "qlib_postclose_poll"


def test_timer_registration_is_safe_when_qmt_invokes_overdue_timer_immediately(
    bridge, monkeypatch,
):
    advances = []
    monkeypatch.setattr(
        bridge, "_advance", lambda context: advances.append(context),
    )

    class Context:
        def schedule_run(self, callback, *args):
            callback(self)
            return 1

    context = Context()
    bridge.init(context)

    assert bridge.g.timer_registered is True
    assert advances == [context]


def _order(coid="20260714001S", side="SELL", priority=10):
    is_buy = side == "BUY"
    return {
        "type": "order", "batch_id": BATCH_ID, "client_order_id": coid,
        "stock_code": "000001.SZ", "side": side,
        "quantity": 0 if is_buy else 800,
        "target_value": 8_000.0 if is_buy else 0.0,
        "price_type": "AFTER_HOURS_CLOSE", "limit_price": 0.0,
        "priority": priority,
        "instrument_qlib": "SZ000001", "reason": "test",
    }


def _write_batch(
    bridge, trade_date, orders, checksum=None, batch_id=BATCH_ID,
    mode="SIMULATE", account_environment="SIMULATION", account_id="1",
):
    order_lines = [json.dumps(o, sort_keys=True, separators=(",", ":")) for o in orders]
    if checksum is None:
        checksum = compute_checksum(order_lines)
    header = {
        "type": "batch_header", "schema_version": "2.0", "batch_id": batch_id,
        "strategy_id": "s", "trade_date": trade_date, "signal_date": trade_date,
        "account_id": account_id, "account_type": "STOCK",
        "account_environment": account_environment, "mode": mode,
        "created_at": "t", "order_count": len(orders), "checksum": checksum,
    }
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    jsonl = inbox / ("signal_%s.jsonl" % batch_id)
    jsonl.write_text(
        "\n".join([json.dumps(header, sort_keys=True)] + order_lines) + "\n")
    (inbox / ("signal_%s.done" % batch_id)).write_text(checksum + "\n")


def _read_fills(bridge, batch_id=BATCH_ID):
    p = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.jsonl" % batch_id)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_expired_batch_skipped(bridge):
    _write_batch(bridge, "2020-01-01", [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert len(fills) == 1
    assert fills[0]["status"] == "SKIPPED"
    assert "expired" in fills[0]["message"]
    # done 已写出、batch 已登记 processed
    done = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.done" % BATCH_ID)
    assert done.exists()
    assert BATCH_ID in bridge.g.processed


def test_future_batch_left_in_inbox(bridge):
    """T-1 晚发布的次日信号不应被提前消费，留在 inbox 等到 trade_date 当天。"""
    _write_batch(bridge, "2099-12-31", [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    # 未认领：文件原地保留，无回执，未登记 processed
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    assert (inbox / ("signal_%s.jsonl" % BATCH_ID)).exists()
    assert (inbox / ("signal_%s.done" % BATCH_ID)).exists()
    assert _read_fills(bridge) == []
    assert BATCH_ID not in bridge.g.processed


def test_future_batch_claimed_on_trade_date(bridge):
    """到了 trade_date 当天，之前留在 inbox 的批次可正常认领。"""
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is not None
    assert bridge.g.batch.batch_id() == BATCH_ID


def test_checksum_mismatch_rejected(bridge):
    _write_batch(bridge, bridge._today(), [_order()], checksum="sha256:deadbeef")
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills and fills[0]["status"] == "SKIPPED"
    assert "checksum" in fills[0]["message"]


def test_duplicate_batch_skipped(bridge):
    bridge._mark_processed(BATCH_ID)
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills and "duplicate" in fills[0]["message"]


def test_valid_batch_claimed_sells_first(bridge):
    orders = [
        _order(coid="20260714002B", side="BUY", priority=20),
        _order(coid="20260714001S", side="SELL", priority=10),
    ]
    _write_batch(bridge, bridge._today(), orders)
    bridge._claim_new_batch()
    batch = bridge.g.batch
    assert batch is not None
    assert [o["side"] for o in batch.orders] == ["SELL", "BUY"]
    # 文件从 inbox 认领后保留在 processing，直到批次完成才归档
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    assert list(inbox.glob("*")) == []
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    assert len(list(processing.glob("signal_*"))) == 2
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    assert list(archive.glob("signal_*")) == []


def test_empty_batch_finalizes_with_empty_receipt_file(bridge, monkeypatch):
    _write_batch(bridge, bridge._today(), [])
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:00")

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert bridge.g.batch is None
    fills = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.jsonl" % BATCH_ID)
    done = fills.with_suffix(".done")
    assert fills.exists() and fills.read_text() == ""
    assert done.exists()


@pytest.mark.parametrize(
    "header_change,reason",
    [
        ({"schema_version": "1.0"}, "schema_version"),
        ({"account_environment": "REAL"}, "account_environment"),
        ({"mode": "REAL"}, "mode"),
    ],
)
def test_protocol_v2_and_simulation_account_are_fail_closed(
    bridge, header_change, reason,
):
    _write_batch(bridge, bridge._today(), [_order()])
    path = Path(bridge.BRIDGE_ROOT) / "inbox" / ("signal_%s.jsonl" % BATCH_ID)
    lines = path.read_text().splitlines()
    header = json.loads(lines[0])
    header.update(header_change)
    path.write_text(
        "\n".join([json.dumps(header, sort_keys=True)] + lines[1:]) + "\n"
    )

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert reason in _read_fills(bridge)[0]["message"]


def test_real_batch_requires_explicit_qmt_real_money_opt_in(
    bridge, monkeypatch,
):
    bridge.ACCOUNT_ENVIRONMENT = "REAL"
    bridge.ACCOUNT_ID = "8890116049"
    bridge.ALLOW_REAL_MONEY = False
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    _write_batch(
        bridge, bridge._today(), [order], mode="LIVE",
        account_environment="REAL", account_id="8890116049",
    )
    monkeypatch.setattr(
        bridge, "passorder",
        lambda *args: pytest.fail("real opt-in missing; must not submit"),
        raising=False,
    )

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    fills = _read_fills(bridge)
    assert fills[0]["status"] == "SKIPPED"
    assert "ALLOW_REAL_MONEY" in fills[0]["message"]


class _RealAccountRow:
    m_strAccountID = "8890116049"
    m_dAvailable = 1_000_000.0


def test_real_account_preflight_accepts_exact_empty_account(bridge, monkeypatch):
    bridge.ACCOUNT_ENVIRONMENT = "REAL"
    bridge.ACCOUNT_ID = "8890116049"
    bridge.ALLOW_REAL_MONEY = True

    def query(account_id, account_type, kind):
        return [_RealAccountRow()] if kind == "ACCOUNT" else []

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", query, raising=False,
    )

    assert bridge._real_account_preflight("8890116049") == (True, "")


@pytest.mark.parametrize("available,positions,reason", [
    (999_000.0, [], "available cash"),
    (1_000_000.0, [object()], "not empty"),
])
def test_real_account_preflight_rejects_unexpected_state(
    bridge, monkeypatch, available, positions, reason,
):
    bridge.ACCOUNT_ENVIRONMENT = "REAL"
    bridge.ACCOUNT_ID = "8890116049"
    bridge.ALLOW_REAL_MONEY = True

    class Account:
        m_strAccountID = "8890116049"
        m_dAvailable = available

    class Position:
        m_nVolume = 100

    def query(account_id, account_type, kind):
        if kind == "ACCOUNT":
            return [Account()]
        return [Position()] if positions else []

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", query, raising=False,
    )

    ok, message = bridge._real_account_preflight("8890116049")
    assert ok is False
    assert reason in message


def test_batch_order_count_is_bounded_before_execution(bridge):
    orders = [
        _order(coid="202607140%03dS" % index)
        for index in range(bridge.MAX_ORDERS_PER_BATCH + 1)
    ]
    _write_batch(bridge, bridge._today(), orders)

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert "maximum" in _read_fills(bridge)[0]["message"]


def test_malformed_batch_is_quarantined_without_blocking_next_batch(bridge):
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    (inbox / "signal_000_bad.jsonl").write_text("{not-json}\n")
    (inbox / "signal_000_bad.done").write_text("sha256:bad\n")

    valid_id = "20260714_csi300_topk10_002"
    valid_order = _order()
    valid_order["batch_id"] = valid_id
    _write_batch(
        bridge, bridge._today(), [valid_order], batch_id=valid_id,
    )

    bridge._claim_new_batch()

    assert bridge.g.batch.batch_id() == valid_id
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    assert (archive / "signal_000_bad.jsonl").exists()
    assert (archive / "signal_000_bad.done").exists()


def test_structurally_invalid_order_fails_closed_without_crashing(bridge):
    invalid = _order()
    invalid.pop("client_order_id")
    _write_batch(bridge, bridge._today(), [invalid])

    bridge._claim_new_batch()

    assert bridge.g.batch is None
    assert BATCH_ID in bridge.g.processed
    assert _read_fills(bridge) == []


def test_archive_collision_preserves_both_batch_copies(bridge):
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    archive = Path(bridge.BRIDGE_ROOT) / "archive"
    jsonl = processing / "signal_repeat.jsonl"
    done = processing / "signal_repeat.done"
    jsonl.write_text("new-jsonl")
    done.write_text("new-done")
    (archive / jsonl.name).write_text("original-jsonl")
    (archive / done.name).write_text("original-done")

    bridge._archive_processing(str(jsonl), str(done))

    assert (archive / jsonl.name).read_text() == "original-jsonl"
    assert (archive / done.name).read_text() == "original-done"
    repeats = sorted(archive.glob("signal_repeat.repeat_*"))
    assert len(repeats) == 2
    assert {path.read_text() for path in repeats} == {"new-jsonl", "new-done"}


def test_processed_batch_state_is_idempotent(bridge):
    bridge._mark_processed(BATCH_ID)
    bridge._mark_processed(BATCH_ID)

    lines = (
        Path(bridge.BRIDGE_ROOT) / "state" / "processed_batches.txt"
    ).read_text().splitlines()
    assert lines == [BATCH_ID]


def test_restart_recovers_active_processing_batch(bridge):
    _write_batch(bridge, bridge._today(), [_order()])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    batch.phase = "BUY"
    batch.phase_started = 1234.5
    batch.submitted[_order()["client_order_id"]] = True
    batch.remaining_cash = 1234.5
    bridge._save_active_state(batch)

    bridge.g.batch = None
    bridge._recover_processing_batch()

    recovered = bridge.g.batch
    assert recovered is not None
    assert recovered.phase == "BUY"
    assert recovered.phase_started == pytest.approx(1234.5)
    assert recovered.remaining_cash == pytest.approx(1234.5)
    assert _order()["client_order_id"] in recovered.submitted


def test_corrupt_active_state_recovers_without_duplicate_submission(bridge):
    orders = [
        _order(coid="20260714001001S", side="SELL", priority=10),
        _order(coid="20260714001002B", side="BUY", priority=20),
    ]
    _write_batch(bridge, bridge._today(), orders, mode="LIVE")
    bridge._claim_new_batch()
    active = (
        Path(bridge.BRIDGE_ROOT) / "state" / ("active_" + BATCH_ID + ".json")
    )
    active.write_text("{broken")
    bridge.g.batch = None

    bridge._recover_processing_batch()

    recovered = bridge.g.batch
    assert recovered.execution_live is True
    assert set(recovered.submitted) == {
        "20260714001001S", "20260714001002B",
    }
    assert active.exists()
    assert list(active.parent.glob(active.name + ".corrupt_*"))


def test_restart_repairs_claim_interrupted_between_two_file_moves(bridge):
    _write_batch(bridge, bridge._today(), [_order()])
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    processing = Path(bridge.BRIDGE_ROOT) / "processing"
    jsonl_name = "signal_%s.jsonl" % BATCH_ID
    done_name = "signal_%s.done" % BATCH_ID
    (inbox / jsonl_name).rename(processing / jsonl_name)
    assert (inbox / done_name).exists()

    bridge._recover_processing_batch()

    assert bridge.g.batch is not None
    assert (processing / jsonl_name).exists()
    assert (processing / done_name).exists()
    assert not list(inbox.glob("signal_*"))


def test_max_affordable_quantity_includes_buy_fees(bridge):
    assert bridge._max_affordable_quantity(10000.0, 10.0, 1600) == 900
    assert bridge._max_affordable_quantity(1000.0, 10.0, 1600) == 0


def test_target_buy_quantity_respects_target_value_cash_and_fees(bridge):
    assert bridge._target_buy_quantity(10_000.0, 10.0, 8_000.0) == 800
    assert bridge._target_buy_quantity(1_000.0, 10.0, 8_000.0) == 0


def test_sell_phase_waits_full_four_minutes_before_any_buy(
    bridge, monkeypatch,
):
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [sell, buy])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    batch.trading_started = True
    batch.phase_started = 100.0
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:30")
    monkeypatch.setattr(bridge.time, "time", lambda: 101.0)

    bridge._process_batch(_TickCtx(10.0), batch)

    assert batch.phase == "SELL"
    assert buy["client_order_id"] not in batch.submitted

    monkeypatch.setattr(bridge.time, "time", lambda: 341.0)
    bridge._process_batch(_TickCtx(10.0), batch)

    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills[buy["client_order_id"]]["status"] == "SKIPPED"


def test_live_execution_gate_is_frozen_at_first_trading_pass(
    bridge, monkeypatch,
):
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [sell, buy], mode="LIVE")
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:30")
    monkeypatch.setattr(bridge.time, "time", lambda: 101.0)
    submitted = []
    account_queries = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    monkeypatch.setattr(
        bridge, "_get_available_cash", lambda account_id: 100_000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *args: account_queries.append(args) or [],
        raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge.g.batch.header["trade_date"])).write_text("")
    assert bridge._live_ok(bridge.g.batch.header["trade_date"])
    monkeypatch.setattr(bridge.time, "time", lambda: 341.0)
    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert submitted == []
    assert account_queries == []
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills[buy["client_order_id"]]["message"] == "simulated"


def test_removing_live_gate_blocks_later_buys_but_keeps_sell_management(
    bridge, monkeypatch,
):
    sell = _order(coid="20260714001001S", side="SELL", priority=10)
    buy = _order(coid="20260714001002B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [sell, buy], mode="LIVE")
    gate = (
        Path(bridge.BRIDGE_ROOT) / "state" /
        ("LIVE_OK_" + bridge._today())
    )
    gate.write_text("")
    bridge._claim_new_batch()
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:30")
    monkeypatch.setattr(bridge.time, "time", lambda: 101.0)
    monkeypatch.setattr(bridge, "_get_can_use_volume", lambda *args: 800)
    monkeypatch.setattr(
        bridge, "_get_available_cash", lambda account_id: 100_000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)
    gate.unlink()
    monkeypatch.setattr(bridge.time, "time", lambda: 341.0)
    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert [args[0] for args in submitted] == [24]
    assert bridge.g.batch.execution_live is True
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills[buy["client_order_id"]]["message"] == "simulated"


def test_buy_phase_uses_one_cash_snapshot_and_reserves_between_orders(
    bridge, monkeypatch,
):
    bridge.MAX_ORDER_QUANTITY = 0
    first = _order(coid="20260714001001B", side="BUY", priority=20)
    second = _order(coid="20260714001002B", side="BUY", priority=20)
    first.update(target_value=8_000.0)
    second.update(target_value=8_000.0, stock_code="600000.SH")
    _write_batch(bridge, bridge._today(), [first, second], mode="LIVE")
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    bridge._claim_new_batch()
    bridge.TRADE_START = "00:00:00"
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:00")

    cash_reads = []
    monkeypatch.setattr(
        bridge, "_get_available_cash",
        lambda account_id: cash_reads.append(account_id) or 10000.0,
    )
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    submitted = []

    def fake_passorder(*args):
        submitted.append({
            "price_type": args[4], "price": args[5], "quantity": args[6],
        })

    monkeypatch.setattr(bridge, "passorder", fake_passorder, raising=False)
    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert len(cash_reads) == 1
    assert [row["quantity"] for row in submitted] == [800, 100]
    assert all(row["price_type"] == 49 for row in submitted)
    assert all(row["price"] == 0.0 for row in submitted)


def test_available_cash_distinguishes_empty_query_from_real_zero(
    bridge, monkeypatch,
):
    monkeypatch.setattr(
        bridge, "get_trade_detail_data", lambda *args: [], raising=False,
    )
    assert bridge._get_available_cash("8881352838") is None

    class Account:
        m_strAccountID = "8881352838"
        m_dAvailable = 0.0

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", lambda *args: [Account()],
        raising=False,
    )
    assert bridge._get_available_cash("8881352838") == 0.0


def test_buy_phase_waits_when_account_cash_unavailable(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    _write_batch(bridge, bridge._today(), [order], mode="LIVE")
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    bridge._claim_new_batch()
    bridge.TRADE_START = "00:00:00"
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:00")
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: None)
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})
    monkeypatch.setattr(
        bridge, "passorder",
        lambda *args: pytest.fail("unavailable cash must not submit"),
        raising=False,
    )

    bridge._process_batch(_TickCtx(10.0), bridge.g.batch)

    assert bridge.g.batch.submitted == {}
    assert bridge.g.batch.remaining_cash is None
    assert _read_fills(bridge) == []


def test_cash_unavailable_at_close_writes_explicit_error(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8881352838", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.phase = "BUY"
    batch.trading_started = True
    bridge.g.batch = batch
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:30:00")
    monkeypatch.setattr(bridge, "_get_orders_by_remark", lambda account_id: {})

    bridge._force_finalize_if_near_close(object(), batch)

    fills = _read_fills(bridge)
    assert len(fills) == 1
    assert fills[0]["status"] == "ERROR"
    assert fills[0]["message"] == "account cash unavailable at close"


def test_removing_live_switch_still_cancels_already_submitted_orders(
    bridge, monkeypatch,
):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    batch.fills[order["client_order_id"]] = {
        "status": "ACCEPTED", "filled_qty": 0, "avg_price": 0.0,
    }

    class Detail:
        m_strOrderSysID = "qmt-order-1"
        m_nOrderStatus = -1
        m_nVolumeTraded = 0
        m_dTradedPrice = 0.0

    canceled = []
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:28:30")
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {order["client_order_id"]: [Detail()]},
    )
    monkeypatch.setattr(bridge, "can_cancel_order", lambda *args: True, raising=False)
    monkeypatch.setattr(
        bridge, "cancel", lambda *args: canceled.append(args), raising=False,
    )

    # There is intentionally no LIVE_OK file: it was removed after submission.
    bridge._force_finalize_if_near_close(object(), batch)

    assert len(canceled) == 1


def test_no_new_orders_are_submitted_after_cancel_cutoff(bridge, monkeypatch):
    order = _order(coid="20260714001001B", side="BUY", priority=20)
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.phase = "BUY"
    (Path(bridge.BRIDGE_ROOT) / "state" /
     ("LIVE_OK_" + bridge._today())).write_text("")
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:28:30")
    submitted = []
    monkeypatch.setattr(
        bridge, "passorder", lambda *args: submitted.append(args), raising=False,
    )
    monkeypatch.setattr(bridge, "_get_available_cash", lambda account_id: 100000.0)

    bridge._process_batch(_TickCtx(10.0), batch)

    assert submitted == []
    assert order["client_order_id"] not in batch.submitted


def test_no_done_no_claim(bridge):
    inbox = Path(bridge.BRIDGE_ROOT) / "inbox"
    (inbox / ("signal_%s.jsonl" % BATCH_ID)).write_text("{}\n")
    bridge._claim_new_batch()
    assert bridge.g.batch is None
    # jsonl 仍留在 inbox（未消费）
    assert (inbox / ("signal_%s.jsonl" % BATCH_ID)).exists()


class _TickCtx:
    """Fake ContextInfo exposing QMT tick and instrument-detail fields."""
    def __init__(
        self, last_price, ask_price=None, bid_price=None,
        up_stop=0.0, down_stop=0.0, detail_error=False,
    ):
        self._last = last_price
        self._ask = [] if ask_price is None else [ask_price]
        self._bid = [] if bid_price is None else [bid_price]
        self._up_stop = up_stop
        self._down_stop = down_stop
        self._detail_error = detail_error

    def get_full_tick(self, codes):
        return {
            c: {
                "lastPrice": self._last,
                "askPrice": self._ask,
                "bidPrice": self._bid,
            }
            for c in codes
        }

    def get_instrumentdetail(self, stock_code):
        if self._detail_error:
            raise RuntimeError("instrument detail unavailable")
        return {
            "UpStopPrice": self._up_stop,
            "DownStopPrice": self._down_stop,
        }


def test_official_close_uses_last_price_without_book_slippage(bridge):
    ctx = _TickCtx(10.50, ask_price=10.99, bid_price=9.01)

    assert bridge._official_close(ctx, "000001.SZ") == 10.50


@pytest.mark.parametrize("last_price", [0.0, float("nan")])
def test_official_close_fails_closed_without_positive_last_price(
    bridge, last_price,
):
    assert bridge._official_close(_TickCtx(last_price), "000001.SZ") == 0.0


def test_simulate_batch_processes_without_qmt_api(bridge, monkeypatch):
    """SIMULATE 模式下全流程不触碰 QMT API，直接产出 simulated 回执。"""
    bridge.TRADE_START = "00:00:00"  # 允许任何时间提交
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:05:00")
    _write_batch(bridge, bridge._today(), [
        _order(coid="20260714001S", side="SELL", priority=10),
        _order(coid="20260714002B", side="BUY", priority=20),
    ])
    bridge._claim_new_batch()
    batch = bridge.g.batch
    assert batch is not None
    batch.trading_started = True
    batch.phase_started = 100.0
    monkeypatch.setattr(bridge.time, "time", lambda: 341.0)
    bridge._process_batch(_TickCtx(10.0), batch)
    # SIMULATE：两单都 SKIPPED simulated，批次终结
    fills = {f["client_order_id"]: f for f in _read_fills(bridge)}
    assert fills["20260714001S"]["status"] == "SKIPPED"
    assert fills["20260714001S"]["message"] == "simulated"
    assert fills["20260714002B"]["message"] == "simulated"
    assert fills["20260714002B"]["requested_qty"] == 800
    assert bridge.g.batch is None  # finalized
    done = Path(bridge.BRIDGE_ROOT) / "outbound" / ("fills_%s.done" % BATCH_ID)
    assert done.exists()
    assert not list((Path(bridge.BRIDGE_ROOT) / "processing").glob("signal_*"))
    assert len(list((Path(bridge.BRIDGE_ROOT) / "archive").glob("signal_*"))) == 2


class _OrderDetail:
    def __init__(self, sysid, status, traded, price):
        self.m_strOrderSysID = sysid
        self.m_nOrderStatus = status
        self.m_nVolumeTraded = traded
        self.m_dTradedPrice = price


def test_summarize_remark_orders_aggregates_star_board_split(bridge):
    """科创板拆单：同 remark 多合同编号应汇总成交量/均价。"""
    details = [
        _OrderDetail("175", bridge.STATUS_SUCCEEDED, 100000, 4.14),
        _OrderDetail("176", bridge.STATUS_SUCCEEDED, 100000, 4.14),
        _OrderDetail("177", bridge.STATUS_SUCCEEDED, 44500, 4.14),
    ]
    summary = bridge._summarize_remark_orders(details, 244500)
    assert summary["fill_status"] == "FILLED"
    assert summary["traded"] == 244500
    assert summary["avg_price"] == pytest.approx(4.14)
    assert summary["qmt_order_id"] == "175,176,177"


def test_summarize_remark_orders_does_not_fill_on_single_child(bridge):
    """仅一笔子单已成且数量不足时，不得误标 FILLED。"""
    details = [_OrderDetail("177", bridge.STATUS_SUCCEEDED, 44500, 4.14)]
    summary = bridge._summarize_remark_orders(details, 244500)
    assert summary["fill_status"] == "ACCEPTED"
    assert summary["traded"] == 44500
    assert "partial" in summary["message"]


def test_poll_status_sums_split_children(bridge, monkeypatch):
    order = _order(coid="20260714001004B", side="BUY", priority=20)
    order["quantity"] = 244500
    order["stock_code"] = "688223.SH"
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {
            order["client_order_id"]: [
                _OrderDetail("175", bridge.STATUS_SUCCEEDED, 100000, 4.14),
                _OrderDetail("176", bridge.STATUS_SUCCEEDED, 100000, 4.14),
                _OrderDetail("177", bridge.STATUS_SUCCEEDED, 44500, 4.14),
            ],
        },
    )

    bridge._poll_status(batch)

    fill = batch.fills[order["client_order_id"]]
    assert fill["status"] == "FILLED"
    assert fill["filled_qty"] == 244500
    assert fill["qmt_order_id"] == "175,176,177"


def test_force_finalize_cancels_all_split_children(bridge, monkeypatch):
    order = _order(coid="20260714001004B", side="BUY", priority=20)
    order["quantity"] = 244500
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [order])
    batch.execution_live = True
    batch.submitted[order["client_order_id"]] = True
    batch.fills[order["client_order_id"]] = {
        "status": "ACCEPTED", "filled_qty": 100000, "avg_price": 4.14,
    }
    canceled = []

    class Working:
        m_strOrderSysID = "175"
        m_nOrderStatus = bridge.STATUS_PART_SUCC
        m_nVolumeTraded = 100000
        m_dTradedPrice = 4.14

    class Working2:
        m_strOrderSysID = "176"
        m_nOrderStatus = -1
        m_nVolumeTraded = 0
        m_dTradedPrice = 0.0

    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:28:30")
    monkeypatch.setattr(
        bridge, "_get_orders_by_remark",
        lambda account_id: {
            order["client_order_id"]: [Working(), Working2()],
        },
    )
    monkeypatch.setattr(bridge, "can_cancel_order", lambda *args: True, raising=False)
    monkeypatch.setattr(
        bridge, "cancel",
        lambda order_id, *args: canceled.append(order_id), raising=False,
    )

    bridge._force_finalize_if_near_close(object(), batch)

    assert set(canceled) == {"175", "176"}


class _AccountRow:
    m_strAccountID = "8881352838"
    m_dAvailable = 123456.78
    m_dBalance = 9876543.21
    m_dInstrumentValue = 9000000.0
    m_dFrozenCash = 0.0


class _PositionRow:
    def __init__(self, symbol, exchange, volume, can_use=0, cost=1.0):
        self.m_strInstrumentID = symbol
        self.m_strExchangeID = exchange
        self.m_nVolume = volume
        self.m_nCanUseVolume = can_use
        self.m_dOpenPrice = cost
        self.m_dMarketValue = volume * cost


def _read_account_snapshot(bridge, batch_id=BATCH_ID):
    p = (Path(bridge.BRIDGE_ROOT) / "outbound" / ("account_%s.jsonl" % batch_id))
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _live_batch(bridge, batch_id=BATCH_ID):
    header = {
        "batch_id": batch_id, "trade_date": bridge._today(), "mode": "LIVE",
        "account_id": "8881352838", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [_order()])
    batch.execution_live = True
    return batch


def test_finalize_writes_broker_account_snapshot(bridge, monkeypatch):
    batch = _live_batch(bridge)

    def fake_query(account_id, account_type, kind):
        if kind == "ACCOUNT":
            return [_AccountRow()]
        return [
            _PositionRow("688223", "SH", 244500, cost=4.14),
            _PositionRow("300308", "SZ", 1100, can_use=1100, cost=1072.36),
            _PositionRow("600000", "", 0),  # 空持仓不入快照
        ]

    monkeypatch.setattr(
        bridge, "get_trade_detail_data", fake_query, raising=False)

    bridge._finalize_batch(batch)

    rows = _read_account_snapshot(bridge)
    account = [r for r in rows if r["type"] == "account_snapshot"]
    positions = {r["stock_code"]: r for r in rows if r["type"] == "broker_position"}
    assert len(account) == 1
    assert account[0]["available_cash"] == pytest.approx(123456.78)
    assert set(positions) == {"688223.SH", "300308.SZ"}
    assert positions["688223.SH"]["shares"] == 244500
    assert positions["300308.SZ"]["can_use_volume"] == 1100
    # .done 与回执一致，Mac 侧只读完整文件
    assert (Path(bridge.BRIDGE_ROOT) / "outbound" /
            ("account_%s.done" % BATCH_ID)).exists()


def test_snapshot_failure_does_not_block_finalize(bridge, monkeypatch):
    batch = _live_batch(bridge)

    def boom(*args):
        raise RuntimeError("ACCOUNT query unavailable")

    monkeypatch.setattr(bridge, "get_trade_detail_data", boom, raising=False)

    bridge._finalize_batch(batch)

    assert batch.finalized
    assert (Path(bridge.BRIDGE_ROOT) / "outbound" /
            ("fills_%s.done" % BATCH_ID)).exists()


def test_simulate_batch_writes_no_broker_snapshot(bridge, monkeypatch):
    header = {
        "batch_id": BATCH_ID, "trade_date": bridge._today(), "mode": "SIMULATE",
        "account_id": "1", "account_environment": "SIMULATION",
        "schema_version": "2.0",
    }
    batch = bridge.Batch(header, [_order()])
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *args: pytest.fail("SIMULATE must not query the broker"),
        raising=False,
    )

    bridge._finalize_batch(batch)

    assert _read_account_snapshot(bridge) == []
    assert not _marker_path(bridge).exists()


def _marker_path(bridge, batch_id=BATCH_ID):
    return (Path(bridge.BRIDGE_ROOT) / "state" /
            ("snapshot_refresh_%s.json" % batch_id))


def test_post_close_refresh_rewrites_snapshot_with_close_values(
        bridge, monkeypatch):
    """finalize writes a marker and 15:31 refreshes the broker snapshot."""
    batch = _live_batch(bridge)
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *a: [_AccountRow()] if a[2] == "ACCOUNT" else [],
        raising=False)
    bridge._finalize_batch(batch)

    marker = _marker_path(bridge)
    assert json.loads(marker.read_text())["trade_date"] == bridge._today()
    assert _read_account_snapshot(bridge)[0]["available_cash"] == \
        pytest.approx(123456.78)

    class CloseAccount(_AccountRow):
        m_dAvailable = 654321.0
        m_dInstrumentValue = 9328041.0

    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *a: [CloseAccount()] if a[2] == "ACCOUNT" else
        [_PositionRow("688223", "SH", 244500, cost=4.14)],
        raising=False)

    # Before 15:31, leave the marker untouched.
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:30:59")
    bridge._refresh_account_snapshots_after_close()
    assert _read_account_snapshot(bridge)[0]["available_cash"] == \
        pytest.approx(123456.78)
    assert marker.exists()

    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:31:30")
    bridge._refresh_account_snapshots_after_close()
    rows = _read_account_snapshot(bridge)
    account = next(r for r in rows if r["type"] == "account_snapshot")
    assert account["available_cash"] == pytest.approx(654321.0)
    assert account["market_value"] == pytest.approx(9328041.0)
    positions = [r for r in rows if r["type"] == "broker_position"]
    assert positions and positions[0]["stock_code"] == "688223.SH"
    assert not marker.exists()

    # 标记已消费：再跑不应再查询券商
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *a: pytest.fail("consumed marker must not re-query"),
        raising=False)
    bridge._refresh_account_snapshots_after_close()


def test_stale_snapshot_marker_dropped_without_query(bridge, monkeypatch):
    """当日没刷新成（QMT 提前关了）：隔日标记直接丢弃，兜底快照仍有效。"""
    _marker_path(bridge).write_text(json.dumps({
        "batch_id": BATCH_ID, "trade_date": "2020-01-01", "account_id": "1"}))
    monkeypatch.setattr(bridge, "_now_hms", lambda: "15:32:00")
    monkeypatch.setattr(
        bridge, "get_trade_detail_data",
        lambda *a: pytest.fail("stale marker must not trigger a snapshot"),
        raising=False)

    bridge._refresh_account_snapshots_after_close()

    assert not _marker_path(bridge).exists()


@pytest.mark.parametrize("symbol,exchange,expected", [
    ("688223", "SH", "688223.SH"),
    ("300308", "SZ", "300308.SZ"),
    ("600000", "SHSE", "600000.SH"),
    ("000001", "SZSE", "000001.SZ"),
    ("688223", "", "688223.SH"),   # 交易所字段缺失时按代码前缀推断
    ("300308", "", "300308.SZ"),
    ("920001", "", "920001.SH"),
])
def test_qmt_stock_code_suffix(bridge, symbol, exchange, expected):
    assert bridge._qmt_stock_code(symbol, exchange) == expected
